"""La API del celular no se deja entrar por donde no debe.

Es una puerta nueva a la base del negocio, abierta 24 horas en la PC de la
caja. Lo que cuida esta prueba, mirando lo que ve quien intenta entrar:

- el PIN viejo de producción (sha256 de '1234', que dejó el setup) NUNCA
  da una sesión, y probar PINs al azar se frena rápido y sin gastar la CPU
  de la caja;
- una sesión robada o vieja deja de servir apenas se cambia el PIN, se
  cierran las sesiones o se desactiva el usuario;
- el archivo de secretos roto o perdido no se regenera solo (dejaría el PIN
  inservible sin que nadie se entere);
- regla 1: solo se entra por Tailscale. Desde la LAN, desde internet por un
  proxy local o desde la propia PC (como entraría un `tailscale serve` o
  `funnel`) da 403, y no hay ninguna clave de config.ini que lo afloje;
- regla 4: ningún log ni respuesta lleva el PIN, la sesión, el token del
  bot ni el encabezado Authorization, tampoco en la traza de un error;
- la API no llama nada que cree la base, la migre, arranque alertas o
  toque el token de [remoto] (se lee el código, no se busca texto);
- los verbos de consola: el PIN nunca por argumento, sin base no se crea
  nada, y habilitar/deshabilitar no rompen config.ini.
"""
import ast
import configparser
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, AQUI)
sys.path.insert(0, RAIZ)

# --------------------------------------------------------------------- #
# Modos hijo (procesos aparte)
# --------------------------------------------------------------------- #
if len(sys.argv) >= 3 and sys.argv[1] == "--asegurar":
    # Dos de estos corren a la vez: los dos tienen que terminar con EL MISMO secreto.
    _base, _largada = sys.argv[2], sys.argv[3]
    from pos_core import paths
    paths.set_base_override(_base)
    from pos_core import acceso_celular as _ac
    while not os.path.exists(_largada):
        pass
    _s = _ac.asegurar_secreto()
    print(_s["firma"] + _s["pimienta"])
    sys.exit(0)

if len(sys.argv) >= 3 and sys.argv[1] == "--leer-secreto":
    # Lee en bucle mientras otros lo crean: nunca puede ver uno a medio escribir.
    _base, _hasta = sys.argv[2], time.time() + float(sys.argv[3])
    from pos_core import paths
    paths.set_base_override(_base)
    from pos_core import acceso_celular as _ac
    _rotos = _lecturas = 0
    _motivos = set()
    while time.time() < _hasta:
        try:
            if _ac.leer_secreto() is not None:
                _lecturas += 1
        except _ac.SecretoIlegibleError as _e:
            _rotos += 1
            _motivos.add(f"{_e} <- {_e.__cause__!r}")
    if _motivos:
        # A stderr: el padre solo lee la cuenta de stdout, y en el CI esto
        # dice QUÉ error vio el lector (en Windows, el PermissionError pasajero).
        print(f"lector de secreto.json: {sorted(_motivos)[:3]}", file=sys.stderr)
    print(f"{_lecturas} {_rotos}")
    sys.exit(0)

if len(sys.argv) >= 3 and sys.argv[1] == "--leer-con-choques":
    # leer_secreto con un open que da PermissionError las primeras 3 veces,
    # como en Windows mientras otro proceso mueve o reemplaza el archivo.
    _base = sys.argv[2]
    from pos_core import paths
    paths.set_base_override(_base)
    from pos_core import acceso_celular as _ac
    _abrir, _choques = open, [3]

    def _open_que_choca(archivo, *a, **k):
        if str(archivo).endswith("secreto.json") and _choques[0] > 0:
            _choques[0] -= 1
            raise PermissionError(13, "El proceso no tiene acceso al archivo porque está siendo utilizado")
        return _abrir(archivo, *a, **k)

    _ac.open = _open_que_choca
    try:
        _s = _ac.leer_secreto()
        print("ok" if (_s and _choques[0] == 0) else f"mal: {_s!r} choques sin usar {_choques[0]}")
    except Exception as _e:
        print(f"mal: {type(_e).__name__}: {_e}")
    sys.exit(0)

if len(sys.argv) >= 2 and sys.argv[1] == "--cli":
    # Un verbo de ApiCelular.exe como lo corre Windows, con getpass simulado
    # (las respuestas vienen en OTTER_PRUEBA_PINES, separadas por comas).
    import getpass
    _respuestas = [p for p in os.environ.get("OTTER_PRUEBA_PINES", "").split(",") if p]

    def _getpass(prompt=""):
        print(prompt, end="")
        if not _respuestas:
            raise EOFError
        return _respuestas.pop(0)

    getpass.getpass = _getpass
    from services import api_celular_servicio
    sys.exit(api_celular_servicio.main(sys.argv[2:]))

import _base_celular as B  # noqa: E402
from pos_core import paths  # noqa: E402

fallos = []


def ok(texto):
    print("OK:", texto)


def falla(texto):
    print("FALLA:", texto)
    fallos.append(texto)


def verificar(condicion, texto_ok, texto_falla):
    if condicion:
        ok(texto_ok)
    else:
        falla(texto_falla)
    return condicion


from pos_core import acceso_celular, config, db  # noqa: E402

RAIZ_TMP = tempfile.mkdtemp(prefix="api_celular_seguridad_")
TMP = os.path.join(RAIZ_TMP, "instalacion")
os.makedirs(TMP)
B.armar_base(TMP)
RUTA_DB = os.path.join(TMP, "database", "stock.db")
enviados = []
B.simular_telegram(enviados)

from services import api_celular  # noqa: E402

api_celular.configurar_logs("servicio")


def usar_instalacion(tmp):
    db.cerrar_conexion()
    paths.set_base_override(tmp)
    db.usar_solo_base_existente(os.path.join(tmp, "database", "stock.db"))


def hash_de_dueno():
    con = sqlite3.connect(RUTA_DB)
    try:
        r = con.execute("SELECT pin_hash, activo FROM Usuarios WHERE nombre = 'dueño'").fetchone()
        return r
    finally:
        con.close()


def correr_cli(base, *args, pines="", entrada=None, timeout=120):
    entorno = dict(os.environ, OTTER_PRUEBA_PINES=pines)
    argumentos = [sys.executable, os.path.abspath(__file__), "--cli", *args]
    if base is not None:
        argumentos += ["--base", base]
    return subprocess.run(argumentos, capture_output=True, text=True, timeout=timeout, env=entorno,
                          input=entrada if entrada is not None else "", cwd=RAIZ)


# ===================================================================== #
# PIN
# ===================================================================== #
SHA_1234 = hashlib.sha256(b"1234").hexdigest()
app = api_celular.crear_app()
with B.cliente(app) as c:
    app.state.estado.refrescar()
    r = c.post("/api/auth/login", json={"pin": "1234"})
    verificar(r.status_code == 503 and r.json().get("codigo") == "pin_no_definido",
              "con la fila de producción (sha256 de '1234'), login con 1234 da 503 pin_no_definido, nunca 200",
              f"login 1234 sobre el hash viejo: {r.status_code} {r.text}")
    r = c.post("/api/auth/login", json={"pin": SHA_1234})
    verificar(r.status_code in (422, 503) and r.status_code != 200,
              "tampoco entra mandando el hash mismo", f"login con el hash: {r.status_code}")
    verificar(app.state.bloqueo.segundos_restantes(B.IP_CELULAR) == 0
              and not app.state.bloqueo._fallos.get(B.IP_CELULAR),
              "con el PIN anulado no se cuentan intentos fallidos (no hay nada contra qué comparar)",
              "el PIN anulado sumó intentos fallidos")

acceso_celular.definir_pin_dueno(B.PIN)
fila = hash_de_dueno()
verificar(fila[0].startswith("pbkdf2_sha256$600000$") and fila[0] != SHA_1234,
          "definir el PIN pisa el sha256 viejo con un PBKDF2 de 600.000 vueltas", f"pin_hash: {fila[0][:30]}")
con = sqlite3.connect(RUTA_DB)
quedan_sha = con.execute("SELECT COUNT(*) FROM Usuarios WHERE pin_hash = ?", (SHA_1234,)).fetchone()[0]
con.close()
verificar(quedan_sha == 0, "la fila con sha256('1234') ya no existe en la base", "quedó el hash viejo")

app = api_celular.crear_app()
with B.cliente(app) as c:
    app.state.estado.refrescar()
    r = c.post("/api/auth/login", json={"pin": B.PIN})
    verificar(r.status_code == 200, "con el PIN nuevo el login anda", f"login: {r.status_code} {r.text}")
    r = c.post("/api/auth/login", json={"pin": "1234"})
    verificar(r.status_code == 401 and r.json().get("codigo") == "pin_incorrecto", "y 1234 ahora da 401",
              f"1234: {r.status_code}")

malos = []
for pin in ("123456", "654321", "111111", "1234", "12345", "123499", "12a456", "abcdef", "", "1234567890123",
            "121212", "012345", " 482915"):
    try:
        acceso_celular.validar_pin_nuevo(pin)
        malos.append(pin)
    except ValueError:
        pass
try:
    acceso_celular.validar_pin_nuevo(B.PIN)
    bueno = True
except ValueError:
    bueno = False
verificar(not malos and bueno, "validar_pin_nuevo rechaza escaleras, repetidos, el 1234, cortos y letras",
          f"PINs aceptados que no debían: {malos} (482915 aceptado: {bueno})")

# ===================================================================== #
# Bloqueo por intentos (sin calcular PBKDF2 estando bloqueado)
# ===================================================================== #
derivaciones = []
_derivar_original = acceso_celular._derivar


def _derivar_contado(*a, **k):
    derivaciones.append(1)
    return b"\0" * 32      # nunca coincide: acá solo se prueban PINs incorrectos


acceso_celular._derivar = _derivar_contado
try:
    app = api_celular.crear_app()
    with B.cliente(app) as c:
        app.state.estado.refrescar()
        codigos = [c.post("/api/auth/login", json={"pin": "999999"}).status_code for _ in range(5)]
        antes = len(derivaciones)
        r = c.post("/api/auth/login", json={"pin": "999999"})
        r2 = c.post("/api/auth/login", json={"pin": B.PIN})
        verificar(codigos == [401] * 5 and r.status_code == 429 and r.json().get("codigo") == "demasiados_intentos"
                  and 0 < r.json().get("reintentar_en_s", 0) <= 300,
                  "5 PIN fallidos desde una IP la bloquean: 429 con reintentar_en_s",
                  f"intentos: {codigos} y después {r.status_code} {r.text}")
        verificar(r2.status_code == 429 and len(derivaciones) == antes,
                  "estando bloqueada, ni el PIN correcto se calcula (no gasta CPU de la caja)",
                  f"bloqueada: {r2.status_code}, cálculos de más: {len(derivaciones) - antes}")
        verificar("Probá de nuevo en 5 min" in r.json().get("detail", ""), "el aviso dice cuánto esperar",
                  f"detalle: {r.json().get('detail')}")
        with B.cliente(app, ip="100.101.102.104") as otra:
            verificar(otra.post("/api/auth/login", json={"pin": "999999"}).status_code == 401,
                      "el bloqueo es por IP: otra IP de la tailnet sigue pudiendo probar", "bloqueó a otra IP")

    app = api_celular.crear_app()
    with B.cliente(app) as c:
        app.state.estado.refrescar()
    estados = []
    for i in range(21):
        with B.cliente(app, ip=f"100.90.0.{i + 1}") as c:
            estados.append(c.post("/api/auth/login", json={"pin": "999999"}).status_code)
    antes = len(derivaciones)
    with B.cliente(app, ip="100.90.1.1") as c:
        r = c.post("/api/auth/login", json={"pin": "999999"})
    verificar(estados == [401] * 21 and r.status_code == 429 and len(derivaciones) == antes,
              "más de 20 fallas en 5 min sumando IPs distintas bloquean TODOS los logins (sin calcular nada)",
              f"21 IPs: {estados}, la 22.ª: {r.status_code}")
finally:
    acceso_celular._derivar = _derivar_original

# ===================================================================== #
# Sesiones (tokens)
# ===================================================================== #
app = api_celular.crear_app()
with B.cliente(app) as c:
    app.state.estado.refrescar()
    token = B.login(c)
    verificar(c.get("/api/auth/yo", headers=B.auth(token)).status_code == 200, "la sesión recién emitida sirve",
              "la sesión no sirvió")
    # Adulterado: otro usuario en el cuerpo con la firma vieja.
    cuerpo, firma = token.rsplit(".", 1)
    datos = json.loads(acceso_celular._unb64(cuerpo[3:]))
    datos["exp"] += 10 ** 6
    adulterado = "v2." + acceso_celular._b64(json.dumps(datos).encode()) + "." + firma
    casos = {"adulterado": adulterado, "con otro prefijo": "v1." + token[3:], "basura": "v2.xxx.yyy",
             "vacío": "", "firma cambiada": cuerpo + "." + firma[::-1]}
    malos = [n for n, t in casos.items() if c.get("/api/auth/yo", headers=B.auth(t)).status_code != 401]
    verificar(not malos, "un token adulterado, con otro prefijo o roto da 401", f"no dieron 401: {malos}")
    r = c.get("/api/auth/yo", headers={"Authorization": token})
    verificar(r.status_code == 401, "el token sin 'Bearer' no sirve", f"sin Bearer: {r.status_code}")
    # Vencido: el reloj de la API 31 días adelante.
    ahora_original = acceso_celular._ahora
    acceso_celular._ahora = lambda: int(time.time()) + 31 * 24 * 3600
    try:
        r = c.get("/api/auth/yo", headers=B.auth(token))
    finally:
        acceso_celular._ahora = ahora_original
    verificar(r.status_code == 401, "a los 31 días la sesión vence (401)", f"vencido: {r.status_code}")
    # Cerrar sesiones (rotar la firma): 401 sin reiniciar la app.
    acceso_celular.rotar_firma()
    verificar(c.get("/api/auth/yo", headers=B.auth(token)).status_code == 401,
              "después de cerrar-sesiones (firma nueva) el token viejo da 401 sin reiniciar nada", "siguió entrando")
    token = B.login(c)
    verificar(c.get("/api/auth/yo", headers=B.auth(token)).status_code == 200,
              "y con el PIN de siempre se vuelve a entrar (la pimienta no cambió)", "no se pudo volver a entrar")
    # Cambiar el PIN: todos los celulares afuera.
    acceso_celular.definir_pin_dueno("739164")
    verificar(c.get("/api/auth/yo", headers=B.auth(token)).status_code == 401,
              "después de cambiar el PIN, la sesión vieja da 401", "la sesión sobrevivió al cambio de PIN")
    token = B.login(c, "739164")
    db.cerrar_conexion()
    with db.transaction() as conn:   # simula: main no tiene función para desactivar un usuario
        conn.execute("UPDATE Usuarios SET activo = 0 WHERE nombre = 'dueño'")
    db.cerrar_conexion()
    verificar(c.get("/api/auth/yo", headers=B.auth(token)).status_code == 401,
              "con el usuario desactivado, 401", "un usuario desactivado siguió entrando")
    with db.transaction() as conn:
        conn.execute("UPDATE Usuarios SET activo = 1 WHERE nombre = 'dueño'")
    db.cerrar_conexion()
acceso_celular.definir_pin_dueno(B.PIN)

# ===================================================================== #
# Secreto (api_celular/secreto.json)
# ===================================================================== #
ruta_secreto = acceso_celular.ruta_secreto()
with open(ruta_secreto, "rb") as f:
    secreto_bueno = f.read()
app = api_celular.crear_app()
with B.cliente(app) as c:
    app.state.estado.refrescar()
    token = B.login(c)
    with open(ruta_secreto, "w", encoding="utf-8") as f:
        f.write('{"version": 1, "firma": "roto')
    with open(ruta_secreto, "rb") as f:
        roto = f.read()
    app.state.estado.refrescar()
    r1 = c.post("/api/auth/login", json={"pin": B.PIN})
    r2 = c.get("/api/productos", headers=B.auth(token))
    salud = c.get("/api/salud").json()
    with open(ruta_secreto, "rb") as f:
        despues = f.read()
    verificar(r1.status_code == 503 and r1.json().get("codigo") == "secreto_ilegible" and r2.status_code == 503
              and r2.json().get("codigo") == "secreto_ilegible" and salud["motivo"] == "secreto_ilegible",
              "con secreto.json roto: 503 secreto_ilegible en el login, en los pedidos y en salud",
              f"secreto roto: login {r1.status_code} {r1.text}, pedido {r2.status_code}, salud {salud['motivo']}")
    verificar(despues == roto, "y el archivo roto queda igual byte a byte (no se regenera nada)",
              "la API tocó el secreto roto")
    os.remove(ruta_secreto)
    app.state.estado.refrescar()
    r1 = c.post("/api/auth/login", json={"pin": B.PIN})
    r2 = c.get("/api/productos", headers=B.auth(token))
    verificar(r1.status_code == 503 and r1.json().get("codigo") == "pin_no_definido"
              and "secreto" in r1.json().get("detail", "") and r2.status_code == 401,
              "sin secreto.json (y el PIN en PBKDF2): 503 pin_no_definido por secreto perdido",
              f"secreto borrado: {r1.status_code} {r1.text}, pedido {r2.status_code}")
    verificar(not os.path.exists(ruta_secreto), "y el servicio NO crea el archivo", "la API creó secreto.json")
with open(ruta_secreto, "wb") as f:
    f.write(secreto_bueno)

# Dos definir-pin a la vez (el botón del Actualizador y el acceso directo):
# terminan con el MISMO secreto, y quien lo lee mientras tanto nunca lo ve a medias.
distintos, rotos_vistos, rondas = [], 0, 8
for ronda in range(rondas):
    base = tempfile.mkdtemp(prefix="secreto_", dir=RAIZ_TMP)
    largada = os.path.join(base, "largada")
    lector = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--leer-secreto", base, "1.5"],
                              stdout=subprocess.PIPE, text=True)
    escritores = [subprocess.Popen([sys.executable, os.path.abspath(__file__), "--asegurar", base, largada],
                                   stdout=subprocess.PIPE, text=True) for _ in range(2)]
    time.sleep(0.4)
    open(largada, "w").close()
    salidas = [e.communicate(timeout=60)[0].strip() for e in escritores]
    lecturas, rotos = map(int, lector.communicate(timeout=60)[0].split())
    rotos_vistos += rotos
    with open(os.path.join(base, "api_celular", "secreto.json"), encoding="utf-8") as f:
        final = json.load(f)
    if not (salidas[0] == salidas[1] == final["firma"] + final["pimienta"]):
        distintos.append(ronda)
    sobrantes = [n for n in os.listdir(os.path.join(base, "api_celular")) if n != "secreto.json"]
    if sobrantes:
        distintos.append(f"ronda {ronda}: temporales {sobrantes}")
verificar(not distintos, f"dos definir-pin a la vez terminan con el mismo secreto ({rondas} rondas)",
          f"rondas con secretos distintos o temporales sueltos: {distintos}")
verificar(rotos_vistos == 0, "quien lee secreto.json mientras se crea nunca lo ve a medio escribir",
          f"se leyó un secreto a medias {rotos_vistos} veces")
# En Windows, abrirlo justo mientras otro lo mueve o lo reemplaza da
# PermissionError por un instante (lo agarró el CI). Se simula para que la
# prueba tenga dientes también en Linux: el lector reintenta y lo lee.
proceso = subprocess.run([sys.executable, os.path.abspath(__file__), "--leer-con-choques", base],
                         capture_output=True, text=True, timeout=60)
verificar(proceso.stdout.strip() == "ok",
          "si secreto.json se está moviendo (PermissionError pasajero), leer_secreto reintenta y lo lee",
          f"con PermissionError pasajero leer_secreto dio: {proceso.stdout.strip()} {proceso.stderr[-500:]}")

# ===================================================================== #
# Filtro de red (regla 1)
# ===================================================================== #
app = api_celular.crear_app()
with B.cliente(app) as c:
    app.state.estado.refrescar()
    token = B.login(c)
H = B.auth(token)
CASOS_RED = [
    ("100.x -> 100.y (celular por Tailscale)", "100.101.102.103", "100.80.1.2", "GET", "/api/productos", 200),
    ("IPv4 mapeada -> fd7a (Tailscale IPv6)", "::ffff:100.70.1.2", "[fd7a:115c:a1e0::5]", "GET", "/api/productos", 200),
    ("la PC a su propia 100.x (tailscale serve/funnel)", "100.80.1.2", "100.80.1.2", "GET", "/api/productos", 403),
    ("la PC a su propia 100.x, salud", "100.80.1.2", "100.80.1.2", "GET", "/api/salud", 403),
    ("LAN -> LAN", "192.168.1.5", "192.168.1.2", "GET", "/api/productos", 403),
    ("100.x entrando por la placa de la LAN", "100.101.102.103", "192.168.1.2", "GET", "/api/productos", 403),
    ("internet -> 100.x", "8.8.8.8", "100.80.1.2", "GET", "/api/productos", 403),
    ("loopback, salud", "127.0.0.1", "127.0.0.1", "GET", "/api/salud", 200),
    ("loopback, login", "127.0.0.1", "127.0.0.1", "POST", "/api/auth/login", 403),
    ("loopback, productos", "127.0.0.1", "127.0.0.1", "GET", "/api/productos", 403),
    ("loopback IPv6, productos", "::1", "[::1]", "GET", "/api/productos", 403),
    ("loopback, salud por POST", "127.0.0.1", "127.0.0.1", "POST", "/api/salud", 403),
    ("'testclient' (sin IP)", "testclient", "100.80.1.2", "GET", "/api/productos", 403),
]
malos = []
for nombre, ip, servidor, metodo, ruta, esperado in CASOS_RED:
    with B.cliente(app, ip=ip, servidor=servidor) as c:
        r = c.request(metodo, ruta, headers=H, json={"pin": B.PIN} if ruta.endswith("login") else None)
    if r.status_code != esperado:
        malos.append(f"{nombre}: {r.status_code} (esperado {esperado})")
    elif esperado == 403 and r.json() != {"detail": "Solo se aceptan conexiones por Tailscale.",
                                          "codigo": "red_no_permitida"}:
        malos.append(f"{nombre}: 403 con otro cuerpo {r.text}")
verificar(not malos, f"filtro de red: los {len(CASOS_RED)} casos dan lo esperado", "filtro de red:\n    " +
          "\n    ".join(malos))

app_total = api_celular.crear_app(permitir_loopback_total=True)
with B.cliente(app_total, ip="127.0.0.1", servidor="127.0.0.1") as c:
    app_total.state.estado.refrescar()
    r = c.get("/api/productos", headers=H)
verificar(r.status_code == 200, "con permitir_loopback_total (solo autoprueba y pruebas), loopback pasa",
          f"loopback total: {r.status_code}")
config.actualizar_config_dict({"api_celular": {"permitir_loopback_total": "true", "redes": "0.0.0.0/0"}})
app = api_celular.crear_app()
with B.cliente(app, ip="127.0.0.1", servidor="127.0.0.1") as c:
    app.state.estado.refrescar()
    r1 = c.get("/api/productos", headers=H)
with B.cliente(app, ip="192.168.1.5", servidor="192.168.1.2") as c:
    r2 = c.get("/api/productos", headers=H)
verificar(r1.status_code == 403 and r2.status_code == 403,
          "escribir permitir_loopback_total o redes en [api_celular] no afloja nada",
          f"con claves en config.ini: loopback {r1.status_code}, LAN {r2.status_code}")
cfg = configparser.ConfigParser(interpolation=None)
cfg.read(os.path.join(TMP, "config.ini"), encoding="utf-8")
cfg.remove_option("api_celular", "permitir_loopback_total")
cfg.remove_option("api_celular", "redes")
config.guardar_config(cfg)

# ===================================================================== #
# Límites
# ===================================================================== #
app = api_celular.crear_app()
with B.cliente(app) as c:
    app.state.estado.refrescar()
    r = c.post("/api/precios/previsualizar", headers=H, content=b'{"codigos": ["' + b"1" * (2 * 1024 * 1024) + b'"]}',
               )
    verificar(r.status_code == 413 and r.json().get("codigo") == "demasiado_grande",
              "un JSON de 2 MB da 413", f"JSON grande: {r.status_code} {r.text[:200]}")

    def en_pedazos():
        for _ in range(40):
            yield b"x" * 65536      # 2,5 MB sin Content-Length

    r = c.post("/api/precios/previsualizar", headers=dict(H, **{"Content-Type": "application/json"}),
               content=en_pedazos())
    verificar(r.status_code == 413, "un envío por pedazos (chunked) sin Content-Length también da 413",
              f"chunked: {r.status_code} {r.text[:200]}")
    r = B.subir_pdf(c, token, b"%PDF-1.4\n" + b"0" * (21 * 1024 * 1024))
    verificar(r.status_code == 413 and r.json() == {"detail": "El PDF supera los 20 MB.", "codigo": "demasiado_grande"},
              "un PDF de 21 MB da 413", f"PDF grande: {r.status_code} {r.text[:200]}")
    r = B.subir_pdf(c, token, b"PK\x03\x04 esto es un zip, no un pdf", "factura.zip")
    verificar(r.status_code == 415, "algo que no es un PDF da 415", f"no pdf: {r.status_code}")
    pin_largo = "9876543210" * 4
    r = c.post("/api/auth/login", json={"pin": pin_largo})
    verificar(r.status_code == 422 and "9876543210" not in r.text and r.json().get("campo") == "pin",
              "un 422 nunca devuelve el valor recibido (un PIN de 40 números no aparece en la respuesta)",
              f"422: {r.text}")
    r = c.post("/api/stock/movimiento", headers=H, json={"codigo": "7790001", "cantidad": 123456789,
                                                         "operacion": "sumar"})
    verificar(r.status_code == 422 and "123456789" not in r.text, "tampoco devuelve números recibidos",
              f"422 cantidad: {r.text}")
    r = c.post("/api/precios/previsualizar", headers=H, content=b'{"codigos": ["1"], "porcentaje": NaN}',
               )
    verificar(r.status_code == 422, "un NaN no pasa como número", f"NaN: {r.status_code}")

# ===================================================================== #
# Logs: nunca el PIN, la sesión, Authorization ni el token del bot
# ===================================================================== #
import requests  # noqa: E402

from pos_core import panel_celular, telegram_bot  # noqa: E402

config.actualizar_config_dict({"telegram": {"habilitado": "true"}})


def _post_que_falla(url, *a, **k):
    raise requests.ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: {url}")


telegram_bot.requests = type("R", (), {"post": staticmethod(_post_que_falla),
                                       "RequestException": requests.RequestException})()
dashboard_original = panel_celular.resumen_dashboard


def _dashboard_que_explota(*a, **k):
    raise RuntimeError(f"https://api.telegram.org/bot{B.BOT_TOKEN}/sendMessage")


app = api_celular.crear_app()
with B.cliente(app) as c:
    app.state.estado.refrescar()
    token = B.login(c)
    c.get("/api/auth/yo", headers=B.auth(token))
    c.post("/api/stock/movimiento", headers=B.auth(token), json={"codigo": B.CAFE, "cantidad": 1, "operacion": "sumar"})
    c.post("/api/auth/login", json={"pin": "999999"})
    r = c.post("/api/config/telegram/probar", headers=B.auth(token), json={})
    verificar(r.status_code == 200 and B.BOT_TOKEN not in r.text and "BOT-DE-PRUEBA" not in r.text,
              "probar sin internet contesta sin el token del bot", f"probar: {r.text}")
    panel_celular.resumen_dashboard = _dashboard_que_explota
    try:
        r = c.get("/api/dashboard", headers=B.auth(token))
    finally:
        panel_celular.resumen_dashboard = dashboard_original
    verificar(r.status_code == 500 and r.json() == {"detail": "Error inesperado en la PC: quedó anotado en el log.",
                                                    "codigo": "error_interno"},
              "un error inesperado da 500 con texto fijo (el detalle va solo al log)", f"500: {r.status_code} {r.text}")
B.simular_telegram(enviados)
# definir-pin desde la consola (proceso aparte, como en Windows)
proceso = correr_cli(TMP, "definir-pin", pines=f"{B.PIN},{B.PIN}")
verificar(proceso.returncode == 0 and "PIN guardado" in proceso.stdout and B.PIN not in proceso.stdout,
          "definir-pin desde la consola guarda el PIN sin mostrarlo", f"definir-pin: {proceso.returncode} "
                                                                     f"{proceso.stdout} {proceso.stderr[-500:]}")
for h in __import__("logging").getLogger().handlers:
    h.flush()

carpeta_logs = os.path.join(TMP, "logs")
contenidos = {}
for nombre in ("api_celular.log", "api_celular_cli.log", "api_celular_cambios.log"):
    ruta = os.path.join(carpeta_logs, nombre)
    contenidos[nombre] = open(ruta, encoding="utf-8").read() if os.path.exists(ruta) else None
verificar(all(v is not None for v in contenidos.values()), "están los tres logs (servicio, consola y cambios)",
          f"faltan logs: {[k for k, v in contenidos.items() if v is None]}")
todo = "\n".join(v or "" for v in contenidos.values())
encontrados = []
for nombre, patron in (("el PIN", re.escape(B.PIN)), ("un token de sesión", r"v2\.[\w-]{10,}\.[\w-]{10,}"),
                       ("'Bearer ' con algo", r"Bearer \S"), ("el token del bot", r"BOT-DE-PRUEBA")):
    if re.search(patron, todo):
        encontrados.append(nombre)
verificar(not encontrados, "ningún log tiene el PIN, la sesión, 'Bearer …' ni el token del bot",
          f"en los logs apareció: {encontrados}")
verificar("Traceback" in contenidos["api_celular.log"] and "[TAPADO]" in contenidos["api_celular.log"],
          "el error inesperado quedó con su traza en el log, con el token del bot tapado (va en el Formatter)",
          "la traza del 500 no está en el log o no está tapada")
verificar("PEDIDO ip=100.101.102.103" in contenidos["api_celular.log"]
          and "LOGIN FALLIDO" in contenidos["api_celular.log"] and "LOGIN OK" in contenidos["api_celular.log"],
          "el log anota cada pedido y los login (con la IP del celular)", "faltan líneas de pedido o login en el log")
verificar("PIN del celular definido" in (contenidos["api_celular_cli.log"] or "")
          and "PIN del celular definido" not in contenidos["api_celular.log"],
          "lo de la consola va a api_celular_cli.log y nunca al log del servicio",
          "la consola escribió en el log del servicio (o no escribió el suyo)")
cambios = [json.loads(linea) for linea in (contenidos["api_celular_cambios.log"] or "").splitlines() if linea.strip()]
verificar(any(x["accion"] == "stock_movimiento" and x["usuario"] == "dueño" and x["ip"] == B.IP_CELULAR
              for x in cambios),
          "cada escritura queda en api_celular_cambios.log con antes, después, IP y sesión",
          f"cambios: {cambios[-3:]}")
import logging  # noqa: E402
sin_tapar = []
for nombre in ("", "uvicorn", "uvicorn.error", "api_celular.cambios"):
    lg = logging.getLogger(nombre)
    for h in lg.handlers:
        if not isinstance(h.formatter, api_celular.FormatterQueTapa):
            sin_tapar.append(f"{nombre or 'raíz'}: {type(h).__name__}")
    if nombre in ("uvicorn", "uvicorn.error") and not lg.propagate and not lg.handlers:
        sin_tapar.append(f"{nombre}: no propaga y no tiene handler")
verificar(not sin_tapar, "todos los handlers de log usan FormatterQueTapa", f"handlers sin tapar: {sin_tapar}")
registro = logging.LogRecord("x", logging.ERROR, __file__, 1, "falló %s", ("Bearer abc.def",), None)
try:
    raise RuntimeError(f"https://api.telegram.org/bot{B.BOT_TOKEN}/getMe")
except RuntimeError:
    registro.exc_info = sys.exc_info()
texto = api_celular.FormatterQueTapa("%(message)s").format(registro)
verificar("BOT-DE-PRUEBA" not in texto and "abc.def" not in texto and "Traceback" in texto,
          "FormatterQueTapa tapa también adentro de la traza", f"formatter: {texto[-300:]}")

# ===================================================================== #
# Lectura del código: lo que la API no llama nunca (con ast, no con texto)
# ===================================================================== #
IMPORTS_PROHIBIDOS = {"services.remote_api", "services.stock_daemon_windows", "pos_core.respaldo",
                      "pos_core.instancia_unica", "pos_core.excel_import"}
LLAMADAS_PROHIBIDAS = {"init_db", "preparar_base", "aplicar_migraciones", "MonitorAlertas",
                       "revisar_umbrales_y_alertar", "token_remoto", "obtener_config_dict", "crear_producto",
                       "facturar_venta_arca", "crear_oferta", "cancelar_oferta", "quitar_todos_los_umbrales_propios",
                       "db_path", "data_dir"}


def prohibidos_en(codigo: str, nombre: str = "<texto>") -> list:
    encontrados = []
    for nodo in ast.walk(ast.parse(codigo, nombre)):
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                if alias.name in IMPORTS_PROHIBIDOS:
                    encontrados.append(f"import {alias.name}")
        elif isinstance(nodo, ast.ImportFrom):
            modulo = nodo.module or ""
            for alias in nodo.names:
                completo = f"{modulo}.{alias.name}" if modulo else alias.name
                if modulo in IMPORTS_PROHIBIDOS or completo in IMPORTS_PROHIBIDOS:
                    encontrados.append(f"from {modulo} import {alias.name}")
                if alias.name in LLAMADAS_PROHIBIDAS:
                    encontrados.append(f"from {modulo} import {alias.name}")
        elif isinstance(nodo, ast.Call):
            f = nodo.func
            final = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if final in LLAMADAS_PROHIBIDAS:
                encontrados.append(f"llamada a {final} (línea {nodo.lineno})")
    return encontrados


MODULOS = ["services/api_celular.py", "services/api_celular_cli.py", "pos_core/panel_celular.py",
           "pos_core/acceso_celular.py"]
hallados = {}
for rel in MODULOS:
    with open(os.path.join(RAIZ, rel), encoding="utf-8") as f:
        prohibidos = prohibidos_en(f.read(), rel)
    if prohibidos:
        hallados[rel] = prohibidos
verificar(not hallados, "la API no importa ni llama nada que cree o migre la base, mande alertas o toque [remoto]",
          f"llamadas o imports prohibidos: {hallados}")
trampa = ('"""Un comentario que dice respaldo y preparar_base no cuenta."""\n'
          "def f():\n    # db.init_db() en un comentario tampoco\n    texto = 'preparar_base()'\n"
          "    from pos_core import db\n    return db.preparar_base()\n")
inocente = ('"""Docstring: init_db, token_remoto, respaldo."""\n'
            "def f():\n    # init_db()\n    return 'db_path() y servicio_windows.remote_api_contesta'\n")
verificar(prohibidos_en(trampa) == ["llamada a preparar_base (línea 6)"] and prohibidos_en(inocente) == []
          and prohibidos_en("from services import remote_api\n") and prohibidos_en("import pos_core.respaldo\n"),
          "la lectura del código detecta una llamada prohibida metida a propósito y no se confunde con textos",
          f"detector: trampa {prohibidos_en(trampa)}, inocente {prohibidos_en(inocente)}")
with open(os.path.join(RAIZ, "services", "api_celular_autoprueba.py"), encoding="utf-8") as f:
    en_autoprueba = prohibidos_en(f.read())
verificar(set(x.split(" ")[2] for x in en_autoprueba if x.startswith("llamada")) <= {"preparar_base", "crear_producto"},
          "la autoprueba solo usa preparar_base y crear_producto (sobre su carpeta temporal)",
          f"autoprueba: {en_autoprueba}")

# ===================================================================== #
# Verbos de consola
# ===================================================================== #
db.cerrar_conexion()
huella = B.volcado_base(RUTA_DB)
proceso = correr_cli(TMP, "definir-pin", "123456")
verificar(proceso.returncode == 2 and "No escribas el PIN en la línea de comandos" in proceso.stdout
          and B.volcado_base(RUTA_DB) == huella,
          "definir-pin con el PIN como argumento sale con 2 y no toca la base", f"definir-pin 123456: "
                                                                             f"{proceso.returncode} {proceso.stdout}")
VACIA = tempfile.mkdtemp(prefix="sin_base_", dir=RAIZ_TMP)
proceso = correr_cli(VACIA, "definir-pin", pines=f"{B.PIN},{B.PIN}")
verificar(proceso.returncode == 2 and "No encontré la base" in proceso.stdout and os.listdir(VACIA) == [],
          "definir-pin contra una carpeta sin base sale con 2 y no crea NADA",
          f"sin base: {proceso.returncode} {proceso.stdout} {os.listdir(VACIA)}")
lento = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--cli", "definir-pin", "--pausa", "--base", VACIA],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=RAIZ)
time.sleep(1.5)
seguia = lento.poll() is None
salida, _ = lento.communicate("\n", timeout=60)
verificar(seguia and lento.returncode == 2 and "Apretá Enter para cerrar esta ventana." in salida,
          "definir-pin --pausa sin base espera el Enter antes de cerrar (la ventana no desaparece sin avisar)",
          f"--pausa: seguía={seguia} código={lento.returncode} {salida}")
proceso = correr_cli(TMP, "definir-pin", pines="123456,123456,111111,111111,482915,999999")
verificar(proceso.returncode == 1 and "No se guardó ningún PIN" in proceso.stdout,
          "definir-pin con tres intentos malos no guarda nada y sale con 1", f"tres intentos: {proceso.stdout}")
for verbo in ("debug", "restart"):
    proceso = correr_cli(TMP, verbo)
    verificar(proceso.returncode == 2
              and "Ese verbo no existe en ApiCelular. Para desarrollo usá «consola»." in proceso.stdout,
              f"'{verbo}' de pywin32 se rechaza con código 2", f"{verbo}: {proceso.returncode} {proceso.stdout}")
proceso = correr_cli(None, "definir-pin")
verificar(proceso.returncode == 2 and "--base" in proceso.stdout, "sin congelar y sin --base no adivina la carpeta",
          f"sin --base: {proceso.returncode} {proceso.stdout}")

from services import api_celular_autoprueba  # noqa: E402
huella = B.volcado_base(RUTA_DB)
listo, texto = api_celular_autoprueba.autoprueba(carpeta=TMP)
usar_instalacion(TMP)
verificar(not listo and "ya tiene una base" in texto and B.volcado_base(RUTA_DB) == huella,
          "la autoprueba se niega a trabajar sobre una carpeta que ya tiene base", f"autoprueba: {listo} {texto}")

# habilitar / deshabilitar: cambian SOLO [api_celular] habilitado
ruta_ini = os.path.join(TMP, "config.ini")


def secciones(ruta):
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(ruta, encoding="utf-8")
    return {s: dict(cfg[s]) for s in cfg.sections()}


antes = secciones(ruta_ini)
proceso = correr_cli(TMP, "deshabilitar")
despues = secciones(ruta_ini)
esperado = {s: dict(v) for s, v in antes.items()}
esperado["api_celular"]["habilitado"] = "false"
verificar(proceso.returncode == 0 and despues == esperado and config.leer_config_celular()["habilitado"] is False
          and "deja de escuchar" in proceso.stdout,
          "deshabilitar cambia solo [api_celular] habilitado (el resto de config.ini igual)",
          f"deshabilitar: {proceso.returncode} {proceso.stdout} {despues.get('api_celular')}")
proceso = correr_cli(TMP, "habilitar")
verificar(proceso.returncode == 0 and secciones(ruta_ini) == antes and config.leer_config_celular()["habilitado"]
          and "vuelve a escuchar" in proceso.stdout,
          "habilitar la vuelve a prender, y config.ini queda como estaba", f"habilitar: {proceso.stdout}")
for nombre, contenido in (("con BOM", "﻿" + open(ruta_ini, encoding="utf-8").read()),
                          ("mal formado", "esto no es un ini\n")):
    copia = tempfile.mkdtemp(prefix="ini_", dir=RAIZ_TMP)
    B.copiar_instalacion(TMP, copia)
    with open(os.path.join(copia, "config.ini"), "w", encoding="utf-8") as f:
        f.write(contenido)
    with open(os.path.join(copia, "config.ini"), "rb") as f:
        bytes_antes = f.read()
    proceso = correr_cli(copia, "deshabilitar")
    with open(os.path.join(copia, "config.ini"), "rb") as f:
        bytes_despues = f.read()
    verificar(proceso.returncode == 1 and bytes_antes == bytes_despues and "no se tocó nada" in proceso.stdout,
              f"deshabilitar con un config.ini {nombre} sale con 1 y lo deja igual byte a byte",
              f"config.ini {nombre}: {proceso.returncode} {proceso.stdout}")

proceso = correr_cli(TMP, "cerrar-sesiones")
verificar(proceso.returncode == 0 and "volver a ingresar el PIN" in proceso.stdout, "cerrar-sesiones rota la firma",
          f"cerrar-sesiones: {proceso.stdout}")

# diagnostico: nunca una IP en pantalla, ni en la cola del log (que tiene la del celular)
with open(os.path.join(carpeta_logs, "api_celular.log"), "a", encoding="utf-8") as f:
    f.write("2026-10-07 10:00:00,000 INFO PEDIDO ip=100.101.102.103 sesion=ab12 GET /api/productos 200 5ms\n")
    f.write("2026-10-07 10:00:01,000 INFO RECHAZO de red ip=192.168.1.5 GET /api/productos\n")
proceso = correr_cli(TMP, "diagnostico", timeout=300)
ips = re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", proceso.stdout)
verificar(proceso.returncode == 0 and not ips and "PIN del celular" in proceso.stdout
          and "x.x.x.x" in proceso.stdout,
          "diagnostico no muestra ninguna IP (tapa las de la cola del log) y sale con 0",
          f"diagnostico: código {proceso.returncode}, IPs {ips}\n{proceso.stdout[-1500:]}{proceso.stderr[-800:]}")
verificar(B.PIN not in proceso.stdout and "BOT-DE-PRUEBA" not in proceso.stdout
          and "TOKEN-REMOTO-DE-PRUEBA" not in proceso.stdout,
          "diagnostico no muestra el PIN ni tokens", "diagnostico mostró un secreto")

api_celular.cerrar_logs()
db.cerrar_conexion()
db.usar_solo_base_existente(None)
shutil.rmtree(RAIZ_TMP, ignore_errors=True)
print()
if fallos:
    print("=== FALLOS API CELULAR (SEGURIDAD) ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== API CELULAR (SEGURIDAD) OK ===")
