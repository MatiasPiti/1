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
   **Una captura de pantalla de la consola es un chat igual**: en septiembre volvieron a quedar
   expuestos así el token de `[remoto]` y el del bot de Telegram, y hay que rotar los dos (está en
   los pendientes). Al mandar capturas de esa pantalla, tapar esas líneas. Y al rotar un token,
   generarlo **sin caracteres confundibles** si va a haber que tipearlo en un celular.
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
- **Respaldo diario verificado + cartel de arranque** — el sistema no tenía NINGUNA copia
  automática y con la base rota no abría en silencio. Ver la sección del riesgo más grande, abajo.
  **Falta recompilar y actualizar la PC del local: hasta entonces no está andando allá.**
- `blindar_local` — reescrito: ahora vigila el PUERTO y no el estado del servicio, guarda la
  evidencia antes de tocar nada, y cubre batería. **Falta correrlo en la PC del local.**
- Revisión de los 3 USBs antes de grabarlos: tres fallos encontrados y arreglados, con `tests/`
  traído al repo (hoy 9 pruebas, todas en verde).
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

### El Dueño Remoto se cayó dos veces en el mismo día (septiembre 2026)

Leo llegó a su casa y no pudo conectar, con Tailscale **en verde de las dos puntas** y la PC del
local encendida. Pasó dos veces el mismo día. Lo que se aprendió:

- **Tailscale en verde solo dice que la VPN está bien.** No dice que haya algo escuchando. Todo el
  tiempo que se pierde mirando IPs y tokens es tiempo perdido: el problema está más arriba.
- **"La PC está encendida" no alcanza.** Suspendida, hibernada o con la sesión cerrada es, para la
  red, lo mismo que apagada.
- **La causa de fondo, confirmada el 9/9/2026 leyendo `sc.exe qc` en la PC del local: el servicio
  estaba en `DEMAND_START` (Manual), no en `Automatic`.** No hacía falta que nadie lo parara: **cada
  vez que se reiniciaba la PC, el servicio no volvía**. En los eventos de esa misma máquina se ven
  reinicios el 8/9 a las 00:44, el 8/9 a las 16:18 y el 9/9 a las 08:15 — cada uno dejó a Leo sin
  conexión. Cuando se corrió el blindaje, el servicio estaba parado otra vez (tercera vez).
- **Es un bug del instalador, no de esa máquina.** `win32serviceutil.HandleCommandLine` con
  `install` y sin `--startup auto` registra el servicio en Manual: es el default de pywin32. **No
  se nota nunca el día de la instalación**, porque uno lo arranca a mano y queda corriendo; se nota
  al primer reinicio, cuando ya no hay nadie mirando. Le habría pasado igual a todos los clientes
  siguientes. Arreglado en tres lugares: el instalador pasa `--startup auto` y configura los
  reintentos de Windows, el propio `StockService.exe install` fuerza `--startup auto` si no se lo
  dan, y el USB de Mantenimiento **detecta el arranque Manual y lo corrige solo**
  (`_verificar_arranque_automatico`). El instalador además ahora verifica el TIPO DE ARRANQUE y no
  solo que el servicio esté corriendo: que corra hoy no dice nada de mañana, y "mañana" era
  justamente cuando fallaba.
- **Pararlo para recompilar y no relanzarlo es una causa REAL pero secundaria** (el `.bat` no lo
  relanza, y el `OtterActualizador` solo lo hace si estaba corriendo antes). Sigue valiendo el
  gotcha documentado arriba.

**El diagnóstico que separa las causas en dos pasos** (se puede hacer entero desde el celular):

1. App de Tailscale: ¿el nodo del local figura **online**? Si dice offline, la PC está dormida o
   Tailscale se cayó ahí — no se arregla en remoto.
2. Navegador: `http://<ip>:8765/health`. Si contesta **algo**, aunque sea
   `{"ok": false, "error": "token inválido"}`, **el servicio está vivo** (rechaza porque el
   navegador no manda el token) y el problema es del lado del Dueño Remoto. Si no carga nada, el
   servicio está caído.

Desde una PC el equivalente es `Test-NetConnection <ip> -Port 8765`: **`PingSucceeded: True` con
`TcpTestSucceeded: False` significa exactamente "la red llega, no hay nada escuchando"**.

**El blindaje quedó en `scripts/blindar_local.ps1`** (con sus pasos en texto plano al lado, en
`scripts/blindar_local_PASOS.txt`). Se corre una vez, como administrador, en la PC del local, y
cierra las tres causas: servicio en `Automatic` + reintentos, una tarea programada `OtterWatchdog`
cada 5 minutos (deja registro en `C:\SistemaDual\watchdog\watchdog.log`, que ahora rota al MB),
la PC que no se suspende más, y Tailscale en modo *unattended* — sin esto último Tailscale se
desconecta al cerrar la sesión de Windows, que es el sospechoso principal del caso "prendida pero
no responde". **El script no está probado en Windows real todavía.**

**La primera versión del blindaje no alcanzaba, y se reescribió.** Lo que estaba mal:

- **Vigilaba el estado del servicio, no el puerto.** La API remota se levanta ADENTRO del servicio
  con `iniciar_si_esta_habilitado()`, que loguea y sigue si falla. O sea que el servicio puede
  decir `Running`, el watchdog darlo por bueno, y que no haya nadie escuchando en el 8765: para
  Leo eso es "no me puedo conectar" y para el watchdog era todo normal. **Ahora abre el puerto**,
  que es lo único que le importa al Dueño Remoto. Con dos frenos: no reinicia si `[remoto]
  habilitado` no es `true` (si no, reiniciaría cada 5 minutos para siempre), y se planta después
  de 3 reinicios en 24 hs — si el puerto sigue muerto, reiniciar no es la solución.
- **`tailscale up --unattended` casi seguro fallaba.** Tailscale contesta que hay que repetir
  TODOS los flags no-default y no aplica nada. Va con `tailscale set --unattended=true`, que
  cambia una sola preferencia, y `up` queda de respaldo.
- **`powercfg` solo cubría enchufado.** Si es notebook, se dormía igual apenas se corta la luz,
  que es justo cuando más importa. Ahora AC y DC.
- **Se perdía la evidencia.** El paso 0 guarda `sc.exe qc`, el estado del servicio, la cola del
  log y 7 días de errores del sistema en
  `C:\SistemaDual\watchdog\estado_antes_del_blindaje.txt` **antes** de cambiar nada. Es la
  última chance de saber por qué se paró la segunda vez: después el `StartupType` queda en
  `Automatic` y esa pista desaparece. **Ese archivo hay que mirarlo.**

### El riesgo más grande no era ese: no había NINGÚN respaldo (septiembre 2026)

Buscando por qué se cayó el Dueño Remoto apareció algo peor, que nunca habíamos mirado: **el
sistema no tenía copias de seguridad automáticas**. La base se copiaba solo cuando corría
`OtterActualizador` o cuando el USB de Mantenimiento la encontraba corrupta — o sea casi nunca. Y
el rescate de último recurso del USB (`_restaurar_backup_mas_reciente`) buscaba justamente esas
copias, así que el día que hiciera falta no iba a encontrar nada. El disco que falla se lleva las
ventas, el stock y los 4587 productos que costó migrar del sistema viejo.

Peor todavía, la otra mitad: con la base dañada los `.exe` **están compilados sin consola**, así
que el cajero hacía doble clic a las 8 de la mañana y no pasaba absolutamente nada. Ni un cartel,
ni un archivo. Verificado, no supuesto.

Lo que quedó hecho:

- **`pos_core/respaldo.py`** — copia diaria adentro del `StockService`, que es lo único que ya
  corre 24/7 y arranca con Windows (no depende de que nadie se acuerde). Usa la API de backup de
  SQLite y no una copia de archivo (con WAL, copiar el `.db` suelto puede llevarse una base a
  medio escribir), **la verifica con `integrity_check` antes de darla por buena**, escribe a un
  temporal para no dejar una copia incompleta con nombre de copia buena, conserva 14 días y no
  corre con menos de 300 MB libres. Van en `backups\`, fuera de `database\`.
- **`pos_core/arranque.py`** — envuelve el arranque de las cuatro apps. Si algo falla antes de que
  exista la ventana, **lo dice en un cartel**, lo escribe en `logs/arranque.log`, y si el problema
  es la base ilegible ofrece restaurar la copia mostrando de qué día es y qué se perdería. La base
  dañada no se borra: queda al lado como `stock.db.danada_<sello>`. Antes de reemplazar el archivo
  llama a `db.cerrar_conexion()` — en Windows, pisar un archivo abierto falla, y eso rompía la
  restauración justo cuando tiene que funcionar.
- **El USB de Mantenimiento**, tres cosas del mismo tipo que el bug del `config.ini`: ya no puede
  pisar `backups\` del cliente con los del espejo; el informe ahora **dice si el negocio está
  respaldado y desde cuándo** (una copia de tres semanas es casi lo mismo que ninguna, y si está
  atrasada lo más probable es que el servicio esté parado); el rescate de último recurso mira
  también las copias diarias; y **cada paso va aislado**, porque con la base rota y sin ninguna
  copia el mantenimiento moría con un traceback y sin informe, justo cuando el informe es lo único
  que queda.
- **El `OtterActualizador` borraba el `config.ini` del Dueño Remoto.** Reemplaza la carpeta de
  cada app entera (`os.rename` de la vieja + `copytree` de la nueva). En el Maestro eso no
  importaba porque el config y la base viven en la carpeta padre, pero **el config del Dueño
  Remoto vive ADENTRO de su carpeta** — con la IP de Tailscale y el token reales. Actualizar la
  laptop de Leo lo dejaba sin panel, y recuperarlo obliga a tipear el token a mano, que es
  justamente lo que prohíbe la regla 4. Ahora el `copytree` ignora los datos del cliente y se
  reponen después desde la carpeta anterior; de paso, tampoco se cuelan el `config.ini` y la
  `database\` de prueba que quedan en `dist\` al probar los `.exe`. Lo cuida
  `tests/test_actualizador.py`.
- **Los logs rotan.** `stock_daemon.log` y el del watchdog escribían para siempre. Un disco lleno
  es una de las formas de corromper una base SQLite en uso: el remedio no puede causar la
  enfermedad.

**Un susto que no era**: `PRAGMA synchronous` nunca se toca en el código, pero el default de
SQLite es `FULL` (verificado, no supuesto), así que un corte de luz **no** pierde ventas ya
cobradas. No hay nada que hacer ahí.

Un detalle que confundió el diagnóstico: **cada app guarda su `config.ini` al lado de su propio
`.exe`** (las tres del Maestro son la excepción: comparten el de la carpeta padre). En la laptop
de Leo el config vive en `C:\Otter\DuenoRemoto\`, no en `C:\SistemaDual\` — y **el Dueño Remoto
solo lo escribe cuando logra conectar por primera vez**, así que "no existe" puede significar "esta
instalación nunca conectó", no que se haya borrado algo.

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
- [x] **Correr `scripts/blindar_local.ps1` en la PC del local.** HECHO el 9/9/2026: las 7 filas
      en SI (servicio en Automatic, watchdog Ready, Tailscale unattended por CLI, servicio
      corriendo, puerto 8765 respondiendo). Encontró el servicio parado por tercera vez y lo
      levantó. **Falta la prueba final**: cerrar sesión —no apagar— y confirmar desde el celular
      que el `/health` sigue contestando (hay que hacerlo con el negocio cerrado, porque cerrar
      sesión cierra la caja).
- [ ] **Hacer el inventario físico, en serio.** El log del servicio está lleno de
      `StockInsuficienteError: disponible 0, se pidió descontar 1`: el catálogo entró con stock 0,
      así que **ninguna venta descuenta stock**. La plata se cobra bien (sale de `self.carrito` y
      `cerrar_ticket` graba la venta), pero el control de stock no está funcionando en la práctica
      y las alertas de Telegram nunca van a servir. Es el pendiente que hace que media mitad del
      sistema no rinda.
- [ ] **Rotar los dos tokens del cliente**: el de `[remoto]` y el del bot de Telegram quedaron
      visibles en una captura de pantalla mandada por chat. El de Telegram se revoca con `/revoke`
      en @BotFather; el de `[remoto]` se cambia en `C:\SistemaDual\config.ini`, se reinicia el
      servicio **y hay que cargar el nuevo también en la laptop de Leo** (si no, se queda sin
      panel). Hacerlo cuando estén las dos máquinas a mano.
- [ ] Probar el circuito de emergencia de punta a punta desde pendrives de verdad: vender offline
      → "Preparar sincronización" → conciliar en el Maestro con `Ctrl+Shift+M`.
- [ ] Configurar la impresora térmica POS-58 en la PC del local.
- [ ] Escribir las ACLs de Tailscale antes de sumar un segundo cliente.
- [ ] Cargar el token y el chat_id del bot de Telegram del cliente.
- [ ] **Recompilar y actualizar la PC del local con el respaldo diario y el cartel de arranque.**
      Nada de eso está andando en el local hasta que Matías recompile (`build\build_all.bat`) y
      pase el `OtterActualizador`. Después, confirmar que a las 24 hs exista
      `C:\SistemaDual\backups\stock_<fecha>.db`.
- [ ] **Decidir dónde va una copia FUERA de la PC.** Las copias diarias protegen contra la base
      dañada, un borrado o un bug, pero **están en el mismo disco**: no cubren que el disco muera
      ni que se roben la máquina. Las dos opciones sensatas son un pendrive que quede puesto
      siempre, o copiar a la PC de Matías por Tailscale. Es una decisión, no código.
- [ ] **Nadie se entera de nada.** Telegram solo avisa umbrales de stock (`revisar_umbrales_y_alertar`)
      y encima está sin configurar. No hay ningún aviso de "el servicio se cayó", "hace 3 días que
      no hay copia" ni "el disco está lleno": todo queda escrito en logs que nadie lee. La app
      Semáforo Clientes tapa una parte, pero solo mientras Matías la mire. Cuando esté el token de
      Telegram, lo barato es mandar por ahí lo que hoy va al `watchdog.log`.
- [ ] Excluir `C:\SistemaDual` del antivirus: los `.exe` de PyInstaller no están firmados y un
      antivirus que ponga uno en cuarentena deja el negocio sin caja sin decir por qué.
- [x] Traer al repo un test que arme el carrito, agregue una línea y lea `celda.get()` de cada
      columna (no solo el dato en `self.carrito`) — el bug del carrito en blanco pasó screening
      precisamente porque ningún test anterior releía el texto real dibujado en pantalla.
      Hecho: `tests/test_usb_caja.py` (y ocho pruebas más, ver `tests/README.md`).

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

## Las herramientas de Matías (consola RG35XX-DS y celular)

Dejaron de estar postergadas: la RG35XX-DS **corre Android 14**, así que son apps Android normales
y andan igual en el celular. **Viven en repos propios, uno por app** — no en este repo, que es el
del sistema del cliente. Matías las pidió así: separadas y ordenadas.

| App | Repo | Para qué |
|---|---|---|
| **Semáforo Clientes** | `MatiasPiti/semaforo-clientes` | Lista de clientes; para cada uno consulta `GET /health` de su `remote_api` cada 20s. Verde = OK, amarillo = PC viva con el servicio caído, violeta = token mal, rojo = sin respuesta. |
| **Chuleta Otter** | `MatiasPiti/chuletas_diagnosticos` | Ocho secciones de diagnóstico, **offline**. Sin permiso de INTERNET a propósito: es la garantía de que funciona con la red caída. El contenido está todo en `Chuleta.kt`. |
| **Ventas Otter** | `MatiasPiti/dashboard-ventas` | Total vendido hoy, más vendidos, y stock ≤ 5. Usa `reports.resumen_dashboard` y `products.listar_stock`, que **ya estaban en la allowlist**: se instala y anda, sin recompilar nada en el local. El stock va en un botón aparte porque trae el catálogo entero (4587 productos). |
| **Precios Otter** | `MatiasPiti/calculadora-precios` | La cadena Costo S/IVA → Precio Costo → % Ganancia → Precio Final, **offline**. `Precios.kt` es un port de `pos_core/precios.py`, **verificado contra el original con 1037 casos al azar, cero diferencias**. |
| **Presupuestos Otter** | `MatiasPiti/presupuestos-otter` | Cotizar un cliente nuevo, **offline**. Ver abajo: es la única que toca el negocio de Matías, no el del cliente. |

**Se compilan sin PC.** Ninguna necesita Android Studio: cada repo tiene
`.github/workflows/apk.yml`, que en cada push a `main` compila el APK en los servidores de GitHub
y lo publica como *release*. Desde el navegador del celular se toca el `.apk` y se instala (hay
que permitir "apps de origen desconocido"). Es un APK de debug: uso propio, no Play Store.

Los datos de cada cliente (IP de Tailscale, puerto, token) se cargan a mano en la app, en el
dispositivo. No están en ningún repo. **Costo asumido de tenerlas separadas: al rotar un token
hay que cargarlo en cada app que lo use** (hoy el semáforo y el dashboard), además de la laptop
de Leo. Matías eligió repos y APKs separadas a propósito, para tener todo seccionado.

**Ninguna se probó todavía contra el local real** — compilan y el APK sale solo, pero el primer
contacto con la API lo hace Matías. La excepción es Precios Otter, que no habla con nadie y sí
está verificada: ver abajo.

**Cómo se verificó Precios Otter, y por qué se puede hacer de nuevo.** Desde este entorno
`dl.google.com` está bloqueado (por eso no se puede compilar un APK acá), pero **Maven Central y
el portal de plugins de Gradle sí responden**: alcanza para compilar y correr Kotlin puro en la
JVM. Como el cálculo de precios no tiene nada de Android, se compiló y se corrió contra
`pos_core/precios.py` con casos al azar comparando los cuatro números — 428 de lectura de números
tecleados (`1.500,50`, `$ 2.499,00`, `.500`, basura) y 609 de la cadena completa con los borde
(margen −100, costos en cero, todo vacío). Cero diferencias, redondeo incluido. La verificación
vive en `verificacion/` de ese repo y **compila el mismo archivo que usa la app**, no una copia,
así que no puede haber deriva. Se le metió un bug a propósito para confirmar que tiene dientes:
agarró el caso caro, `761.917` leído como 761,92.

**El criterio, para la próxima app:** si una parte de la lógica se puede escribir sin dependencias
de Android, conviene separarla así — se verifica de verdad en vez de compilarse a ciegas. Se
aplicó también en Presupuestos Otter.

### Cuánto cobra Matías por el sistema (para Presupuestos Otter)

Esto es el negocio de Matías, no el del cliente, y **es dato suyo**: si cambia, cambia acá y en la
app (los precios son editables adentro de la app y quedan guardados).

- **Instalación: la suma de las partes que el cliente pida.** El sistema completo —el que se le
  instaló a El Galpón Del Nono— son **USD 800**.
- **Abono mensual: USD 95, para todos, obligatorio.** Cubre mantenimiento, soporte y
  actualizaciones personalizadas.
- El reparto por módulo que trae la app de fábrica suma exactamente esos 800 y **lo propuso
  Claude, no Matías**: 350 el núcleo (Caja + Panel), 90 el servicio de stock + Telegram, 90 el
  Dueño Remoto, 80 los USBs, 120 ARCA y 70 la migración del catálogo. Es un punto de partida para
  editar, no un precio acordado.
- Se cotiza **en USD y en pesos**. La cotización del dólar se carga a mano —nunca desde una API,
  que puede romperse o quedar bloqueada— y la app avisa cuando la última tiene más de una semana.

La pantalla del cliente describe cada parte **por lo que le resuelve al negocio**, no por cómo se
llama el ejecutable ("seguís vendiendo desde un pendrive si la computadora falla", no
"`USB_Caja`"), y va rotada 180° para leerla desde el otro lado del mostrador. Si el aparato expone
una segunda pantalla (la de arriba de la RG35XX-DS) el resumen va ahí; **eso no se pudo probar**,
así que si no la encuentra usa la pantalla completa y un toque da vuelta el resumen.

### Evaluado y postergado: la app de góndola

Escanear un código de barras con la cámara para ver y cambiar precios caminando el local. Es la
idea original de Matías (venía de querer un lector en la consola). **Matías la postergó**, pero el
análisis ya está hecho:

- Sale **sin tocar la PC del cliente**: `precios.buscar_para_precios` y `precios.actualizar_precios`
  ya están en la allowlist.
- **Solo celular, no la RG:** la consola no tiene cámara.
- **Escribe en la base real del negocio**, así que la confirmación tiene que mostrar el nombre del
  producto en grande y el precio de antes y después: un escaneo equivocado más un precio mal
  tecleado cambia lo que cobra la caja al instante.
- **`actualizar_precios` no registra quién cambió el precio** — solo pisa `actualizado_en`. Con el
  Panel del Dueño alcanzaba (una PC, en el local); con un celular en el bolsillo, ya no tanto.
  Arreglarlo implica tocar el sistema del cliente y recompilar: es una decisión aparte.

**Lo que NO se puede hacer, y por qué** (evaluado, descartado): una app en la RG conectada **por
USB** que repare la PC sola. Cuando se conecta la RG a la PC, Windows es el *host* y la RG el
*dispositivo*: en esa dirección Android no puede ejecutar nada en Windows. Las vueltas posibles
son hacerse pasar por un teclado USB (escribe a ciegas, no puede leer la respuesta, así que no hay
"comprueba y rehace") o compartir red por USB, que es lo mismo que ya da Tailscale. Y hay una
trampa de fondo: **la API remota vive adentro del `StockService`, así que si lo que se cayó es el
servicio, ninguna app puede pedirle que se arranque a sí mismo.** Por eso el watchdog corre en la
PC y no en la consola. Si algún día se hace un "arreglar desde la app", va con reglas fijas y
lista blanca de acciones, nunca con un modelo decidiendo solo: un agente con permiso para ejecutar
cosas en la PC de la caja es exactamente lo que puede dejar el negocio cerrado a las 8 de la
mañana.

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
