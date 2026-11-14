"""El Dueño Remoto tiene que abrir, y cuando no conecta tiene que decir por qué sin mostrar la dirección.

Es el programa que usa Leo desde su casa, sin nadie cerca. Tres cosas que
le pasaban:

1. EL CARTEL DE "NO SE PUDO CONECTAR" TRAÍA LA IP ADENTRO. El texto era el
   de la librería, tal cual: "HTTPConnectionPool(host='100.x.y.z',
   port=8765): Max retries exceeded with url: /rpc (Caused by ...". No se
   entiende, y es justo el cartel que se fotografía para preguntar qué pasa:
   la dirección de Tailscale del negocio terminaba en un chat (regla 4).
   Tampoco decía lo único que cambia qué hacer: si el pedido no llegó a
   salir (se repite tranquilo) o si se cortó con el local ya trabajando.

2. SI ALGO FALLABA AL ABRIR, NO SE VEÍA NADA. El .exe no tiene consola y el
   arranque no estaba envuelto: doble clic y no pasa nada, sin cartel y sin
   una línea en ningún log.

3. NO PODER LEER O GUARDAR SU config.ini LO DEJABA AFUERA. Con el archivo
   ilegible la ventana ni aparecía; y si conectaba bien pero no podía
   guardar la conexión, se quedaba en "Probando conexión..." para siempre.
"""
import os
import socket
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="dueno_remoto_")
from pos_core import paths
paths.set_base_override(base)

from tkinter import messagebox
from pos_core import arranque, config
from pos_core.dueno_backend import RemoteBackend, RemoteError

fallos = []
TOKEN = "otter-TOKEN-REAL-DEL-CLIENTE"

# Que la prueba no dependa de un proxy del entorno: en el negocio no lo hay.
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "*"


def puerto_cerrado():
    """Un puerto de esta PC donde no escucha nadie."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    puerto = s.getsockname()[1]
    s.close()
    return puerto


def servidor(atender):
    """Escucha en un puerto libre y atiende UNA conexión con `atender(conexion)`."""
    escucha = socket.socket()
    escucha.bind(("127.0.0.1", 0))
    escucha.listen(1)

    def _hilo():
        try:
            conexion, _ = escucha.accept()
            atender(conexion)
        except OSError:
            pass
        finally:
            escucha.close()
    threading.Thread(target=_hilo, daemon=True).start()
    return escucha.getsockname()[1]


def error_de(puerto, espera=2):
    try:
        RemoteBackend(f"http://127.0.0.1:{puerto}", TOKEN, timeout=espera).alerts.obtener_umbral_global()
    except RemoteError as e:
        return e
    return None


def sin_la_direccion(error, puerto, donde):
    texto = f"{error} {error!r}"
    for prohibido in ("127.0.0.1", str(puerto), "HTTPConnectionPool", "/rpc", TOKEN):
        if prohibido in texto:
            fallos.append(f"{donde}: el cartel muestra «{prohibido}» (la dirección del local, o texto "
                           f"de la librería): {error}")
            return False
    if error.__cause__ is not None or not error.__suppress_context__:
        fallos.append(f"{donde}: el error original (con la dirección) sigue encadenado y sale en la traza")
        return False
    return True


# --------------------------------------------------------------------- #
# 1. El local no contesta: se dice en castellano y sin la dirección
# --------------------------------------------------------------------- #
puerto = puerto_cerrado()
e = error_de(puerto)
if e is None:
    fallos.append("la prueba esperaba que el local no contestara")
elif sin_la_direccion(e, puerto, "local apagado"):
    if "No se pudo conectar con el local" not in str(e) or "No se cambió nada" not in str(e):
        fallos.append(f"con el local apagado el cartel no dice que no se cambió nada: {e}")
    else:
        print("OK: con el local apagado, el cartel dice qué revisar, que no se cambió nada, y no muestra la dirección")

# --------------------------------------------------------------------- #
# 2. Recibió el pedido y no contestó a tiempo: puede haberse hecho igual
# --------------------------------------------------------------------- #
evento = threading.Event()


def _recibe_y_se_queda(conexion):
    conexion.recv(65536)
    evento.wait(10)             # trabaja y trabaja, sin contestar
    conexion.close()


puerto = servidor(_recibe_y_se_queda)
e = error_de(puerto, espera=1)
evento.set()
if e is None:
    fallos.append("la prueba esperaba que el local no contestara a tiempo")
elif sin_la_direccion(e, puerto, "local que no contesta a tiempo"):
    if "Puede que lo haya hecho igual" not in str(e):
        fallos.append(f"el local recibió el pedido y no contestó, y el cartel no avisa que pudo haberse "
                       f"hecho igual: {e}")
    else:
        print("OK: si el local recibió el pedido y no contestó a tiempo, avisa que pudo haberse hecho igual")

# --------------------------------------------------------------------- #
# 3. Se cortó en el medio (reiniciaron el servicio con el pedido adentro)
# --------------------------------------------------------------------- #


def _recibe_y_corta(conexion):
    conexion.recv(65536)
    conexion.close()


puerto = servidor(_recibe_y_corta)
e = error_de(puerto)
if e is None:
    fallos.append("la prueba esperaba que la conexión se cortara")
elif sin_la_direccion(e, puerto, "conexión cortada en el medio"):
    if "Puede que lo haya hecho igual" not in str(e):
        fallos.append(f"se cortó la conexión con el pedido ya recibido y el cartel no lo avisa: {e}")
    else:
        print("OK: si la conexión se corta con el pedido ya recibido, también avisa")

# Un nombre que no se puede resolver (Tailscale apagado en la laptop, con la
# dirección cargada por nombre): tampoco salió nada.
try:
    RemoteBackend("http://pc-del-local.invalid:8765", TOKEN).alerts.obtener_umbral_global()
    fallos.append("un nombre que no existe no dio error")
except RemoteError as e:
    if "pc-del-local" in f"{e} {e!r}":
        fallos.append(f"el cartel muestra el nombre de la PC del local: {e}")
    elif "No se cambió nada" not in str(e):
        fallos.append(f"con un nombre que no se puede resolver el pedido nunca salió, y el cartel dice "
                       f"otra cosa: {e}")
    else:
        print("OK: si la dirección no se puede resolver (Tailscale apagado), dice que no se cambió nada")

# --------------------------------------------------------------------- #
# 4. Con su config.ini ilegible, la pantalla de conexión abre igual
# --------------------------------------------------------------------- #
import apps.dueno_remoto.main as remoto

ruta_config = paths.config_path()
with open(ruta_config, "w", encoding="utf-8") as f:
    f.write("esto no es un ini\n")
try:
    pantalla = remoto.PantallaConexion()
    pantalla.update()
    if pantalla.entry_url.get() or pantalla.entry_token.get():
        fallos.append("con el config.ini ilegible la pantalla abrió con datos de no se sabe dónde")
    elif "Cargá" not in pantalla.lbl_estado.cget("text"):
        fallos.append("con el config.ini ilegible la pantalla no avisa que hay que cargar la conexión de nuevo")
    else:
        print("OK: con su config.ini ilegible, la pantalla de conexión abre con los campos vacíos y lo avisa")
    pantalla.destroy()
except Exception as e:
    fallos.append(f"con su config.ini ilegible el Dueño Remoto no abre ({type(e).__name__}: {e}): "
                   f"el dueño hace doble clic y no pasa nada")
os.remove(ruta_config)

# --------------------------------------------------------------------- #
# 5. Conectó pero no pudo guardar la conexión: entra igual
# --------------------------------------------------------------------- #
avisos = []
messagebox.showwarning = lambda titulo, mensaje="", **k: avisos.append(titulo)
_verificar = RemoteBackend.verificar_conexion
_guardar = config.actualizar_config_dict
RemoteBackend.verificar_conexion = lambda self: True


def _no_se_puede_guardar(cambios):
    raise PermissionError("la carpeta es de solo lectura")


config.actualizar_config_dict = _no_se_puede_guardar
pantalla = remoto.PantallaConexion()
pantalla.entry_url.insert(0, "http://100.101.102.103:8765")
pantalla.entry_token.insert(0, TOKEN)
try:
    pantalla._conectar()
except Exception as e:
    fallos.append(f"no poder guardar la conexión tiró {type(e).__name__}: la ventana queda en "
                   f"'Probando conexión...' para siempre, con el local contestando bien")
if pantalla.backend_listo is None:
    fallos.append("conectó bien pero, por no poder guardar la conexión, no deja entrar al panel")
elif not avisos:
    fallos.append("no avisó que la conexión no quedó guardada")
else:
    print("OK: si conecta pero no puede guardar la conexión, entra igual y avisa")
config.actualizar_config_dict = _guardar
RemoteBackend.verificar_conexion = _verificar

# --------------------------------------------------------------------- #
# 6. Si no puede abrir, lo dice (cartel y log), con los pasos de la laptop
# --------------------------------------------------------------------- #
carteles = []
arranque._dialogo = lambda titulo, mensaje, preguntar=False: (carteles.append((titulo, mensaje)), True)[1]
ruta_log = os.path.join(paths.logs_dir(), "arranque.log")


def _se_rompe(*a, **k):
    raise RuntimeError("falta un archivo del programa")


for donde, preparar in (
        ("la pantalla de conexión", lambda: setattr(remoto, "PantallaConexion", _se_rompe)),
        ("el panel", lambda: setattr(remoto, "_armar_panel", _se_rompe))):
    _pantalla, _panel = remoto.PantallaConexion, remoto._armar_panel
    carteles.clear()
    if os.path.exists(ruta_log):
        os.remove(ruta_log)
    preparar()
    if donde == "el panel":
        # La conexión sale bien y la pantalla se cierra sola: lo que falla es lo que sigue.
        class _PantallaLista:
            backend_listo = object()

            def mainloop(self):
                pass
        remoto.PantallaConexion = _PantallaLista
    try:
        remoto.main()
        fallos.append(f"si no se puede armar {donde}, el Dueño Remoto siguió como si nada")
    except SystemExit:
        if len(carteles) != 1 or "no pudo abrir" not in carteles[0][0]:
            fallos.append(f"si no se puede armar {donde}, no hay cartel que lo diga: {carteles}")
        elif "falta un archivo del programa" not in carteles[0][1]:
            fallos.append(f"el cartel no dice qué falló: {carteles[0][1]}")
        elif "USB de Mantenimiento" in carteles[0][1]:
            fallos.append("el cartel de la laptop manda a usar el USB de Mantenimiento, que es para la PC del local")
        elif not os.path.isfile(ruta_log):
            fallos.append(f"si no se puede armar {donde}, no queda nada en logs/arranque.log")
        else:
            print(f"OK: si no se puede armar {donde}, hay cartel (con los pasos de la laptop) y queda en el log")
    except Exception as e:
        fallos.append(f"si no se puede armar {donde}, el programa muere con {type(e).__name__} sin "
                       f"cartel: doble clic y no pasa nada")
    remoto.PantallaConexion, remoto._armar_panel = _pantalla, _panel

print()
if fallos:
    print("=== FALLOS DUEÑO REMOTO ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== DUEÑO REMOTO OK ===")
