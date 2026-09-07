"""Regresión: el cambio de init_db() -> preparar_base() no puede romper
nada de lo que ya andaba (Caja y Panel del Maestro), ni impedir que una
app abra si la migración falla (regla 6: ante la duda, la caja abre)."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="maestro_")
from pos_core import paths
paths.set_base_override(base)

from tkinter import messagebox
messagebox.showinfo = lambda *a, **k: None
messagebox.showwarning = lambda *a, **k: None
messagebox.showerror = lambda *a, **k: print("ERROR DIALOG:", a)

fallos = []

# --- base nueva: preparar_base no debe hacer nada raro ---
from pos_core import db
cambios = db.preparar_base()
print("BASE NUEVA, cambios:", cambios)
if cambios:
    fallos.append(f"sobre una base recién creada la migración quiso cambiar algo: {cambios}")
# idempotente: correrla dos veces no cambia nada
if db.preparar_base():
    fallos.append("preparar_base() no es idempotente")

from pos_core import products
products.crear_producto(codigo="7790001", nombre="Yerba Playadito 1kg",
                        precio_venta=3500.0, stock_inicial=10, usuario="test")

# --- las dos apps del Maestro abren y venden ---
from apps.master_caja.main import AppCaja
app = AppCaja()
app.update()
app.buscador.insert(0, "7790001")
app._on_buscar()
app.update()
if len(app._filas_carrito) != 1:
    fallos.append("la Caja Maestra no cargó el producto")
else:
    print("CAJA MAESTRA, carrito:", [c.get() for c in list(app._filas_carrito.values())[0]["widgets"]])
app.destroy()

from apps.master_dueno.main import AppDueno
app2 = AppDueno()
app2.update()
print("PANEL DEL DUEÑO:", app2.title(), "-", len(app2.nb.tabs()), "pestañas -", app2.geometry())
app2.destroy()

# --- si la migración explota, la app tiene que abrir igual ---
original = db.aplicar_migraciones
db.aplicar_migraciones = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("base rara"))
try:
    r = db.preparar_base()
    print("MIGRACIÓN ROTA -> preparar_base devolvió:", r, "(y no explotó)")
except Exception as e:
    fallos.append(f"preparar_base propagó el error de migración: {e}")
finally:
    db.aplicar_migraciones = original

print()
if fallos:
    print("=== FALLOS REGRESIÓN ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== REGRESIÓN MAESTRO OK ===")
