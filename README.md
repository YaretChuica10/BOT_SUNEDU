
# BOT_SUNEDU

El programa consulta SUNEDU desde Chrome normal mediante PyAutoGUI. Espera el estado visible de Cloudflare sin interactuar con el CAPTCHA.

## Consultar varios DNIs y guardar en Excel

El archivo `dnis.txt` contiene los nueve DNIs proporcionados, uno por línea. Ejecuta desde la carpeta del proyecto:

```powershell
.\.venv\Scripts\python.exe main.py --lote dnis.txt
```

Las consultas se realizan consecutivamente y el Excel se guarda en `resultados/`. Se crea un archivo distinto en cada ejecución y se guarda el avance después de cada consulta. Cada consulta usa una ventana nueva y el lote intenta cerrarla al terminar, conservando las ventanas previas de Chrome. Mantén la sesión de Windows desbloqueada y evita usar el mouse o el teclado durante la ejecución.

El Excel contiene tres hojas:

- `Consultas` registra una fila por DNI, con estado, cantidad de grados, fecha, duración y detalle del error cuando corresponde.
- `Grados` contiene una fila por grado, con DNI, nombres, institución y fechas. Las fechas conservan el texto de SUNEDU, incluidos años de dos dígitos y valores sin información.
- `Texto SUNEDU` conserva el texto capturado para revisar la extracción. Si no se reconoce el formato, se informa `Revisar extracción`.

El lote continúa después de un error de consulta. Si lo detienes con Ctrl+C o la esquina de seguridad de PyAutoGUI, conserva el avance que haya podido guardar. Las consultas pendientes quedan identificadas. Un estado de error no equivale a ausencia de grados. Cierra el Excel mientras el bot lo escribe.

Los DNIs se guardan como texto para conservar los ceros iniciales. El programa no imprime los DNIs en los mensajes de seguimiento del lote. `dnis.txt` y `resultados/` están excluidos de Git.

## Consulta individual

```powershell
.\.venv\Scripts\python.exe main.py
```

Este modo solicita un DNI y muestra el texto de la página en consola.

## Dependencias

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

La ejecución actual utiliza `main.py`, `rpa_query.py` y, para el lote, `batch_query.py`.
