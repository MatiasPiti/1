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

Y la API del celular (ApiCelular, opcional), que se reemplaza con su propia
función: tampoco puede perder lo que haya adentro ni traerse lo de prueba.
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
    """Lo que hace _hacer_actualizacion con cada app: la función de verdad,
    no una copia de su lógica (la copia que había acá no tenía la vuelta
    atrás, y por eso nunca se vio que la vuelta atrás no andaba)."""
    from apps.actualizador.main import reemplazar_programa
    reemplazar_programa(origen_app, destino_app, _log)


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

# ------------------------------------------------------------------ #
# 3. API del celular (opcional): misma regla, con su propio reemplazo
# ------------------------------------------------------------------ #
# ApiCelular se reemplaza con servicio_windows.reemplazar_carpeta (reintenta
# el rename mientras el proceso que acaba de parar suelta el .exe). Si
# alguien dejó datos adentro de la carpeta del programa (api_celular\, el
# secreto del PIN), tienen que sobrevivir; y la basura de probar el .exe en
# dist\ no puede llegar a la PC del local (regla 3).
from apps.actualizador import main as act

dist = os.path.join(raiz, "dist")
origen_c = os.path.join(dist, "ApiCelular")
os.makedirs(os.path.join(origen_c, "_internal"))
os.makedirs(os.path.join(origen_c, "database"))
with open(os.path.join(origen_c, "ApiCelular.exe"), "w") as f:
    f.write("API NUEVA")
with open(os.path.join(origen_c, "_internal", "base_library.zip"), "w") as f:
    f.write("libreria")
with open(os.path.join(origen_c, "config.ini"), "w") as f:
    f.write("[api_celular]\nhabilitado = false\n")
with open(os.path.join(origen_c, "database", "stock.db"), "w") as f:
    f.write("base de prueba del build")

destino_c = os.path.join(instalacion, "ApiCelular")
os.makedirs(os.path.join(destino_c, "api_celular"))
with open(os.path.join(destino_c, "ApiCelular.exe"), "w") as f:
    f.write("API VIEJA")
SECRETO = '{"firma": "de-prueba", "pimienta": "de-prueba"}'
with open(os.path.join(destino_c, "api_celular", "secreto.json"), "w") as f:
    f.write(SECRETO)

if "api_celular" not in act._DATOS_DEL_CLIENTE:
    fallos.append("'api_celular' no está entre los datos del cliente del Actualizador")
salio_bien = act._reemplazar_celular(dist, instalacion, _log)
if not salio_bien or open(os.path.join(destino_c, "ApiCelular.exe")).read() != "API NUEVA":
    fallos.append("no se actualizó ApiCelular.exe")
if not os.path.isfile(os.path.join(destino_c, "_internal", "base_library.zip")):
    fallos.append("ApiCelular quedó sin su _internal (no arrancaría)")
secreto = os.path.join(destino_c, "api_celular", "secreto.json")
if not os.path.isfile(secreto) or open(secreto).read() != SECRETO:
    fallos.append("se perdió el secreto que había adentro de ApiCelular: todos los PIN quedarían inservibles")
colados_c = [n for n in os.listdir(destino_c) if n.lower() in ("config.ini", "database")]
if colados_c:
    fallos.append(f"el build metió datos de prueba adentro de ApiCelular: {colados_c}")
if os.path.exists(destino_c + ".anterior"):
    fallos.append("quedó ApiCelular.anterior tirada después de una actualización que salió bien")
if open(os.path.join(instalacion, "config.ini")).read() != CONFIG_MAESTRO:
    fallos.append("actualizar ApiCelular tocó el config.ini real del Maestro")
print("API DEL CELULAR TRAS ACTUALIZAR:",
      "exe nuevo," if salio_bien else "NO actualizada,",
      "secreto conservado" if os.path.isfile(secreto) else "SECRETO PERDIDO",
      "| basura del build:", colados_c or "ninguna")

# ------------------------------------------------------------------ #
# 4. Si la copia se corta a la mitad, la versión anterior vuelve ENTERA
# ------------------------------------------------------------------ #
# Se saca el pendrive, el antivirus frena un .exe nuevo, se llena el disco:
# la copia falla con la carpeta nueva a medio hacer. Antes quedaba así (un
# programa que no abre) con la versión buena apartada en <app>.anterior, y
# el cartel decía "la instalación anterior sigue funcionando". Y el
# reintento empezaba por borrar la apartada, con el config.ini del Dueño
# Remoto adentro.
import shutil as _shutil
from pos_core import servicio_windows as _sw

_sw._dormir = lambda s: None         # sin esperas de verdad en la prueba


def _instalar_dueno_remoto(carpeta):
    """La laptop de Leo andando, con su config real."""
    if os.path.isdir(carpeta):
        _shutil.rmtree(carpeta)
    os.makedirs(os.path.join(carpeta, "_internal"))
    with open(os.path.join(carpeta, "DuenoRemoto.exe"), "w") as f:
        f.write("EXE VIEJO")
    with open(os.path.join(carpeta, "_internal", "base_library.zip"), "w") as f:
        f.write("libreria vieja")
    with open(os.path.join(carpeta, "config.ini"), "w") as f:
        f.write(CONFIG_REAL)


def _estado(carpeta):
    return {"exe": open(os.path.join(carpeta, "DuenoRemoto.exe")).read()
            if os.path.isfile(os.path.join(carpeta, "DuenoRemoto.exe")) else None,
            "config": open(os.path.join(carpeta, "config.ini")).read()
            if os.path.isfile(os.path.join(carpeta, "config.ini")) else None,
            "internal": os.path.isfile(os.path.join(carpeta, "_internal", "base_library.zip"))}


_instalar_dueno_remoto(destino)
copytree_original = _shutil.copytree


def _copia_que_se_corta(src, dst, *a, **k):
    os.makedirs(dst)
    with open(os.path.join(dst, "DuenoRemoto.exe"), "w") as f:
        f.write("EXE NUEVO A MEDIO COPIAR")
    raise OSError("se sacó el pendrive a mitad de la copia")


_shutil.copytree = _copia_que_se_corta
try:
    _actualizar_carpeta(origen, destino)
    fallos.append("la copia se cortó y la actualización no avisó nada")
except OSError:
    pass
finally:
    _shutil.copytree = copytree_original

quedo = _estado(destino)
print("TRAS UNA COPIA CORTADA:", quedo["exe"], "| config:", "real" if quedo["config"] == CONFIG_REAL else quedo["config"])
if quedo != {"exe": "EXE VIEJO", "config": CONFIG_REAL, "internal": True}:
    fallos.append(f"la copia se cortó a la mitad y la versión anterior NO volvió entera: quedó {quedo} "
                   f"(un programa a medio copiar no abre)")
else:
    print("OK: si la copia se corta, la versión anterior vuelve a su lugar entera, con su config")

# Y el reintento (lo que hace quien ve el error) actualiza sin perder nada.
_actualizar_carpeta(origen, destino)
quedo = _estado(destino)
if quedo != {"exe": "EXE NUEVO CON LOS ARREGLOS", "config": CONFIG_REAL, "internal": True}:
    fallos.append(f"el reintento después de una copia cortada dejó {quedo}")
elif os.path.exists(destino + ".anterior"):
    fallos.append("quedó DuenoRemoto.anterior tirada después de un reintento que salió bien")
else:
    print("OK: el reintento actualiza y conserva el config.ini real")

# ------------------------------------------------------------------ #
# 5. Lo que deja un corte de luz a mitad de la copia
# ------------------------------------------------------------------ #
# a) la carpeta nueva a medias (sin config) + la buena apartada
_instalar_dueno_remoto(destino + ".anterior")
if os.path.isdir(destino):
    _shutil.rmtree(destino)
os.makedirs(destino)
with open(os.path.join(destino, "DuenoRemoto.exe"), "w") as f:
    f.write("EXE NUEVO A MEDIO COPIAR")
_actualizar_carpeta(origen, destino)
quedo = _estado(destino)
if quedo["config"] != CONFIG_REAL:
    fallos.append("después de un corte a mitad de la copia, actualizar de nuevo PERDIÓ el config.ini "
                   "real del Dueño Remoto (estaba en la carpeta apartada, que se borraba sin mirar)")
elif quedo["exe"] != "EXE NUEVO CON LOS ARREGLOS" or not quedo["internal"]:
    fallos.append(f"después de un corte, actualizar de nuevo dejó el programa mal: {quedo}")
else:
    print("OK: tras un corte a mitad de la copia, actualizar de nuevo rescata el config.ini real")

# b) solo quedó la apartada (se cortó antes de empezar a copiar)
_instalar_dueno_remoto(destino + ".anterior")
_shutil.rmtree(destino)
_actualizar_carpeta(origen, destino)
quedo = _estado(destino)
if quedo != {"exe": "EXE NUEVO CON LOS ARREGLOS", "config": CONFIG_REAL, "internal": True}:
    fallos.append(f"con solo la carpeta apartada, actualizar dejó {quedo}")
elif os.path.exists(destino + ".anterior"):
    fallos.append("quedó la carpeta apartada después de actualizar")
else:
    print("OK: si solo quedó la carpeta apartada, se repone y se actualiza sin perder el config")

# c) un resto a medio borrar de una actualización que salió BIEN no pisa nada
_instalar_dueno_remoto(destino)
os.makedirs(destino + ".anterior")
with open(os.path.join(destino + ".anterior", "config.ini"), "w") as f:
    f.write("[conexion_remota]\ntoken = TOKEN-VIEJO-QUE-YA-NO-SIRVE\n")
_actualizar_carpeta(origen, destino)
if _estado(destino)["config"] != CONFIG_REAL:
    fallos.append("un resto viejo de <app>.anterior pisó el config.ini actual del cliente")
else:
    print("OK: un resto viejo de la carpeta apartada no pisa el config.ini actual")

print()
if fallos:
    print("=== FALLOS ACTUALIZADOR ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== ACTUALIZADOR OK ===")
