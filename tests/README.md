# Pruebas

Scripts sueltos, sin framework: cada uno arma su propia base en una carpeta
temporal (`paths.set_base_override`), abre la app de verdad y termina con
código 0 si pasó. Se corren todos juntos con `correr_todos.py`.

```bash
python3.12 -m venv venv && venv/bin/pip install -r requirements.txt
xvfb-run -a venv/bin/python tests/correr_todos.py     # Linux (las apps son Tkinter)
venv\Scripts\python tests\correr_todos.py             # Windows
```

| Archivo | Qué cuida |
|---|---|
| `test_usb_caja.py` | La Caja del USB de punta a punta: escanear, sumar cantidad, quitar un artículo suelto sin llevarse los otros, editar cantidad, cobrar, descontar stock y exportar la sincronización. **Lee el texto real de cada celda con `celda.get()`**, que es lo único que agarra el bug del carrito en blanco: las celdas son `tk.Entry` de solo lectura, así que un `config(text=...)` no dibuja nada y `cget("text")` siempre devuelve `""`. |
| `test_usb_dueno.py` | Que el Panel del USB abra con sus 9 pestañas, muestre el cartel de emergencia y el botón de sincronizar, marque todo con origen `USB_DUENO`, **no** tenga el `Ctrl+Shift+M` de conciliación (es exclusivo del Maestro) y entre en una pantalla de 1366x768. |
| `test_usb_mantenimiento.py` | Que reponer archivos del programa **nunca** pise el `config.ini` ni la base del cliente, aunque el espejo los traiga adentro (pasa si se probaron los `.exe` desde `dist\`). |
| `test_mantenimiento_completo.py` | La corrida entera de "REPARAR TODO" sobre una instalación Maestro: migración de esquema, stock negativo corregido con su auditoría, reporte escrito, y el `config.ini` real intacto. |
| `test_reparacion_por_tipo.py` | Que reparar a mano la carpeta de un USB no le meta adentro las apps del Maestro. |
| `test_pendrive_reusado.py` | Pendrive con base de una versión anterior + ejecutable nuevo: la base se pone al día sola al arrancar, sin perder lo que ya había, y la pantalla de precios no se cae con `no such column`. |
| `test_respaldo_y_arranque.py` | Que exista una copia diaria verificada sin que nadie se acuerde, que las viejas se borren y las de la ventana no, y que si la base se rompe la app **avise** y ofrezca restaurar la copia en vez de no abrir en silencio a las 8 de la mañana. |
| `test_respaldos_en_mantenimiento.py` | El USB de Mantenimiento frente a las copias del cliente: que no las pise con las del espejo, que el informe diga si el negocio está respaldado y desde cuándo, que el rescate de último recurso use las copias diarias, y que con la base rota **y** sin ninguna copia el informe salga igual en vez de morir con un traceback. |
| `test_actualizador.py` | Que actualizar no pierda los datos del cliente ni traiga los de prueba del build. El caso caro es el Dueño Remoto: su `config.ini` (IP de Tailscale y token reales) vive ADENTRO de su carpeta, que el Actualizador reemplaza entera — se perdía, y recuperarlo obliga a tipear el token a mano. |
| `test_blindaje.py` | El `.exe` de un botón: que encuentre los `.ps1` que va a ejecutar (si no, el botón no hace NADA y la ventana se ve igual de bien), que el blindaje siga cubriendo las ocho causas conocidas, que el SSH siga limitado al rango de Tailscale, y que **no haya lógica duplicada** entre el `.py` y los `.ps1`. |
| `test_carrito_visible.py` | Que al agregar una línea el carrito **baje solo** para mostrarla. No mira banderas internas: le pregunta al canvas por dónde está mirando y a la celda dónde quedó dibujada. Cubre el escaneo, el re-escaneo de una línea que quedó arriba fuera de vista, el artículo sin código, y que escanear **no** cambie la línea seleccionada (es la que borra `Supr`). |
| `test_copiar_codigo.py` | Que se pueda copiar **solo el código** de un producto en toda grilla del Panel que lo muestre, sin arrastrar el nombre y el precio. Lee el portapapeles de verdad con `clipboard_get()`. El caso que agarra una implementación ingenua es Auditoría, donde el código **no** es la primera columna. También cuida que copiar la fila entera siga existiendo y que una grilla sin código no se quede muda con `Ctrl+C`. |
| `test_revision_final.py` | La revisión que el Actualizador hace solo al terminar en la PC del local: servicio en Automatic, servicio corriendo, **puerto** contestando, antivirus, respaldo al día y la evidencia del blindaje al Escritorio. Cuida lo que importa: que **ninguna fila se gane un SI sin comprobar nada**, que una copia de hace 20 días diga NO, y que si toda la revisión falla la actualización siga dada por buena (regla 6). |
| `test_umbral_global.py` | Que el umbral global se pueda **apagar**. Le pasó a Leo: puso 20/20, salieron las alertas, y después 0/0 no apagó nada — porque mandar cada alerta le creaba al producto un umbral propio con el 20/20 congelado adentro. Corre el bot de verdad (con el envío simulado) y mira lo único que importa: si siguen llegando alertas. Cubre además que el cooldown de 4 hs siga frenando la repetida, que un umbral propio puesto a mano siga pisando al global, y que una base vieja se migre sin perder el cooldown. |
| `test_maestro.py` | Regresión de la Caja y el Panel del Maestro, y que una migración que falle no impida abrir la app (regla 6: ante la duda, la caja abre). |
| `test_config_atomico.py` | Que guardar `config.ini` sea atómico para todas las apps: con varios procesos guardando a la vez el archivo nunca queda ilegible ni pierde `[remoto] token`. Con el guardado viejo falla (se probó sacando el arreglo). |
| `test_api_celular_contrato.py` | La API del celular levantada de verdad sobre una base armada con el código de main: cada respuesta tiene la forma exacta del fixture `apps/movil_dueno/test/fixtures/contrato_api_celular_v2.json` (el mismo que parsea la app), y cada escritura deja la base como la dejaría el Panel (stock por `stock_service`, cadena de precios sin inventar costos, el precio que cobra la Caja con ofertas, y las mismas alertas que manda el bot). |
| `test_api_celular_seguridad.py` | Que la API del celular no deje entrar por donde no debe: el sha256 viejo del 1234 nunca da sesión, probar PINs al azar se frena rápido, una sesión vieja deja de servir al cambiar el PIN, el `secreto.json` roto no se regenera solo, solo se entra por Tailscale (desde la LAN, por un proxy local o desde la propia PC da 403), ningún log ni respuesta lleva el PIN, la sesión ni el token del bot (tampoco en un traceback), y —leyendo el código con `ast`— que la API no llame nada que cree la base, la migre o arranque alertas. |
| `test_api_celular_proceso.py` | La API corriendo de verdad (uvicorn en un puerto): cargar stock desde el celular mientras la Caja vende no pierde ni inventa unidades, un `X-Forwarded-For` no hace pasar un pedido local por uno del celular, `/api/salud` contesta al instante aunque la API esté tapada de trabajo, el supervisor nunca se muere (puerto ocupado, apagada a propósito, mismo puerto que el Dueño Remoto, hilo caído) y la autoprueba del exe pasa sin dejar basura. |
| `test_despliegue_celular.py` | ApiCelular en Actualizador, Instalador, Mantenimiento, `servicio_windows` y `build_all.bat`: siempre opcional (si falla, la caja se actualiza igual), `update` y nunca `remove` + `install`, STOP_PENDING no cuenta como parado, y un servicio Deshabilitado no se toca. |

`OTTER_INTEGRACION=1` (lo usa el CI): una prueba que en tu máquina se saltea porque falta algo
(por ejemplo `pwsh` para parsear los `.ps1`) cuenta como **falla**. Saltear en silencio en el CI es lo
mismo que no probar.
