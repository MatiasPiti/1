"""Prueba real de USB_Dueno: que abra, que tenga las pestañas, el cartel
de emergencia, el botón de sincronización, que NO tenga Ctrl+Shift+M y que
la pantalla de precios ande."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="usbdueno_")
from pos_core import paths
paths.set_base_override(base)
from pos_core.db import init_db
init_db()

from pos_core import products
from tkinter import messagebox
messagebox.showinfo = lambda *a, **k: None
messagebox.showwarning = lambda *a, **k: None
messagebox.showerror = lambda *a, **k: print("ERROR DIALOG:", a)

products.crear_producto(codigo="7790001", nombre="Yerba Playadito 1kg",
                        precio_venta=3500.0, stock_inicial=10, usuario="test")

import tkinter as tk
from apps.usb_dueno.main import AppUsbDueno
app = AppUsbDueno()
app.update()

fallos = []

print("TITULO:", app.title())
if "Emergencia" not in app.title():
    fallos.append("el título no avisa que es el modo emergencia")

# origen parcheado -> todo movimiento queda marcado como del USB
import apps.master_dueno.main as maestro_mod
print("ORIGEN:", maestro_mod.ORIGEN)
if maestro_mod.ORIGEN != "USB_DUENO":
    fallos.append(f"ORIGEN quedó en {maestro_mod.ORIGEN}, no USB_DUENO")

# cartel rojo + botón de sincronización presentes
textos = []
def _recorrer(w):
    for h in w.winfo_children():
        try:
            t = h.cget("text")
            if t:
                textos.append(str(t))
        except Exception:
            pass
        _recorrer(h)
_recorrer(app)
if not any("EMERGENCIA" in t for t in textos):
    fallos.append("falta el cartel de MODO EMERGENCIA")
if not any("Preparar sincronizaci" in t for t in textos):
    fallos.append("falta el botón 'Preparar sincronización'")

# pestañas
pestanas = [app.nb.tab(i, "text") for i in app.nb.tabs()]
print("PESTAÑAS:", pestanas)
if len(pestanas) < 5:
    fallos.append(f"solo hay {len(pestanas)} pestañas: {pestanas}")

# la ventana tiene que entrar en una pantalla chica (1366x768)
app.update_idletasks()
geo = app.geometry()
print("GEOMETRIA:", geo, "pantalla:", app.winfo_screenwidth(), "x", app.winfo_screenheight())

# Ctrl+Shift+M NO debe conciliar acá (es exclusivo del Maestro)
binds = app.bind_all()
print("BINDS GLOBALES:", binds)
if "<Control-Shift-M>" in str(binds) or "<Control-Shift-KeyPress-M>" in str(binds):
    fallos.append("Ctrl+Shift+M sigue activo en el USB Dueño (debe ser solo del Maestro)")

# --- pantalla de precios (lo nuevo de 3799aae) ---
from pos_core import precios
p = precios.buscar_para_precios("7790001")
print("PRECIOS BUSCAR:", p)
if not p:
    fallos.append("precios.buscar_para_precios no encontró el producto")
else:
    r = precios.recalcular(precio_costo=2000.0, precio_final=3000.0, cambio="precio_final")
    print("RECALCULO precio_final=3000 sobre costo 2000:", r)
    if r["margen"] is None or abs(r["margen"] - 50.0) > 0.01:
        fallos.append(f"recalcular dio margen {r['margen']}, esperaba 50%")
    r2 = precios.recalcular(costo_sin_iva=1000.0, margen=100.0, cambio="margen")
    print("RECALCULO costo s/IVA 1000 + 100%:", r2)
    if r2["precio_final"] is None:
        fallos.append("recalcular no calculó precio final desde costo+margen")

# --- exportar sincronización ---
from pos_core import sync_export
ruta = sync_export.exportar_dueno()
print("SYNC EXPORTADO:", os.path.basename(ruta), os.path.getsize(ruta), "bytes")

app.destroy()
print()
if fallos:
    print("=== FALLOS USB_DUENO ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== USB_DUENO OK ===")
