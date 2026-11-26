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
