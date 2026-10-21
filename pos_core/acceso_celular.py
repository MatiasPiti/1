"""Acceso de la app del celular: PIN del dueño, secreto de la API y tokens.

Lo usan la API del celular (services/api_celular.py), que solo LEE el
secreto, y sus verbos de consola (services/api_celular_cli.py), que son los
únicos que lo crean o lo cambian.

Por qué así:

- El PIN vive en la tabla Usuarios que ya existe (fila 'dueño', rol DUEÑO),
  hasheado con PBKDF2-SHA256 de 600.000 vueltas, una sal por PIN y una
  PIMIENTA que vive FUERA de la base (api_celular/secreto.json). Una copia
  de la base —los respaldos diarios, o una que alguien se lleve— no alcanza
  para sacar el PIN por fuerza bruta, y Leo puede repetir ese PIN en otro
  lado.
- Todo hash que no tenga el formato pbkdf2 NUNCA autentica. En producción
  hay una fila 'dueño' con sha256('1234') que dejó el setup viejo: queda
  "anulada" hasta que Leo define un PIN nuevo en la PC, y definir-pin la pisa.
- El secreto no se regenera solo jamás: si está roto se dice y no se toca.
  Regenerarlo en silencio dejaría el PIN inservible sin que nadie lo note
  hasta que Leo no pueda entrar.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time

from pos_core import paths
from pos_core.db import get_connection, transaction

USUARIO_DUENO = "dueño"
ROL_DUENO = "DUEÑO"
ITERACIONES = 600_000
PREFIJO = "pbkdf2_sha256"
DURACION_TOKEN_S = 30 * 24 * 3600
PREFIJO_TOKEN = "v2."

# PINs que pasan las reglas de forma pero cualquiera prueba primero.
_PINES_OBVIOS = {
    "121212", "112233", "123123", "100200", "159753", "147258", "258369", "369258",
    "147852", "159357", "951753", "741852", "963852", "852456", "456852", "102030",
    "123321", "654456", "112211", "101010", "202020", "696969", "131313", "520520",
    "789456", "456789", "987654321", "147369", "000111", "111000",
}


class SecretoIlegibleError(Exception):
    """api_celular/secreto.json existe pero no se puede leer. No se regenera."""


# --------------------------------------------------------------------- #
# PIN
# --------------------------------------------------------------------- #

def validar_pin_nuevo(pin: str) -> None:
    """ValueError (en castellano, para mostrar tal cual) si el PIN no sirve."""
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9]{6,12}", pin):
        raise ValueError("El PIN tiene que tener de 6 a 12 números (solo números).")
    if len(set(pin)) == 1:
        raise ValueError("El PIN no puede ser el mismo número repetido.")
    pasos = {(int(b) - int(a)) % 10 for a, b in zip(pin, pin[1:])}
    if pasos == {1} or pasos == {9}:
        raise ValueError("El PIN no puede ser una escalera (como 123456 o 654321).")
    if pin.startswith("1234"):
        raise ValueError("El PIN no puede empezar con 1234: ese quedó quemado.")
    for largo in (2, 3):
        if len(pin) % largo == 0 and pin == pin[:largo] * (len(pin) // largo):
            raise ValueError("El PIN no puede ser un mismo grupo de números repetido (como 121212).")
    if pin in _PINES_OBVIOS:
        raise ValueError("Ese PIN es de los que se prueban primero: elegí otro.")


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode("ascii")


def _unb64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _idp(secreto: dict) -> str:
    """Identificador corto de la pimienta: dice con qué secreto se hasheó."""
    return hashlib.sha256(bytes.fromhex(secreto["pimienta"])).hexdigest()[:8]


def _derivar(pin: str, pimienta_hex: str, sal: bytes, iteraciones: int) -> bytes:
    con_pimienta = hmac.new(bytes.fromhex(pimienta_hex), pin.encode("utf-8"), hashlib.sha256).digest()
    return hashlib.pbkdf2_hmac("sha256", con_pimienta, sal, iteraciones, 32)


def hashear_pin(pin: str, secreto: dict) -> str:
    sal = os.urandom(16)
    dk = _derivar(pin, secreto["pimienta"], sal, ITERACIONES)
    return f"{PREFIJO}${ITERACIONES}${_idp(secreto)}${_b64(sal)}${_b64(dk)}"


def _partes_hash(pin_hash: str):
    """(iteraciones, idp, sal, dk) o None si no tiene el formato vigente."""
    try:
        prefijo, iteraciones, idp, sal, dk = pin_hash.split("$")
        if prefijo != PREFIJO:
            return None
        return int(iteraciones), idp, _unb64(sal), _unb64(dk)
    except (ValueError, AttributeError):
        return None


def _fila_usuario(nombre: str):
    return get_connection().execute(
        "SELECT nombre, pin_hash, rol, activo FROM Usuarios WHERE nombre = ?", (nombre,)
    ).fetchone()


def estado_pin() -> str:
    """'definido' | 'no_definido' | 'anulado' | 'secreto_perdido' |
    'secreto_ilegible' | 'base_no_disponible'. Nunca crea nada."""
    try:
        fila = _fila_usuario(USUARIO_DUENO)
    except sqlite3.DatabaseError:
        # Con la base en modo "solo existente", si stock.db no está la
        # lectura falla en vez de crear un archivo vacío.
        return "base_no_disponible"
    if fila is None or not fila["activo"] or fila["rol"] != ROL_DUENO:
        return "no_definido"
    partes = _partes_hash(fila["pin_hash"] or "")
    if partes is None:
        return "anulado"
    try:
        secreto = leer_secreto()
    except SecretoIlegibleError:
        return "secreto_ilegible"
    if secreto is None or partes[1] != _idp(secreto):
        return "secreto_perdido"
    return "definido"


def verificar_pin(pin: str):
    """El usuario si el PIN coincide; None si no. Con un estado distinto de
    'definido' devuelve None SIN calcular nada (quien llama contesta 503)."""
    if estado_pin() != "definido":
        return None
    fila = _fila_usuario(USUARIO_DUENO)
    iteraciones, _, sal, dk = _partes_hash(fila["pin_hash"])
    calculado = _derivar(str(pin), leer_secreto()["pimienta"], sal, iteraciones)
    return USUARIO_DUENO if hmac.compare_digest(calculado, dk) else None


def definir_pin_dueno(pin: str) -> None:
    """Crea o pisa la fila 'dueño' con el PIN nuevo (y pisa el hash viejo)."""
    validar_pin_nuevo(pin)
    secreto = asegurar_secreto()
    pin_hash = hashear_pin(pin, secreto)
    with transaction() as conn:
        conn.execute(
            """INSERT INTO Usuarios (nombre, pin_hash, rol, activo) VALUES (?, ?, ?, 1)
               ON CONFLICT(nombre) DO UPDATE SET pin_hash = excluded.pin_hash,
                                                 rol = excluded.rol, activo = 1""",
            (USUARIO_DUENO, pin_hash, ROL_DUENO),
        )


def autor(usuario: str) -> str:
    """Cómo queda firmado en la base todo lo que se escribe desde el celular."""
    return f"{usuario} (celular)"


# --------------------------------------------------------------------- #
# Secreto (api_celular/secreto.json)
# --------------------------------------------------------------------- #

_cache_secreto = {"clave": None, "valor": None}
_lock_cache = threading.Lock()


def ruta_secreto() -> str:
    # Fuera de config.ini (la API remota expone config.ini entero) y fuera
    # de la carpeta del programa (el Actualizador la reemplaza entera).
    return os.path.join(paths.get_base_path(), "api_celular", "secreto.json")


def _validar_secreto(datos) -> dict:
    hex64 = re.compile(r"[0-9a-f]{64}")
    if (not isinstance(datos, dict) or datos.get("version") != 1
            or not isinstance(datos.get("firma"), str) or not hex64.fullmatch(datos["firma"])
            or not isinstance(datos.get("pimienta"), str) or not hex64.fullmatch(datos["pimienta"])):
        raise SecretoIlegibleError("secreto.json no tiene el formato esperado")
    return {"version": 1, "firma": datos["firma"], "pimienta": datos["pimienta"]}


# En Windows, mirar o abrir secreto.json justo mientras otro proceso lo crea
# (os.rename) o lo reemplaza (os.replace de cerrar-sesiones) da
# PermissionError por un instante. El archivo nunca está a medio escribir
# (se mueve entero), así que no es "ilegible": se reintenta. Lo agarró el CI
# en Windows; en Linux no pasa. Mismo caso que config.ini (config._leer_texto).
_REINTENTOS_LECTURA = 40
_ESPERA_LECTURA_S = 0.025


def _reintentando(funcion):
    for intento in range(_REINTENTOS_LECTURA):
        try:
            return funcion()
        except PermissionError:
            if intento == _REINTENTOS_LECTURA - 1:
                raise
            time.sleep(_ESPERA_LECTURA_S)


def _leer_json(ruta: str):
    with open(ruta, "r", encoding="utf-8") as f:
        return json.load(f)


def leer_secreto():
    """El secreto, o None si no existe. SecretoIlegibleError si está roto.

    Se relee solo si el archivo cambió (fecha, tamaño o archivo distinto):
    así cerrar-sesiones, que lo reemplaza, se nota sin reiniciar el servicio.
    """
    ruta = ruta_secreto()
    try:
        st = _reintentando(lambda: os.stat(ruta))
    except FileNotFoundError:
        return None
    except OSError as e:
        raise SecretoIlegibleError(f"no se pudo mirar secreto.json: {type(e).__name__}") from e
    clave = (ruta, st.st_mtime_ns, st.st_size, getattr(st, "st_ino", 0))
    with _lock_cache:
        if _cache_secreto["clave"] == clave:
            return dict(_cache_secreto["valor"])
    try:
        valor = _validar_secreto(_reintentando(lambda: _leer_json(ruta)))
    except FileNotFoundError:
        return None
    except SecretoIlegibleError:
        raise
    except (OSError, ValueError) as e:
        raise SecretoIlegibleError(f"secreto.json ilegible: {type(e).__name__}") from e
    with _lock_cache:
        _cache_secreto["clave"], _cache_secreto["valor"] = clave, valor
    return dict(valor)


def _escribir_temporal(carpeta: str, datos: dict) -> str:
    fd, tmp = tempfile.mkstemp(dir=carpeta, prefix="secreto.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return tmp


def asegurar_secreto() -> dict:
    """SOLO la CLI. Devuelve el secreto, creándolo si falta.

    Si existe y está roto: SecretoIlegibleError, sin pisarlo. Si falta, se
    escribe ENTERO en un temporal de la misma carpeta y se mueve a su lugar
    SIN PISAR (os.rename en Windows y os.link en POSIX fallan si el destino
    ya existe): si dos definir-pin corren a la vez (el botón del Actualizador
    y el acceso directo), el segundo usa el del primero en vez de cambiarle
    la pimienta con la que el primero ya hasheó. Y el servicio nunca puede
    leer un archivo a medio escribir.
    """
    existente = leer_secreto()
    if existente is not None:
        return existente
    ruta = ruta_secreto()
    carpeta = os.path.dirname(ruta)
    os.makedirs(carpeta, exist_ok=True)
    tmp = _escribir_temporal(carpeta, {"version": 1, "firma": secrets.token_hex(32),
                                       "pimienta": secrets.token_hex(32)})
    try:
        if os.name == "nt":
            os.rename(tmp, ruta)
            tmp = None
        else:
            os.link(tmp, ruta)
    except FileExistsError:
        pass   # otro lo creó mientras tanto: vale el suyo
    finally:
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass
    secreto = leer_secreto()
    if secreto is None:
        raise SecretoIlegibleError("no se pudo crear secreto.json")
    return secreto


def rotar_firma() -> None:
    """SOLO la CLI (cerrar-sesiones): firma nueva, MISMA pimienta (el PIN sigue
    valiendo) y todos los tokens emitidos dejan de servir. Acá reemplazar es
    justo lo que se quiere, así que va con os.replace."""
    secreto = leer_secreto()
    if secreto is None:
        raise FileNotFoundError(ruta_secreto())
    secreto["firma"] = secrets.token_hex(32)
    ruta = ruta_secreto()
    tmp = _escribir_temporal(os.path.dirname(ruta), secreto)
    try:
        for intento in range(20):
            try:
                os.replace(tmp, ruta)
                tmp = None
                return
            except PermissionError:
                if intento == 19:
                    raise
                time.sleep(0.05 * (intento + 1))
    finally:
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass


# --------------------------------------------------------------------- #
# Tokens de sesión
# --------------------------------------------------------------------- #

def _ahora() -> int:
    return int(time.time())


def _huella(firma_hex: str, pin_hash: str) -> str:
    # Cambiar el PIN cambia la huella: todos los celulares vuelven a pedirlo.
    return hmac.new(bytes.fromhex(firma_hex), pin_hash.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def _firmar(firma_hex: str, texto: str) -> str:
    return _b64(hmac.new(bytes.fromhex(firma_hex), texto.encode("ascii"), hashlib.sha256).digest())


def emitir_token(usuario: str) -> dict:
    """{"token", "expira" (epoch s), "sid"} para un usuario con PIN definido."""
    secreto = leer_secreto()
    fila = _fila_usuario(usuario)
    if secreto is None or fila is None or _partes_hash(fila["pin_hash"] or "") is None:
        raise ValueError("No se puede emitir una sesión sin PIN definido")
    ahora = _ahora()
    sid = secrets.token_hex(4)
    datos = {"v": 2, "u": usuario, "sid": sid, "iat": ahora, "exp": ahora + DURACION_TOKEN_S,
             "h": _huella(secreto["firma"], fila["pin_hash"])}
    cuerpo = PREFIJO_TOKEN + _b64(json.dumps(datos, separators=(",", ":")).encode("utf-8"))
    return {"token": f"{cuerpo}.{_firmar(secreto['firma'], cuerpo)}", "expira": datos["exp"], "sid": sid}


def validar_token(token: str):
    """{"usuario", "sid"} o None. Se revalida contra Usuarios en CADA pedido:
    cambiar el PIN, desactivar el usuario o correr cerrar-sesiones deja
    afuera a todos los celulares al instante, sin reiniciar nada.
    SecretoIlegibleError sale para afuera (es un 503, no un 401)."""
    if not isinstance(token, str) or not token.startswith(PREFIJO_TOKEN):
        return None
    secreto = leer_secreto()
    if secreto is None:
        return None
    try:
        cuerpo, firma = token.rsplit(".", 1)
        if not hmac.compare_digest(firma, _firmar(secreto["firma"], cuerpo)):
            return None
        datos = json.loads(_unb64(cuerpo[len(PREFIJO_TOKEN):]))
        if datos.get("v") != 2 or int(datos["exp"]) <= _ahora():
            return None
        usuario, sid, huella = datos["u"], datos["sid"], datos["h"]
        if not all(isinstance(x, str) for x in (usuario, sid, huella)):
            return None
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None
    fila = _fila_usuario(usuario)
    if fila is None or not fila["activo"] or fila["rol"] != ROL_DUENO:
        return None
    partes = _partes_hash(fila["pin_hash"] or "")
    if partes is None or partes[1] != _idp(secreto):
        return None
    if not hmac.compare_digest(huella, _huella(secreto["firma"], fila["pin_hash"])):
        return None
    return {"usuario": usuario, "sid": sid}
