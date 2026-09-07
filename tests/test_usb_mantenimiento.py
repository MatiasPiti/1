"""Prueba de USB_Mantenimiento sobre una instalación simulada.

Simula lo que va a pasar de verdad: Matías compila, PRUEBA los .exe desde
dist\\ (con lo que se crean database/ y config.ini de prueba adentro de
dist\\USB_Caja\\), build_all.bat copia esa carpeta a espejo_apps\\, y
después el USB repara una instalación real del cliente.
"""
import os, sys, tempfile, shutil, configparser
sys.path.insert(0, "/home/user/1")

usb_dev = tempfile.mkdtemp(prefix="usbmant_")   # el pendrive de mantenimiento
from pos_core import paths
paths.set_base_override(usb_dev)

from apps.usb_dev import mantenimiento

fallos = []

# ---------------------------------------------------------------- #
# 1. El "espejo" tal como queda tras compilar Y PROBAR los exe en dist\
# ---------------------------------------------------------------- #
espejo = os.path.join(usb_dev, "espejo_apps", "USB_Caja")
os.makedirs(os.path.join(espejo, "_internal"), exist_ok=True)
os.makedirs(os.path.join(espejo, "database"), exist_ok=True)
with open(os.path.join(espejo, "USB_Caja.exe"), "w") as f:
    f.write("EXE NUEVO CON EL ARREGLO DEL CARRITO")
with open(os.path.join(espejo, "_internal", "libtk.dll"), "w") as f:
    f.write("dll nueva")
# lo que queda si se probó el exe desde dist\ antes de copiar el espejo:
with open(os.path.join(espejo, "config.ini"), "w") as f:
    f.write("[general]\nnombre_negocio = PRUEBA\n[remoto]\ntoken = token-de-prueba\n")
with open(os.path.join(espejo, "database", "stock.db"), "w") as f:
    f.write("BASE DE PRUEBA CON VENTAS FALSAS")

# ---------------------------------------------------------------- #
# 2. La instalación real del cliente (el pendrive de Leo)
# ---------------------------------------------------------------- #
instalacion = tempfile.mkdtemp(prefix="usbcaja_cliente_")
os.makedirs(os.path.join(instalacion, "database"), exist_ok=True)
with open(os.path.join(instalacion, "USB_Caja.exe"), "w") as f:
    f.write("EXE VIEJO")                     # distinto tamaño -> se debe reponer
CONFIG_REAL = ("[general]\nnombre_negocio = El Galpon Del Nono\n"
               "[remoto]\ntoken = TOKEN-REAL-DEL-CLIENTE-NO-TOCAR\nip = 100.100.100.100\n")
with open(os.path.join(instalacion, "config.ini"), "w") as f:
    f.write(CONFIG_REAL)
BASE_REAL = "BASE REAL CON LAS VENTAS DEL NEGOCIO"
with open(os.path.join(instalacion, "database", "stock.db"), "w") as f:
    f.write(BASE_REAL)

# ---------------------------------------------------------------- #
# 3. Reparar archivos de la app, como hace "REPARAR TODO"
# ---------------------------------------------------------------- #
log = []
mantenimiento.reparar_archivos_app(instalacion, "USB_CAJA", usb_dev, log)
print("\n".join(log))

exe = open(os.path.join(instalacion, "USB_Caja.exe")).read()
print("\nEXE tras reparar:", exe)
if exe != "EXE NUEVO CON EL ARREGLO DEL CARRITO":
    fallos.append("el .exe viejo NO se repuso con el nuevo (para eso existe el espejo)")

config_despues = open(os.path.join(instalacion, "config.ini")).read()
print("CONFIG.INI tras reparar:\n", config_despues)
if config_despues != CONFIG_REAL:
    fallos.append("¡EL CONFIG.INI REAL DEL CLIENTE FUE PISADO POR EL DE PRUEBA DEL ESPEJO!")

base_despues = open(os.path.join(instalacion, "database", "stock.db")).read()
print("BASE tras reparar:", base_despues)
if base_despues != BASE_REAL:
    fallos.append("¡LA BASE REAL DEL CLIENTE FUE PISADA POR LA DE PRUEBA DEL ESPEJO!")

print()
if fallos:
    print("=== FALLOS USB_MANTENIMIENTO ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== USB_MANTENIMIENTO OK ===")
