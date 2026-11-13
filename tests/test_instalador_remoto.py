"""El Instalador del Dueño Remoto tiene que dejar la dirección y el token donde el programa los lee.

Lo que pasaba: el instalador pedía la dirección de la PC del local y el
token, decía "Dirección y token guardados", probaba la conexión, daba
CONECTADO... y los había escrito en C:\\Otter\\config.ini. El Dueño Remoto
lee su config.ini de al lado de su .exe (C:\\Otter\\DuenoRemoto\\), así que al
abrirlo la pantalla de conexión aparecía vacía y había que cargar todo de
nuevo, con el token tipeado a mano — justo lo que la regla 4 dice que no se
hace nunca.

Y si el .exe se había probado desde dist\\ antes de llevarlo, viajaba además
el config.ini de esas pruebas (regla 3), que la app sí leía: abría con la
dirección y el token de la máquina de quien compila.

La prueba instala de verdad en una carpeta temporal y después mira lo que
ve el dueño: la pantalla de conexión del Dueño Remoto, abierta como la
abriría el .exe instalado.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pos_core import paths, config, dueno_backend
from apps.instalador import main as inst

fallos = []
raiz = tempfile.mkdtemp(prefix="instalador_remoto_")

# ---- lo recién compilado, probado desde dist\ (por eso trae basura de prueba) ----
dist = os.path.join(raiz, "dist")
origen_app = os.path.join(dist, "DuenoRemoto")
os.makedirs(os.path.join(origen_app, "_internal"))
os.makedirs(os.path.join(origen_app, "logs"))
with open(os.path.join(origen_app, "DuenoRemoto.exe"), "w") as f:
    f.write("EXE")
with open(os.path.join(origen_app, "config.ini"), "w", encoding="utf-8") as f:
    f.write("[conexion_remota]\nurl = http://127.0.0.1:8765\ntoken = token-de-las-pruebas\n")
with open(os.path.join(origen_app, "logs", "prueba.log"), "w") as f:
    f.write("log de probar en dist")

URL, TOKEN = "http://100.101.102.103:8765", "otter-TOKEN-REAL-DEL-CLIENTE"
destino = os.path.join(raiz, "Otter")


class InstaladorDePrueba:
    def __init__(self):
        self.origen = dist
        self.lineas = []

    def _log(self, texto):
        self.lineas.append(str(texto))

    def _crear_acceso_directo(self, nombre, ruta):
        pass

    _instalar_remoto = inst.Instalador._instalar_remoto
    _copiar_apps = inst.Instalador._copiar_apps
    _configurar = inst.Instalador._configurar


# La prueba de conexión del instalador no puede salir a la red.
probadas = []
dueno_backend.RemoteBackend.verificar_conexion = lambda self: (probadas.append((self.base_url, self.token)), True)[1]

app = InstaladorDePrueba()
app._instalar_remoto({"destino_remoto": destino, "url_remota": URL, "token_remoto": TOKEN,
                      "accesos_remoto": False})
print("\n".join(l for l in app.lineas if l.strip()))

if probadas != [(URL, TOKEN)]:
    fallos.append(f"el instalador no probó la conexión con lo que se cargó: {probadas}")

# ---- lo que ve el Dueño Remoto al abrir: se para donde estaría su .exe ----
carpeta_app = os.path.join(destino, "DuenoRemoto")
argv0 = sys.argv[0]
sys.argv[0] = os.path.join(carpeta_app, "DuenoRemoto.py")
paths.set_base_override(None)
try:
    leido = config.obtener_config_dict().get("conexion_remota", {})
    if (leido.get("url"), leido.get("token")) != (URL, TOKEN):
        fallos.append(f"el Dueño Remoto instalado lee url={leido.get('url')!r} token={leido.get('token')!r}: "
                       f"lo que se cargó en el instalador quedó en un archivo que el programa no mira")
    else:
        print("OK: el Dueño Remoto instalado lee la dirección y el token que se cargaron en el instalador")

    # Y en pantalla: los dos campos ya vienen cargados (no hay que tipear el token).
    from apps.dueno_remoto.main import PantallaConexion
    conectados = []
    PantallaConexion._conectar = lambda self: conectados.append(True)   # sin salir a la red
    pantalla = PantallaConexion()
    pantalla.update()
    en_pantalla = (pantalla.entry_url.get(), pantalla.entry_token.get())
    if en_pantalla != (URL, TOKEN):
        fallos.append(f"la pantalla de conexión abre con {en_pantalla}: hay que cargar todo de nuevo a mano")
    elif pantalla.entry_token.cget("show") in ("", None):
        fallos.append("el token se dibuja en claro en la pantalla de conexión")
    else:
        print("OK: la pantalla de conexión abre con la dirección y el token ya cargados (y el token tapado)")
    pantalla.destroy()
finally:
    sys.argv[0] = argv0

# ---- lo de prueba del build no viaja al cliente (regla 3) ----
with open(os.path.join(carpeta_app, "config.ini"), encoding="utf-8") as f:
    texto_config = f.read()
if "token-de-las-pruebas" in texto_config or "127.0.0.1" in texto_config:
    fallos.append("el config.ini de las pruebas en dist\\ quedó instalado en la laptop del cliente")
elif os.path.exists(os.path.join(carpeta_app, "logs")):
    fallos.append("los logs de las pruebas en dist\\ viajaron a la laptop del cliente")
elif not os.path.isfile(os.path.join(carpeta_app, "DuenoRemoto.exe")) \
        or not os.path.isdir(os.path.join(carpeta_app, "_internal")):
    fallos.append("no se copió el programa completo")
else:
    print("OK: lo que quedó de probar el .exe en dist\\ no viaja al cliente")

# ---- reinstalar encima no pierde lo que el cliente ya tenía ----
paths.set_base_override(carpeta_app)
config.actualizar_config_dict({"impresora": {"nombre": "POS-58"}})     # algo que el cliente configuró
app2 = InstaladorDePrueba()
app2._instalar_remoto({"destino_remoto": destino, "url_remota": URL, "token_remoto": TOKEN + "-NUEVO",
                       "accesos_remoto": False})
paths.set_base_override(carpeta_app)
despues = config.obtener_config_dict()
if despues.get("conexion_remota", {}).get("token") != TOKEN + "-NUEVO":
    fallos.append("reinstalar encima no dejó el token nuevo donde el programa lo lee")
elif despues.get("impresora", {}).get("nombre") != "POS-58":
    fallos.append("reinstalar encima borró lo que el cliente ya tenía en su config.ini")
else:
    print("OK: reinstalar encima actualiza el token y conserva lo demás del config.ini del cliente")

print()
if fallos:
    print("=== FALLOS INSTALADOR (DUEÑO REMOTO) ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== INSTALADOR (DUEÑO REMOTO) OK ===")
