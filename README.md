# BOT_SUNEDU

Las esperas comprueban condiciones mediante accesibilidad y terminan al cumplirse, con plazos máximos de 60 segundos para formulario y CAPTCHA y 45 para resultados. No hay pausas fijas antes de buscar ni clics sobre el CAPTCHA. El sondeo consulta el estado cada 0,5 segundos.

Los mensajes de diagnóstico se muestran en stderr sin guardar archivos. Se informa cuando aparece el widget, cuando muestra éxito y cuando expone un error de conexión. Si accesibilidad no expone el estado del widget, la consulta se detiene sin asumir que la validación terminó. También se prueba el acceso al script de challenges.cloudflare.com con HTTPX y se informa su estado HTTP o clase de error. Esta prueba independiente no confirma la carga del recurso dentro de Chrome. PyAutoGUI no ofrece observación de la red del navegador.

El flujo actual entra a la página principal, pulsa la opción de grados y títulos y después busca el campo DNI. Falta comprobar esta navegación en ejecución.

Prueba RPA en Windows con Google Chrome normal, PyAutoGUI y accesibilidad de Windows (pywinauto).

Chrome usa un perfil separado en `%LOCALAPPDATA%\BOT_SUNEDU\ChromeRPA` para que la instancia se inicie con accesibilidad habilitada, incluso si tu Chrome habitual ya está abierto. Ese perfil puede conservar cookies e historial propios de Chrome.

```powershell
.\.venv\Scripts\python.exe main.py
```

Ingresa ?nicamente un DNI autorizado. Mant?n Windows desbloqueado y evita usar mouse o teclado durante la prueba. Para detenerla, lleva el mouse a la esquina superior izquierda.

El programa abre Chrome normal, localiza el campo DNI y el bot?n Buscar por sus controles accesibles y hace clic con PyAutoGUI. Espera la validaci?n normal del sitio sin interactuar con Cloudflare. Si el sitio presenta resultados con fechas reconocibles, muestra el texto accesible de la p?gina en consola. Si informa un bloqueo o no se confirma el resultado dentro del plazo, muestra un error. Chrome queda abierto para revisar lo ocurrido.

Esta prueba extrae texto visible, no captura JSON de red. La salida puede incluir etiquetas del formulario junto con nombres, grados, universidades y fechas. Falta comprobar el reconocimiento de resultados con un DNI autorizado. La detecci?n depende del texto y accesibilidad que exponga SUNEDU.

No usa Playwright ni Camoufox en la ejecuci?n RPA. Las pruebas anteriores permanecen en el proyecto. No guarda datos personales ni credenciales en logs. Chrome puede conservar su historial normal de navegaci?n.
