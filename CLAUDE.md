# Contexto del proyecto — Otter

> Este archivo es la memoria del proyecto. Claude Code lo carga solo al abrir el repo.
> La **arquitectura** está en `README.md` (detallada, con diagramas) — no se repite acá.
> Esto es lo otro: quién es el cliente, en qué estado está todo, qué se decidió y por qué,
> qué falta, y qué **nunca** hay que hacer.

## Quién es quién

- **Matías** — el desarrollador. Es con quien hablás. **Respondele siempre en español.**
- **Leo** — el dueño del negocio, el cliente. No es técnico. No toca el código ni Tailscale.
- **El negocio** — kiosco/almacén "El Galpón Del Nono", San Luis 2892, Casilda (Santa Fe).

Esto no es un ejercicio: es un sistema que va a estar cobrando plata real, todo el día, con un
cajero que no sabe de computadoras y sin nadie cerca para arreglarlo. Cuando haya que elegir,
elegí lo que falla menos, no lo que es más elegante.

## Qué es Otter

POS + control de stock en Python/Tkinter/SQLite que compila a **7 ejecutables Windows portables**
(PyInstaller `--onedir`). Ver `README.md` para el detalle. En una línea cada uno:

| Ejecutable | Dónde va | Para qué |
|---|---|---|
| `MaestroCaja` | PC del local | La caja. Vende, imprime ticket, descuenta stock. |
| `MaestroDueno` | PC del local | Panel del dueño: stock, precios, reportes, ARCA, ofertas. |
| `StockService` | PC del local | Servicio de Windows: alertas Telegram + API remota. |
| `DuenoRemoto` | Laptop de Leo | El mismo panel, contra la PC del local por Tailscale. |
| `USB_Caja` | Pendrive | Caja de emergencia si se rompe la PC. Base propia, se concilia después. |
| `USB_Dueno` | Pendrive | Panel de emergencia. |
| `USB_Mantenimiento` | Pendrive de Matías | Diagnóstico y reparación en el local. |
| `OtterInstalador` | Pendrive de instalación | Hace toda la instalación en un botón. |

**Build:** `build\build_all.bat` desde la raíz, en Windows. Genera todo en `dist\`.

## Reglas duras — no romper ninguna

1. **La API remota (`services/remote_api.py`) NUNCA se expone a internet ni con port forwarding.**
   Toda su seguridad se apoya en que solo se llegue al puerto por la VPN (Tailscale).
2. **La laptop de Leo recibe `DuenoRemoto.exe`, jamás `MaestroDueno.exe`.** El segundo abre
   perfecto y sin dar ningún error, pero se crea su propia base vacía y nunca muestra una venta
   del negocio. Es el error más caro posible y el instalador existe en parte para evitarlo.
3. **Nunca copiar `dist\database\` ni `dist\config.ini` al cliente** — tienen datos de prueba.
4. **El token `galpon-nono-686dd219a7d742b8` quedó expuesto en un chat: no se usa nunca más.**
   El instalador genera uno nuevo solo.
5. La cuenta de Tailscale es de Matías y es la llave de la red de todos los clientes:
   **2FA activado**, y **escribir las ACLs antes de sumar un segundo cliente** — si no, los
   clientes se ven entre sí.
6. **Ante la duda, la caja abre.** Ninguna validación, candado o chequeo nuevo puede impedir que
   el sistema arranque a las 8 de la mañana. Cuando algo falle raro, que falle abriendo.

## Estado actual

Todo está en `main`, commiteado y pusheado. La rama `claude/dual-pos-portable-emergency-dvt5ym`
quedó vieja (tiene solo dos subidas manuales de archivos por la web): **el trabajo va a `main`**.

Verificado en Windows real: los 7 ejecutables compilan, las apps abren, el Excel de 4587
productos carga, el servicio instala y arranca, la API remota autentica.

Verificado en sandbox (37 suites de tests, todas en verde): venta completa solo con teclado con
la plata cuadrando, 8 facturas ARCA simultáneas, 7 procesos concurrentes sobre el stock,
atomicidad con fallas de disco inyectadas, pedidos hostiles contra la API remota, instalación
completa por su camino real, y el candado de instancia única (probado matando el proceso).

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
- **`StockService` es el único ejecutable sin `--noconsole`, y es a propósito.** Con `--noconsole`
  se queda sin stdout, `install` falla al imprimir su primer mensaje y la instalación se aborta
  sin mostrar ningún error: el servicio simplemente nunca aparece. Está explicado en el .bat.

## Lo que falta hacer

**Antes de dejarlo andando en el local:**
- [ ] Rehacer la build completa (`build\build_all.bat`) — el `.bat` cambió y el `espejo_apps` del
      USB de Mantenimiento todavía tiene una versión vieja del servicio.
- [ ] Probar el circuito de emergencia de punta a punta desde pendrives de verdad: vender offline
      → "Preparar sincronización" → conciliar en el Maestro con `Ctrl+Shift+M`.
- [ ] Configurar la impresora térmica POS-58 en la PC del local.
- [ ] Instalar Tailscale en la PC del local; escribir las ACLs.
- [ ] Cargar el token y el chat_id del bot de Telegram del cliente.

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

## Cómo probar

No hay suite de tests en el repo — se fue probando con scripts sueltos en un scratchpad temporal
que **no sobrevive a la sesión**. Para volver a probar acá:

```bash
python3.12 -m venv venv && venv/bin/pip install -r requirements.txt
xvfb-run -a venv/bin/python <script>     # las apps son Tkinter, necesitan display
```

Las apps se pueden instanciar directo (`AppCaja()`, `AppDueno(backend=...)`) y manejar con
`app.update()`; para probar la API remota se levanta con `remote_api.iniciar_servidor(...)` en un
puerto libre. Los `messagebox` se reemplazan por stubs para que no bloqueen.

**Vale la pena traer esos tests al repo** la próxima vez que se toque algo serio: hoy cada cambio
se revalida desde cero.

## Cómo trabajar en este repo

- Commits y explicaciones **en español**, describiendo el problema real del negocio, no la
  mecánica del código ("quitar un artículo suelto borraba todos los artículos sueltos", no
  "refactor de la lista del carrito").
- Los comentarios en el código explican **por qué**, sobre todo cuando algo parece raro pero
  tiene un motivo (ver `build_all.bat` y el `--noconsole`).
- Matías prefiere respuestas **cortas y concretas**. Cuando pide un resumen de lo hecho, que sea
  breve de verdad.
