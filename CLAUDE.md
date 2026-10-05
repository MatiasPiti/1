# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> El proyecto, el README y los comentarios están en español (es-AR). Mantené ese idioma en código, commits y documentación nueva.

## Qué es este repositorio

Monorepo Python de un **Sistema Dual de Caja y Stock** para un comercio. Una sola base de código
compila (con PyInstaller) a **5 ejecutables Windows portables**:

- **MaestroCaja** (`apps/master_caja/`) — caja de la PC fija, cobra tickets.
- **MaestroDueno** (`apps/master_dueno/`) — panel del dueño: dashboard, stock, edición masiva,
  facturas PDF, carga Excel, alertas de Telegram, y el panel oculto de sincronización.
- **USB_Caja** (`apps/usb_caja/`) — caja portátil de emergencia (DB propia en el pendrive).
- **USB_Dueno** (`apps/usb_dueno/`) — panel del dueño portátil (reutiliza `master_dueno` + banner).
- **USB_Mantenimiento** (`apps/usb_dev/`) — herramienta de reparación/diagnóstico (del desarrollador).

La UI de escritorio es **tkinter**. Además hay una **app Android "Panel Dueño"** (`apps/movil_dueno/`,
Flutter) que habla con **`services/api_dueno.py`** (FastAPI, también se compila como `ApiDueno.exe`)
corriendo en la PC del local; el celular llega por Tailscale. Fuera de eso, la única red es el bot de
Telegram (saliente, best-effort).

## Comandos

```bash
pip install -r requirements.txt            # requests, matplotlib, pdfplumber, openpyxl, pyinstaller, pywin32 (solo win32)
python scripts/setup_inicial.py --base apps/master_dueno   # crea la base del panel + usuario 'dueño' (sin --base: carpeta del script)
python apps/master_dueno/main.py           # panel del dueño (alta de productos, Excel, alertas, stock)
python apps/master_caja/main.py            # caja (cobrar)
python apps/usb_caja/main.py               # caja portátil
python apps/usb_dueno/main.py              # panel dueño portátil
python apps/usb_dev/mantenimiento.py       # mantenimiento
python services/api_dueno.py --base apps/master_dueno   # API del celular (puerto 8765) sobre la base de dev del panel
python services/api_dueno.py --base apps/master_dueno --definir-pin 1234   # crea/cambia el PIN del dueño y sale
build\build_all.bat                        # compila los 5 .exe + StockService + ApiDueno (requiere Windows + deps)

python -m pytest -q                        # tests de la API y de reglas de negocio (tests/)
python -m pytest tests/test_api_dueno.py -k lector   # uno solo

cd apps/movil_dueno                        # app Android (Flutter 3.47+)
flutter analyze && flutter test            # análisis + tests
flutter test test/sesion_test.dart --plain-name "bloquea"   # uno solo
flutter build apk --release
```

- Los tests de Python usan una instalación aislada en `tmp_path` vía `SISTEMA_DUAL_BASE`
  (`tests/conftest.py`); nunca tocan una `stock.db` real.
- El APK lo compila GitHub Actions (`.github/workflows/panel-dueno.yml`, artefacto `panel-dueno-apk`);
  acá no hay SDK de Android. Firma con los secrets `ANDROID_*` si existen (ver `apps/movil_dueno/README.md`):
  `key.properties` lleva solo `storeFile` y las contraseñas/alias llegan como variables de entorno
  (`build.gradle.kts::datoFirma`), porque `key.properties` toma la `\` como escape.
- Las apps necesitan un entorno gráfico (tkinter). En headless, importá y ejercitá `pos_core/*`
  directamente en vez de abrir las ventanas.
- `database/`, `SYNC_DATA/`, `logs/`, `config.ini` y `*.db` están gitignored: son estado local/por-USB,
  no se versionan.

## Arquitectura y reglas que cruzan varios archivos

La separación clave es **`pos_core/` = lógica de negocio pura (sin UI)**, importada por todas las apps
y servicios. Toda regla de negocio vive ahí; `apps/*/main.py` solo arma la UI y llama a `pos_core`.
Un mismo `sql/schema.sql` define las 3 clases de DB (Maestro, USB_Caja, USB_Dueño), que son archivos
SQLite **independientes**.

Invariantes que hay que respetar al tocar este código (romper uno corrompe datos silenciosamente):

1. **El stock se escribe SOLO por `pos_core/stock_service.py`.** Usa *versionado optimista*: cada fila
   de `Productos` tiene `version`; todo `UPDATE` de stock lleva `WHERE id = ? AND version = ?` y, si
   `rowcount == 0` (otro escribió primero), reintenta la transacción hasta 5 veces
   (`_con_reintento_optimista`). Nunca hagas un `UPDATE ... SET stock` fuera de este módulo.
2. **Toda escritura va envuelta en `pos_core/db.py::transaction()`** (`BEGIN IMMEDIATE ... COMMIT/ROLLBACK`).
   La DB corre en `journal_mode = WAL` y `synchronous = FULL`: la prioridad declarada es *cero pérdida
   de datos ante corte de luz*. No abras conexiones ni transacciones por fuera de `db.py`.
3. **La identidad entre bases es `uuid_unico` (UUID4), nunca el `id` autoincremental.** El `id` es local
   a cada `.db`. Toda entidad que puede nacer en un USB (`Ventas`, `Movimientos_Stock`, `Productos`)
   genera su `uuid_unico` al crearse, y ese es el que viaja en los JSON y el que usa la conciliación
   para detectar duplicados. `Detalle_Ventas` y `Movimientos_Stock` referencian al producto por
   **`codigo`** (clave natural/EAN), no por `producto_id`.
4. **La bandera `sincronizado` maneja el export.** `pos_core/sync_export.py` selecciona `sincronizado = 0`,
   escribe el JSON y **recién después** marca esas filas como sincronizadas, dentro de la misma
   transacción. Así, si el archivo nunca se aplica en el Maestro, una exportación futura los vuelve a
   incluir. Mantené ese orden (escribir → marcar), nunca al revés.
5. **Solo el Maestro concilia** (`pos_core/reconciliation.py`). La conciliación **reproduce el DELTA**
   del movimiento (no copia un stock absoluto) y **deduplica por `uuid_unico`**. Siempre hay un
   `analizar_export_*()` (dry-run de solo lectura) antes de `aplicar_export_*()`.
6. **`Detalle_Ventas` guarda snapshot histórico** (`producto_nombre`, `precio_unitario`): un cambio de
   precio posterior **no** altera tickets ya cobrados. Un precio cambiado en un USB mientras la caja
   vendía con el viejo no es un conflicto: son eventos independientes con timestamp propio; el precio
   se concilia "hacia adelante" comparando `actualizado_en`.
7. **Rutas portables:** todo (`database/`, `SYNC_DATA/`, `logs/`, `config.ini`) cuelga de
   `pos_core/paths.py::get_base_path()`, **nunca** de una letra de unidad fija. El mismo USB debe
   funcionar montado como `E:`, `F:` o `G:`.
8. **Telegram es best-effort y nunca puede romper el core.** El monitor de alertas
   (`telegram_bot.MonitorAlertas`, cada 5 min con cooldown de 4 h) lo arranca `services/api_dueno.py::main`,
   que es lo único que corre 24 h en la PC; ninguna app de escritorio lo arranca. `pos_core/telegram_bot.py` atrapa
   `requests.RequestException`; sin internet no manda nada, pero cobrar/descontar stock sigue 100%
   offline. No introduzcas dependencias de red en el camino de cobro.
9. **Edición masiva redondea a la centena superior** (`pos_core/bulk_edit.py::redondear_a_centena_superior`),
   cada producto en su propia transacción. Ej.: `calcular_nuevo_precio(2500, porcentaje=3)` → `2600`.
10. **Ningún renglón de un PDF se descarta en silencio.** `pdf_import.py` prueba tablas estructuradas
    (`pdfplumber.extract_tables`) y cae a una batería de regex; lo que no matchea va a
    `lineas_no_reconocidas` para carga manual, y un PDF sin texto se marca `es_pdf_escaneado`.

## Refuerzo de integridad (servicios)

`services/stock_daemon_windows.py` corre con `pythonw.exe` (sin ventana) como *watchdog*: el descuento
real ya ocurre dentro de `pos_core/sales.py::cerrar_ticket()`, pero el demonio revisa cada 5s si quedó
una línea de venta sin su `SALIDA_VENTA` (p. ej. corte de luz entre el INSERT de la venta y el
descuento) y la reintenta. En producción se registra como Servicio de Windows real con
`services/stock_windows_service.py` (pywin32).

## Módulo oculto de sincronización

En `MaestroDueno`, **Ctrl+Shift+M** abre `apps/master_dueno/panel_sync.py`: detecta carpetas
`SYNC_DATA/` en las unidades montadas, muestra el `ResumenConciliacion` (ventas nuevas, duplicados
omitidos, conflictos de precio) en dry-run, y al aplicar escribe tanto en `Log_Sincronizacion` (DB)
como en `sincronizacion_exitosa.txt` (dentro del propio USB).

## App del dueño en el celular (API + Flutter)

- **Una funcionalidad nueva atraviesa 3 capas:** lógica en `pos_core/` → endpoint en
  `services/api_dueno.py` (capa fina, sin reglas de negocio) → `lib/api/cliente_api.dart` +
  `lib/api/modelos.dart` en la app. El contrato JSON tiene que coincidir; hay un servidor falso que lo
  imita en `apps/movil_dueno/test/servidor_falso.dart` y conviene actualizarlo junto con
  `tests/test_api_dueno.py`.
- **Base de datos de la API:** cada app resuelve `database/` relativo a **su propia carpeta**
  (`get_base_path()`), así que MaestroCaja y MaestroDueno no comparten base solo por estar en
  `C:\SistemaDual`. La API debe arrancar con `--base <carpeta de MaestroDueno>` (setea
  `SISTEMA_DUAL_BASE`, que tiene prioridad en `paths.get_base_path()`). Con el despliegue actual las
  ventas de MaestroCaja quedan en la base de su carpeta y no se ven en la app (unificar está pendiente).
  Si `<base>/database/stock.db` no existe o no hay ningún usuario DUEÑO activo, la API **no arranca**:
  sale con código 2 y deja el motivo en `logs/api_dueno.log`. `--definir-pin <PIN>` (4 a 12 dígitos,
  `usuarios.definir_pin_dueno`) crea o actualiza el usuario 'dueño' en esa base y sale con 0 sin levantar
  el servidor; en desarrollo también sirve `scripts/setup_inicial.py --base <carpeta>`.
- **Sesión:** login con el PIN de un usuario `rol='DUEÑO'` (`pos_core/usuarios.py`, sha256); devuelve un
  token HMAC de 30 días firmado con `config.ini [api] secreto` (se autogenera solo si `config.ini` se leyó
  bien y no lo tiene; se lee una vez por proceso, así que borrarlo y reiniciar la API invalida todos los
  celulares). El token lleva una huella HMAC del `pin_hash` y en cada pedido se revalida contra el usuario:
  cambiar el PIN (`--definir-pin`), desactivarlo o quitarle el rol DUEÑO → 401 en todos los celulares.
  5 PIN fallidos por IP bloquean esa IP 5 min, y más de 20 fallidos en 5 min sumando todas las IPs
  bloquean todos los logins 5 min. `config.ini` se guarda de forma atómica (`config.guardar_config`:
  temporal + `os.replace`), y quien lo vaya a reescribir tiene que leerlo con `cargar_config(estricto=True)`,
  que ante un archivo ilegible tira `ConfigIlegibleError` en vez de pisarlo con defaults. Todo lo que escribe
  la app queda con `usuario = "<nombre> (app)"` y `origen = 'MAESTRO'`.
- **Umbrales de alerta:** el efectivo es la fila de `Configuracion_Alertas` del producto o, si no hay, la
  global (`producto_codigo IS NULL`). Usá `pos_core/alertas.py` (no `ON CONFLICT(producto_codigo)`:
  en SQLite cada NULL es distinto para el UNIQUE y eso duplicaba filas globales).
- **Facturas desde la app:** dos pasos (`/api/facturas/analizar` → revisión en el celular →
  `/api/facturas/aplicar`). El parser puede leer la misma línea por tabla y por texto: se marca
  `posible_duplicado` y la app la deja destildada, pero no se descarta.
- **Convenciones de la app:** todo campo donde se escribe un código usa `CampoCodigo`
  (`lib/widgets/comunes.dart`), que trae el botón de cámara; la plata se formatea con
  `formato.moneda()` (`$ 2.500`, símbolo adelante); las pestañas viven en un `IndexedStack` con
  `TickerMode` para no animar lo que no se ve; colores en `lib/tema.dart` (paleta del logo, temas
  claro/oscuro elegibles). Textos de UI en español rioplatense.
