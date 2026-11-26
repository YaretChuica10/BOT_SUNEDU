"""Consumidor continuo de SQL Server. Detener con Ctrl+C y esperar el cierre."""

import asyncio
import re
import signal
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

    async def preparar(self):
        for _ in range(3):
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


async def servir(store, intervalo):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    anterior = signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(stop.set))
    propietario = str(uuid4())
    # En este flujo, el otro programa administra PETICIONES_SUNEDU desde SQL.
    try:
        while not stop.is_set():
            tareas = set()
            try:
                async with AsyncCamoufox(headless=False, main_world_eval=True) as browser:
                    ventanas = Ventanas(browser)
                    await ventanas.preparar()
                    limite = monotonic() + REINICIO_SEGUNDOS
                    registrar('servicio', 'Navegador disponible; Ctrl+C detiene la recepción.')
                    try:
                        while not stop.is_set() and monotonic() < limite and browser.is_connected():
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
                                await pausa(stop, min(intervalo, max(0, limite - monotonic())))
                    finally:
                        registrar('servicio', 'Esperando consultas y guardados pendientes.')
                        if tareas:
                            await asyncio.gather(*tareas)
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
    registrar('servicio', 'Detenido.')


