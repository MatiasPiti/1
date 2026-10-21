"""El Panel lee la plata a la argentina en el alta, el ajuste por monto y las ofertas.

La pestaña Precios ya leía "1.500" como mil quinientos (pos_core/precios.
recalcular), pero el alta de un producto nuevo, el ajuste masivo por monto
fijo y las ofertas usaban float(): "1.500" quedaba en 1,5. Un precio fijo de
oferta escrito "1.500" hacía cobrar $ 1,50 en la Caja toda la semana, sin
ningún aviso. Se prueba con el Panel de verdad (sin ventanas de aviso).
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="panel_numeros_")
from pos_core import paths  # noqa: E402
paths.set_base_override(base)

from tkinter import messagebox  # noqa: E402
errores_mostrados = []
messagebox.showinfo = lambda *a, **k: None
messagebox.showwarning = lambda *a, **k: None
messagebox.showerror = lambda *a, **k: errores_mostrados.append(a)

from pos_core import db, precios, products  # noqa: E402

fallos = []


def ok(texto):
    print("OK:", texto)


def fila(sql, params=()):
    con = sqlite3.connect(os.path.join(base, "database", "stock.db"))
    con.row_factory = sqlite3.Row
    try:
        r = con.execute(sql, params).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


db.preparar_base()
products.crear_producto(codigo="7790001", nombre="Yerba Mate 1kg", precio_venta=3500, usuario="prueba")
db.cerrar_conexion()

# el criterio, el mismo de la hoja de precios
casos = {"1.500": 1500.0, "1.500,50": 1500.5, "$ 2.500": 2500.0, "1500": 1500.0, "0.50": 0.5, "12,5": 12.5,
         "": None, "mil": None}
distintos = {t: (precios.leer_numero(t), e) for t, e in casos.items() if precios.leer_numero(t) != e}
if distintos:
    fallos.append(f"leer_numero no lee como la hoja de precios: {distintos}")
else:
    ok("leer_numero: '1.500' es mil quinientos, '1.500,50' lleva centavos, vacío o texto es None")

from apps.master_dueno.main import AppDueno  # noqa: E402

app = AppDueno()
app.update()


def escribir(entrada, texto):
    entrada.delete(0, "end")
    entrada.insert(0, texto)


# Alta de un producto nuevo con "1.500"
escribir(app.alta_codigo, "7790500")
escribir(app.alta_nombre, "Galletitas de Agua")
escribir(app.alta_precio, "1.500")
escribir(app.alta_stock, "6")
app._crear_producto_nuevo()
p = fila("SELECT precio_venta, stock FROM Productos WHERE codigo = '7790500'")
if not p or p["precio_venta"] != 1500 or p["stock"] != 6:
    fallos.append(f"el alta con precio '1.500' guardó {p} (tiene que ser 1500)")
else:
    ok("el alta del Panel con precio '1.500' guarda $ 1.500")
errores_mostrados.clear()
escribir(app.alta_codigo, "7790501")
escribir(app.alta_nombre, "Otro")
escribir(app.alta_precio, "mil")
app._crear_producto_nuevo()
if not errores_mostrados or fila("SELECT 1 x FROM Productos WHERE codigo = '7790501'"):
    fallos.append("un precio que no es número no avisó (o se dio de alta igual)")
else:
    ok("un precio que no es número avisa y no da de alta nada")

# Ajuste masivo por monto fijo con "1.000"
app.filtro_codigos = ["7790001"]
app.bulk_tree.selection_set(())
escribir(app.bulk_fijo, "1.000")
app._aplicar_bulk_fijo()
p = fila("SELECT precio_venta FROM Productos WHERE codigo = '7790001'")
if not p or p["precio_venta"] != 4500:
    fallos.append(f"sumar '1.000' por monto fijo dejó la yerba en {p} (de 3500 tiene que quedar en 4500)")
else:
    ok("el ajuste por monto fijo con '1.000' suma mil pesos, no uno")

# Oferta de precio fijo con "1.500"
escribir(app.oferta_codigo, "7790001")
app.oferta_tipo.set("Precio fijo promocional")
escribir(app.oferta_valor, "1.500")
escribir(app.oferta_dias, "7")
app._crear_oferta_nueva()
o = fila("SELECT tipo_descuento, valor FROM Ofertas WHERE producto_codigo = '7790001' ORDER BY id DESC LIMIT 1")
if not o or o["tipo_descuento"] != "PRECIO_FIJO" or o["valor"] != 1500:
    fallos.append(f"la oferta de precio fijo '1.500' quedó {o} (la Caja cobraría eso)")
else:
    ok("una oferta de precio fijo '1.500' queda en $ 1.500 (no en $ 1,50)")

app.destroy()
db.cerrar_conexion()
print()
if fallos:
    print("=== FALLOS NÚMEROS DEL PANEL ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== NÚMEROS DEL PANEL OK ===")
