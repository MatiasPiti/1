"""La reimportación del sistema viejo trae los COSTOS, y la pantalla abre con ellos.

Pedido de Matías (26/11/2026): Leo abría un producto en Precios y el Costo
S/IVA, el Precio Costo y el % Ganancia estaban vacíos, porque el Excel solo
traía el precio de venta. Ahora el generador los saca de MAS_STO (COS_SIV,
COS_STO, POR_STO) y la reimportación los guarda. Se prueba con el generador
de verdad, la misma función que usa el Panel, y la pantalla del Panel.

Y la trampa que eso destapa: con los costos cargados, pasar por un campo sin
tocarlo (Enter o Tab) recalculaba la cadena y el precio final quedaba con
centavos que nadie puso ($4601 -> $4600.73). En los .DBF de septiembre pasa
con 2380 de 2414 productos: el sistema viejo guarda el precio redondo.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import types

AQUI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AQUI)
try:
    import dbfread  # noqa: F401
except ImportError:
    sys.modules["dbfread"] = types.SimpleNamespace(DBF=None)
_spec = importlib.util.spec_from_file_location(
    "generar_lista_precios", os.path.join(AQUI, "scripts", "generar_lista_precios.py"))
generador = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generador)

base = tempfile.mkdtemp(prefix="costos_lista_")
from pos_core import paths  # noqa: E402
paths.set_base_override(base)

from tkinter import messagebox  # noqa: E402
errores = []
messagebox.showinfo = lambda *a, **k: None
messagebox.showwarning = lambda *a, **k: None
messagebox.showerror = lambda *a, **k: errores.append(a)

from pos_core import actualizacion_precios as ap, db, products  # noqa: E402

fallos = []


def ok(t):
    print("OK:", t)


def costos(codigo):
    con = sqlite3.connect(os.path.join(base, "database", "stock.db"))
    try:
        return con.execute("SELECT costo_sin_iva, precio_compra, margen_ganancia, precio_venta "
                           "FROM Productos WHERE codigo = ?", (codigo,)).fetchone()
    finally:
        con.close()


def producto(interno, nombre, precio, siv=0, cos=0, por=0):
    return {"COD_STO": interno, "NOM_STO": nombre, "PRE_STO": precio, "RUB_STO": "ALMACEN",
            "SRU_STO": "GENERAL", "COS_STO": cos, "COS_SIV": siv, "POR_STO": por, "IVA_STO": 21}


def excel(productos, barras, nombre):
    filas, _ = generador.armar(productos, barras)
    ruta = os.path.join(base, nombre)
    generador.escribir_importacion(filas, ruta)
    return ruta


db.preparar_base()
# Lo que hay hoy en el local: los productos con el precio y sin costo.
products.crear_producto(codigo="7791111111116", nombre="SAPHIRUS TEXTIL 250ML.", precio_venta=4500,
                        usuario="prueba")
products.crear_producto(codigo="7792222222225", nombre="YERBA PLAYADITO 1KG", precio_venta=3000,
                        usuario="prueba")
products.crear_producto(codigo="7793333333334", nombre="SIN COSTO EN EL VIEJO", precio_venta=900,
                        usuario="prueba")
# Uno al que alguien ya le cargó el costo en Otter, y el viejo no tiene costo.
db.get_connection().execute("UPDATE Productos SET precio_compra = 700 WHERE codigo = '7793333333334'")
db.get_connection().commit()

# El sistema viejo, como en los .DBF reales: COS_SIV * 1,21 = COS_STO y
# COS_STO * (1 + POR_STO) da el precio, redondeado.
viejo = [
    producto("7791111111116", "SAPHIRUS TEXTIL 250ML.", 4601, siv=2646.35, cos=3202.08, por=43.69),
    producto("7792222222225", "YERBA PLAYADITO 1KG", 3000, siv=1652.89, cos=2000.0, por=50.0),
    producto("7793333333334", "SIN COSTO EN EL VIEJO", 900),
    producto("7794444444443", "PRODUCTO NUEVO 500G", 1500, siv=826.45, cos=1000.0, por=50.0),
]
ruta = excel(viejo, {}, "Otter_importar.xlsx")

plan = ap.analizar(ruta)
if plan["costos"] != 3:
    fallos.append(f"el ensayo cuenta {plan['costos']} productos con costos nuevos, eran 3")
if "se les cargan los costos" not in plan["informe"]:
    fallos.append("el informe del ensayo no dice cuántos costos se cargan")
if costos("7792222222225")[1] not in (None, 0):
    fallos.append("el ENSAYO escribió costos en la base")

hecho = ap.aplicar(ruta, usuario="prueba")
if costos("7791111111116") != (2646.35, 3202.08, 43.69, 4601):
    fallos.append(f"SAPHIRUS no quedó con los costos del sistema viejo: {costos('7791111111116')}")
if costos("7792222222225") != (1652.89, 2000.0, 50.0, 3000):
    fallos.append(f"un producto que ya tenía el precio no recibió los costos: {costos('7792222222225')}")
if costos("7794444444443") != (826.45, 1000.0, 50.0, 1500):
    fallos.append(f"el producto nuevo no se creó con sus costos: {costos('7794444444443')}")
if costos("7793333333334")[1] != 700:
    fallos.append(f"un costo vacío en el sistema viejo borró el que había en Otter: {costos('7793333333334')}")
if hecho["costos"] != 3 or "Costos cargados:        3" not in hecho["informe"]:
    fallos.append(f"el resultado dice {hecho['costos']} costos cargados, eran 3")
if hecho["errores"]:
    fallos.append(f"errores al aplicar: {hecho['errores']}")
if not fallos:
    ok("se cargan Costo S/IVA, Precio Costo y % Ganancia; un costo vacío del viejo no borra el de Otter")

otra_vez = ap.aplicar(ruta, usuario="prueba")
if otra_vez["costos"] or otra_vez["precios"]:
    fallos.append(f"reimportar el mismo Excel volvió a escribir: {otra_vez['costos']} costos, "
                  f"{otra_vez['precios']} precios")
else:
    ok("reimportar el mismo Excel no cambia nada")

# Un Excel viejo, sin las columnas de costo, no toca los costos.
import openpyxl  # noqa: E402
wb = openpyxl.Workbook()
wb.active.append(["Código", "Nombre", "Precio Venta", "Stock Inicial", "Proveedor"])
wb.active.append(["7792222222225", "YERBA PLAYADITO 1KG", 3100, 0, ""])
ruta_vieja = os.path.join(base, "sin_costos.xlsx")
wb.save(ruta_vieja)
ap.aplicar(ruta_vieja, usuario="prueba")
if costos("7792222222225") != (1652.89, 2000.0, 50.0, 3100):
    fallos.append(f"un Excel sin columnas de costo tocó los costos: {costos('7792222222225')}")
else:
    ok("un Excel sin las columnas de costo actualiza el precio y deja los costos")
db.cerrar_conexion()

# --------------------------------------------------------------------- #
# La pantalla: abre con los costos, y pasar con Enter no cambia el precio.
from apps.master_dueno.main import AppDueno  # noqa: E402

app = AppDueno()
app.nb.select(app.tab_stock)
app.update()
e = app.entradas_precio
app.precio_buscar.insert(0, "7791111111116")
app._buscar_para_precios()
app.update()
vistos = {c: e[c].get() for c in e}
if vistos != {"costo_sin_iva": "2646.35", "precio_costo": "3202.08", "margen": "43.69",
              "precio_final": "4601.00"}:
    fallos.append(f"la pantalla de Precios no abre con los costos cargados: {vistos}")
else:
    ok("la pantalla de Precios abre con Costo S/IVA, Precio Costo y % Ganancia")

e["costo_sin_iva"].focus_force()
app.update()
if app.focus_get() is not e["costo_sin_iva"]:
    print("SALTEADA la parte de teclado: no hay gestor de ventanas que dé el foco")
else:
    for campo in ("costo_sin_iva", "precio_costo", "margen", "precio_final"):
        e[campo].event_generate("<Return>")
        app.update()
    if e["precio_final"].get() != "4601.00":
        fallos.append(f"pasar por los costos con Enter, sin tocarlos, cambió el precio: "
                      f"{e['precio_final'].get()}")
    app.btn_guardar_precio.event_generate("<Return>")
    app.update()
    if costos("7791111111116")[3] != 4601:
        fallos.append(f"guardó otro precio sin que nadie lo tocara: {costos('7791111111116')}")
    else:
        ok("recorrer los costos con Enter sin tocarlos guarda el mismo $4601")

    # Y si Leo cambia el costo, sí se recalcula manteniendo la ganancia.
    app.precio_buscar.insert(0, "7792222222225")
    app._buscar_para_precios()
    app.update()
    e["precio_costo"].focus_force()
    app.update()
    e["precio_costo"].delete(0, "end")
    e["precio_costo"].insert(0, "2.200")
    e["precio_costo"].event_generate("<Return>")
    app.update()
    if e["precio_final"].get() != "3300.00":
        fallos.append(f"subir el costo a 2.200 con 50 % no dio $3300: {e['precio_final'].get()}")
    else:
        ok("cambiar el costo sí recalcula: 2.200 + 50 % = $3300")
if errores:
    fallos.append(f"apareció un cartel de error: {errores}")
app.destroy()

if fallos:
    print("\nFALLÓ:\n - " + "\n - ".join(fallos))
    sys.exit(1)
print("\nTodo OK")
