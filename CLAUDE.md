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

La UI es **tkinter**. No hay backend web ni red salvo el bot de Telegram (saliente, best-effort).

## Comandos

```bash
pip install -r requirements.txt            # requests, matplotlib, pdfplumber, openpyxl, pyinstaller, pywin32 (solo win32)
python scripts/setup_inicial.py            # crea database/stock.db + usuario 'dueño' (primer arranque)
python apps/master_dueno/main.py           # panel del dueño (alta de productos, Excel, alertas, stock)
python apps/master_caja/main.py            # caja (cobrar)
python apps/usb_caja/main.py               # caja portátil
python apps/usb_dueno/main.py              # panel dueño portátil
python apps/usb_dev/mantenimiento.py       # mantenimiento
build\build_all.bat                        # compila los 5 .exe (requiere Windows + deps)
```

- **No hay suite de tests en el repo** (el README menciona una verificación de `bulk_edit`, pero no
  hay `tests/` ni pytest configurado). Si agregás tests, no existe convención previa: usá `pytest` y
  documentá el comando acá.
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
8. **Telegram es best-effort y nunca puede romper el core.** `pos_core/telegram_bot.py` atrapa
   `requests.RequestException`; sin internet no manda nada, pero cobrar/descontar stock sigue 100%
   offline. No introduzcas dependencias de red en el camino de cobro.
9. **Edición masiva redondea a la centena superior** (`pos_core/bulk_edit.py::redondear_a_centena_superior`),
   cada producto en su propia transacción. Ej.: `calcular_nuevo_precio(2500, porcentaje=3)` → `2600`.
10. **El `id` del PDF/Excel nunca se descarta en silencio.** `pdf_import.py` prueba tablas estructuradas
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

## Contexto de trabajo

El **Panel del Dueño** (`apps/master_dueno/main.py`) es la superficie de funcionalidad del dueño
—dashboard, stock, edición masiva, facturas, Excel, alertas— y es la referencia para cualquier app
complementaria (p. ej. una app móvil del dueño). Cualquier cliente nuevo debe apoyarse en `pos_core/`
y respetar los invariantes de arriba en vez de reimplementar lógica de stock/ventas.
