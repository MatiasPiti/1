"""La API del celular, corriendo DE VERDAD (uvicorn en un puerto, pedidos HTTP reales).

Las otras pruebas usan el TestClient, que no abre ningún puerto. Esta levanta
el servidor con la MISMA iniciar_servidor() que usa el servicio de Windows
(lo que se prueba es lo que corre) y mira lo que importa en la PC del local:

- que cargar stock desde el celular mientras la Caja vende al mismo tiempo
  no pierda ni invente unidades (versionado optimista de main);
- que un proxy local (`tailscale serve`, un X-Forwarded-For) no pueda hacer
  pasar un pedido de la propia PC por uno del celular;
- que /api/salud conteste al instante aunque la API esté tapada de trabajo:
  el autochequeo la mira cada minuto y, si no contesta, reinicia el
  proceso; con una salud lenta, mataría una factura a mitad de camino;
- que el supervisor nunca se muera: puerto ocupado, apagada a propósito,
  puerto igual al del Dueño Remoto, cambio de puerto, hilo caído;
- que el diagnóstico de despliegue la reconozca por su firma y no la
  confunda con la API remota ni con cualquier otro programa en el puerto;
- que la autoprueba del exe (desde el código fuente) pase y no deje basura.
"""
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, AQUI)
sys.path.insert(0, RAIZ)

if len(sys.argv) >= 4 and sys.argv[1] == "--caja":
    # La Caja vendiendo en OTRO proceso, al mismo tiempo que el celular carga stock.
    _base, _codigo, _n = sys.argv[2], sys.argv[3], int(sys.argv[4])
    from pos_core import paths
    paths.set_base_override(_base)
    from pos_core import sales
    _largada = os.path.join(_base, "largada")
    while not os.path.exists(_largada):
        time.sleep(0.001)
    for _ in range(_n):
        sales.cerrar_ticket([{"codigo": _codigo, "nombre": "x", "cantidad": 1, "precio_unitario": 100}],
                            metodo_pago="EFECTIVO", usuario="El Galpón Del Nono")
    sys.exit(0)

if len(sys.argv) >= 3 and sys.argv[1] == "--escuchar":
    # Otro programa escuchando en el puerto (sin SO_REUSEADDR).
    _s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _s.bind(("127.0.0.1", int(sys.argv[2])))
    _s.listen(5)
    print("escuchando", flush=True)
    time.sleep(60)
    sys.exit(0)

import _base_celular as B  # noqa: E402
from pos_core import paths  # noqa: E402

INTEGRACION = os.environ.get("OTTER_INTEGRACION") == "1"
fallos = []


def ok(texto):
    print("OK:", texto)


def falla(texto):
    print("FALLA:", texto)
    fallos.append(texto)


def salteada(que, por_que):
    if INTEGRACION:
        falla(f"SALTEADA con OTTER_INTEGRACION=1 (cuenta como falla): {que} ({por_que})")
    else:
        print(f"SALTEADA: {que} ({por_que})")


def verificar(condicion, texto_ok, texto_falla):
    if condicion:
        ok(texto_ok)
    else:
        falla(texto_falla)
    return condicion


def puerto_libre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def pedir(puerto, metodo, ruta, cuerpo=None, token=None, encabezados=None, timeout=30):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    pedido = urllib.request.Request(f"http://127.0.0.1:{puerto}{ruta}", data=datos, method=metodo)
    if datos is not None:
        pedido.add_header("Content-Type", "application/json")
    if token:
        pedido.add_header("Authorization", f"Bearer {token}")
    for k, v in (encabezados or {}).items():
        pedido.add_header(k, v)
    abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with abridor.open(pedido, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "null")
        except ValueError:
            return e.code, None


def escucha(puerto) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", puerto), timeout=1):
            return True
    except OSError:
        return False


from pos_core import acceso_celular, config, db  # noqa: E402

RAIZ_TMP = tempfile.mkdtemp(prefix="api_celular_proceso_")
TMP = os.path.join(RAIZ_TMP, "instalacion")
os.makedirs(TMP)
B.armar_base(TMP)
acceso_celular.definir_pin_dueno(B.PIN)
db.cerrar_conexion()
RUTA_DB = os.path.join(TMP, "database", "stock.db")

from services import api_celular  # noqa: E402

api_celular.configurar_logs("servicio")


def stock_de(codigo):
    import sqlite3
    con = sqlite3.connect(RUTA_DB)
    try:
        return con.execute("SELECT stock FROM Productos WHERE codigo = ?", (codigo,)).fetchone()[0]
    finally:
        con.close()


# ===================================================================== #
# Servidor de verdad: carga de stock en paralelo con la Caja en otro proceso
# ===================================================================== #
estado = api_celular.EstadoApi()
puerto = puerto_libre()
app = api_celular.crear_app(estado=estado, permitir_loopback_total=True)
servidor = api_celular.iniciar_servidor(puerto, host="127.0.0.1", app=app)
verificar(servidor.esperar_inicio(20), f"uvicorn arrancó de verdad en el {puerto} con iniciar_servidor()",
          "el servidor no arrancó")
verificar(servidor.server.config.proxy_headers is False and servidor.hilo.name == "ApiCelular"
          and servidor.hilo.daemon, "uvicorn corre en el hilo ApiCelular con proxy_headers apagado",
          f"config: proxy_headers={servidor.server.config.proxy_headers}")
status, salud = pedir(puerto, "GET", "/api/salud")
verificar(status == 200 and salud["servicio"] == "otter-api-celular" and salud["pin_configurado"],
          "/api/salud contesta por HTTP real", f"salud: {status} {salud}")
status, login = pedir(puerto, "POST", "/api/auth/login", {"pin": B.PIN})
token = login["token"]

inicial = stock_de(B.FIDEOS)
VENTAS = 15
caja = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--caja", TMP, B.FIDEOS, str(VENTAS)])
resultados = []


def sumar_uno():
    resultados.append(pedir(puerto, "POST", "/api/stock/movimiento",
                            {"codigo": B.FIDEOS, "cantidad": 1, "operacion": "sumar", "motivo": "carga en paralelo"},
                            token=token, timeout=60))


hilos = [threading.Thread(target=sumar_uno) for _ in range(20)]
time.sleep(0.5)
open(os.path.join(TMP, "largada"), "w").close()
for h in hilos:
    h.start()
for h in hilos:
    h.join(120)
caja.wait(120)
bien = sum(1 for s, _ in resultados if s == 200)
ocupada = sum(1 for s, r in resultados if s == 503 and r and r.get("codigo") == "base_ocupada")
final = stock_de(B.FIDEOS)
verificar(caja.returncode == 0 and bien + ocupada == 20 and final == inicial + bien - VENTAS,
          f"20 cargas desde el celular a la vez que {VENTAS} ventas en la Caja: el stock da exacto "
          f"({inicial} + {bien} - {VENTAS} = {final})",
          f"stock {inicial} -> {final} con {bien} cargas OK, {ocupada} ocupadas y {VENTAS} ventas "
          f"(caja {caja.returncode}): {[s for s, _ in resultados]}")
verificar(bien == 20, "y ninguna carga se perdió por la concurrencia", f"cargas que no entraron: {20 - bien}")

# Salud con los 8 hilos ocupados (k4): una factura grande o un ajuste de 2000
# productos no pueden hacer que el autochequeo crea que la API está colgada.


def _lento():
    time.sleep(4)
    return {"ok": True}


app.add_api_route("/prueba/lento", _lento, methods=["GET"])
lentos = [threading.Thread(target=lambda: pedir(puerto, "GET", "/prueba/lento", timeout=60)) for _ in range(12)]
for h in lentos:
    h.start()
time.sleep(0.8)
inicio = time.perf_counter()
status, _ = pedir(puerto, "GET", "/api/salud", timeout=10)
demora = time.perf_counter() - inicio
verificar(status == 200 and demora < 1, f"con los 8 hilos ocupados, /api/salud contesta en {demora:.2f} s",
          f"salud con los hilos ocupados: {status} en {demora:.2f} s")


async def _hilos():
    import anyio.to_thread
    return {"hilos": anyio.to_thread.current_default_thread_limiter().total_tokens}


app.add_api_route("/prueba/hilos", _hilos, methods=["GET"])
verificar(pedir(puerto, "GET", "/prueba/hilos")[1] == {"hilos": 8},
          "el pool de hilos de la API está limitado a 8 (la caja corre en la misma PC)",
          f"hilos: {pedir(puerto, 'GET', '/prueba/hilos')}")
for h in lentos:
    h.join(60)
servidor.detener()
verificar(not servidor.vivo and not escucha(puerto), "detener() para el servidor y suelta el puerto",
          "el servidor siguió vivo después de detener()")

# Puerto reusado: parar y levantar enseguida en el mismo puerto anda (Linux:
# SO_REUSEADDR por las conexiones en TIME_WAIT), pero con otro programa
# ESCUCHANDO ahí falla igual.
if os.name != "nt":
    try:
        servidor = api_celular.iniciar_servidor(puerto, host="127.0.0.1", app=api_celular.crear_app(estado=estado))
        servidor.esperar_inicio(20)
        verificar(pedir(puerto, "GET", "/api/salud")[0] == 200, "parar y volver a levantar en el mismo puerto anda",
                  "no volvió a levantar")
        servidor.detener()
    except OSError as e:
        falla(f"no se pudo volver a levantar en el mismo puerto enseguida: {e}")
else:
    # En Windows el socket va con SO_EXCLUSIVEADDRUSE (nadie puede robar el
    # puerto) y puede no dejar re-escuchar mientras haya conexiones en
    # TIME_WAIT: para eso está el supervisor, que reintenta cada minuto.
    print("(en Windows no se prueba re-escuchar al instante en el mismo puerto: lo cubre el supervisor)")
ocupado = puerto_libre()
otro = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--escuchar", str(ocupado)],
                        stdout=subprocess.PIPE, text=True)
otro.stdout.readline()
try:
    servidor = api_celular.iniciar_servidor(ocupado, host="127.0.0.1", app=api_celular.crear_app(estado=estado))
    servidor.detener()
    falla("con otro programa escuchando en el puerto, iniciar_servidor no falló (le robó el puerto)")
except OSError:
    ok("con otro programa escuchando en el puerto, iniciar_servidor falla (no le roba el puerto)")

# ===================================================================== #
# Un proxy local no hace pasar a la propia PC por el celular
# ===================================================================== #
puerto_srv = puerto_libre()
servidor = api_celular.iniciar_servidor(puerto_srv, host="127.0.0.1", app=api_celular.crear_app(estado=estado))
servidor.esperar_inicio(20)
xff = {"X-Forwarded-For": "100.101.102.103", "X-Real-IP": "100.101.102.103", "Forwarded": "for=100.101.102.103"}
s_salud, _ = pedir(puerto_srv, "GET", "/api/salud", encabezados=xff)
s_login, cuerpo = pedir(puerto_srv, "POST", "/api/auth/login", {"pin": B.PIN}, encabezados=xff)
s_prod, _ = pedir(puerto_srv, "GET", "/api/productos", token=token, encabezados=xff)
servidor.detener()
for h in logging.getLogger().handlers:
    h.flush()
with open(os.path.join(TMP, "logs", "api_celular.log"), encoding="utf-8") as f:
    log_txt = f.read()
verificar(s_salud == 200 and s_login == 403 and s_prod == 403 and cuerpo.get("codigo") == "red_no_permitida",
          "con X-Forwarded-For desde 127.0.0.1 sigue siendo loopback: salud sí, login y productos 403",
          f"con X-Forwarded-For: salud {s_salud}, login {s_login}, productos {s_prod}")
verificar("RECHAZO de red ip=127.0.0.1 POST /api/auth/login" in log_txt and "100.101.102.103" not in log_txt,
          "el rechazo queda anotado con la IP real (127.0.0.1), no con la del encabezado",
          "el log le creyó al X-Forwarded-For (o no anotó el rechazo)")

# ===================================================================== #
# Supervisor: nunca se muere y no llena el log
# ===================================================================== #


class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def avanzar(self, s):
        self.t += s


class EstadoContado(api_celular.EstadoApi):
    def __init__(self):
        super().__init__()
        self.veces = 0

    def refrescar(self):
        self.veces += 1
        super().refrescar()


class Anotador(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lineas = []

    def emit(self, record):
        self.lineas.append(record.getMessage())


anotador = Anotador()
logging.getLogger().addHandler(anotador)
reloj = Reloj()
reloj_anotar_original = api_celular._reloj_anotar
api_celular._reloj_anotar = reloj
api_celular._anotados.clear()


def escribir_config(**api):
    cambios = {"api_celular": {k: str(v) for k, v in api.items()}}
    config.actualizar_config_dict(cambios)


try:
    p1 = puerto_libre()
    bloqueo = socket.socket()
    bloqueo.bind(("127.0.0.1", p1))
    bloqueo.listen(5)
    escribir_config(habilitado="true", puerto=p1)
    est = EstadoContado()
    sup = api_celular.Supervisor(autochequeo=False, estado=est, reloj=reloj, host="127.0.0.1",
                                 permitir_loopback_total=True)
    lanzo = False
    for _ in range(10):
        try:
            sup.paso()
        except Exception:
            lanzo = True
        reloj.avanzar(61)
    avisos_bind = [l for l in anotador.lineas if "No se pudo escuchar" in l]
    verificar(not lanzo and sup.servidor is None and len(avisos_bind) == 1,
              "con el puerto ocupado, paso() no lanza, no hay servidor y lo anota UNA sola vez (10 vueltas)",
              f"puerto ocupado: lanzó={lanzo}, servidor={sup.servidor}, avisos={len(avisos_bind)}")
    bloqueo.close()
    sup.paso()
    verificar(sup.servidor is not None and sup.servidor.esperar_inicio(20) and pedir(p1, "GET", "/api/salud")[0] == 200,
              "cuando el puerto se libera, en la vuelta siguiente arranca", "no arrancó al liberarse el puerto")
    veces = est.veces
    for _ in range(4):
        reloj.avanzar(15)
        sup.paso()
    verificar(est.veces - veces == 4, "el estado de /api/salud se refresca cada 15 s",
              f"refrescos en 60 s: {est.veces - veces}")
    # El hilo de uvicorn se muere: se relanza con espera, sin matar el proceso.
    sup.servidor.server.should_exit = True
    sup.servidor.hilo.join(10)
    sup.paso()
    sigue_caido = sup.servidor is None
    reloj.avanzar(5)
    sup.paso()
    verificar(sigue_caido and sup.servidor is not None and sup.servidor.esperar_inicio(20)
              and pedir(p1, "GET", "/api/salud")[0] == 200,
              "si el hilo de uvicorn se muere, lo relanza a los 5 s sin matar el proceso",
              f"hilo caído: esperó={sigue_caido}, servidor={sup.servidor}")
    # Cambio de puerto: para y vuelve a arrancar en el nuevo.
    p2 = puerto_libre()
    escribir_config(puerto=p2)
    reloj.avanzar(31)
    sup.paso()
    verificar(sup.servidor is not None and sup.servidor.esperar_inicio(20) and pedir(p2, "GET", "/api/salud")[0] == 200
              and not escucha(p1), "cambiar el puerto en config.ini lo mueve en menos de 30 s sin reiniciar",
              "no se movió de puerto")
    # Apagada a propósito: deja de escuchar sin reiniciar nada.
    escribir_config(habilitado="false")
    reloj.avanzar(31)
    sup.paso()
    time.sleep(0.3)
    verificar(sup.servidor is None and not escucha(p2), "con habilitado = false deja de escuchar (el proceso sigue)",
              "siguió escuchando apagada")
    sup.paso()
    verificar(sup.servidor is None, "y no se vuelve a prender sola", "se volvió a prender")
    # Puerto igual al de [remoto]: nunca le gana el puerto al Dueño Remoto.
    p3 = puerto_libre()
    config.actualizar_config_dict({"api_celular": {"habilitado": "true", "puerto": str(p3)},
                                   "remoto": {"puerto": str(p3)}})
    reloj.avanzar(31)
    sup.paso()
    verificar(sup.servidor is None and not escucha(p3)
              and any("puerto del Dueño Remoto" in l or "es del Dueño Remoto" in l for l in anotador.lineas),
              "con [api_celular] puerto = [remoto] puerto no escucha y lo anota", "escuchó en el puerto del remoto")
    config.actualizar_config_dict({"remoto": {"puerto": "8765"}, "api_celular": {"puerto": str(p2)}})
    reloj.avanzar(31)
    sup.paso()
    verificar(sup.servidor is not None and sup.servidor.esperar_inicio(20),
              "al corregir el puerto vuelve a escuchar", "no volvió")
    # config.ini ilegible: sigue con lo último que leyó.
    with open(os.path.join(TMP, "config.ini"), "rb") as f:
        ini_bueno = f.read()
    with open(os.path.join(TMP, "config.ini"), "w", encoding="utf-8") as f:
        f.write("esto no es un ini")
    reloj.avanzar(31)
    sup.paso()
    verificar(sup.servidor is not None and sup.servidor.vivo and pedir(p2, "GET", "/api/salud")[0] == 200,
              "con config.ini ilegible sigue escuchando con lo último que leyó", "se cayó con el config ilegible")
    with open(os.path.join(TMP, "config.ini"), "wb") as f:
        f.write(ini_bueno)
    sup.detener()
    verificar(sup.servidor is None and not escucha(p2), "detener() (lo que hace SvcStop) para todo",
              "detener no paró el servidor")
    sup.paso()
    verificar(sup.servidor is None, "después de detener(), paso() no vuelve a levantar nada", "se levantó de nuevo")

    # Autochequeo: con 3 fallas seguidas sale para que actúe Windows; si contesta, la cuenta vuelve a 0.
    salidas = []
    sup = api_celular.Supervisor(autochequeo=True, estado=api_celular.EstadoApi(), reloj=reloj, host="127.0.0.1",
                                 permitir_loopback_total=True)
    sup._salir = salidas.append
    sup.paso()
    sup.servidor.esperar_inicio(20)
    pedir_salud_original = api_celular._pedir_salud
    try:
        reloj.avanzar(61)
        sup.paso()
        verificar(sup._fallas_chequeo == 0, "el autochequeo contra su propia /api/salud da bien",
                  f"autochequeo falló estando sana: {sup._fallas_chequeo}")

        def _no_contesta(*a, **k):
            raise TimeoutError("timed out")

        api_celular._pedir_salud = _no_contesta
        for _ in range(2):
            reloj.avanzar(61)
            sup.paso()
        api_celular._pedir_salud = pedir_salud_original
        reloj.avanzar(61)
        sup.paso()
        cuenta_volvio = sup._fallas_chequeo == 0 and salidas == []
        api_celular._pedir_salud = _no_contesta
        for _ in range(3):
            reloj.avanzar(61)
            sup.paso()
        verificar(cuenta_volvio and salidas == [3],
                  "con 3 autochequeos fallidos SEGUIDOS sale con código 3 (Windows lo reinicia); 2 y uno bueno, no",
                  f"autochequeo: volvió a 0={cuenta_volvio}, salidas={salidas}")
    finally:
        api_celular._pedir_salud = pedir_salud_original
        sup.detener()
finally:
    api_celular._reloj_anotar = reloj_anotar_original
    logging.getLogger().removeHandler(anotador)
    try:
        otro.kill()
    except Exception:
        pass

# ===================================================================== #
# El diagnóstico de despliegue la reconoce por su firma
# ===================================================================== #
try:
    from pos_core import servicio_windows
    funciones = (servicio_windows.api_celular_contesta, servicio_windows.remote_api_contesta)
except (ImportError, AttributeError) as e:
    funciones = None
    salteada("api_celular_contesta / remote_api_contesta contra servidores reales",
             f"pos_core.servicio_windows todavía no las trae: {e}")
if funciones:
    import http.server

    from services import api_celular_cli, api_celular_servicio
    iguales = {
        "firma": (servicio_windows.FIRMA_CELULAR, api_celular.FIRMA),
        "puerto": (servicio_windows.PUERTO_CELULAR, api_celular.PUERTO_POR_DEFECTO,
                   config.PUERTO_CELULAR_POR_DEFECTO),
        "servicio": (servicio_windows.SERVICIO_CELULAR, api_celular.SERVICIO, api_celular_servicio.NOMBRE_SERVICIO),
        "regla de firewall": (servicio_windows.REGLA_FIREWALL_CELULAR, api_celular_cli.REGLA_FIREWALL),
        "tarea del watchdog": (servicio_windows.TAREA_WATCHDOG_CELULAR, api_celular_cli.TAREA_WATCHDOG),
    }
    distintas = {k: v for k, v in iguales.items() if len(set(v)) != 1}
    verificar(not distintas, "las constantes repetidas (firma, puerto, servicio, regla, tarea) son iguales en "
                             "servicio_windows, la API y la consola", f"constantes distintas: {distintas}")

    from services import remote_api
    p_cel, p_rem, p_otro, p_vacio = puerto_libre(), puerto_libre(), puerto_libre(), puerto_libre()
    cel = api_celular.iniciar_servidor(p_cel, host="127.0.0.1", app=api_celular.crear_app(estado=estado))
    cel.esperar_inicio(20)
    rem = remote_api.iniciar_servidor(puerto=p_rem, token="TOKEN-REMOTO-DE-PRUEBA", escuchar_en="127.0.0.1")
    class _Callado(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    cualquiera = http.server.ThreadingHTTPServer(("127.0.0.1", p_otro), _Callado)
    threading.Thread(target=cualquiera.serve_forever, daemon=True).start()
    try:
        resultados = {
            "celular": servicio_windows.api_celular_contesta(p_cel)[0],
            "remote_api": servicio_windows.api_celular_contesta(p_rem)[0],
            "otro": servicio_windows.api_celular_contesta(p_otro)[0],
            "vacío": servicio_windows.api_celular_contesta(p_vacio)[0],
            "remote_api_contesta": servicio_windows.remote_api_contesta(p_rem)[0],
            "remote_api_contesta(celular)": servicio_windows.remote_api_contesta(p_cel)[0],
        }
    finally:
        cel.detener()
        rem.shutdown()
        cualquiera.shutdown()
    verificar(resultados == {"celular": True, "remote_api": False, "otro": False, "vacío": False,
                             "remote_api_contesta": True, "remote_api_contesta(celular)": False},
              "servicio_windows reconoce la API del celular por su firma, y no la confunde con la API remota, "
              "con otro programa ni con un puerto vacío", f"reconocimiento: {resultados}")

api_celular.cerrar_logs()

# ===================================================================== #
# La autoprueba del exe, desde el código fuente
# ===================================================================== #
logs_raiz = os.path.join(RAIZ, "logs")
habia_logs = os.path.isdir(logs_raiz)
antes_tmp = set(os.listdir(tempfile.gettempdir()))
proceso = subprocess.run([sys.executable, os.path.join(RAIZ, "services", "api_celular_servicio.py"), "autoprueba"],
                         capture_output=True, text=True, timeout=300, cwd=RAIZ)
quedaron = [n for n in set(os.listdir(tempfile.gettempdir())) - antes_tmp if n.startswith("otter_autoprueba_")]
verificar(proceso.returncode == 0 and proceso.stdout.strip().startswith("AUTOPRUEBA OK"),
          f"la autoprueba desde el fuente pasa: {proceso.stdout.strip()}",
          f"autoprueba: {proceso.returncode} {proceso.stdout} {proceso.stderr[-2000:]}")
verificar(habia_logs or not os.path.isdir(logs_raiz), "la autoprueba no deja un logs\\ en la raíz del repo",
          "la autoprueba creó logs\\ en la raíz")
verificar(not quedaron, "y borra su carpeta temporal al terminar", f"quedaron: {quedaron}")

shutil.rmtree(RAIZ_TMP, ignore_errors=True)
print()
if fallos:
    print("=== FALLOS API CELULAR (PROCESO) ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== API CELULAR (PROCESO) OK ===")
