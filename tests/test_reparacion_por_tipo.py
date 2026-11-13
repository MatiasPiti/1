"""Reparar a mano una carpeta no puede meterle las apps del Maestro a quien no las usa.

Dos casos, el segundo es la regla 2 del proyecto:

1. La carpeta de un USB de emergencia: no lleva MaestroCaja\\ ni las otras.
2. La carpeta del Dueño Remoto en la laptop del dueño. El 21/9/2026 se la
   eligió a mano (C:\\Otter) y el mantenimiento, que daba "Maestro" por
   descarte a todo lo que no fuera un USB, le copió adentro MaestroCaja\\,
   MaestroDueno\\ y StockService\\ (2642 archivos). MaestroDueno.exe en esa
   laptop abre perfecto, contra una base vacía, y nunca muestra una venta.

Y de paso, lo mismo que en el arranque: una base OCUPADA no es una base
DAÑADA, y el mantenimiento no puede "repararla" copiándole un backup encima.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
usb_dev = tempfile.mkdtemp(prefix="usbmant3_")
from pos_core import paths
paths.set_base_override(usb_dev)
from apps.usb_dev import mantenimiento

fallos = []

# espejo con las 3 apps del Maestro + la del USB Caja
for app, contenido in [("MaestroCaja", "maestro caja"), ("MaestroDueno", "maestro dueno"),
                       ("StockService", "servicio"), ("USB_Caja", "usb caja nuevo")]:
    d = os.path.join(usb_dev, "espejo_apps", app)
    os.makedirs(d)
    with open(os.path.join(d, f"{app}.exe"), "w") as f:
        f.write(contenido)

# una carpeta de USB Caja elegida a mano
carpeta = tempfile.mkdtemp(prefix="usb_caja_suelto_")
with open(os.path.join(carpeta, "USB_Caja.exe"), "w") as f:
    f.write("viejo")

tipo = mantenimiento.tipo_de_instalacion(carpeta)
print("TIPO DETECTADO:", tipo)
if tipo != "USB_CAJA":
    fallos.append(f"se detectó como {tipo} en vez de USB_CAJA")

log = []
mantenimiento.reparar_archivos_app(carpeta, tipo, usb_dev, log)
print("\n".join(log))
quedaron = sorted(os.listdir(carpeta))
print("CONTENIDO DE LA CARPETA:", quedaron)
for intruso in ("MaestroCaja", "MaestroDueno", "StockService"):
    if intruso in quedaron:
        fallos.append(f"se le copió {intruso} adentro a un USB de emergencia")
if open(os.path.join(carpeta, "USB_Caja.exe")).read() != "usb caja nuevo":
    fallos.append("no se repuso el USB_Caja.exe")

# ------------------------------------------------------------------ #
# 2. La laptop del dueño: C:\Otter con el Dueño Remoto adentro
# ------------------------------------------------------------------ #
laptop = tempfile.mkdtemp(prefix="otter_laptop_")
os.makedirs(os.path.join(laptop, "DuenoRemoto"))
with open(os.path.join(laptop, "DuenoRemoto", "DuenoRemoto.exe"), "w") as f:
    f.write("dueño remoto")
CONFIG_LEO = "[conexion_remota]\nurl = http://100.100.100.100:8765\ntoken = TOKEN-REAL-DE-LEO\n"
with open(os.path.join(laptop, "DuenoRemoto", "config.ini"), "w") as f:
    f.write(CONFIG_LEO)
# Lo que quedó de aquella vez: una carpeta database\ suelta. No la convierte en Maestro.
os.makedirs(os.path.join(laptop, "database"))


def _foto(carpeta):
    return sorted(os.path.relpath(os.path.join(raiz, n), carpeta)
                  for raiz, _dirs, nombres in os.walk(carpeta) for n in nombres)


antes = _foto(laptop)
tipo_laptop = mantenimiento.tipo_de_instalacion(laptop)
print("TIPO DETECTADO (laptop del dueño):", tipo_laptop)
if tipo_laptop != "DUENO_REMOTO":
    fallos.append(f"la carpeta del Dueño Remoto se detectó como {tipo_laptop}: si es MAESTRO, "
                   f"el mantenimiento le copia MaestroDueno.exe a la laptop del dueño (regla 2)")

log_laptop = mantenimiento.ejecutar_mantenimiento(laptop, tipo_laptop)
print("\n".join(log_laptop))
despues = _foto(laptop)
if despues != antes:
    fallos.append(f"el mantenimiento tocó la carpeta del Dueño Remoto: apareció "
                   f"{sorted(set(despues) - set(antes))[:5]}")
for intruso in ("MaestroCaja", "MaestroDueno", "StockService"):
    if os.path.exists(os.path.join(laptop, intruso)):
        fallos.append(f"¡se copió {intruso} a la laptop del dueño! (regla 2)")
if open(os.path.join(laptop, "DuenoRemoto", "config.ini")).read() != CONFIG_LEO:
    fallos.append("se tocó el config.ini del Dueño Remoto (IP y token reales)")
if not any("NO SE TOCÓ NADA" in l and "OtterActualizador" in l for l in log_laptop):
    fallos.append("el informe no explica por qué no se reparó la carpeta del Dueño Remoto")
if not fallos:
    print("OK: la carpeta del Dueño Remoto no se toca, y el informe dice por qué")

# Aunque alguien le pase "MAESTRO" a mano a la reparación de archivos, no hay
# forma de decidirlo por descarte: una carpeta cualquiera es DESCONOCIDA.
cualquiera = tempfile.mkdtemp(prefix="carpeta_cualquiera_")
with open(os.path.join(cualquiera, "fotos.txt"), "w") as f:
    f.write("nada que ver")
tipo_cualquiera = mantenimiento.tipo_de_instalacion(cualquiera)
log_cualquiera = mantenimiento.ejecutar_mantenimiento(cualquiera, tipo_cualquiera)
if tipo_cualquiera != "DESCONOCIDA" or sorted(os.listdir(cualquiera)) != ["fotos.txt"]:
    fallos.append(f"una carpeta que no es de Otter se trató como {tipo_cualquiera} y quedó con "
                   f"{sorted(os.listdir(cualquiera))}")
else:
    print("OK: una carpeta que no es de Otter es DESCONOCIDA y no se le copia nada")

# El Maestro se sigue reconociendo por lo que tiene.
for contenido, esperado in ((("MaestroCaja",), "MAESTRO"), (("StockService",), "MAESTRO")):
    carpeta_m = tempfile.mkdtemp(prefix="maestro_")
    for sub in contenido:
        os.makedirs(os.path.join(carpeta_m, sub))
    if mantenimiento.tipo_de_instalacion(carpeta_m) != esperado:
        fallos.append(f"una instalación con {contenido} no se reconoció como {esperado}")
solo_base = tempfile.mkdtemp(prefix="maestro_sin_programas_")
os.makedirs(os.path.join(solo_base, "database"))
with open(os.path.join(solo_base, "database", "stock.db"), "w") as f:
    f.write("")
if mantenimiento.tipo_de_instalacion(solo_base) != "MAESTRO":
    fallos.append("un Maestro al que se le borraron los programas (queda la base) ya no se puede reparar")
else:
    print("OK: el Maestro se reconoce por sus apps o por su base, no por descarte")

# ------------------------------------------------------------------ #
# 3. Una base OCUPADA no se "repara" pisándola con un backup
# ------------------------------------------------------------------ #
import sqlite3

inst = tempfile.mkdtemp(prefix="maestro_ocupado_")
os.makedirs(os.path.join(inst, "database"))
os.makedirs(os.path.join(inst, "backups"))
base_viva = os.path.join(inst, "database", "stock.db")
con = sqlite3.connect(base_viva)
con.execute("CREATE TABLE Ventas (id INTEGER PRIMARY KEY, total REAL)")
con.execute("INSERT INTO Ventas (total) VALUES (2500)")       # la venta de hoy
con.commit()
con.close()
con = sqlite3.connect(os.path.join(inst, "backups", "stock_2026-11-01.db"))   # la copia vieja, sana
con.execute("CREATE TABLE Ventas (id INTEGER PRIMARY KEY, total REAL)")
con.commit()
con.close()
with open(base_viva, "rb") as f:
    bytes_antes = f.read()

conectar_original = mantenimiento.sqlite3.connect


def _ocupada(ruta, *a, **k):
    if os.path.abspath(ruta) == os.path.abspath(base_viva):
        raise sqlite3.OperationalError("database is locked")
    return conectar_original(ruta, *a, **k)


mantenimiento.sqlite3.connect = _ocupada
try:
    log_db = []
    mantenimiento.verificar_y_reparar_db(inst, log_db)
finally:
    mantenimiento.sqlite3.connect = conectar_original
print("\n".join(log_db))
with open(base_viva, "rb") as f:
    if f.read() != bytes_antes:
        fallos.append("con la base OCUPADA el mantenimiento la pisó con un backup: se perdió la venta de hoy")
if any("CORRUPCIÓN" in l or "restauró" in l for l in log_db):
    fallos.append("una base ocupada se informó como corrupta")
elif not any("NO se tocó" in l for l in log_db):
    fallos.append("con la base ocupada el informe no dice que no se tocó")
else:
    print("OK: una base ocupada no se toca y el informe lo dice (no la trata como corrupta)")

print()
if fallos:
    print("=== FALLOS ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== TIPO DE CARPETA OK ===")
