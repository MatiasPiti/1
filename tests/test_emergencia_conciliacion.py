"""El circuito de emergencia de punta a punta: lo que se vende en el pendrive tiene que llegar al Maestro.

El día que se rompe la PC del local se vende desde el USB, y después eso se
concilia en el Maestro (Ctrl+Shift+M). Nunca se había probado el camino
entero, y tenía tres fallas que solo aparecen ese día:

1. VENTAS QUE NO LLEGAN. Cada "Preparar sincronización" exportaba solo lo
   nuevo desde la vez anterior y pisaba el archivo fijo (export_caja.json),
   que es el único que lista el panel de conciliación. Con dos días de
   emergencia, al Maestro llegaban solo las ventas del segundo; apretar el
   botón dos veces seguidas dejaba el archivo vacío.

2. STOCK DUPLICADO. Al pendrive se le carga la lista exportada del Maestro,
   con el stock de ese día. Esa carga queda como un movimiento de entrada
   por producto, y al conciliar se reproducía en el Maestro: el stock del
   catálogo entero se sumaba otra vez.

3. PRODUCTO NUEVO CONTADO DOS VECES. Uno creado en el USB del Dueño entraba
   al Maestro con el stock que tenía en el pendrive Y ADEMÁS con sus
   movimientos: creado con 10 y 5 más después, llegaba con 30.

Acá se arman tres bases de verdad (el Maestro y los dos pendrives), se
vende, se exporta y se concilia, mirando las ventas y el stock que quedan
en el Maestro.
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pos_core import paths, db

RAIZ = tempfile.mkdtemp(prefix="emergencia_")
MAESTRO = os.path.join(RAIZ, "SistemaDual")
USB_CAJA = os.path.join(RAIZ, "usb_caja")
USB_DUENO = os.path.join(RAIZ, "usb_dueno")
for carpeta in (MAESTRO, USB_CAJA, USB_DUENO):
    os.makedirs(carpeta)

fallos = []


def usar(base):
    """Cambia de máquina: cierra la base anterior y abre la de `base`."""
    db.cerrar_conexion()
    paths.set_base_override(base)
    db.preparar_base()


from pos_core import excel_import, products, reconciliation, sales, stock_service, sync_export


def stock(codigo):
    fila = db.get_connection().execute(
        "SELECT stock FROM Productos WHERE codigo = ?", (codigo,)).fetchone()
    return None if fila is None else fila["stock"]


def cuantas_ventas():
    return db.get_connection().execute("SELECT COUNT(*) c FROM Ventas").fetchone()["c"]


def total_vendido():
    return db.get_connection().execute("SELECT COALESCE(SUM(total), 0) t FROM Ventas").fetchone()["t"]


def vender(codigo, nombre, cantidad, precio):
    sales.cerrar_ticket([{"codigo": codigo, "nombre": nombre, "cantidad": cantidad,
                          "precio_unitario": precio}],
                        metodo_pago="EFECTIVO", usuario="cajero", origen="USB_CAJA")


# --------------------------------------------------------------------- #
# El Maestro, con stock de verdad, y la lista que se lleva a los pendrives
# --------------------------------------------------------------------- #
usar(MAESTRO)
products.crear_producto(codigo="111", nombre="COCA COLA 1,5L.", precio_venta=2500.0,
                        stock_inicial=20, usuario="test")
products.crear_producto(codigo="222", nombre="YERBA PLAYADITO 1KG", precio_venta=4000.0,
                        stock_inicial=10, usuario="test")
lista = os.path.join(RAIZ, "lista_de_precios.xlsx")
excel_import.exportar_lista_precios(lista)

# --------------------------------------------------------------------- #
# 1. USB Caja: dos días de emergencia, dos "Preparar sincronización"
# --------------------------------------------------------------------- #
usar(USB_CAJA)
carga = excel_import.cargar_masivo(lista, usuario="cajero", origen="USB_CAJA")
if carga["creados"] != 2 or stock("111") != 20:
    fallos.append(f"la prueba no pudo cargar el catálogo en el pendrive: {carga}")

vender("111", "COCA COLA 1,5L.", 1, 2500.0)          # día 1
vender("222", "YERBA PLAYADITO 1KG", 2, 4000.0)
sync_export.exportar_caja()
time.sleep(1.1)                                       # otro nombre de archivo con fecha
vender("111", "COCA COLA 1,5L.", 3, 2500.0)          # día 2
sync_export.exportar_caja()
time.sleep(1.1)
sync_export.exportar_caja()                           # y el botón apretado una vez de más

archivo_fijo = os.path.join(USB_CAJA, "SYNC_DATA", "export_caja.json")
with open(archivo_fijo, encoding="utf-8") as f:
    exportado = json.load(f)
if len(exportado["ventas"]) != 3:
    fallos.append(f"export_caja.json (el que lista el panel) trae {len(exportado['ventas'])} de las 3 "
                   f"ventas del pendrive: las otras no llegan nunca al Maestro")
else:
    print("OK: después de tres 'Preparar sincronización', export_caja.json trae las 3 ventas del pendrive")
if any(m["tipo"] == "ENTRADA_EXCEL" for m in exportado["movimientos_stock"]):
    fallos.append("la carga del catálogo en el pendrive viaja como si fuera mercadería que entró")
if os.path.exists(archivo_fijo + ".tmp"):
    fallos.append("quedó un .tmp tirado en SYNC_DATA")

# --------------------------------------------------------------------- #
# 2. Conciliar en el Maestro: llegan las 3 ventas y el stock queda bien
# --------------------------------------------------------------------- #
usar(MAESTRO)
previa = reconciliation.analizar_export_caja(archivo_fijo)
if (previa.ventas_nuevas, previa.movimientos_aplicados) != (3, 3):
    fallos.append(f"el resumen previo dice {previa.ventas_nuevas} ventas y {previa.movimientos_aplicados} "
                   f"movimientos; eran 3 y 3")
if cuantas_ventas() != 0:
    fallos.append("el resumen previo (que no tiene que tocar nada) importó ventas")

resumen = reconciliation.aplicar_export_caja(archivo_fijo, usuario_dev="dev")
if resumen.errores:
    fallos.append(f"la conciliación de la Caja dio errores: {resumen.errores}")
if cuantas_ventas() != 3 or total_vendido() != 18000.0:
    fallos.append(f"al Maestro llegaron {cuantas_ventas()} ventas por ${total_vendido():.2f}; "
                   f"el pendrive vendió 3 por $18000.00")
else:
    print("OK: al Maestro llegan las 3 ventas del pendrive ($ 18.000)")
if (stock("111"), stock("222")) != (16, 8):
    fallos.append(f"el stock del Maestro quedó en coca={stock('111')} yerba={stock('222')}; "
                   f"tenía 20 y 10, se vendieron 4 y 2: tenía que quedar 16 y 8")
else:
    print("OK: el stock del Maestro baja lo que se vendió (20 -> 16, 10 -> 8), sin sumar el catálogo del USB")

# Conciliar de nuevo (el mismo archivo, o uno anterior con fecha) no cambia nada.
anteriores = sorted(n for n in os.listdir(os.path.join(USB_CAJA, "SYNC_DATA")) if n.startswith("export_caja_"))
for nombre in ["export_caja.json"] + anteriores:
    reconciliation.aplicar_export_caja(os.path.join(USB_CAJA, "SYNC_DATA", nombre), usuario_dev="dev")
if cuantas_ventas() != 3 or (stock("111"), stock("222")) != (16, 8):
    fallos.append(f"repetir la conciliación cambió algo: {cuantas_ventas()} ventas, "
                   f"coca={stock('111')} yerba={stock('222')}")
else:
    print(f"OK: repetir la conciliación (y los {len(anteriores)} archivos con fecha) no duplica nada")

# --------------------------------------------------------------------- #
# 3. Un archivo de la versión anterior del USB: no puede duplicar el stock
# --------------------------------------------------------------------- #
# Los pendrives ya grabados exportan con el formato viejo, CON la carga del
# catálogo adentro. El Maestro nuevo la tiene que saltear igual.
viejo = os.path.join(RAIZ, "export_caja_viejo.json")
with open(viejo, "w", encoding="utf-8") as f:
    json.dump({"tipo": "export_caja", "generado_en": "2026-11-01T10:00:00.000", "ventas": [],
               "detalle_ventas": [],
               "movimientos_stock": [
                   {"uuid_unico": "carga-111", "producto_codigo": "111", "tipo": "ENTRADA_EXCEL",
                    "cantidad": 20, "stock_resultante": 20, "motivo": "Carga masiva inicial",
                    "ticket_uuid": None, "usuario": "cajero", "origen": "USB_CAJA",
                    "fecha_hora": "2026-11-01T09:00:00.000"},
                   {"uuid_unico": "venta-vieja-111", "producto_codigo": "111", "tipo": "SALIDA_VENTA",
                    "cantidad": 1, "stock_resultante": 19, "motivo": "Venta", "ticket_uuid": "t-viejo",
                    "usuario": "cajero", "origen": "USB_CAJA", "fecha_hora": "2026-11-01T09:30:00.000"}]},
              f)
if reconciliation.es_export_completo(viejo) or not reconciliation.es_export_completo(archivo_fijo):
    fallos.append("no se distingue un archivo de la versión anterior (solo lo nuevo) de uno completo")
previa_vieja = reconciliation.analizar_export_caja(viejo)
resumen_viejo = reconciliation.aplicar_export_caja(viejo, usuario_dev="dev")
if stock("111") != 15:
    fallos.append(f"con un archivo del formato anterior el stock de la coca quedó en {stock('111')}: "
                   f"tenía 16 y el archivo trae una venta de 1 (y la carga del catálogo, que no se aplica)")
elif resumen_viejo.movimientos_omitidos_carga_inicial != 1 or previa_vieja.movimientos_omitidos_carga_inicial != 1:
    fallos.append("el resumen no dice que salteó la carga del catálogo del USB")
else:
    print("OK: un archivo del formato anterior tampoco duplica el stock (saltea la carga del catálogo y lo dice)")

# --------------------------------------------------------------------- #
# 4. USB Dueño: mercadería que entró y un producto nuevo
# --------------------------------------------------------------------- #
usar(USB_DUENO)
excel_import.cargar_masivo(lista, usuario="dueño", origen="USB_DUENO")
products.crear_producto(codigo="999", nombre="PRODUCTO NUEVO EN EL USB", precio_venta=100.0,
                        stock_inicial=10, usuario="dueño", origen="USB_DUENO")
sync_export.exportar_dueno()                           # una exportación que nadie concilia
time.sleep(1.1)
stock_service.sumar_stock_manual("999", 5, usuario="dueño", origen="USB_DUENO")
stock_service.sumar_stock_manual("111", 6, usuario="dueño", origen="USB_DUENO")   # llegaron 6 cocas
en_el_usb = stock("999")
sync_export.exportar_dueno()
archivo_dueno = os.path.join(USB_DUENO, "SYNC_DATA", "export_dueño.json")

usar(MAESTRO)
coca_antes = stock("111")
previa_dueno = reconciliation.analizar_export_dueno(archivo_dueno)
resumen_dueno = reconciliation.aplicar_export_dueno(archivo_dueno, usuario_dev="dev")
if resumen_dueno.errores:
    fallos.append(f"la conciliación del Dueño dio errores: {resumen_dueno.errores}")
if stock("999") is None:
    fallos.append("el producto nuevo del USB no llegó al Maestro (su primera exportación no se había "
                   "conciliado, y la segunda ya no lo traía)")
elif stock("999") != en_el_usb:
    fallos.append(f"el producto nuevo del USB llegó con stock {stock('999')}; en el pendrive tenía {en_el_usb}")
else:
    print(f"OK: el producto creado en el USB del Dueño llega con su stock ({en_el_usb}), no con el doble")
if stock("111") != coca_antes + 6:
    fallos.append(f"entraron 6 cocas desde el USB del Dueño y el stock pasó de {coca_antes} a {stock('111')}")
elif stock("222") != 8:
    fallos.append(f"la yerba, que el USB del Dueño no tocó, cambió de stock: {stock('222')}")
else:
    print("OK: la mercadería cargada en el USB del Dueño se suma una vez, y lo que no se tocó queda igual")
if (previa_dueno.productos_nuevos, previa_dueno.movimientos_aplicados) != (resumen_dueno.productos_nuevos,
                                                                           resumen_dueno.movimientos_aplicados):
    fallos.append(f"el resumen previo del Dueño ({previa_dueno.productos_nuevos} nuevos, "
                   f"{previa_dueno.movimientos_aplicados} movimientos) no coincide con lo que se aplicó "
                   f"({resumen_dueno.productos_nuevos}, {resumen_dueno.movimientos_aplicados})")
elif resumen_dueno.movimientos_omitidos_carga_inicial != 2:
    fallos.append(f"tenía que saltear la carga del catálogo de los 2 productos que ya estaban en el Maestro: "
                   f"salteó {resumen_dueno.movimientos_omitidos_carga_inicial}")
else:
    print("OK: el resumen previo del Dueño dice exactamente lo que después se aplica")

reconciliation.aplicar_export_dueno(archivo_dueno, usuario_dev="dev")
if (stock("999"), stock("111")) != (en_el_usb, coca_antes + 6):
    fallos.append("repetir la conciliación del Dueño cambió el stock")
else:
    print("OK: repetir la conciliación del Dueño no cambia nada")

db.cerrar_conexion()
print()
if fallos:
    print("=== FALLOS EMERGENCIA ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== EMERGENCIA Y CONCILIACIÓN OK ===")
