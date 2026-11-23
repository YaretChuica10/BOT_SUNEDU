"""Consulta interactiva con credenciales obtenidas mediante el flujo oficial."""

import getpass
import json
import sys

import httpx
import pyautogui

from rpa_query import RpaError

API_URL = (
    "https://constanciasapi.sunedu.gob.pe/api/VerificaGrados/"
    "obtener-grados-nac-ext-ct-verifica"
)


class ConsultaError(Exception):
    """Error seguro para mostrar en la terminal."""


def consultar(dni, bearer, token, ip_address, nombres="", cookie="", user_agent=""):
    """Conserva los campos originales para evitar suponer nombres de universidades o fechas."""
    if len(dni) != 8 or not dni.isascii() or not dni.isdigit():
        raise ConsultaError("El DNI debe contener exactamente ocho dígitos.")
    if not bearer or not token or not ip_address:
        raise ConsultaError("Faltan Bearer, Token o IpAddress de la solicitud autorizada.")
    headers = {"Authorization": f"Bearer {bearer}", "Accept": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    if user_agent:
        headers["User-Agent"] = user_agent
    payload = {
        "NumeroDocumento": dni,
        "ApellidosNombres": nombres,
        "IpAddress": ip_address,
        "Token": token,
    }
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            response = client.post(API_URL, headers=headers, json=payload)
    except httpx.RequestError:
        raise ConsultaError("Error de conexión con SUNEDU. Comprueba tu red e inténtalo después.") from None
    if response.headers.get("cf-mitigated") == "challenge":
        raise ConsultaError("Cloudflare requiere verificación. Completa el flujo oficial en el navegador.")
    if response.status_code == 401:
        raise ConsultaError("Autenticación rechazada. Renueva la sesión y el Bearer mediante el flujo oficial.")
    if response.status_code == 403:
        raise ConsultaError("Acceso rechazado. Verifica permisos, Token y sesión en el flujo oficial.")
    if response.status_code == 429:
        raise ConsultaError("Límite de consultas alcanzado. Espera antes de volver a consultar.")
    if not 200 <= response.status_code < 300:
        raise ConsultaError(f"SUNEDU respondió HTTP {response.status_code}.")
    try:
        data = response.json()
    except ValueError:
        raise ConsultaError("La respuesta no es JSON. Revisa la verificación del sitio en el navegador.") from None
    if not isinstance(data, dict) or not isinstance(data.get("oData"), dict):
        raise ConsultaError("Respuesta inesperada. Hace falta revisar el contrato de respuesta de SUNEDU.")
    result = {}
    for name in ("GradosNacionales", "GradosExtranjeros"):
        if name not in data["oData"]:
            raise ConsultaError("Faltan las colecciones de grados en la respuesta.")
        records = data["oData"][name]
        if records is None:
            records = []
        if not isinstance(records, list):
            raise ConsultaError("Formato inesperado en la colección de grados.")
        result[name] = records
    return {"oData": result}


def consultar_automaticamente(dni):
    """Inicia el flujo verificado y se detiene donde falta el contrato oficial."""
    if len(dni) != 8 or not dni.isascii() or not dni.isdigit():
        raise ConsultaError("El DNI debe contener exactamente ocho d?gitos.")
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            response = client.post(
                "https://constanciasapi.sunedu.gob.pe/api/Token/generar-token",
                json={"subject": "constancias-app"},
            )
    except httpx.RequestError:
        raise ConsultaError("No se pudo conectar con el servicio oficial de autenticaci?n.") from None
    if response.headers.get("cf-mitigated") == "challenge":
        raise ConsultaError("Cloudflare exige verificaci?n de navegador que HTTPX no puede completar.")
    if response.status_code in (401, 403):
        raise ConsultaError("El servicio oficial rechaz? la autenticaci?n autom?tica (HTTP 401/403).")
    if response.status_code == 429:
        raise ConsultaError("L?mite de solicitudes alcanzado. Int?ntalo despu?s.")
    if not response.is_success:
        raise ConsultaError(f"Autenticaci?n respondi? HTTP {response.status_code}.")
    try:
        auth = response.json()
    except ValueError:
        raise ConsultaError("Autenticaci?n devolvi? una respuesta incompatible con JSON.") from None
    if not isinstance(auth, dict) or auth.get("bSuccess") is not True or not isinstance(auth.get("oData"), str) or not auth["oData"]:
        raise ConsultaError("El servicio oficial no entreg? un JWT en el formato esperado.")
    raise ConsultaError(
        "JWT obtenido mediante el flujo oficial. Consulta detenida: falta confirmar el origen "
        "y validaci?n del campo Token del cuerpo. El portal usa Cloudflare Turnstile, "
        "cuya verificaci?n de navegador no puede completarse con HTTPX. "
        "Se necesita confirmar el contrato oficial de consulta o un acceso autorizado compatible."
    )


def main():
    try:
        dni = getpass.getpass("DNI autorizado (8 dígitos, entrada oculta): ").strip()
        from rpa_query import consultar_rpa
        print(consultar_rpa(dni))
        return 0
    except (ConsultaError, RpaError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except pyautogui.FailSafeException:
        print("RPA detenida mediante la esquina de seguridad del mouse.", file=sys.stderr)
        return 130
    except (KeyboardInterrupt, EOFError):
        print("\nConsulta cancelada.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
