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
def _copia_de_hace(dias):
    fecha = datetime.now() - timedelta(days=dias)
    ruta = os.path.join(paths.backups_dir(), f"stock_{fecha:%Y-%m-%d}.db")
    with open(ruta, "wb") as f:
        f.write(b"copia de prueba")
    os.utime(ruta, (fecha.timestamp(), fecha.timestamp()))
    return ruta


# El servicio andando todos los días: hoy + 13 días + una de hace 60.
recientes = [_copia_de_hace(d) for d in range(1, 14)]
vieja = _copia_de_hace(60)
borradas = respaldo.limpiar_viejas()
print("LIMPIEZA:", borradas)
if os.path.exists(vieja):
    fallos.append("no borró la copia de 60 días")
if not os.path.isfile(copias[0]["ruta"]):
    fallos.append("¡borró la copia de hoy!")
if not all(os.path.isfile(r) for r in recientes):
    fallos.append("borró copias de los últimos 14 días")

# El servicio estuvo parado tres semanas (vacaciones, la PC rota, o el
# servicio en Stopped como en septiembre): las copias que hay son todas
# "viejas". Al volver, la primera copia NO puede borrarlas todas de un saque
# y dejar al negocio con una sola.
for r in recientes:
    os.remove(r)
de_antes = [_copia_de_hace(d) for d in range(20, 34)]       # 14 copias, de antes del parate
borradas = respaldo.limpiar_viejas()
quedan = [c["archivo"] for c in respaldo.listar_copias()]
if len(quedan) < respaldo.MINIMO_A_CONSERVAR:
    fallos.append(f"el servicio volvió después de tres semanas parado y de las 15 copias quedaron "
                   f"{len(quedan)}: las de antes del parate eran la única vuelta atrás")
elif not os.path.isfile(copias[0]["ruta"]):
    fallos.append("¡borró la copia de hoy!")
elif len(borradas) != 1 or os.path.exists(de_antes[-1]):
    fallos.append(f"tenía que irse solo la más vieja (entra una, sale una) y se fueron: {borradas}")
else:
    print(f"OK: después de un parate largo no se borran todas las copias de golpe (quedan {len(quedan)})")
for r in de_antes:
    if os.path.exists(r):
        os.remove(r)

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
# Lo último que se vendió puede estar en el -wal y no en el .db. Al
# restaurar el -wal se borra (tiene que borrarse), así que tiene que quedar
# guardado junto con la base dañada.
with open(paths.db_path() + "-wal", "wb") as f:
    f.write(b"las ultimas ventas del dia")


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
wal_guardado = [f for f in danadas if f.endswith("-wal")]
if not wal_guardado:
    fallos.append("al restaurar se perdió el -wal de la base dañada: ahí podían estar las últimas ventas")
else:
    with open(os.path.join(paths.data_dir(), wal_guardado[0]), "rb") as f:
        if f.read() != b"las ultimas ventas del dia":
            fallos.append("el -wal guardado junto a la base dañada no es el que había")

# ---------------------------------------------------------------- #
# 3. Base OCUPADA no es base DAÑADA
# ---------------------------------------------------------------- #
# "database is locked" o "unable to open database file" es que otro programa
# la tiene tomada un momento (el antivirus, una actualización). La base está
# sana. Antes se ofrecía igual "la base está dañada, ¿restauro la copia?": si
# el cajero decía que sí, se pisaba una base buena con la de ayer y se
# perdían las ventas del día.
import sqlite3 as _sqlite3
import time as _time

_dormir_original = _time.sleep
esperas = []
_time.sleep = lambda s: esperas.append(s)
try:
    with open(paths.db_path(), "rb") as f:
        base_sana = f.read()

    for texto_error in ("database is locked", "unable to open database file"):
        intentos = {"n": 0}

        def _siempre_ocupada():
            intentos["n"] += 1
            raise _sqlite3.OperationalError(texto_error)

        respuestas.clear()
        respuestas.append(True)        # si preguntara, diría que SÍ a restaurar
        dialogos.clear()
        esperas.clear()
        try:
            arranque.iniciar("Otter Caja", _siempre_ocupada, lambda: None)
            fallos.append(f"con '{texto_error}' dijo que abrió")
        except SystemExit:
            pass
        if any(preguntar for _t, _m, preguntar in dialogos) or any("dañada" in t for t, _m, _p in dialogos):
            fallos.append(f"con '{texto_error}' (base ocupada, no dañada) ofreció restaurar la copia: "
                           f"decir que sí pisa una base sana con la de ayer")
        elif not any("Otro programa" in (m or "") for _t, m, _p in dialogos):
            fallos.append(f"con '{texto_error}' no explicó que la base está ocupada: {_resumen(dialogos)}")
        elif intentos["n"] != arranque.REINTENTOS_BASE_OCUPADA + 1:
            fallos.append(f"con '{texto_error}' probó {intentos['n']} veces; tenía que insistir "
                           f"{arranque.REINTENTOS_BASE_OCUPADA + 1} antes de avisar")
        else:
            print(f"OK: '{texto_error}' se reintenta y, si sigue, avisa SIN ofrecer restaurar la copia")
        with open(paths.db_path(), "rb") as f:
            if f.read() != base_sana:
                fallos.append(f"con '{texto_error}' se tocó la base")

    # Si se libera enseguida (lo normal con el antivirus), la app abre sola.
    intentos = {"n": 0}

    def _ocupada_dos_veces():
        intentos["n"] += 1
        if intentos["n"] <= 2:
            raise _sqlite3.OperationalError("database is locked")
        preparar_base()

    dialogos.clear()
    abrio = {"si": False}
    try:
        arranque.iniciar("Otter Caja", _ocupada_dos_veces,
                         lambda: abrio.__setitem__("si", True))
    except SystemExit:
        pass
    if not abrio["si"] or dialogos:
        fallos.append(f"la base estuvo ocupada un momento y la app no abrió sola: {_resumen(dialogos)}")
    else:
        print("OK: si la base se libera a los segundos, la app abre sola, sin ningún cartel")
finally:
    _time.sleep = _dormir_original

# ---------------------------------------------------------------- #
# 4. Después de restaurar, la Caja abre la base RESTAURADA
# ---------------------------------------------------------------- #
# Hasta acá se arrancó con una función de prueba. La Caja de verdad arranca
# con su _preparar(), que fija la base en la carpeta PADRE del .exe. Tras
# restaurar, ese arranque corre por segunda vez, y antes subía un nivel
# más: la Caja abría sin ningún error contra una base NUEVA Y VACÍA en
# C:\\database, justo después de haber restaurado bien la de verdad.
import apps.master_caja.main as caja_maestro

raiz = tempfile.mkdtemp(prefix="instalacion_")
instalacion = os.path.join(raiz, "SistemaDual")
os.makedirs(os.path.join(instalacion, "MaestroCaja"))
argv0_original = sys.argv[0]
sys.argv[0] = os.path.join(instalacion, "MaestroCaja", "MaestroCaja.py")   # donde estaría el .exe
caja_maestro._unica_instancia = lambda *a, **k: True
try:
    _db.cerrar_conexion()
    paths.set_base_override(None)
    caja_maestro._preparar()
    if os.path.dirname(os.path.dirname(paths.db_path())) != instalacion:
        fallos.append(f"la Caja no usa la base de su instalación: {paths.db_path()}")
    products.crear_producto(codigo="111", nombre="COCA COLA 1,5L.", precio_venta=2500.0,
                            stock_inicial=5, usuario="test")
    if not respaldo.hacer_copia()["hecha"]:
        fallos.append("no se pudo hacer la copia diaria de la instalación de prueba")

    # Otro día: la Caja se abre de nuevo y la base está rota.
    _db.cerrar_conexion()
    paths.set_base_override(None)
    base_real = os.path.join(instalacion, "database", "stock.db")
    with open(base_real, "wb") as f:
        f.write(b"esto ya no es una base de datos\n" * 100)
    for _sufijo in ("-wal", "-shm"):
        if os.path.exists(base_real + _sufijo):
            os.remove(base_real + _sufijo)

    respuestas.clear()
    respuestas.append(True)
    dialogos.clear()
    arranque.iniciar("Otter Caja", caja_maestro._preparar, lambda: None)

    if os.path.abspath(paths.db_path()) != os.path.abspath(base_real):
        fallos.append(f"después de restaurar, la Caja quedó usando {paths.db_path()} y no la base "
                       f"restaurada de su instalación ({base_real})")
    elif os.path.exists(os.path.join(raiz, "database")):
        fallos.append("después de restaurar apareció una base nueva FUERA de la instalación")
    else:
        vistos = _db.get_connection().execute("SELECT COUNT(*) FROM Productos").fetchone()[0]
        if vistos != 1:
            fallos.append(f"después de restaurar la Caja ve {vistos} productos (tenía que ver 1)")
        else:
            print("OK: después de restaurar, la Caja abre la base restaurada (no una vacía una carpeta más arriba)")
except SystemExit:
    fallos.append("la Caja de verdad no abrió después de restaurar la copia")
finally:
    sys.argv[0] = argv0_original
    _db.cerrar_conexion()
    paths.set_base_override(base)

# ---------------------------------------------------------------- #
# 5. Si la VENTANA no se puede armar, también se avisa
# ---------------------------------------------------------------- #
# Hasta acá, lo que fallaba era preparar la base. Si lo que falla es armar
# la ventana (un módulo que no entró en el .exe, una pantalla que se cae al
# dibujarse), el programa moría igual que antes de que existiera este
# cartel: doble clic y no pasa nada.
dialogos.clear()
ruta_log_arranque = os.path.join(paths.logs_dir(), "arranque.log")
if os.path.exists(ruta_log_arranque):
    os.remove(ruta_log_arranque)


def _ventana_que_no_arma():
    raise ImportError("No module named 'algo_que_falto_en_el_exe'")


try:
    arranque.iniciar("Otter Caja", lambda: None, _ventana_que_no_arma)
    fallos.append("la ventana no se pudo armar y el arranque siguió como si nada")
except SystemExit:
    if len(dialogos) != 1 or "no pudo abrir" not in dialogos[0][0]:
        fallos.append(f"la ventana no se pudo armar y no hubo cartel: {dialogos}")
    elif "algo_que_falto_en_el_exe" not in dialogos[0][1]:
        fallos.append(f"el cartel no dice qué falló: {dialogos[0][1]}")
    elif not os.path.isfile(ruta_log_arranque):
        fallos.append("la ventana no se pudo armar y no quedó nada en logs/arranque.log")
    else:
        print("OK: si la ventana no se puede armar, hay cartel y queda en logs/arranque.log")
except Exception as e:
    fallos.append(f"la ventana no se pudo armar y el programa murió con {type(e).__name__}, sin cartel: "
                   f"es el doble clic que no hace nada")

print()
if fallos:
    print("=== FALLOS ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== RESPALDO Y ARRANQUE OK ===")
