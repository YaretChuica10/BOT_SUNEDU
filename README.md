# BOT_SUNEDU

Servicio continuo con AsyncCamoufox y SQL Server.

## Archivos

- main.py: carga .env e inicia el servicio.
- service.py: administra ventanas, peticiones, reinicio diario y guardado.
- sunedu_client.py: navega, espera Cloudflare y procesa el JSON.
- database.py: reclama peticiones y actualiza o inserta grados.

## Iniciar y detener

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Configurar SQLSERVER_CONNECTION_STRING y SQL_POLL_SECONDS en .env, usando
.env.example como plantilla. Requiere ODBC Driver para SQL Server y el binario
de Camoufox instalado. Detener con Ctrl+C y esperar las consultas pendientes.
Mantener abierta la sesión de Windows. No publicar .env.

## Ubuntu sin escritorio

Usar Python 3.10 o posterior. Instalar `xvfb` y el Microsoft ODBC Driver 18
para SQL Server siguiendo la [guía de Microsoft](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server).
El controlador ODBC se instala en Ubuntu y el navegador de Camoufox se descarga
por separado; ninguno se instala con `requirements.txt`.

```bash
sudo apt-get update
sudo apt-get install -y xvfb
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m camoufox fetch
cp .env.example .env
# Editar .env con el servidor, usuario y contraseña reales.
.venv/bin/python database.py
.venv/bin/python main.py
```

El programa usa una pantalla virtual de Camoufox en Linux. Comprobar que
`odbcinst -q -d` incluya `ODBC Driver 18 for SQL Server` antes de arrancarlo.
Usar una sola instancia del servicio y detenerla con SIGINT o SIGTERM para esperar las
consultas activas. La conectividad con SUNEDU y SQL Server debe verificarse
desde el propio servidor antes de dejarlo en producción.

## Flujo SQL

Se toman filas de PETICIONES_SUNEDU con FLAG y PETICIONTOMADA en NULL, marcando
PETICIONTOMADA=1. El DNI se obtiene pasando por PERSONA y DNI. Los resultados van
a SUNEDU_V2: se actualizan fechas del grado que coincide por persona, título e
institución, o se inserta un grado nuevo. Las coincidencias múltiples se distinguen
por fecha de diploma; si son ambiguas, no se guarda. FLAG=1 se confirma junto al
resultado en la misma transacción.

MODALIDAD no se extrae actualmente. ID_DNI y FECHA_CONSULTA_FIN no se modifican.
Una respuesta fallida de SUNEDU no se convierte en ausencia confirmada de grados.
Los errores dejan FLAG=NULL y PETICIONTOMADA=1. Para reintentar una petición ya
terminada, el programa externo puede restablecer PETICIONTOMADA a NULL.

Se mantienen tres ventanas reutilizables; las adicionales son temporales. Cada
ventana tiene su contexto independiente. El navegador reinicia cada 24 horas,
esperando trabajos y guardados activos. Cloudflare sigue su validación normal.

Cuando no hay peticiones, espera SQL_POLL_SECONDS (5 por defecto). Los errores SQL
transitorios durante el guardado se reintentan conservando datos en memoria. Si
SQL sigue inaccesible, el cierre también espera. Un cierre forzado puede dejar
peticiones tomadas que deben revisarse manualmente.

Diagnóstico de conexión y esquema sin modificar registros:

```powershell
.\.venv\Scripts\python.exe database.py
```

## Recorrido de fondo

Una ventana queda reservada para un DNI a la vez. Se ejecuta primero
SP_OBTENERDNI_SUELDO (periodo por defecto del SP) y, si no devuelve filas,
SP_OBTENERDNI_DNI. Los grados y SP_ACTUALIZAR_PROCES0_DNI @DniId se confirman
juntos en una transacción. El avance persistente es DNI.PROCESADO_SUNEDU.

Las otras dos ventanas y las temporales atienden PETICIONES_SUNEDU. El fondo no
inicia otro DNI mientras haya peticiones activas. Una consulta de fondo en curso
termina normalmente. El reinicio diario y Ctrl+C esperan también ese trabajo.

Si falla un DNI de fondo, permanece en 0 y se reintenta después de 60 segundos.
Los SP TOP(1) devuelven el mismo pendiente hasta completarlo: un fallo persistente
o un DNI sin PERSONA asociada requiere revisión y no se salta automáticamente.
Ejecutar una sola instancia del servicio para este recorrido: los SP no reservan
el DNI de fondo. No se modificaron los SP ni se agregaron tablas o archivos.
