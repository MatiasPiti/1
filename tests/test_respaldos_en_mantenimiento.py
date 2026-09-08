"""El USB de Mantenimiento frente a las copias diarias del cliente.

Tres cosas que tienen que valer, y las tres ya fallaron alguna vez en este
proyecto por el mismo motivo (el espejo del USB pisando datos reales):

  1. Reponer archivos NO puede tocar backups\\ del cliente. Si el espejo se
     compiló con copias de prueba adentro, pisaría las copias reales — o
     sea, se llevaría puesto justo lo que sirve para recuperarse.
  2. El informe tiene que DECIR si el negocio está respaldado y desde
     cuándo. Que exista la carpeta no alcanza: una copia de tres semanas
     es casi lo mismo que no tener ninguna.
  3. Si la base está rota más allá de reparación, el último recurso tiene
     que encontrar las copias diarias de backups\\, que son las que en la
     práctica van a existir (antes solo miraba los .backup_* sueltos, que
     casi nunca están).
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

usb_dev = tempfile.mkdtemp(prefix="usbmant_resp_")
from pos_core import paths
paths.set_base_override(usb_dev)
from pos_core.db import _schema_sql
from apps.usb_dev import mantenimiento

fallos = []

# ---- instalación del cliente, con base sana y copias diarias ----
inst = tempfile.mkdtemp(prefix="maestro_resp_")
os.makedirs(os.path.join(inst, "database"))
os.makedirs(os.path.join(inst, "backups"))
os.makedirs(os.path.join(inst, "MaestroCaja"))
db_path = os.path.join(inst, "database", "stock.db")


def _base_con(ruta, codigo, nombre):
    conn = sqlite3.connect(ruta)
    conn.executescript(_schema_sql())
    conn.execute("""INSERT INTO Productos (uuid_unico, codigo, nombre, precio_venta, stock)
                    VALUES (?,?,?, 3500.0, 10)""", (f"u-{codigo}", codigo, nombre))
    conn.commit()
    conn.close()


_base_con(db_path, "7790001", "Yerba Playadito 1kg")

# La copia diaria REAL del cliente: tiene un producto que la base viva no
# tiene, para poder demostrar después cuál de las dos quedó en su lugar.
hoy = datetime.now().strftime("%Y-%m-%d")
copia_real = os.path.join(inst, "backups", f"stock_{hoy}.db")
_base_con(copia_real, "7790002", "Fideos Matarazzo")
CONTENIDO_REAL = open(copia_real, "rb").read()

with open(os.path.join(inst, "MaestroCaja", "MaestroCaja.exe"), "w") as f:
    f.write("EXE VIEJO")

# ---- espejo del USB, mal compilado: se coló una carpeta backups\ ----
espejo = os.path.join(usb_dev, "espejo_apps", "MaestroCaja")
os.makedirs(os.path.join(espejo, "backups"))
with open(os.path.join(espejo, "MaestroCaja.exe"), "w") as f:
    f.write("EXE NUEVO, CON LOS ARREGLOS DE SEPTIEMBRE")
with open(os.path.join(espejo, "backups", f"stock_{hoy}.db"), "wb") as f:
    f.write(b"COPIA DE PRUEBA DEL BUILD, NO ES DEL CLIENTE")

log = mantenimiento.ejecutar_mantenimiento(inst, "MAESTRO")
print("\n".join(l for l in log if l.startswith("[RESPALDO]") or l.startswith("[ARCHIVOS]")))
print()

# 0. El .exe viejo SÍ se repone: la exclusión no puede haber frenado de
#    más y dejado al USB sin hacer su trabajo.
if open(os.path.join(inst, "MaestroCaja", "MaestroCaja.exe")).read() != \
        "EXE NUEVO, CON LOS ARREGLOS DE SEPTIEMBRE":
    fallos.append("el .exe viejo no se repuso (la exclusión frenó de más)")

# 1. Las copias del cliente siguen siendo las del cliente.
if open(copia_real, "rb").read() != CONTENIDO_REAL:
    fallos.append("¡el espejo del USB pisó la copia de seguridad real del cliente!")

# 2. El informe habla de los respaldos, y dice que están al día.
linea = next((l for l in log if l.startswith("[RESPALDO]")), None)
if linea is None:
    fallos.append("el informe no dice nada sobre las copias de seguridad")
elif "ATENCIÓN" in linea:
    fallos.append(f"marcó como problema una copia de hoy: {linea}")

# ---- ahora sin ninguna copia: el informe tiene que gritar ----
inst_sin = tempfile.mkdtemp(prefix="maestro_sin_resp_")
os.makedirs(os.path.join(inst_sin, "database"))
_base_con(os.path.join(inst_sin, "database", "stock.db"), "7790001", "Yerba")
log_sin = mantenimiento.ejecutar_mantenimiento(inst_sin, "MAESTRO")
linea_sin = next((l for l in log_sin if l.startswith("[RESPALDO]")), "")
print(linea_sin)
if "ATENCIÓN" not in linea_sin:
    fallos.append(f"no avisó que NO hay ninguna copia de seguridad: {linea_sin!r}")

# ---- copia vieja: también tiene que avisar ----
inst_vieja = tempfile.mkdtemp(prefix="maestro_resp_vieja_")
os.makedirs(os.path.join(inst_vieja, "database"))
os.makedirs(os.path.join(inst_vieja, "backups"))
_base_con(os.path.join(inst_vieja, "database", "stock.db"), "7790001", "Yerba")
hace_10 = datetime.now() - timedelta(days=10)
vieja = os.path.join(inst_vieja, "backups", f"stock_{hace_10:%Y-%m-%d}.db")
_base_con(vieja, "7790001", "Yerba")
os.utime(vieja, (hace_10.timestamp(), hace_10.timestamp()))
log_vieja = mantenimiento.ejecutar_mantenimiento(inst_vieja, "MAESTRO")
linea_vieja = next((l for l in log_vieja if l.startswith("[RESPALDO]")), "")
print(linea_vieja)
if "ATENCIÓN" not in linea_vieja:
    fallos.append(f"no avisó que la última copia tiene 10 días: {linea_vieja!r}")

# ---- 3. Base destruida: el último recurso usa la copia diaria ----
inst_rota = tempfile.mkdtemp(prefix="maestro_rota_")
os.makedirs(os.path.join(inst_rota, "database"))
os.makedirs(os.path.join(inst_rota, "backups"))
db_rota = os.path.join(inst_rota, "database", "stock.db")
with open(db_rota, "wb") as f:
    f.write(b"esto ya no es una base de datos\n" * 200)
copia_salvadora = os.path.join(inst_rota, "backups", f"stock_{hoy}.db")
_base_con(copia_salvadora, "7790003", "Azúcar Ledesma 1kg")

log_rota = mantenimiento.ejecutar_mantenimiento(inst_rota, "MAESTRO")
print("\n".join(l for l in log_rota if l.startswith("[DB]")))
print()

try:
    conn = sqlite3.connect(db_rota)
    filas = conn.execute("SELECT codigo FROM Productos").fetchall()
    conn.close()
    codigos = [f[0] for f in filas]
    print("TRAS EL RESCATE, LA BASE TIENE:", codigos)
    if "7790003" not in codigos:
        fallos.append(f"no restauró desde la copia diaria (quedó {codigos})")
except Exception as e:
    fallos.append(f"la base sigue sin abrir después del mantenimiento: {e}")

# ---- 4. Lo peor posible: base rota Y ninguna copia. El informe SALE ----
# No es un caso teórico: es el que hace que valga la pena conectar el USB.
# Si acá el mantenimiento explota, Matías se queda sin el único papel que
# explica qué pasó.
inst_perdida = tempfile.mkdtemp(prefix="maestro_perdida_")
os.makedirs(os.path.join(inst_perdida, "database"))
with open(os.path.join(inst_perdida, "database", "stock.db"), "wb") as f:
    f.write(b"no queda nada\n" * 200)
try:
    log_perdida = mantenimiento.ejecutar_mantenimiento(inst_perdida, "MAESTRO")
    print("CON TODO PERDIDO, EL INFORME IGUAL SALE:", len(log_perdida), "líneas")
    if not any("Fin del mantenimiento" in l for l in log_perdida):
        fallos.append("el informe quedó cortado por la mitad")
    if not any("NO hay ningún backup sano" in l for l in log_perdida):
        fallos.append("no dijo con todas las letras que no hay nada para restaurar")
except Exception as e:
    fallos.append(f"con la base rota y sin copias, el mantenimiento explotó: {e!r}")

print()
if fallos:
    print("=== FALLOS RESPALDOS EN MANTENIMIENTO ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== RESPALDOS EN MANTENIMIENTO OK ===")
