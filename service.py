"""Consumidor continuo de SQL Server. Detener con Ctrl+C y esperar el cierre."""

import asyncio
import re
import signal
import sys
from time import monotonic
from uuid import uuid4

import pyodbc
from camoufox.async_api import AsyncCamoufox

from sunedu_client import consultar, registrar, PORTAL
from database import SqlStore, diagnostico_sql

REINICIO_SEGUNDOS = 24 * 60 * 60


class Ventanas:
    """Tres páginas permanentes; el exceso usa contextos temporales."""
    def __init__(self, browser):
        self.browser = browser
        self.libres = []

    async def crear(self):
        contexto = await self.browser.new_context()
        try:
            pagina = await contexto.new_page()
            await pagina.goto(PORTAL, wait_until='domcontentloaded', timeout=60000)
            return contexto, pagina
        except BaseException:
            await contexto.close()
            raise

    async def preparar(self, cantidad=3):
        for _ in range(cantidad):
            self.libres.append(await self.crear())

    async def new_context(self):
        permanente = bool(self.libres)
        pareja = self.libres.pop() if permanente else await self.crear()
        return VentanaPrestada(self, pareja, permanente)


class VentanaPrestada:

    """Gestiona el préstamo de un contexto de Camoufox,
    reutilizando las sesiones permanentes y cerrando las temporales
    al finalizar cada consulta para liberar recursos."""

    reutilizable = True

    def __init__(self, ventanas, pareja, permanente):
        self.ventanas = ventanas
        self.contexto, self.pagina = pareja
        self.permanente = permanente

    async def __aenter__(self):
        if self.pagina.is_closed():
            await self.contexto.close()
            self.contexto, self.pagina = await self.ventanas.crear()
        return self

    async def new_page(self):
        return self.pagina

    async def __aexit__(self, *args):
        if not self.permanente:
            await self.contexto.close()
            return
        try:
            # Nueva navegación elimina formulario y resultados anteriores.
            await self.pagina.goto(PORTAL, wait_until='domcontentloaded', timeout=60000)
        except Exception:
            await self.contexto.close()
            self.contexto, self.pagina = await self.ventanas.crear()
        self.ventanas.libres.append((self.contexto, self.pagina))


def clasificar(respuesta):
    """Valida la respuesta de SUNEDU y devuelve los grados
    académicos o el código de error correspondiente."""
    
    if 'error' in respuesta:
        return [], 'CONSULTA_FALLIDA'
    datos = respuesta.get('resultado', {})
    # Un aviso de campos aún no mapeados no invalida grados que sí llegaron.
    # Sí se descarta una respuesta si SUNEDU reporta que falló alguno de sus servicios.
    odata = datos.get('oData', {})
    if any(odata.get(campo) is False for campo in (
        'ServicioGradosNacionalesRespondeOk',
        'ServicioGradosExtranjerosRespondeOk',
    )):
        return [], 'RESPUESTA_INCOMPLETA'
    if not isinstance(datos.get('grados'), list):
        return [], 'RESPUESTA_INVALIDA'
    return datos['grados'], None


async def procesar(store, propietario, solicitud, dni, browser):
    grados, error = [], 'DNI_INVALIDO'
    if re.fullmatch(r'[0-9]{8}', dni):
        try:
            respuesta, _, _ = await consultar(solicitud, dni, browser)
            grados, error = clasificar(respuesta)
        except Exception:
            error = 'CONSULTA_FALLIDA'
    while True:
        try:
            await asyncio.to_thread(store.finalizar, solicitud, propietario, dni, grados, error)
            registrar(solicitud, 'Error: ' + error if error else 'Completado')
            return
        except pyodbc.Error as exc:
            # Errores transitorios conservan los resultados en memoria para reintentar.
            codigo = str(exc.args[0]) if exc.args else ''
            if codigo.startswith(('08', 'HYT', '40')):
                registrar(solicitud, 'SQL no disponible; se reintentará guardar en 5 segundos.')
                await asyncio.sleep(5)
                continue
            if error == 'SQL_GUARDADO_FALLIDO':
                registrar(solicitud, 'No se pudo guardar el estado. Revisar solicitud Procesando.')
                return
            grados, error = [], 'SQL_GUARDADO_FALLIDO'
        except ValueError as exc:
            # Mensajes locales de validación; no contienen datos del documento.
            registrar(solicitud, f'No se guardó: {exc}')
            return
        except Exception:
            registrar(solicitud, 'No se pudo confirmar el estado SQL. Revisar solicitud Procesando.')
            return


async def pausa(stop, segundos):
    try:
        await asyncio.wait_for(stop.wait(), timeout=segundos)
    except TimeoutError:
        pass


async def procesar_fondo(store, ventanas):
    """Una sola consulta de fondo; el bucle principal atiende peticiones en paralelo."""
    try:
        fila = await asyncio.to_thread(store.siguiente_fondo)
        if fila is None:
            return None
        dni_id, dni = fila
        etiqueta = f'fondo-ID-{dni_id}'
        if not re.fullmatch(r'[0-9]{8}', dni):
            registrar(etiqueta, 'DNI inválido; permanece pendiente.')
            return False
        respuesta, _, _ = await consultar(etiqueta, dni, ventanas)
        grados, error = clasificar(respuesta)
        if error:
            registrar(etiqueta, f'{error}; permanece pendiente.')
            return False
        await asyncio.to_thread(store.finalizar_fondo, dni_id, dni, grados)
        registrar(etiqueta, 'Guardado; PROCESADO_SUNEDU=1.')
        return True
    except Exception as exc:
        mensaje = diagnostico_sql(exc) if isinstance(exc, pyodbc.Error) else f'Fallo de fondo ({type(exc).__name__}); permanece pendiente.'
        registrar('fondo', mensaje)
        return False


async def servir(store, intervalo):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    anterior = signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(stop.set))
    anterior_term = signal.signal(signal.SIGTERM, lambda *_: loop.call_soon_threadsafe(stop.set))
    propietario = str(uuid4())
    # En este flujo, el otro programa administra PETICIONES_SUNEDU desde SQL.
    try:
        while not stop.is_set():
            tareas = set()
            fondo = None
            proximo_fondo = 0
            try:
                async with AsyncCamoufox(headless='virtual' if sys.platform.startswith('linux') else False, main_world_eval=True) as browser:
                    ventanas = Ventanas(browser)
                    await ventanas.preparar(2)
                    ventana_fondo = Ventanas(browser)
                    await ventana_fondo.preparar(1)
                    proxima_recarga_fondo = monotonic() + 300
                    limite = monotonic() + REINICIO_SEGUNDOS
                    registrar('servicio', 'Navegador disponible; Ctrl+C detiene la recepción.')
                    try:
                        while not stop.is_set() and monotonic() < limite and browser.is_connected():
                            if fondo is not None and fondo.done():
                                correcto = fondo.result()
                                fondo = None
                                proximo_fondo = monotonic() + (0 if correcto is True else intervalo if correcto is None else 60)
                            try:
                                fila = await asyncio.to_thread(store.reclamar, propietario)
                            except Exception as exc:
                                mensaje = diagnostico_sql(exc) if isinstance(exc, pyodbc.Error) else 'Error interno al leer SQL.'
                                registrar('servicio', mensaje)
                                await pausa(stop, intervalo)
                                continue
                            if fila:
                                # Sin semáforo: cada solicitud inicia su contexto independiente.
                                tarea = asyncio.create_task(procesar(store, propietario, *fila, ventanas))
                                tareas.add(tarea)
                                tarea.add_done_callback(tareas.discard)
                            else:
                                if not tareas and fondo is None and monotonic() >= proximo_fondo:
                                    if monotonic() >= proxima_recarga_fondo:
                                        _, pagina = ventana_fondo.libres[0]
                                        await pagina.reload(wait_until='domcontentloaded', timeout=60000)
                                        proxima_recarga_fondo = monotonic() + 300
                                    fondo = asyncio.create_task(procesar_fondo(store, ventana_fondo))
                                espera = min(intervalo, max(0, limite - monotonic()))
                                if fondo is not None:
                                    await asyncio.wait({fondo}, timeout=espera)
                                else:
                                    await pausa(stop, espera)
                    finally:
                        registrar('servicio', 'Esperando consultas y guardados pendientes.')
                        if tareas:
                            await asyncio.gather(*tareas)
                        if fondo is not None:
                            await fondo
                if not stop.is_set():
                    registrar('servicio', 'Reiniciando navegador.')
            except Exception as exc:
                if stop.is_set():
                    registrar('servicio', 'Finalizando el navegador tras la solicitud de cierre.')
                else:
                    registrar('servicio', f'Error de navegador ({type(exc).__name__}); se reintentará el arranque.')
                    await pausa(stop, 5)
    finally:
        signal.signal(signal.SIGINT, anterior)
        signal.signal(signal.SIGTERM, anterior_term)
    registrar('servicio', 'Detenido.')


