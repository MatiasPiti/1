# Contexto del proyecto — Otter

> Este archivo es la memoria del proyecto. Claude Code lo carga solo al abrir el repo.
> La **arquitectura** está en `README.md` (detallada, con diagramas) — no se repite acá.
> Esto es lo otro: quién es el cliente, en qué estado está todo, qué se decidió y por qué,
> qué falta, y qué **nunca** hay que hacer.

## Quién es quién

- **Matías** — el desarrollador. Es con quien hablás. **Respondele siempre en español, corto y concreto.**
- **Leo** — el dueño del negocio, el cliente. No es técnico. No toca el código ni Tailscale.
- **El negocio** — kiosco/almacén "El Galpón Del Nono", San Luis 2892, Casilda (Santa Fe).

Esto no es un ejercicio: es un sistema que está cobrando plata real, todo el día, con un
cajero que no sabe de computadoras y sin nadie cerca para arreglarlo. Cuando haya que elegir,
elegí lo que falla menos, no lo que es más elegante.

## Qué es Otter

POS + control de stock en Python/Tkinter/SQLite que compila a **9 ejecutables Windows portables**
(PyInstaller `--onedir`). Ver `README.md` para el detalle. En una línea cada uno:

| Ejecutable | Dónde va | Para qué |
|---|---|---|
| `MaestroCaja` | PC del local | La caja. Vende, imprime ticket, descuenta stock. |
| `MaestroDueno` | PC del local | Panel del dueño: stock, precios, reportes, ARCA, ofertas. |
| `StockService` | PC del local | Servicio de Windows: alertas Telegram + API remota. |
| `DuenoRemoto` | Laptop de Leo | El mismo panel, contra la PC del local por Tailscale. Necesita que el local esté prendido — no reemplaza a `USB_Dueno` (ver más abajo). |
| `USB_Caja` | Pendrive | Caja de emergencia si se rompe la PC del local. Base propia, se concilia después. |
| `USB_Dueno` | Pendrive | Panel de emergencia del dueño si se rompe la PC del local. Base propia, se concilia después. |
| `USB_Mantenimiento` | Pendrive de Matías | Diagnóstico y reparación en el local. |
| `OtterInstalador` | Pendrive de instalación | Hace toda la instalación en un botón. |
| `OtterActualizador` | Pendrive | Pone al día una instalación que ya anda, sin tocar datos ni config. |

**Build:** `build\build_all.bat` desde la raíz, en Windows. Genera todo en `dist\`.

## Reglas duras — no romper ninguna

1. **La API remota (`services/remote_api.py`) NUNCA se expone a internet ni con port forwarding.**
   Toda su seguridad se apoya en que solo se llegue al puerto por la VPN (Tailscale).
2. **La laptop de Leo recibe `DuenoRemoto.exe`, jamás `MaestroDueno.exe`.** El segundo abre
   perfecto y sin dar ningún error, pero se crea su propia base vacía y nunca muestra una venta
   del negocio. Es el error más caro posible y el instalador existe en parte para evitarlo.
3. **Nunca copiar `dist\database\` ni `dist\config.ini` al cliente** — tienen datos de prueba.
4. **El token `galpon-nono-686dd219a7d742b8` quedó expuesto en un chat: no se usa nunca más.**
   El instalador genera uno nuevo solo. El token y la IP de Tailscale **reales** del cliente viven
   únicamente en su `config.ini` — nunca se los pide ni se los repite en un chat; se transfieren
   copiando y pegando directo en la PC (nunca tipeados: `l`/`I`/`1` y `O`/`0` se confunden).
5. La cuenta de Tailscale es de Matías y es la llave de la red de todos los clientes:
   **2FA activado**, y **escribir las ACLs antes de sumar un segundo cliente** — si no, los
   clientes se ven entre sí.
6. **Ante la duda, la caja abre.** Ninguna validación, candado o chequeo nuevo puede impedir que
   el sistema arranque a las 8 de la mañana. Cuando algo falle raro, que falle abriendo.

## Estado actual

**Instalación y cobro cerrados (septiembre 2026).** El sistema está instalado y funcionando en el
local: PC del local (`MaestroCaja` + `MaestroDueno` + `StockService`) y laptop de Leo con
`DuenoRemoto`, conectadas por Tailscale. **Matías ya cobró los USD 800 de instalación.** Todo
verificado en Windows real, no solo en sandbox.

Todo está en `main`, commiteado y pusheado. La rama `claude/dual-pos-portable-emergency-dvt5ym`
quedó vieja (tiene solo dos subidas manuales de archivos por la web): **el trabajo va a `main`**.

Últimos commits relevantes:
- `16d9652` — memoria del proyecto en `CLAUDE.md`.
- `3799aae` — pantalla de precios sin depender del Excel, ventanas que entran en pantallas chicas,
  scroll del carrito con barra + flechas.
- `15de1a6` — la edición masiva de precios no se veía (la fila de "Aplicar %" quedaba tapada abajo
  del corte de `MarcoDesplazable`).
- `ff32d8a` — **el carrito de la Caja quedaba en blanco** al escanear o cargar a mano (ver detalle
  abajo, en "Decisiones que ya se tomaron"). Encontrado y arreglado en producción, en vivo, con el
  negocio ya anduviendo.

### Bugs encontrados y arreglados durante la instalación real

Estos son gotchas de infraestructura que van a volver a aparecer si se reinstala o se depura de
nuevo — documentados para no perder tiempo re-descubriéndolos:

- **`sc` en PowerShell NO es el comando de servicios de Windows.** Es un alias de `Set-Content`
  (escribir archivos). `sc query NombreServicio` no tira ningún error de servicio: silenciosamente
  intenta escribir un archivo llamado `query`. Para consultar servicios reales, usar
  `Get-Service -Name NombreServicio` o `sc.exe query NombreServicio` (con el `.exe` explícito).
- **El `StockService` puede quedar `Stopped`** después de una instalación o de que el
  `OtterActualizador` lo pare para actualizar (no lo relanza si algo falla, a propósito, para no
  frenar la actualización). Sin el servicio corriendo, `DuenoRemoto` da "no se pudo conectar"
  aunque IP, token y Tailscale estén perfectos. Diagnóstico: `Get-Service SistemaDualStockService`
  → si dice `Stopped`, `Start-Service SistemaDualStockService` (como administrador) y confirmar con
  `netstat -ano | findstr 8765` que algo quedó en `LISTENING`.
- **PowerShell abre en `C:\Windows\system32` cuando se lo ejecuta "como administrador"**, no en la
  carpeta donde se lo abrió antes — hay que volver a `cd` a la carpeta del proyecto.
- **PyInstaller puede fallar con `PermissionError` al recompilar un ejecutable que sigue corriendo**
  (o cuyo `.exe` quedó bloqueado por un proceso colgado/servicio activo). Antes de recompilar:
  cerrar la app y, si es un servicio, pararlo (`Get-Service` / `Start-Service` / `sc.exe`).
- **`OtterActualizador` frena limpio ante un archivo bloqueado** (`PermissionError`) sin borrar la
  instalación anterior — es el comportamiento correcto, ante la duda no rompe nada. Solución:
  cerrar el `.exe` que está bloqueando (Task Manager si hace falta) y volver a apretar
  "ACTUALIZAR".

### Revisión previa a armar los 3 USBs (septiembre 2026)

Antes de que Matías grabara los pendrives se revisó todo el circuito USB y se encontraron
tres cosas, las tres arregladas y con test que las cuida (ver `tests/`):

- **El USB de Mantenimiento pisaba el `config.ini` real del cliente.** La lista de exclusiones
  de `reparar_archivos_app` solo se aplicaba a las CARPETAS, no a los archivos sueltos: la
  base (`database\`) estaba protegida, pero el `config.ini` de la raíz —donde viven el token
  y la IP de Tailscale reales— se reemplazaba por el del espejo. Y el espejo trae el de
  prueba, porque `build_all.bat` copiaba `dist\<App>\` entero y ahí queda todo lo que las
  apps se crean al probarlas (`config.ini`, `database\`, `logs\`, `tickets\`). Resultado:
  conectar el USB para reparar dejaba al Dueño Remoto sin conexión, sin decir nada.
  Ahora el filtro corre también archivo por archivo Y el `.bat` limpia los datos del espejo.
- **Un pendrive reusado rompía la pantalla de precios.** `init_db()` hace `CREATE TABLE IF NOT
  EXISTS`: sobre una base que ya existe NO agrega las columnas nuevas. Con una base anterior
  a septiembre y el ejecutable nuevo encima, la caja vendía bien pero Precios tiraba
  `no such column: subrubro`. Ahora las cuatro apps arrancan con `pos_core.db.preparar_base()`
  (init + migración), y si la migración falla **no** se propaga el error: la app abre igual.
- **Reparar a mano la carpeta de un USB lo trataba como Maestro** y le copiaba adentro
  `MaestroCaja\`, `MaestroDueno\` y `StockService\`. Ahora el tipo se deduce del contenido
  (`tipo_de_instalacion`).

Además: **un USB de emergencia por pendrive, con el `.exe` en la raíz.** La detección
automática busca `USB_Caja.exe` / `USB_Dueno.exe` en la raíz de cada unidad, no en subcarpetas,
y dos apps en la misma raíz chocarían sus carpetas `_internal\` de PyInstaller. El README decía
que se podían poner las dos en un mismo pendrive: se corrigió.

### Decisiones que ya se tomaron (no re-litigar)

- **El ticket queda con el formato que tiene.** El encabezado dice `OTTER` y el renglón dice
  `Cajero: El Galpón Del Nono` (antes decía el usuario de Windows). Matías pidió explícitamente
  que no se cambie nada más de ese ticket.
- **La caja se maneja entera con teclado** (flechas para navegar, Enter para confirmar, Suprimir
  para sacar una línea, Enter sobre una línea para editar la cantidad sin re-escanear). El mouse
  sigue funcionando, pero el diseño manda que se pueda vender sin tocarlo.
- **Cada línea del carrito lleva un `_id` propio** (`apps/caja_carrito.py`). NO se identifica por
  código: el código no es único en el carrito (todos los "artículo sin código" comparten uno
  reservado), y referenciar por código hacía que sacar un artículo suelto borrara todos los
  demás — un ticket de $2600 pasaba a $900.
- **Facturar ARCA va bajo un candado** (`pos_core/arca._candado_facturacion`). El número de
  comprobante sale de preguntarle a ARCA cuál fue el último y sumarle uno: dos facturas en
  paralelo pedían el mismo número y ARCA rechazaba la segunda.
- **Tk se toca solo desde el hilo de Tk.** El instalador y el cartel de conexión del Dueño Remoto
  hacían trabajo pesado en un hilo y escribían en pantalla desde ahí. Si aparece un hilo nuevo,
  que encole y que pinte el hilo principal.
- **Facturas PDF sin código de barras:** se emparejan por nombre (`pos_core/matching.py`) pero
  **nunca se aplican solas** — siempre las confirma una persona. Emparejar mal le suma el stock a
  otro producto y no se nota hasta que la góndola no cierra.
- **La pantalla de precios manda sobre el Excel.** El dueño escanea, ve lo cargado hoy y
  cambia lo que quiera (`pos_core/precios.py`). De los cuatro números encadenados —Costo
  S/IVA → Precio Costo → % Ganancia → Precio Venta Final— **solo el final es obligatorio**;
  lo que se recalcula depende de **qué campo tocó**, que es lo único que no obliga a
  adivinar su intención. `"1.500"` se lee como mil quinientos (convención argentina) y el
  campo se reescribe normalizado para que se note al instante si se entendió mal.
- **El carrito reusa sus celdas en vez de redibujarlas.** Antes destruía y recreaba las 150
  celdas de un ticket de 30 líneas por cada tecla (61 ms por escaneo, 103 ms por flecha).
  Si se toca `_refrescar_grilla_carrito`, mantener el criterio: la plata sale siempre de
  `self.carrito`, el dibujo es solo dibujo.
  - **Gotcha real que costó una venta a ciegas:** las celdas del carrito (`celda_texto` en
    `apps/theme.py`) son `tk.Entry` de **solo lectura**, no `Label` (para poder seleccionar/copiar
    texto). Un `Entry` no tiene la opción `text`: hacer `celda.config(text=...)` no actualiza nada
    (la celda queda vacía para siempre) y silenciosamente no vuelve a fallar, así que no saltaba en
    los tests anteriores porque nunca se probó releer el contenido real de la celda tras un
    refresco. Para escribirle texto a una celda hay que abrirla, escribir, volver a cerrarla —ver
    `_escribir_celda()` en `apps/caja_carrito.py`— y para cambiarle el color de fondo hay que tocar
    `readonlybackground`, no solo `bg` (es lo que Tk realmente pinta cuando el `Entry` está en modo
    `readonly`). El total cobrado nunca estuvo mal —sale de `self.carrito`, no de la pantalla— pero
    el cajero vendía sin ver el ticket, lo cual no es aceptable igual.
  - **Al testear el carrito, no uses `celda.cget("text")`** (siempre da `""` en un `Entry`) — leé
    `celda.get()`.
- **Ninguna ventana se pide más grande que el área útil del escritorio** (`ajustar_ventana`
  en `apps/theme.py`, que le pregunta a Windows por el work area). En la PC del cliente
  (1366x768) el Panel del Dueño quedaba tapado por la barra de tareas. Las pestañas van
  adentro de `MarcoDesplazable`: en pantalla chica aparece barra, en grande se expanden
  igual que antes.
- **`StockService` es el único ejecutable sin `--noconsole`, y es a propósito.** Con `--noconsole`
  se queda sin stdout, `install` falla al imprimir su primer mensaje y la instalación se aborta
  sin mostrar ningún error: el servicio simplemente nunca aparece. Está explicado en el .bat.
- **`USB_Dueno` sigue siendo necesario aunque ya funcione Tailscale + `DuenoRemoto`.** Son
  soluciones a problemas distintos: `DuenoRemoto` necesita que la PC del local esté viva y
  prendida (es una ventana remota a la base real); `USB_Dueno` es para cuando esa PC **se rompió**
  — trae su propia base y arranca en cualquier PC sin depender de que el local funcione. Sacarlo
  dejaría a Leo sin panel el día que la PC del local falle, que es exactamente el escenario para el
  que existe (misma lógica que `USB_Caja`).

## Lo que falta hacer

**Pendiente:**
- [ ] Probar el circuito de emergencia de punta a punta desde pendrives de verdad: vender offline
      → "Preparar sincronización" → conciliar en el Maestro con `Ctrl+Shift+M`.
- [ ] Configurar la impresora térmica POS-58 en la PC del local.
- [ ] Escribir las ACLs de Tailscale antes de sumar un segundo cliente.
- [ ] Cargar el token y el chat_id del bot de Telegram del cliente.
- [x] Traer al repo un test que arme el carrito, agregue una línea y lea `celda.get()` de cada
      columna (no solo el dato en `self.carrito`) — el bug del carrito en blanco pasó screening
      precisamente porque ningún test anterior releía el texto real dibujado en pantalla.
      Hecho: `tests/test_usb_caja.py` (y seis pruebas más, ver `tests/README.md`).

**ARCA (facturación electrónica):**
- [ ] El cliente **ya tiene un certificado real** de su sistema viejo en `c:\mmarket\feafip\`
      (`certificado.crt` + `clave.key`). Recuperarlos.
- [ ] Probar contra **Homologación** antes de tocar Producción.
- [ ] Confirmar las URLs actuales de WSAA/WSFEv1 en el portal de ARCA (vienen migrando de
      `afip.gov.ar` a `arca.gob.ar`).

**Catálogo:**
- [ ] Se extrajo el catálogo real del POS viejo (Visual FoxPro, `.DBF` leídos con `dbfread`; los
      archivos están en `github.com/MatiasPiti/proyect`) y se entregó
      `Lista_Precios_El_Galpon.xlsx` — **4587 productos**, con stock y proveedor en blanco a
      pedido de Matías.
- [ ] Quedaron **8 productos afuera por colisión de código**: cargarlos a mano.
- [ ] Hacer el inventario físico: el catálogo entra con stock 0.

**Postergado (no urgente):** apps personales de Matías para la consola RG35XX-DS (un dashboard de
clientes, una herramienta de notas). Se habló y se dejó para después.

**Postergado (evaluado, no decidido):** hacer opcional el control de stock (toggle
`[general] control_stock` en `config.ini`) para clientes que no quieren cargarlo (compran sin
factura, no restan por rotura/vencimiento). Sin ese servicio no hay alertas de Telegram — eso ya
se aceptó como consecuencia obvia. Recomendación cuando se retome: modificar el sistema actual en
vez de bifurcarlo, un solo punto de control en `sales.cerrar_ticket()` + ocultar pestañas, con
regresión completa antes de tocar producción.

## Cómo probar

Las pruebas viven en `tests/` (scripts sueltos, sin framework: cada uno termina con código 0 si
pasó). Detalle de qué cuida cada una en `tests/README.md`.

```bash
python3.12 -m venv venv && venv/bin/pip install -r requirements.txt
xvfb-run -a venv/bin/python tests/correr_todos.py   # las apps son Tkinter, necesitan display
xvfb-run -a venv/bin/python tests/test_usb_caja.py  # una sola, con su salida completa
```

En Linux hace falta `python3-tk` instalado en el sistema y crear el venv con
`--system-site-packages` (o el venv queda sin `tkinter` y no abre ninguna app).

Las apps se pueden instanciar directo (`AppCaja()`, `AppDueno(backend=...)`) y manejar con
`app.update()`; para probar la API remota se levanta con `remote_api.iniciar_servidor(...)` en un
puerto libre. Los `messagebox` se reemplazan por stubs para que no bloqueen. Para crear un
producto de prueba sin pelearse con el esquema de `Productos`, usar
`pos_core.products.crear_producto(codigo=..., nombre=..., precio_venta=..., stock_inicial=...,
usuario=...)` en vez de un `INSERT` a mano (tiene columnas `NOT NULL` como `uuid_unico` que ese
helper completa solo). Para aislar la base, `pos_core.paths.set_base_override(ruta_temporal)`
antes de `pos_core.db.preparar_base()`.

**Al agregar una prueba, que mire lo que se ve, no solo lo que se guardó**: ya hubo un bug (el
carrito en blanco) que un test de "¿se agregó al `self.carrito`?" no hubiera agarrado — hacía
falta releer el texto real dibujado en pantalla con `celda.get()`.

## Cómo trabajar en este repo

- Commits y explicaciones **en español**, describiendo el problema real del negocio, no la
  mecánica del código ("quitar un artículo suelto borraba todos los artículos sueltos", no
  "refactor de la lista del carrito").
- Los comentarios en el código explican **por qué**, sobre todo cuando algo parece raro pero
  tiene un motivo (ver `build_all.bat` y el `--noconsole`).
- Matías prefiere respuestas **cortas y concretas**. Cuando pide un resumen de lo hecho, que sea
  breve de verdad.
- Matías compila y despliega él mismo desde Windows, guiado paso a paso por consola — no tiene
  acceso de shell a este repo desde su lado salvo `git`/`PowerShell`/`PyInstaller`. Cuando haga
  falta un comando, dárselo completo y copiable, no en pasos sueltos para armar.
