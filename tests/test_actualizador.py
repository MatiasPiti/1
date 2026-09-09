"""El Actualizador no puede perder los datos del cliente ni traer los de prueba.

Dos escenarios, y el segundo es el error más caro del proyecto:

  1. Maestro: el config y la base viven en la carpeta PADRE, así que
     reemplazar MaestroCaja\\ nunca los tocó. Lo que sí puede pasar es que
     el build traiga adentro un config.ini y una database\\ de prueba, de
     cuando se probaron los .exe desde dist\\.
  2. Dueño Remoto: su config.ini —con la IP de Tailscale y el token
     REALES— vive AL LADO de su .exe. La carpeta se reemplaza entera, así
     que actualizar la laptop de Leo lo borraba y lo dejaba sin panel.
     Recuperarlo obliga a tipear el token a mano, que es justo lo que la
     regla 4 dice que no hay que hacer nunca.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apps.actualizador.main import _ignorar_datos, _devolver_datos_del_cliente

fallos = []
mensajes = []


def _log(texto):
    mensajes.append(texto)


def _actualizar_carpeta(origen_app, destino_app):
    """Lo mismo que hace _hacer_actualizacion con cada app."""
    anterior = destino_app + ".anterior"
    if os.path.isdir(destino_app):
        os.rename(destino_app, anterior)
    shutil.copytree(origen_app, destino_app, ignore=_ignorar_datos)
    _devolver_datos_del_cliente(anterior, destino_app, _log)
    shutil.rmtree(anterior, ignore_errors=True)


# ------------------------------------------------------------------ #
# 1. Dueño Remoto: el config real vive ADENTRO de la carpeta
# ------------------------------------------------------------------ #
raiz = tempfile.mkdtemp(prefix="actualizador_")

# Lo recién compilado, probado en dist\ (por eso trae basura de prueba).
origen = os.path.join(raiz, "dist", "DuenoRemoto")
os.makedirs(os.path.join(origen, "_internal"))
with open(os.path.join(origen, "DuenoRemoto.exe"), "w") as f:
    f.write("EXE NUEVO CON LOS ARREGLOS")
with open(os.path.join(origen, "_internal", "base_library.zip"), "w") as f:
    f.write("libreria")
with open(os.path.join(origen, "config.ini"), "w") as f:
    f.write("[conexion_remota]\nurl = http://127.0.0.1:8765\ntoken = token-de-prueba\n")
os.makedirs(os.path.join(origen, "logs"))
with open(os.path.join(origen, "logs", "basura.log"), "w") as f:
    f.write("log de la prueba en dist")

# La laptop de Leo, andando.
CONFIG_REAL = "[conexion_remota]\nurl = http://100.100.100.100:8765\ntoken = TOKEN-REAL-DE-LEO\n"
destino = os.path.join(raiz, "Otter", "DuenoRemoto")
os.makedirs(destino)
with open(os.path.join(destino, "DuenoRemoto.exe"), "w") as f:
    f.write("EXE VIEJO")
with open(os.path.join(destino, "config.ini"), "w") as f:
    f.write(CONFIG_REAL)

_actualizar_carpeta(origen, destino)

quedo = open(os.path.join(destino, "config.ini")).read()
print("CONFIG DE LEO TRAS ACTUALIZAR:")
print("   ", quedo.replace("\n", " | ").strip())
if quedo != CONFIG_REAL:
    fallos.append("¡se perdió el config.ini real del Dueño Remoto! Leo se queda sin panel")
if open(os.path.join(destino, "DuenoRemoto.exe")).read() != "EXE NUEVO CON LOS ARREGLOS":
    fallos.append("no se actualizó el .exe")
if not os.path.isfile(os.path.join(destino, "_internal", "base_library.zip")):
    fallos.append("no copió el _internal de PyInstaller (la app no abriría)")
if os.path.isdir(os.path.join(destino, "logs")):
    fallos.append("se trajo los logs de prueba del build")

# ------------------------------------------------------------------ #
# 2. Maestro: la carpeta NO tiene datos, y el build no puede meterle
# ------------------------------------------------------------------ #
origen_m = os.path.join(raiz, "dist", "MaestroCaja")
os.makedirs(os.path.join(origen_m, "database"))
with open(os.path.join(origen_m, "MaestroCaja.exe"), "w") as f:
    f.write("EXE NUEVO")
with open(os.path.join(origen_m, "config.ini"), "w") as f:
    f.write("config de prueba del build")
with open(os.path.join(origen_m, "database", "stock.db"), "w") as f:
    f.write("base de prueba del build")

instalacion = os.path.join(raiz, "SistemaDual")
destino_m = os.path.join(instalacion, "MaestroCaja")
os.makedirs(destino_m)
with open(os.path.join(destino_m, "MaestroCaja.exe"), "w") as f:
    f.write("EXE VIEJO")
# Lo real del Maestro vive en la carpeta PADRE.
os.makedirs(os.path.join(instalacion, "database"))
CONFIG_MAESTRO = "[remoto]\nhabilitado = true\ntoken = TOKEN-REAL\n"
with open(os.path.join(instalacion, "config.ini"), "w") as f:
    f.write(CONFIG_MAESTRO)
with open(os.path.join(instalacion, "database", "stock.db"), "w") as f:
    f.write("LA BASE REAL DEL NEGOCIO")

_actualizar_carpeta(origen_m, destino_m)

if open(os.path.join(instalacion, "config.ini")).read() != CONFIG_MAESTRO:
    fallos.append("se tocó el config.ini real del Maestro")
if open(os.path.join(instalacion, "database", "stock.db")).read() != "LA BASE REAL DEL NEGOCIO":
    fallos.append("¡se tocó la base real del negocio!")
colados = [n for n in os.listdir(destino_m) if n.lower() in ("config.ini", "database")]
print("BASURA DEL BUILD QUE SE COLÓ EN LA INSTALACIÓN:", colados or "ninguna")
if colados:
    fallos.append(f"el build metió datos de prueba en la instalación: {colados}")

print()
if fallos:
    print("=== FALLOS ACTUALIZADOR ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== ACTUALIZADOR OK ===")
