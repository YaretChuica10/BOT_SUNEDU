"""Consulta as?ncrona de SUNEDU y normalizaci?n del JSON."""
import asyncio
import json
import re
import sys
from datetime import datetime
from time import monotonic
from urllib.parse import urlsplit
from playwright.async_api import Error, TimeoutError

PORTAL = "https://enlinea.sunedu.gob.pe/"
DIRECTO = "https://constanciasweb.sunedu.gob.pe/#/modulos/grados-y-titulos"
API_PATH = "/api/VerificaGrados/obtener-grados-nac-ext-ct-verifica"


class ConsultaCamoufoxError(Exception):
    """Error seguro para mostrar sin credenciales ni datos de la consulta."""


def interpretar_respuesta(data, dni):
    if not isinstance(data, dict) or data.get("bSuccess") is not True:
        mensaje = str(data.get('sMessage') or '').lower() if isinstance(data, dict) else ''
        causa = 'Respuesta rechazada por SUNEDU; no se confirmó ausencia de grados.'
        if any(t in mensaje for t in ('captcha', 'turnstile', 'token', 'autoriz', 'sesión', 'sesion')):
            causa = 'SUNEDU rechazó la validación o autenticación de la consulta.'
        elif any(t in mensaje for t in ('no se encontr', 'no cuenta con', 'no registra')):
            causa = 'SUNEDU informó que no encontró información, pero devolvió bSuccess=false.'
        elif any(t in mensaje for t in ('intente', 'inténtelo', 'disponible', 'servicio')):
            causa = 'SUNEDU informó un problema de disponibilidad del servicio.'
        raise ConsultaCamoufoxError(causa)
    source = data.get("oData")
    if not isinstance(source, dict):
        raise ConsultaCamoufoxError("La respuesta no contiene oData válido.")
    records = []
    raw = {}
    for collection in ("GradosNacionales", "GradosExtranjeros"):
        if collection not in source:
            raise ConsultaCamoufoxError("La respuesta no contiene ambas colecciones de grados.")
        items = source[collection]
        if items is None:
            items = []
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ConsultaCamoufoxError("La colección de grados tiene un formato inesperado.")
        raw[collection] = items
        for item in items:
            documento = item.get("DocuNum", item.get("NumeroDocumento"))
            if documento is not None and str(documento).strip() != dni:
                raise ConsultaCamoufoxError("La respuesta incluye un documento diferente al solicitado.")
            if collection == "GradosNacionales":
                nombres = item.get("Persona") or " ".join(str(item.get(key) or "") for key in ("Apepat", "Apemat", "Nombre")).strip()
                record = {
                    "Nombres": nombres,
                    "Grado o título": item.get("GradTitu"),
                    "Institución": item.get("Universidad"),
                    "Fecha de diploma": item.get("DiplFec"),
                    "Fecha matrícula": item.get("MatriFec"),
                    "Fecha egreso": item.get("EgresFec"),
                }
            else:
                entidades = item.get("Entidades") or []
                if isinstance(entidades, str):
                    try:
                        entidades = json.loads(entidades)
                    except ValueError:
                        entidades = []
                instituciones = []
                if isinstance(entidades, list):
                    instituciones = [str(entidad["UNIVERSIDAD"]) for entidad in entidades if isinstance(entidad, dict) and entidad.get("UNIVERSIDAD")]
                record = {
                    "Nombres": " ".join(str(item.get(key) or "") for key in ("Apellidos", "Nombre")).strip(),
                    "Grado o título": item.get("DenominacionDiploma"),
                    "Institución": "; ".join(instituciones) or item.get("NombreEntidad"),
                    "Fecha de diploma": item.get("FechaEmisionDiploma"),
                    "Fecha de resolución": item.get("FechaResolucion"),
                }
            record.update({"DNI": dni, "Origen": collection})
            records.append(record)
    avisos = []
    for field, label in (("ServicioGradosNacionalesRespondeOk", "nacionales"), ("ServicioGradosExtranjerosRespondeOk", "extranjeros")):
        if source.get(field) is False:
            avisos.append(f"Servicio de grados {label} no disponible.")
    if any(not all(record.get(key) for key in ("Nombres", "Grado o título", "Institución")) for record in records):
        avisos.append("Hay campos sin mapear. Revisar los registros JSON originales.")
    return {"grados": records, "oData": raw, "avisos": avisos}


def registrar(numero, mensaje):
    hora = datetime.now().astimezone().isoformat(timespec="milliseconds")
    print(f"[{hora}] Consulta {numero}: {mensaje}", file=sys.stderr, flush=True)


async def acceso_directo(page, dni):
    try:
        await page.goto(DIRECTO, wait_until="domcontentloaded", timeout=30000)
        await page.locator('input[formcontrolname="dni"]').fill(dni)
        limite = monotonic() + 60
        while monotonic() < limite:
            if await page.evaluate('''mw:(() => {
                const response = document.querySelector('[name="cf-turnstile-response"]');
                if (response && response.value.trim()) return true;
                try { return typeof turnstile !== 'undefined' && !!turnstile.getResponse(); }
                catch { return false; }
            })()'''):
                return page
            await asyncio.sleep(0.25)
    except Error:
        pass
    return None


async def consultar(numero, dni, browser):
    inicio = monotonic()
    registrar(numero, "INICIO")
    resultado = {"consulta": numero, "dni": dni}
    etapa = 'abrir ventana'
    try:
        # Un navegador compartido, cookies y páginas aisladas por consulta.
        async with await browser.new_context() as context:
            page = await context.new_page()
            etapa = 'abrir formulario'
            page.set_default_timeout(30000)
            reutilizable = getattr(context, 'reutilizable', False)
            frame = None if reutilizable else await acceso_directo(page, dni)
            if frame is None:
                registrar(numero, "Acceso directo no validado. Usando portal.")
                if not reutilizable or page.url.rstrip('/') != PORTAL.rstrip('/'):
                    await page.goto(PORTAL, wait_until="domcontentloaded", timeout=60000)
                await page.get_by_text(re.compile(r"Verifica si.*inscrito", re.I)).first.locator("..").get_by_role("button").click()
                await page.get_by_role("button", name="Aplicativo", exact=True).click()
                iframe = page.locator('iframe[src*="constanciasweb.sunedu.gob.pe/"]')
                await iframe.wait_for(state="visible")
                handle = await iframe.element_handle()
                frame = await handle.content_frame() if handle else None
            else:
                registrar(numero, "Acceso directo validado.")
            if frame is None:
                raise ConsultaCamoufoxError("No se cargó el formulario oficial.")
            await frame.locator('input[formcontrolname="dni"]').fill(dni)
            etapa = 'validación Cloudflare'
            registrar(numero, "Formulario completado. Esperando validación normal.")
            limite = monotonic() + 60
            while True:
                validado = await frame.evaluate('''mw:(() => {
                    const response = document.querySelector(
                        'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]'
                    );
                    if (response && response.value.trim()) return true;
                    try {
                        return typeof turnstile !== 'undefined' && !!turnstile.getResponse();
                    } catch { return false; }
                })()''')
                if validado:
                    break
                if monotonic() >= limite:
                    raise ConsultaCamoufoxError(
                        "No se detectó validación automática en 60 segundos. "
                        "Cloudflare puede requerir intervención humana o presentar un error de carga."
                    )
                await asyncio.sleep(0.25)
            registrar(numero, "Validación completada. Consultando.")
            etapa = 'buscar y recibir respuesta'
            async with page.expect_response(lambda response: (
                urlsplit(response.url).hostname == "constanciasapi.sunedu.gob.pe"
                and urlsplit(response.url).path == API_PATH
                and response.request.method == "POST"
            ), timeout=60000) as pending:
                await frame.get_by_text("Buscar", exact=True).click()
            response = await pending.value
            registrar(numero, f'Respuesta SUNEDU HTTP {response.status}.')
            if response.status in (401, 403):
                raise ConsultaCamoufoxError("SUNEDU rechazó la autenticación o validación.")
            if response.status == 429:
                raise ConsultaCamoufoxError("SUNEDU limitó las consultas. Inténtalo después.")
            if not response.ok:
                raise ConsultaCamoufoxError(f"SUNEDU respondió HTTP {response.status}.")
            try:
                etapa = 'leer JSON'
                data = await response.json()
            except ValueError:
                raise ConsultaCamoufoxError("La respuesta no es JSON válido.") from None
            etapa = 'interpretar JSON'
            resultado["resultado"] = interpretar_respuesta(data, dni)
            registrar(numero, f"JSON interpretado: {len(resultado['resultado']['grados'])} grados.")
            etapa = 'devolver ventana al portal'
    except ConsultaCamoufoxError as exc:
        resultado["error"] = str(exc)
    except TimeoutError:
        resultado["error"] = "Se agotó el plazo para cargar el formulario o recibir la respuesta."
    except Error:
        resultado["error"] = "Error de navegador o conexión con SUNEDU."
    except Exception as exc:
        # No imprimir excepciones que puedan incluir URLs, tokens o datos.
        resultado["error"] = f"Error de ejecución ({type(exc).__name__})."
    finally:
        if 'error' in resultado:
            registrar(numero, f"Fallo en {etapa}: {resultado['error']}")
        fin = monotonic()
        registrar(numero, f"FIN; duración {fin - inicio:.2f} s")
    return resultado, inicio, fin


