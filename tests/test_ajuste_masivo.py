"""El ajuste masivo de precios no puede cobrar $ 100 de más por un error de cuenta.

La regla del negocio: un aumento se redondea hacia ARRIBA a la centena
(2500 + 3 % = 2575 -> 2600), y un resultado que ya cae justo en la centena
no se toca. Lo que pasaba: 3000 + 10 % tiene que dar 3300, pero la cuenta
con decimales binarios da 3300.0000000000005 y el techo a la centena lo
llevaba a 3400. Cien pesos de más en cada producto de precio "redondo" cada
vez que el dueño sube un grupo (pasaba con 3000, 6000, 7000, 11000... al
10 %, y con otros al 8 % y al 12 %).

Se prueba la cuenta contra aritmética exacta (fracciones, sin decimales
binarios) y, lo que importa de verdad, el precio que queda GUARDADO al
apretar "Aplicar %" en el Panel sobre un filtro.
"""
import math
import os
import sys
import tempfile
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="ajuste_masivo_")
from pos_core import paths
paths.set_base_override(base)
from pos_core import db
db.preparar_base()

from pos_core import bulk_edit, products, filters

fallos = []


def precio_de(codigo):
    return db.get_connection().execute(
        "SELECT precio_venta FROM Productos WHERE codigo = ?", (codigo,)).fetchone()["precio_venta"]


# --------------------------------------------------------------------- #
# 1. Los casos que se ven en el mostrador
# --------------------------------------------------------------------- #
casos = [
    # (precio, porcentaje, esperado, por qué)
    (2500, 3, 2600, "el ejemplo de la regla: 2575 sube a 2600"),
    (3000, 10, 3300, "cae justo en la centena: no puede ir a 3400"),
    (6000, 10, 6600, "idem"),
    (7500, 8, 8100, "idem, al 8 %"),
    (2600, 0, 2600, "un múltiplo exacto de 100 no se mueve"),
    (2500, -5, 2400, "una baja también redondea hacia arriba: 2375 -> 2400"),
    (100, -100, 0, "nunca queda negativo"),
]
for precio, pct, esperado, motivo in casos:
    dado = bulk_edit.calcular_nuevo_precio(precio, porcentaje=pct)
    if dado != esperado:
        fallos.append(f"{precio} {pct:+}% dio {dado} y tenía que dar {esperado} ({motivo})")
if not fallos:
    print(f"OK: los {len(casos)} casos del mostrador dan lo que dice la regla (3000 + 10 % = 3300)")

if bulk_edit.calcular_nuevo_precio(2500.10, monto_fijo=99.90) != 2600:
    fallos.append("2500,10 + 99,90 no dio 2600")
if bulk_edit.calcular_nuevo_precio(1234.56, porcentaje=3.5, redondear=False) != 1277.77:
    fallos.append("sin redondear a la centena tiene que quedar en centavos (1277,77)")

# --------------------------------------------------------------------- #
# 2. Contra aritmética exacta, en una grilla grande
# --------------------------------------------------------------------- #
distintos = []
total = 0
for pct in (-50, -10, -5, 3, 3.5, 5, 7.5, 8, 10, 12, 15, 20, 25, 30, 50, 100):
    for centavos in range(10000, 3000001, 2500):       # de $ 100 a $ 30.000, de a $ 25
        precio = centavos / 100
        exacto = Fraction(centavos, 100) * (1 + Fraction(str(pct)) / 100)
        en_centavos = Fraction(round(exacto * 100), 100)
        esperado = max(0, math.ceil(en_centavos / 100) * 100)
        dado = bulk_edit.calcular_nuevo_precio(precio, porcentaje=pct)
        total += 1
        if dado != esperado:
            distintos.append((precio, pct, dado, esperado))
if distintos:
    fallos.append(f"{len(distintos)} de {total} ajustes no dan lo que da la cuenta exacta; "
                   f"los primeros: {distintos[:5]}")
else:
    print(f"OK: {total} ajustes comparados contra la cuenta exacta, cero diferencias")

# --------------------------------------------------------------------- #
# 3. Lo que queda guardado al apretar "Aplicar %" en el Panel
# --------------------------------------------------------------------- #
products.crear_producto(codigo="F1", nombre="FIDEOS", precio_venta=3000.0, usuario="test")
products.crear_producto(codigo="F2", nombre="FIDEOS TIRABUZON", precio_venta=2500.0, usuario="test")
products.crear_producto(codigo="F3", nombre="FIDEOS MOÑO", precio_venta=7000.0, usuario="test")
filters.guardar_filtro_manual("FIDEOS", ["F1", "F2", "F3"])

from tkinter import messagebox
carteles = []
for nombre in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, nombre, lambda *a, _n=nombre, **k: carteles.append((_n, a)))
messagebox.askyesno = lambda *a, **k: True

from apps.master_dueno.main import AppDueno
app = AppDueno()
app.update()
app.buscar_filtro.insert(0, "fideos")
app._refrescar_lista_filtros()
app.lista_filtros.selection_set(0)
app._cargar_filtro_guardado()
app.update()
app.bulk_pct.insert(0, "10")
app._aplicar_bulk_pct()
app.update()

guardados = {c: precio_de(c) for c in ("F1", "F2", "F3")}
esperados = {"F1": 3300.0, "F2": 2800.0, "F3": 7700.0}
if guardados != esperados:
    fallos.append(f"'Aplicar %' con 10 dejó guardado {guardados} y tenía que dejar {esperados}")
else:
    print("OK: 'Aplicar %' del Panel deja 3000 -> 3300, 2500 -> 2800 y 7000 -> 7700")

# Lo que muestra la grilla del filtro es lo que quedó guardado.
en_pantalla = {app.bulk_tree.item(i, "values")[0]: float(app.bulk_tree.item(i, "values")[2])
               for i in app.bulk_tree.get_children()}
if en_pantalla != esperados:
    fallos.append(f"la grilla del filtro muestra {en_pantalla} y lo guardado es {esperados}")
else:
    print("OK: la grilla del filtro muestra los mismos precios que quedaron guardados")
app.destroy()

print()
if fallos:
    print("=== FALLOS AJUSTE MASIVO ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== AJUSTE MASIVO OK ===")
