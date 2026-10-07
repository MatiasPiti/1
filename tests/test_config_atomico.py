"""config.ini tiene que sobrevivir a dos programas guardando a la vez.

En la PC del local el MISMO config.ini lo comparten la Caja, el Panel, el
StockService y ahora la API del celular. Con el guardado de antes (abrir
con "w" y escribir), dos escritores a la vez dejaban el archivo ilegible o
perdían claves: en el experimento de la espec, de 160 claves escritas por 4
procesos sobrevivían 13, y se perdía el [remoto] token — el que necesita la
laptop de Leo para conectarse. Y un config.ini ilegible no lo abre ninguna
app: tampoco la Caja.

Esta prueba mira lo que le importa al negocio: que el archivo se pueda leer
SIEMPRE, que no se pierda ninguna clave y que el token quede intacto.
También que el arreglo nunca deje peor que antes (regla 6): si el guardado
atómico no se puede, se guarda como siempre.

Correrla en Windows es obligatorio: ahí os.replace se comporta distinto con
el archivo abierto por otro programa (el antivirus, otra app leyendo).
"""
import configparser
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

# --------------------------------------------------------------------- #
# Modo hijo: un escritor que corre en su propio proceso
# --------------------------------------------------------------------- #
if len(sys.argv) >= 4 and sys.argv[1] == "--escritor":
    _base, _n = sys.argv[2], int(sys.argv[3])
    from pos_core import paths
    paths.set_base_override(_base)
    from pos_core import config as _config
    for _i in range(40):
        _config.actualizar_config_dict({"carga": {f"p{_n}_{_i}": str(_i)}})
    sys.exit(0)

if len(sys.argv) >= 3 and sys.argv[1] == "--corte-de-luz":
    # Simula un corte de luz justo después de escribir el temporal y antes
    # de reemplazar: el proceso muere sin llegar a ningún finally.
    _base = sys.argv[2]
    from pos_core import paths
    paths.set_base_override(_base)
    from pos_core import config as _config
    os.fsync = lambda fd: os._exit(9)
    _config.actualizar_config_dict({"telegram": {"habilitado": "false"}})
    sys.exit(0)

from pos_core import paths

fallos = []


def ok(texto):
    print("OK:", texto)


def falla(texto):
    print("FALLA:", texto)
    fallos.append(texto)


RAIZ_TMP = tempfile.mkdtemp(prefix="config_atomico_")


def nueva_base(contenido=None) -> str:
    base = tempfile.mkdtemp(dir=RAIZ_TMP)
    paths.set_base_override(base)
    if contenido is not None:
        with open(os.path.join(base, "config.ini"), "w", encoding="utf-8") as f:
            f.write(contenido)
    return base


def leer_bytes(ruta: str) -> bytes:
    with open(ruta, "rb") as f:
        return f.read()


def temporales(base: str) -> list:
    return [n for n in os.listdir(base) if n.startswith("config.ini.") and n.endswith(".tmp")]


from pos_core import config

CONFIG_REAL = """[general]
nombre_local = Kiosco de prueba

[remoto]
habilitado = true
puerto = 8765
token = TOKEN-REMOTO-DE-PRUEBA

[telegram]
bot_token = 123456:BOT-DE-PRUEBA
chat_id_default = -987650
habilitado = true
"""

# --------------------------------------------------------------------- #
# 1. Cuatro procesos guardando a la vez
# --------------------------------------------------------------------- #
base = nueva_base(CONFIG_REAL)
ruta = os.path.join(base, "config.ini")
hijos = [subprocess.Popen([sys.executable, os.path.abspath(__file__), "--escritor", base, str(n)])
         for n in range(4)]
lecturas, ilegibles = 0, []
while any(h.poll() is None for h in hijos):
    # Mientras escriben, el archivo se tiene que poder leer SIEMPRE (como lo
    # leería la Caja al abrir en ese mismo instante).
    try:
        config.cargar_config(estricto=True)
        lecturas += 1
    except config.ConfigIlegibleError as e:
        ilegibles.append(str(e))
    time.sleep(0.002)
codigos = [h.wait(120) for h in hijos]
if any(codigos):
    falla(f"algún escritor terminó con error: {codigos}")
if ilegibles:
    falla(f"config.ini quedó ilegible {len(ilegibles)} veces mientras 4 programas guardaban "
          f"(de {lecturas + len(ilegibles)} lecturas): {ilegibles[:2]}")
else:
    ok(f"config.ini se pudo leer las {lecturas} veces que se miró mientras 4 programas guardaban")
final = config.cargar_config(estricto=True)
claves = set(final["carga"].keys()) if final.has_section("carga") else set()
esperadas = {f"p{n}_{i}" for n in range(4) for i in range(40)}
if claves != esperadas:
    falla(f"se perdieron claves guardadas a la vez: sobrevivieron {len(claves & esperadas)} de 160")
else:
    ok("sobrevivieron las 160 claves que guardaron 4 programas a la vez")
if final.get("remoto", "token", fallback="") != "TOKEN-REMOTO-DE-PRUEBA":
    falla("el [remoto] token cambió o se perdió: la laptop de Leo se quedaría sin panel")
else:
    ok("el [remoto] token quedó intacto")
if final.get("telegram", "bot_token", fallback="") != "123456:BOT-DE-PRUEBA":
    falla("se perdió el token del bot de Telegram")
if temporales(base):
    falla(f"quedaron temporales sueltos: {temporales(base)}")

# --------------------------------------------------------------------- #
# 2. Una falla al armar el archivo no toca el original
# --------------------------------------------------------------------- #
base = nueva_base(CONFIG_REAL)
ruta = os.path.join(base, "config.ini")
antes = leer_bytes(ruta)


class EscritorQueFalla(configparser.ConfigParser):
    def write(self, f, space_around_delimiters=True):
        f.write("[general]\nnombre_local = a medio escr")
        raise RuntimeError("falla a mitad de la escritura")


roto = EscritorQueFalla()
roto.read_string(CONFIG_REAL)
try:
    config.guardar_config(roto)
    falla("un error a mitad de cfg.write no salió como excepción")
except RuntimeError:
    pass
if leer_bytes(ruta) != antes:
    falla("un error a mitad de cfg.write cambió el config.ini original")
else:
    ok("un error a mitad de cfg.write deja el config.ini original byte a byte")
if temporales(base):
    falla(f"un error a mitad de cfg.write dejó temporales: {temporales(base)}")

# Y un corte de luz de verdad (el proceso muere después de escribir el
# temporal y antes de reemplazar): el original queda entero.
proceso = subprocess.run([sys.executable, os.path.abspath(__file__), "--corte-de-luz", base], timeout=60)
if proceso.returncode != 9:
    falla(f"la simulación del corte de luz no cortó donde debía (código {proceso.returncode})")
elif leer_bytes(ruta) != antes:
    falla("un corte de luz a mitad del guardado cambió el config.ini")
else:
    try:
        config.cargar_config(estricto=True)
        ok("un corte de luz a mitad del guardado deja el config.ini viejo entero y legible "
           f"(queda un temporal suelto que no molesta: {len(temporales(base))})")
    except config.ConfigIlegibleError as e:
        falla(f"después del corte de luz config.ini no se puede leer: {e}")
for t in temporales(base):
    os.remove(os.path.join(base, t))

# --------------------------------------------------------------------- #
# 3. os.replace que falla (Windows: el antivirus o un lector lo tienen abierto)
# --------------------------------------------------------------------- #
replace_original = os.replace


def replace_que_falla(veces):
    estado = {"quedan": veces, "llamadas": 0}

    def _replace(origen, destino):
        estado["llamadas"] += 1
        if estado["quedan"] is None or estado["quedan"] > 0:
            if estado["quedan"] is not None:
                estado["quedan"] -= 1
            raise PermissionError(13, "El proceso no tiene acceso al archivo porque está siendo utilizado")
        return replace_original(origen, destino)
    return _replace, estado


base = nueva_base(CONFIG_REAL)
ruta = os.path.join(base, "config.ini")
reemplazo, estado = replace_que_falla(2)
sleep_original = time.sleep
os.replace, time.sleep = reemplazo, (lambda s: None)
try:
    config.actualizar_config_dict({"telegram": {"habilitado": "false"}})
finally:
    os.replace, time.sleep = replace_original, sleep_original
leido = config.cargar_config(estricto=True)
if leido.get("telegram", "habilitado") != "false" or leido.get("remoto", "token") != "TOKEN-REMOTO-DE-PRUEBA":
    falla("con os.replace fallando 2 veces no se guardó bien")
elif estado["llamadas"] != 3:
    falla(f"con os.replace fallando 2 veces se esperaban 3 intentos y hubo {estado['llamadas']}")
else:
    ok("os.replace que falla 2 veces con PermissionError: se reintenta y guarda igual")
if temporales(base):
    falla(f"quedaron temporales: {temporales(base)}")

reemplazo, estado = replace_que_falla(None)
os.replace, time.sleep = reemplazo, (lambda s: None)
try:
    config.actualizar_config_dict({"telegram": {"habilitado": "true"}, "nueva": {"clave": "valor"}})
finally:
    os.replace, time.sleep = replace_original, sleep_original
leido = config.cargar_config(estricto=True)
if (leido.get("telegram", "habilitado") != "true" or leido.get("nueva", "clave", fallback="") != "valor"
        or leido.get("remoto", "token") != "TOKEN-REMOTO-DE-PRUEBA"):
    falla("con os.replace fallando siempre, el último recurso (escritura directa) no guardó bien")
else:
    ok(f"os.replace que falla siempre ({estado['llamadas']} intentos): cae a la escritura de siempre y guarda bien")
if temporales(base):
    falla(f"con os.replace fallando siempre quedaron temporales sueltos: {temporales(base)}")
else:
    ok("y no quedan temporales sueltos")

# Una carpeta donde no se puede crear el temporal: se guarda como antes.
mkstemp_original = tempfile.mkstemp


def mkstemp_que_falla(*a, **k):
    raise PermissionError(13, "sin permiso para crear archivos en la carpeta")


tempfile.mkstemp = mkstemp_que_falla
try:
    config.actualizar_config_dict({"telegram": {"habilitado": "false"}})
finally:
    tempfile.mkstemp = mkstemp_original
if config.cargar_config(estricto=True).get("telegram", "habilitado") != "false":
    falla("sin poder crear el temporal no se guardó (tiene que guardar como antes)")
else:
    ok("sin poder crear el temporal en la carpeta, guarda como antes")

# --------------------------------------------------------------------- #
# 4. El candado entre procesos nunca bloquea
# --------------------------------------------------------------------- #
base = nueva_base(CONFIG_REAL)
if os.name != "nt":
    import fcntl
    tomado = open(os.path.join(base, "config.ini.lock"), "a+b")
    fcntl.flock(tomado.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    inicio = time.monotonic()
    config.actualizar_config_dict({"telegram": {"habilitado": "false"}})
    demora = time.monotonic() - inicio
    fcntl.flock(tomado.fileno(), fcntl.LOCK_UN)
    tomado.close()
    if config.cargar_config(estricto=True).get("telegram", "habilitado") != "false":
        falla("con el candado trabado por otro, no se guardó")
    elif demora > 5:
        falla(f"con el candado trabado por otro, guardar tardó {demora:.1f} s (tiene que seguir a los 2 s)")
    else:
        ok(f"con el candado trabado por otro programa, guarda igual después de esperar {demora:.1f} s")
else:
    import msvcrt
    tomado = open(os.path.join(base, "config.ini.lock"), "a+b")
    tomado.seek(0)
    msvcrt.locking(tomado.fileno(), msvcrt.LK_NBLCK, 1)
    resultado = {}

    def _guardar():
        inicio_ = time.monotonic()
        config.actualizar_config_dict({"telegram": {"habilitado": "false"}})
        resultado["demora"] = time.monotonic() - inicio_

    hilo = threading.Thread(target=_guardar)
    hilo.start()
    hilo.join(10)
    tomado.seek(0)
    msvcrt.locking(tomado.fileno(), msvcrt.LK_UNLCK, 1)
    tomado.close()
    if "demora" not in resultado or resultado["demora"] > 5:
        falla("con el candado trabado por otro, guardar se colgó")
    else:
        ok(f"con el candado trabado, guarda igual después de esperar {resultado['demora']:.1f} s")

# token_remoto bajo candado: lo genera una vez y no lo cambia
base = nueva_base("[general]\nnombre_local = x\n")
t1 = config.token_remoto()
t2 = config.token_remoto()
if not t1 or t1 != t2:
    falla("token_remoto() no devuelve siempre el mismo token")
else:
    ok("token_remoto() genera el token una sola vez")

# --------------------------------------------------------------------- #
# 5. cargar_config(estricto=True) y el comportamiento de siempre
# --------------------------------------------------------------------- #


def cargar_como_antes():
    """cargar_config() tal como estaba antes de este cambio."""
    cfg = configparser.ConfigParser()
    cfg.read_dict(config._DEFAULTS)
    cfg.read(config.config_path(), encoding="utf-8")
    return cfg


def como_dict(cfg):
    return {s: dict(cfg[s]) for s in cfg.sections()}


casos_ilegibles = {
    "inexistente": None,
    "vacío": "",
    "solo espacios": "   \n\n  ",
    "mal formado": "esto no es un ini\nclave = valor\n",
    "con BOM (Bloc de notas)": "﻿[general]\nnombre_local = Kiosco\n",
}
for nombre, contenido in casos_ilegibles.items():
    base = nueva_base(contenido)
    ruta = os.path.join(base, "config.ini")
    antes = leer_bytes(ruta) if os.path.exists(ruta) else None
    try:
        config.cargar_config(estricto=True)
        falla(f"cargar_config(estricto=True) con un config.ini {nombre} no lanzó ConfigIlegibleError")
    except config.ConfigIlegibleError:
        ok(f"cargar_config(estricto=True) con un config.ini {nombre}: ConfigIlegibleError")
    # Sin estricto: EXACTAMENTE como antes (mismos valores, o la misma excepción).
    try:
        esperado = ("valor", como_dict(cargar_como_antes()))
    except Exception as e:
        esperado = ("error", type(e))
    try:
        obtenido = ("valor", como_dict(config.cargar_config()))
    except Exception as e:
        obtenido = ("error", type(e))
    if obtenido != esperado:
        falla(f"cargar_config() sin estricto con un config.ini {nombre} cambió de comportamiento: "
              f"antes {esperado[0]} {esperado[1] if esperado[0] == 'error' else ''}, ahora {obtenido[0]}")
    else:
        ok(f"cargar_config() sin estricto con un config.ini {nombre} se comporta igual que antes ({obtenido[0]})")
    if (leer_bytes(ruta) if os.path.exists(ruta) else None) != antes:
        falla(f"leer un config.ini {nombre} lo modificó")

base = nueva_base(CONFIG_REAL)
if como_dict(config.cargar_config(estricto=True)) != como_dict(cargar_como_antes()):
    falla("con un config.ini sano, cargar_config(estricto=True) no da lo mismo que antes")
else:
    ok("con un config.ini sano, estricto y no estricto dan lo mismo que antes")

# --------------------------------------------------------------------- #
# 6. leer_config_celular: la regla del primer token, igual que el watchdog
# --------------------------------------------------------------------- #
if "api_celular" in config._DEFAULTS:
    falla("[api_celular] está en config._DEFAULTS: cualquier guardado de otra app escribiría habilitado = false")
else:
    ok("[api_celular] no está en los valores por defecto")

# Los 13 casos de exp-diseno-correccion/k5_regla_ini.py, con lo que tiene que
# leer (los mismos que lee el watchdog: cero diferencias en k5).
CASOS_K5 = [
    ("simple", "[api_celular]\nhabilitado = true\npuerto = 8766\n", True, 8766),
    ("comentario_misma_linea",
     "[api_celular]\nhabilitado = true     ; la escribe el Instalador\npuerto = 8766   ; falta = 8766\n", True, 8766),
    ("mayusculas_valor", "[api_celular]\nhabilitado = TRUE\npuerto = 8770\n", True, 8770),
    ("clave_mayuscula", "[api_celular]\nHabilitado = si\n", True, 8766),
    ("sí_con_acento", "[api_celular]\nhabilitado = sí\n", True, 8766),
    ("false", "[api_celular]\nhabilitado = false\npuerto = 8766\n", False, 8766),
    ("sin_seccion", "[remoto]\nhabilitado = true\n", False, 8766),
    ("seccion_mayus", "[API_CELULAR]\nhabilitado = true\n", False, 8766),
    ("dos_puntos", "[api_celular]\nhabilitado: 1\npuerto: 9000\n", True, 9000),
    ("puerto_basura", "[api_celular]\nhabilitado = true\npuerto = 87x6\n", True, 8766),
    ("puerto_fuera_rango", "[api_celular]\nhabilitado = true\npuerto = 99999\n", True, 8766),
    ("pegado_punto_coma", "[api_celular]\nhabilitado = true;x\npuerto = 8766;x\n", False, 8766),
    ("otra_seccion_despues", "[api_celular]\nhabilitado = false\n[telegram]\nhabilitado = true\n", False, 8766),
]
diferencias = 0
for nombre, texto, hab, puerto in CASOS_K5:
    for variante, contenido in (("", texto), (" (con BOM)", "﻿" + texto)):
        base = nueva_base(contenido)
        r = config.leer_config_celular()
        if (r["habilitado"], r["puerto"], r["leido"]) != (hab, puerto, True):
            diferencias += 1
            falla(f"leer_config_celular, caso {nombre}{variante}: dio habilitado={r['habilitado']} "
                  f"puerto={r['puerto']} leido={r['leido']}; se esperaba {hab}/{puerto}")
if not diferencias:
    ok(f"leer_config_celular da lo esperado en los {len(CASOS_K5)} casos de k5, con y sin BOM")

# [remoto] puerto con la misma regla
for texto, esperado in (("[remoto]\npuerto = 8770 ; x\n", 8770), ("[remoto]\npuerto = abc\n", 8765),
                        ("[general]\nnombre_local = x\n", 8765)):
    base = nueva_base(texto)
    r = config.leer_config_celular()
    if r["puerto_remoto"] != esperado:
        falla(f"[remoto] puerto con {texto!r} dio {r['puerto_remoto']}, se esperaba {esperado}")
ok("[remoto] puerto se lee con la misma regla (8765 si falta o es basura)")

# Archivo ausente o ilegible: leido False y valores de "apagada", sin lanzar.
for nombre, contenido in (("ausente", None), ("mal formado", "no es un ini\n"), ("vacío", ""),
                          ("binario", None)):
    base = nueva_base(contenido)
    if nombre == "binario":
        with open(os.path.join(base, "config.ini"), "wb") as f:
            f.write(b"\xff\xfe\x00[api_celular]\x00\x81\x82")
    try:
        r = config.leer_config_celular()
    except Exception as e:
        falla(f"leer_config_celular con config.ini {nombre} lanzó {type(e).__name__}")
        continue
    esperado_vacio = nombre == "vacío"
    if (r["habilitado"], r["puerto"], r["puerto_remoto"]) != (False, 8766, 8765):
        falla(f"leer_config_celular con config.ini {nombre} no dio 'apagada': {r}")
    elif r["leido"] != esperado_vacio:
        falla(f"leer_config_celular con config.ini {nombre} dio leido={r['leido']}")
    else:
        ok(f"leer_config_celular con config.ini {nombre}: apagada, leido={r['leido']}, sin lanzar")

# Nunca usa _DEFAULTS: aunque los defaults dijeran "prendida en el 9999", un
# archivo sin [api_celular] es "apagada".
defaults_originales = config._DEFAULTS
config._DEFAULTS = dict(defaults_originales, api_celular={"habilitado": "true", "puerto": "9999"},
                        remoto={"habilitado": "true", "puerto": "9998", "token": ""})
try:
    base = nueva_base("[general]\nnombre_local = x\n")
    r = config.leer_config_celular()
finally:
    config._DEFAULTS = defaults_originales
if (r["habilitado"], r["puerto"], r["puerto_remoto"]) != (False, 8766, 8765):
    falla(f"leer_config_celular usó los valores por defecto de config.py: {r}")
else:
    ok("leer_config_celular lee solo el archivo real, nunca los valores por defecto")

# Con ruta explícita (la usa el Actualizador sobre la carpeta destino)
base = nueva_base(None)
otra = tempfile.mkdtemp(dir=RAIZ_TMP)
with open(os.path.join(otra, "config.ini"), "w", encoding="utf-8") as f:
    f.write("[api_celular]\nhabilitado = true\npuerto = 8800\n")
r = config.leer_config_celular(os.path.join(otra, "config.ini"))
if (r["habilitado"], r["puerto"]) != (True, 8800):
    falla(f"leer_config_celular(ruta) no leyó la ruta pedida: {r}")
else:
    ok("leer_config_celular(ruta) lee el archivo que se le pasa")

shutil.rmtree(RAIZ_TMP, ignore_errors=True)
print()
if fallos:
    print("=== FALLOS CONFIG ATÓMICO ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== CONFIG ATÓMICO OK ===")
