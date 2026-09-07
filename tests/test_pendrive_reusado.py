"""Pendrive reusado: base vieja + ejecutable nuevo, arrancando las apps
tal cual lo hace el .exe (preparar_base + abrir la ventana)."""
import os, sys, sqlite3, subprocess, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base_dir = tempfile.mkdtemp(prefix="pendrive_reusado_")
os.makedirs(os.path.join(base_dir, "database"), exist_ok=True)
db_path = os.path.join(base_dir, "database", "stock.db")

# "Base de una versión anterior" = el esquema de hoy sin las tres columnas
# que agregó el cambio de septiembre. Se arma así, y no con un git show de
# un commit fijo, para que el test siga corriendo en cualquier clon.
from pos_core.db import _schema_sql
COLUMNAS_NUEVAS = ("subrubro", "costo_sin_iva", "margen_ganancia")
schema_viejo = "\n".join(l for l in _schema_sql().splitlines()
                         if not any(l.strip().startswith(c) for c in COLUMNAS_NUEVAS))
conn = sqlite3.connect(db_path)
conn.executescript(schema_viejo)
conn.execute("""INSERT INTO Productos (uuid_unico, codigo, nombre, precio_venta, stock)
                VALUES ('u-1', '7790001', 'Yerba de la base vieja', 3500.0, 7)""")
conn.execute("""INSERT INTO Ventas (uuid_unico, fecha_hora, total, metodo_pago, usuario, origen, sincronizado)
                VALUES ('v-vieja', '2026-08-01T10:00:00.000', 999.0, 'EFECTIVO', 'leo', 'USB_CAJA', 0)""")
conn.commit()
conn.close()

from pos_core import paths
paths.set_base_override(base_dir)

from tkinter import messagebox
messagebox.showinfo = lambda *a, **k: None
messagebox.showwarning = lambda *a, **k: None
messagebox.showerror = lambda *a, **k: print("ERROR DIALOG:", a)

fallos = []

# lo que hace ahora el arranque del .exe
from pos_core.db import preparar_base
cambios = preparar_base()
print("MIGRACIÓN AL ARRANCAR:", cambios)

# los datos que ya estaban tienen que seguir ahí
conn = sqlite3.connect(db_path)
prod = conn.execute("SELECT nombre, stock FROM Productos WHERE codigo='7790001'").fetchone()
ventas = conn.execute("SELECT COUNT(*) FROM Ventas").fetchone()[0]
conn.close()
print("PRODUCTO QUE YA ESTABA:", prod, " VENTAS QUE YA ESTABAN:", ventas)
if prod is None or prod[1] != 7 or ventas != 1:
    fallos.append("la migración perdió datos que ya estaban en el pendrive")

# la pantalla de precios, que antes se caía
from pos_core import precios
try:
    r = precios.buscar_para_precios("7790001")
    print("PRECIOS SOBRE BASE VIEJA:", r["producto"]["nombre"])
except Exception as e:
    fallos.append(f"la pantalla de precios sigue rota sobre base vieja: {type(e).__name__}: {e}")

# y las dos apps del USB tienen que abrir sobre esa base
from apps.usb_caja.main import AppUsbCaja
app = AppUsbCaja()
app.update()
app.buscador.insert(0, "7790001")
app._on_buscar()
app.update()
if len(app._filas_carrito) != 1:
    fallos.append("USB_Caja no pudo cargar el producto de la base vieja")
else:
    celdas = list(app._filas_carrito.values())[0]["widgets"]
    print("CARRITO SOBRE BASE VIEJA:", [c.get() for c in celdas])
app.destroy()

from apps.usb_dueno.main import AppUsbDueno
app2 = AppUsbDueno()
app2.update()
print("USB_DUENO ABRIÓ:", app2.title(), "-", len(app2.nb.tabs()), "pestañas")
app2.destroy()

print()
if fallos:
    print("=== FALLOS ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== PENDRIVE REUSADO OK ===")
