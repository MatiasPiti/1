"""Reparar a mano la carpeta de un USB no debe meterle las apps del Maestro."""
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

print()
if fallos:
    print("=== FALLOS ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== TIPO DE CARPETA OK ===")
