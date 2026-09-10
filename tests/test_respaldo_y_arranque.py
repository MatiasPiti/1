"""Lo que pasa cuando la base se rompe, que es el peor día del negocio.

Cubre las dos mitades del mismo problema:
  1. Que exista una copia diaria, verificada, sin que nadie se acuerde.
  2. Que si la base se daña, el sistema lo DIGA y ofrezca la copia, en vez
     de no abrir en silencio a las 8 de la mañana.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="respaldo_")
from pos_core import paths
paths.set_base_override(base)
from pos_core.db import preparar_base
preparar_base()

from pos_core import products, respaldo, arranque

fallos = []

products.crear_producto(codigo="7790001", nombre="Yerba Playadito 1kg",
                        precio_venta=3500.0, stock_inicial=10, usuario="test")

# ---------------------------------------------------------------- #
# 1. La copia diaria
# ---------------------------------------------------------------- #
resultado = respaldo.hacer_copia()
print("COPIA:", resultado["motivo"], os.path.basename(resultado["ruta"] or ""))
if not resultado["hecha"]:
    fallos.append(f"no se hizo la primera copia: {resultado['motivo']}")

copias = respaldo.listar_copias()
if len(copias) != 1:
    fallos.append(f"esperaba 1 copia, hay {len(copias)}")

# La copia tiene que ser una base sana Y tener los datos.
import sqlite3
conn = sqlite3.connect(copias[0]["ruta"])
integridad = conn.execute("PRAGMA integrity_check").fetchone()[0]
cuantos = conn.execute("SELECT COUNT(*) FROM Productos").fetchone()[0]
conn.close()
print("COPIA VERIFICADA:", integridad, "-", cuantos, "producto(s) adentro")
if integridad != "ok":
    fallos.append("la copia no pasa integrity_check")
if cuantos != 1:
    fallos.append(f"la copia no tiene los datos: {cuantos} productos")

# No se duplica en el mismo día.
segunda = respaldo.hacer_copia()
if segunda["hecha"]:
    fallos.append("hizo dos copias el mismo día")

# Las viejas se borran; las de la ventana se conservan.
vieja = os.path.join(paths.backups_dir(), "stock_2020-01-01.db")
with open(vieja, "wb") as f:
    f.write(b"copia vieja")
antigua = (datetime.now() - timedelta(days=60)).timestamp()
os.utime(vieja, (antigua, antigua))
borradas = respaldo.limpiar_viejas()
print("LIMPIEZA:", borradas)
if os.path.exists(vieja):
    fallos.append("no borró la copia de 60 días")
if not os.path.isfile(copias[0]["ruta"]):
    fallos.append("¡borró la copia de hoy!")

# ---------------------------------------------------------------- #
# 2. La base se rompe: el sistema tiene que avisar y ofrecer la copia
# ---------------------------------------------------------------- #
# Se cierra la conexión primero: un .exe recién abierto no tiene ninguna
# cacheada, y sin esto el test estaría probando contra un archivo que ya
# no existe en disco (y no probaría nada).
from pos_core import db as _db
_db.cerrar_conexion()
with open(paths.db_path(), "wb") as f:
    f.write(b"esto ya no es una base de datos\n" * 100)
for _sufijo in ("-wal", "-shm"):
    _sidecar = paths.db_path() + _sufijo
    if os.path.exists(_sidecar):
        os.remove(_sidecar)

# El cartel se responde desde el test: primero que NO, después que SÍ.
dialogos = []
respuestas = []


def _dialogo_falso(titulo, mensaje, preguntar=False):
    dialogos.append((titulo, mensaje, preguntar))
    return respuestas.pop(0) if preguntar and respuestas else True


def _resumen(dialogos):
    """Título + primera línea del mensaje.

    El título solo no alcanza: el cartel de "se restauró" y el de "no se
    pudo restaurar" comparten título (los dos son el nombre de la app), así
    que mirando solo títulos un fallo se lee como un éxito. Pasó de verdad
    al correr esto en Windows.
    """
    return [f"{t} | {(m or '').splitlines()[0][:60]}" for t, m, _ in dialogos]


arranque._dialogo = _dialogo_falso

# --- 2a. El cajero dice que NO: no se toca nada y se avisa ---
respuestas.append(False)
dialogos.clear()
abrio = {"si": False}
try:
    arranque.iniciar("Otter Caja", lambda: preparar_base(), lambda: None)
    abrio["si"] = True
except SystemExit:
    pass

if abrio["si"]:
    fallos.append("con la base rota y sin restaurar, dijo que abrió igual")
titulos = [d[0] for d in dialogos]
print("DIÁLOGOS (dijo que NO):")
for linea in _resumen(dialogos):
    print("   ", linea)
if not any("dañada" in t for t in titulos):
    fallos.append("no ofreció restaurar la copia")
if not any("no pudo abrir" in t for t in titulos):
    fallos.append("no explicó que no pudo abrir")
if not os.path.isfile(os.path.join(paths.logs_dir(), "arranque.log")):
    fallos.append("no dejó registro en logs/arranque.log")

# --- 2b. El cajero dice que SÍ: se restaura y el sistema abre ---
# A propósito NO se cierra la conexión acá: así queda igual que en
# producción, donde el intento de arranque que acaba de fallar dejó su
# propio handle abierto sobre la base dañada. En Windows eso impide
# renombrar el archivo, y por ahí se caía la restauración entera.
respuestas.append(True)
dialogos.clear()
ventana_construida = {"si": False}


def _construir():
    ventana_construida["si"] = True
    return None


try:
    arranque.iniciar("Otter Caja", lambda: preparar_base(), _construir)
except SystemExit:
    fallos.append("dijo que sí a restaurar y aun así no abrió")

print("DIÁLOGOS (dijo que SÍ):")
for linea in _resumen(dialogos):
    print("   ", linea)
if any("No se pudo restaurar" in (m or "") for _, m, _ in dialogos):
    fallos.append("la restauración falló: ver el detalle en los diálogos de arriba")
if not ventana_construida["si"]:
    fallos.append("restauró pero no llegó a construir la ventana")

# La base restaurada tiene que ser usable y traer los datos.
try:
    filas = products.listar_stock("7790001")
    print("TRAS RESTAURAR:", len(filas), "producto(s) — la caja puede vender")
    if len(filas) != 1:
        fallos.append("la base restaurada no tiene el producto")
except Exception as e:
    fallos.append(f"la base restaurada no se puede usar: {e}")

# La base dañada se guarda, no se tira: puede tener las ventas del día.
danadas = [f for f in os.listdir(paths.data_dir()) if ".danada_" in f]
print("BASE DAÑADA GUARDADA:", danadas)
if not danadas:
    fallos.append("borró la base dañada en vez de guardarla al lado")

print()
if fallos:
    print("=== FALLOS ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== RESPALDO Y ARRANQUE OK ===")
