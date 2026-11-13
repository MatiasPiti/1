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
# float() acepta "inf", "nan" y "1e999": como precio no son números (un precio
# infinito quedaba guardado y la Caja lo mostraba así).
raros = {t: precios.leer_numero(t) for t in ("inf", "-inf", "infinity", "nan", "1e999", float("inf"), float("nan"))
         if precios.leer_numero(t) is not None}
if raros:
    fallos.append(f"leer_numero aceptó como precio algo que no es un número: {raros}")
else:
    ok("leer_numero no acepta 'inf', 'nan' ni '1e999' como precio")

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

# Una oferta de "$ fijo de descuento" igual o mayor que el precio dejaría el
# producto en $ 0, y la Caja lo cobraría $ 0 sin avisar (es el mismo agujero
# que el 100 % de descuento, que ya estaba cerrado).
from pos_core import ofertas, sales
products.crear_producto(codigo="7790600", nombre="Alfajor Triple", precio_venta=900.0, usuario="test")
errores_mostrados.clear()
escribir(app.oferta_codigo, "7790600")
app.oferta_tipo.set("$ fijo de descuento")
escribir(app.oferta_valor, "1.000")
escribir(app.oferta_dias, "7")
app._crear_oferta_nueva()
if fila("SELECT 1 x FROM Ofertas WHERE producto_codigo = '7790600'") or not errores_mostrados:
    fallos.append("se pudo crear una oferta de $ 1.000 de descuento sobre un producto de $ 900 "
                   "(la Caja lo cobraría $ 0)")
else:
    ok("una oferta de $ fijo mayor que el precio se rechaza y dice por qué")

# Y si la oferta era buena pero después le bajaron el precio al producto por
# debajo del descuento: la Caja cobra el precio de lista, nunca $ 0.
ofertas.crear_oferta(codigo="7790600", tipo_descuento="MONTO_FIJO", valor=500.0, descripcion="",
                     dias=7, usuario="test")
en_caja = [p for p in sales.buscar_productos("7790600") if p["codigo"] == "7790600"][0]
if en_caja["precio_venta"] != 400.0 or not en_caja["en_oferta"]:
    fallos.append(f"una oferta normal de $ 500 sobre $ 900 no dio $ 400 en la Caja: {en_caja}")
with db.transaction() as conn:
    conn.execute("UPDATE Productos SET precio_venta = 450 WHERE codigo = '7790600'")
en_caja = [p for p in sales.buscar_productos("7790600") if p["codigo"] == "7790600"][0]
if en_caja["precio_venta"] != 450.0:
    fallos.append(f"con el precio por debajo del descuento, la Caja cobraría ${en_caja['precio_venta']:.2f} "
                   f"(tiene que cobrar el de lista, $ 450, nunca $ 0)")
else:
    ok("si el precio queda por debajo del descuento, la Caja cobra el de lista y no $ 0")

app.destroy()
db.cerrar_conexion()
print()
if fallos:
    print("=== FALLOS NÚMEROS DEL PANEL ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== NÚMEROS DEL PANEL OK ===")
