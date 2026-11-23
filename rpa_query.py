"""Prueba RPA con Chrome normal, controles accesibles y PyAutoGUI."""

import os
from pathlib import Path
import subprocess
import sys
import unicodedata
import re
import cv2
import numpy as np

import httpx
import pyautogui
from pywinauto import Desktop
from pywinauto.timings import TimeoutError as WaitTimeout, wait_until_passes

PORTAL = "https://enlinea.sunedu.gob.pe/"


class RpaError(Exception):
    """Error apto para mostrar sin datos de sesión."""


def comprobar_conexion_cloudflare():
    """Prueba HTTP independiente. Nunca imprime cuerpos, cookies o URL de retos."""
    try:
        with httpx.Client(timeout=15, follow_redirects=True, max_redirects=5) as client:
            response = client.get(
                "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit",
            )
    except httpx.RequestError as error:
        print(
            f"[Red HTTPX] challenges.cloudflare.com falló ({type(error).__name__}). "
            "Comprueba DNS, TLS, proxy y conexión. Esta prueba es independiente de Chrome.",
            file=sys.stderr,
        )
        return
    print(
        f"[Red HTTPX] Script de Cloudflare, HTTP final {response.status_code}, "
        f"redirecciones {len(response.history)}, "
        f"JavaScript {'sí' if response.is_success and 'javascript' in response.headers.get('content-type', '').lower() else 'no confirmado'}. "
        "Prueba independiente de la sesión de Chrome.",
        file=sys.stderr,
    )


def esperar(condicion, timeout, mensaje):
    """Sondea el estado y termina en cuanto se cumple, con un plazo máximo."""
    def comprobar():
        result = condicion()
        if not result:
            raise LookupError()
        return result
    try:
        return wait_until_passes(timeout, 0.5, comprobar, exceptions=(LookupError,))
    except WaitTimeout:
        raise RpaError(mensaje) from None


def campo_dni(page_controls):
    for control in page_controls:
        name = control.window_text().strip().lower()
        if control.element_info.control_type == "Edit" and (
            control.element_info.automation_id == "DNI"
            or "documento de identidad" in name or name == "dni"
        ):
            return control
    return None


def esperar_cloudflare(window):
    registrado = False

    def comprobar():
        nonlocal registrado
        labels = {
            " ".join(control.window_text().casefold().strip(" ¡!.").split())
            for control in controles(window) if control.is_visible()
        }
        names = "\n".join(labels)
        # Comprueba primero el éxito: el iframe puede conservar en su título
        # la instrucción de verificar aunque el reto ya haya terminado.
        if labels.intersection({
            "success", "éxito", "correcto", "listo", "verificado",
            "verification successful", "verificación completada",
            "verificación correcta", "verificación exitosa", "verificación satisfactoria",
            "operación exitosa", "operation successful",
        }):
            print("[Chrome] Cloudflare muestra una validación completada.", file=sys.stderr)
            return True
        if any(message in names for message in (
            "unable to connect", "err_connection", "err_name_not_resolved",
            "err_cert", "no se puede acceder", "no se ha podido establecer conexión",
        )):
            print("[Chrome] El contenido accesible informa un error de conexión.", file=sys.stderr)
            raise RpaError("Falló la conexión visible del sitio o widget de Cloudflare. La consulta no fue enviada.")
        if not registrado and any(marker in names for marker in ("cloudflare", "turnstile", "challenges.cloudflare.com")):
            print("[Chrome] Widget de Cloudflare presente en accesibilidad. Validación pendiente.", file=sys.stderr)
            registrado = True
        return False

    esperar(comprobar, 60,
            "No se pudo confirmar la validación de Cloudflare mediante accesibilidad en 60 segundos. "
            "Chrome normal no expone sus eventos de red a esta RPA. No se puede afirmar que el recurso "
            "falló ni que la validación terminó. La consulta no fue enviada.")


def buscar_chrome():
    for root in (os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("LOCALAPPDATA", "")):
        executable = Path(root) / "Google/Chrome/Application/chrome.exe"
        if executable.is_file():
            return executable
    raise RpaError("No se encontró Google Chrome instalado.")


def controles(window):
    try:
        return window.descendants()
    except Exception:
        raise RpaError("No se pudo leer la ventana de Chrome. Mantén la sesión de Windows desbloqueada.") from None


def texto_visible(window, incluir_fuera_de_pantalla=False):
    lines = []
    for control in controles(window):
        if control.element_info.control_type in ("Text", "DataItem") and (incluir_fuera_de_pantalla or control.is_visible()):
            text = control.window_text().strip()
            if text and (not lines or lines[-1] != text):
                lines.append(text)
    return "\n".join(lines)


def encontrar_buscar(window):
    candidates = {}
    for control in controles(window):
        if control.element_info.control_type not in ("Button", "Text", "Hyperlink", "Custom"):
            continue
        name = " ".join(control.window_text().casefold().split())
        if not re.match(r"^buscar(?:\s|$|[\W])", name):
            continue
        if not control.is_visible() or not control.is_enabled():
            continue
        parent = control.parent()
        if parent.element_info.control_type == "Button":
            control = parent
        if not control.is_visible() or not control.is_enabled():
            continue
        rectangle = control.rectangle()
        if rectangle.width() <= 0 or rectangle.height() <= 0:
            continue
        candidates[(rectangle.left, rectangle.top, rectangle.right, rectangle.bottom)] = control
    if candidates:
        # El texto y el contenedor pueden representar el mismo botón con
        # rectángulos distintos. Elige el menor solo si los demás lo contienen.
        ordered = sorted(candidates.values(), key=lambda item: item.rectangle().width() * item.rectangle().height())
        selected = ordered[0]
        point = selected.rectangle().mid_point()
        if all(
            item.rectangle().left <= point.x <= item.rectangle().right
            and item.rectangle().top <= point.y <= item.rectangle().bottom
            for item in ordered
        ):
            return selected
    return None


def encontrar_buscar_visual(window):
    """Compara únicamente el botón de referencia. La pantalla queda en memoria."""
    window.set_focus()
    rect = window.rectangle()
    screen_width, screen_height = pyautogui.size()
    left, top = max(0, rect.left), max(0, rect.top)
    width, height = min(screen_width, rect.right) - left, min(screen_height, rect.bottom) - top
    if width <= 0 or height <= 0:
        return None
    reference = cv2.imread(str(Path(__file__).parent / "rpa_assets" / "buscar.png"))
    if reference is None:
        raise RpaError("Falta la referencia visual rpa_assets/buscar.png.")
    screenshot = cv2.cvtColor(np.array(pyautogui.screenshot(region=(left, top, width, height))), cv2.COLOR_RGB2BGR)
    best_score, best_point = 0, None
    for scale in np.arange(0.7, 1.81, 0.05):
        template = cv2.resize(reference, None, fx=float(scale), fy=float(scale))
        h, w = template.shape[:2]
        if h > height or w > width:
            continue
        scores = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, point = cv2.minMaxLoc(scores)
        if score > best_score:
            best_score = score
            best_point = (left + point[0] + w // 2, top + point[1] + h // 2)
    return best_point if best_score >= 0.90 else None


def pulsar(window, control):
    window.set_focus()
    rectangle = control.rectangle()
    if rectangle.width() <= 0 or rectangle.height() <= 0:
        raise RpaError("El control del formulario no está visible.")
    pyautogui.click(rectangle.mid_point().x, rectangle.mid_point().y)


def consultar_rpa(dni, cerrar_al_terminar=False):
    if len(dni) != 8 or not dni.isascii() or not dni.isdigit():
        raise RpaError("El DNI debe contener ocho dígitos.")
    # Un perfil separado evita que Chrome delegue en una instancia ya abierta
    # que haya ignorado la opción de accesibilidad del nuevo proceso.
    profile = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BOT_SUNEDU" / "ChromeRPA"
    desktop = Desktop(backend="uia")
    anteriores = {window.handle for window in desktop.windows(class_name="Chrome_WidgetWin_1")}
    subprocess.Popen([
        str(buscar_chrome()), f"--user-data-dir={profile}", "--new-window",
        "--force-renderer-accessibility", "--no-first-run", PORTAL,
    ])
    seleccionada = {}
    try:
        comprobar_conexion_cloudflare()
        return consultar_formulario(dni, desktop, anteriores, seleccionada)
    finally:
        if cerrar_al_terminar and "window" in seleccionada:
            try:
                seleccionada["window"].close()
            except Exception:
                print("[Chrome] La ventana del lote no pudo cerrarse automáticamente.", file=sys.stderr)


def consultar_formulario(dni, desktop, anteriores, seleccionada):
    navigation = {"clicked": False, "aplicativo": False}

    def encontrar_formulario():
        for candidate in desktop.windows(class_name="Chrome_WidgetWin_1"):
            if candidate.handle in anteriores:
                continue
            if "window" in seleccionada and candidate.handle != seleccionada["window"].handle:
                continue
            if "SUNEDU" not in candidate.window_text().upper():
                continue
            seleccionada["window"] = candidate
            page_controls = controles(candidate)
            field = campo_dni(page_controls)
            if field is not None and field.is_visible() and field.is_enabled():
                return candidate, field
            if not navigation["clicked"]:
                for control in page_controls:
                    name = " ".join(control.window_text().lower().split())
                    if "verifica" in name and "inscrito" in name and control.element_info.control_type in ("Text", "Hyperlink", "Button") and control.is_visible() and control.is_enabled():
                        pulsar(candidate, control)
                        navigation["clicked"] = True
                        print("[Chrome] Opción de consulta abierta automáticamente.", file=sys.stderr)
                        break
            elif not navigation["aplicativo"]:
                for control in page_controls:
                    name = " ".join(control.window_text().lower().split())
                    if name == "aplicativo" and control.element_info.control_type in ("Button", "TabItem") and control.is_visible() and control.is_enabled():
                        pulsar(candidate, control)
                        navigation["aplicativo"] = True
                        print("[Chrome] Pestaña Aplicativo seleccionada automáticamente.", file=sys.stderr)
                        break
        return None

    window, field = esperar(
        encontrar_formulario, 60,
        "El formulario DNI no estuvo visible y habilitado en 60 segundos. No se ingresaron datos.",
    )
    print("[Chrome] Formulario DNI visible y habilitado.", file=sys.stderr)
    pulsar(window, field)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.write(dni)
    esperar_cloudflare(window)
    button = esperar(lambda: encontrar_buscar(window) or encontrar_buscar_visual(window), 15,
                     "La validación terminó, pero no se identificó un único control Buscar visible y habilitado.")
    before = texto_visible(window, incluir_fuera_de_pantalla=True)
    if isinstance(button, tuple):
        pyautogui.click(*button)
    else:
        pulsar(window, button)
    print("[Chrome] Buscar pulsado. Esperando resultados.", file=sys.stderr)
    def obtener_resultado():
        text = texto_visible(window, incluir_fuera_de_pantalla=True)
        lower = "".join(character for character in unicodedata.normalize("NFD", text.casefold()) if not unicodedata.combining(character))
        if "no se encontraron resultados" in lower:
            return "SUNEDU no encontro resultados para la consulta."
        if "verificaci" in lower and ("fallida" in lower or "debe completar" in lower):
            raise RpaError("SUNEDU informa que la validacion de seguridad no fue completada.")
        documento_confirmado = re.search(rf"\bDNI\s*:?\s*{re.escape(dni)}\b", text, re.IGNORECASE)
        if documento_confirmado and text != before and any(label in lower for label in ("fecha de diploma", "fecha de expedici", "fecha de inscripci", "fecha de resoluci", "fecha de emision")):
            return text
        return None

    return esperar(obtener_resultado, 45,
                   "No se confirmaron resultados en 45 segundos. Chrome queda abierto para revisar el mensaje.")
