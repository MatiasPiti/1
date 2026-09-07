"""Mantenimiento completo sobre una instalación Maestro simulada:
que corra entero sin explotar, que migre la base vieja, que arregle una
base corrupta y que NO toque config.ini ni la base al reponer archivos."""
import os, sys, sqlite3, subprocess, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

usb_dev = tempfile.mkdtemp(prefix="usbmant2_")
from pos_core import paths
paths.set_base_override(usb_dev)
from apps.usb_dev import mantenimiento

fallos = []

# ---- instalación Maestro simulada, con base VIEJA ----
inst = tempfile.mkdtemp(prefix="maestro_cliente_")
os.makedirs(os.path.join(inst, "database"))
os.makedirs(os.path.join(inst, "MaestroCaja"))
os.makedirs(os.path.join(inst, "logs"))
db_path = os.path.join(inst, "database", "stock.db")
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
                VALUES ('u-1','7790001','Yerba', 3500.0, -3)""")   # stock negativo a corregir
conn.commit()
conn.close()
CONFIG_REAL = "[remoto]\ntoken = TOKEN-REAL\nip = 100.100.100.100\n"
with open(os.path.join(inst, "config.ini"), "w") as f:
    f.write(CONFIG_REAL)
with open(os.path.join(inst, "MaestroCaja", "MaestroCaja.exe"), "w") as f:
    f.write("EXE VIEJO")

# ---- espejo del USB, como queda tras compilar y probar en dist\ ----
espejo = os.path.join(usb_dev, "espejo_apps", "MaestroCaja")
os.makedirs(espejo)
with open(os.path.join(espejo, "MaestroCaja.exe"), "w") as f:
    f.write("EXE NUEVO CON LOS ARREGLOS")
with open(os.path.join(espejo, "config.ini"), "w") as f:
    f.write("[remoto]\ntoken = token-de-prueba\n")

log = mantenimiento.ejecutar_mantenimiento(inst, "MAESTRO")
print("\n".join(log))
print()

if open(os.path.join(inst, "config.ini")).read() != CONFIG_REAL:
    fallos.append("el config.ini real fue pisado")
if open(os.path.join(inst, "MaestroCaja", "MaestroCaja.exe")).read() != "EXE NUEVO CON LOS ARREGLOS":
    fallos.append("el .exe viejo no se repuso")

conn = sqlite3.connect(db_path)
cols = [r[1] for r in conn.execute("PRAGMA table_info(Productos)")]
stock = conn.execute("SELECT stock FROM Productos WHERE codigo='7790001'").fetchone()[0]
mov = conn.execute("SELECT COUNT(*) FROM Movimientos_Stock").fetchone()[0]
conn.close()
if "subrubro" not in cols:
    fallos.append("la migración de esquema no corrió")
if stock != 0:
    fallos.append(f"el stock negativo no se corrigió (quedó en {stock})")
if mov != 1:
    fallos.append(f"la corrección de stock no dejó auditoría ({mov} movimientos)")

if not os.path.isfile(os.path.join(usb_dev, "reporte_mantenimiento.txt")):
    fallos.append("no se escribió reporte_mantenimiento.txt")

print()
if fallos:
    print("=== FALLOS MANTENIMIENTO COMPLETO ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== MANTENIMIENTO COMPLETO OK ===")
