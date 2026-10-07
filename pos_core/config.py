"""Lectura/escritura de config.ini junto al ejecutable (portable).

En la PC del local el mismo config.ini lo comparten la Caja, el Panel, el
StockService y la API del celular. Por eso guardarlo es atómico (un
temporal en la misma carpeta + os.replace) y las funciones que leen y
reescriben van bajo un candado entre procesos: con el guardado de antes,
dos programas guardando a la vez dejaban el archivo ilegible o perdían el
[remoto] token (verificado: de 160 claves escritas a la vez sobrevivían 13).
"""

import configparser
import io
import os
import re
import secrets
import tempfile
import time
from contextlib import contextmanager

from pos_core.paths import config_path

_DEFAULTS = {
    "telegram": {"bot_token": "", "chat_id_default": "", "habilitado": "false"},
    # nombre_local es lo que se imprime como encabezado de cada ticket.
    "general": {"nombre_local": "El Galpón Del Nono", "modo": "MAESTRO"},
    "impresora": {"nombre": ""},  # vacío = usar la impresora predeterminada de Windows
    "arca": {
        "habilitado": "false",
        "ambiente": "homologacion",       # homologacion | produccion
        "cuit": "",
        "punto_venta": "",
        "tipo_comprobante": "B",          # B (Resp. Inscripto a Consumidor Final) | C (Monotributista)
        "certificado_path": "",           # .crt/.pem del certificado digital emitido por ARCA
        "clave_privada_path": "",         # .key privada del mismo par (NUNCA se sube a ningún lado)
    },
    "remoto": {
        "habilitado": "false",
        "puerto": "8765",
        "token": "",  # se autogenera la primera vez que hace falta (ver token_remoto())
    },
    "conexion_remota": {
        # Configuración del lado del Dueño Remoto (apps/dueno_remoto): a
        # qué URL de la PC del local conectarse y con qué token — se
        # copian de la sección [remoto] del config.ini del Maestro.
        "url": "",
        "token": "",
    },
}


class ConfigIlegibleError(Exception):
    """config.ini no existe, está vacío o no se puede leer.

    La lanza solo cargar_config(estricto=True), que es la que usa quien va
    a REESCRIBIR el archivo: si lo leyera "como siempre" (defaults + lo que
    se pudo leer) y guardara, pisaría el config real del cliente —con el
    token de [remoto] y el de Telegram— con valores por defecto.
    """


def cargar_config(estricto: bool = False) -> configparser.ConfigParser:
    """La config de la app: los valores por defecto pisados por config.ini.

    estricto=False es EXACTAMENTE el comportamiento de siempre (si el
    archivo no está quedan los defaults; uno mal formado lanza el error de
    configparser). estricto=True lanza ConfigIlegibleError si el archivo no
    existe, está vacío, no se puede abrir o configparser lo rechaza.
    """
    cfg = configparser.ConfigParser()
    cfg.read_dict(_DEFAULTS)
    if not estricto:
        cfg.read(config_path(), encoding="utf-8")
        return cfg

    ruta = config_path()
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            texto = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigIlegibleError(f"No se pudo abrir {ruta}: {type(e).__name__}") from e
    if not texto.strip():
        raise ConfigIlegibleError(f"{ruta} está vacío")
    try:
        cfg.read_string(texto, source=ruta)
    except configparser.Error as e:
        # Incluye el BOM de un Bloc de notas: configparser no lo saca y
        # rechaza el archivo entero (MissingSectionHeaderError).
        raise ConfigIlegibleError(f"{ruta} está mal formado: {type(e).__name__}") from e
    return cfg


def _guardar_directo(texto: str, ruta: str) -> None:
    """El guardado de siempre. Queda como último recurso: nunca peor que antes."""
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(texto)


def guardar_config(cfg: configparser.ConfigParser) -> None:
    """Guarda de forma atómica: o queda el archivo viejo entero, o el nuevo.

    Se escribe un temporal en la MISMA carpeta (mismo disco, así os.replace
    es atómico), con fsync (sin eso un corte de luz puede dejar 0 bytes) y
    después se reemplaza. En Windows un lector o el antivirus con el archivo
    abierto hacen fallar el replace: se reintenta. Si nada de eso se puede,
    se guarda como antes (regla 6: el remedio no puede dejar peor que hoy).
    """
    # El texto se arma ANTES de abrir ningún archivo: si armarlo falla, sale
    # la excepción sin haber tocado nada (antes, un error a mitad de
    # cfg.write dejaba config.ini truncado).
    buffer = io.StringIO()
    cfg.write(buffer)
    texto = buffer.getvalue()

    ruta = config_path()
    carpeta = os.path.dirname(ruta) or "."
    try:
        fd, tmp = tempfile.mkstemp(dir=carpeta, prefix="config.ini.", suffix=".tmp")
    except OSError:
        return _guardar_directo(texto, ruta)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())
        if os.name != "nt":
            # mkstemp lo crea con permisos 0600: que el reemplazo conserve los
            # del archivo que había (en Windows mandan los de la carpeta).
            try:
                os.chmod(tmp, os.stat(ruta).st_mode & 0o7777)
            except OSError:
                pass
        for intento in range(20):
            try:
                os.replace(tmp, ruta)
                return
            except PermissionError:
                time.sleep(0.05 * (intento + 1))
    except Exception:
        pass
    try:
        os.remove(tmp)
    except OSError:
        pass
    _guardar_directo(texto, ruta)


def _tomar_candado(descriptor) -> bool:
    """Candado exclusivo sin esperar, como instancia_unica._bloquear."""
    try:
        import msvcrt          # Windows
    except ImportError:
        import fcntl           # Linux (las pruebas corren acá)
        try:
            fcntl.flock(descriptor.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    try:
        descriptor.seek(0)
        msvcrt.locking(descriptor.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def _soltar_candado(descriptor) -> None:
    try:
        import msvcrt
    except ImportError:
        import fcntl
        try:
            fcntl.flock(descriptor.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        return
    try:
        descriptor.seek(0)
        msvcrt.locking(descriptor.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


# Cuándo soltó el candado ESTE proceso por última vez (ver _candado_config).
_ultima_liberacion = [float("-inf")]


@contextmanager
def _candado_config(espera_s: float = 2.0):
    """Que dos programas no lean-modifiquen-guarden config.ini a la vez.

    Espera como mucho `espera_s` y, si no lo consigue, SIGUE IGUAL: un
    candado trabado (o una carpeta donde no se puede crear el .lock) nunca
    puede impedir que una app guarde su config ni que la caja abra.

    Para que esa espera casi nunca se agote: se vuelve a mirar cada 10 ms, y
    un proceso que lo ACABA de soltar deja pasar primero a los que esperan.
    Sin eso, uno que guarda varias veces seguidas lo retomaba al instante y
    los demás, que miran cada tanto, no entraban nunca: con la PC cargada se
    les agotaban los 2 s y guardaban sin candado, perdiendo claves (medido:
    6 de 25 rondas de 4 programas x 40 guardados; 0 de 25 así).
    """
    descriptor, tomado = None, False
    try:
        if time.monotonic() - _ultima_liberacion[0] < 0.05:
            time.sleep(0.02)
        descriptor = open(config_path() + ".lock", "a+b")
        limite = time.monotonic() + espera_s
        while True:
            tomado = _tomar_candado(descriptor)
            if tomado or time.monotonic() >= limite:
                break
            time.sleep(0.01)
    except Exception:
        tomado = False
    try:
        yield
    finally:
        if descriptor is not None:
            if tomado:
                _soltar_candado(descriptor)
                _ultima_liberacion[0] = time.monotonic()
            try:
                descriptor.close()
            except Exception:
                pass


def obtener_config_dict(seccion: str = None) -> dict:
    """Versión JSON-serializable de la config, para exponerla vía la API
    remota (un configparser.ConfigParser no se puede mandar tal cual por
    HTTP). Si se pasa `seccion`, devuelve SOLO esa sección — así una
    pantalla que solo necesita, por ejemplo, la config de ARCA no manda
    de más por la red (el token remoto o el bot_token de Telegram no
    tienen por qué viajar en esa respuesta). Sin `seccion`, se mantiene
    el comportamiento de siempre (toda la config)."""
    cfg = cargar_config()
    if seccion is not None:
        return {seccion: dict(cfg[seccion]) if seccion in cfg else {}}
    return {s: dict(cfg[s]) for s in cfg.sections()}


def actualizar_config_dict(cambios: dict) -> None:
    """cambios: {seccion: {clave: valor}}. Solo pisa las claves que vengan,
    el resto de la config queda como estaba."""
    with _candado_config():
        cfg = cargar_config()
        for seccion, valores in cambios.items():
            if seccion not in cfg:
                cfg[seccion] = {}
            for clave, valor in valores.items():
                cfg.set(seccion, clave, "" if valor is None else str(valor))
        guardar_config(cfg)


def token_remoto() -> str:
    """Token de autenticación de la API remota. Se autogenera una sola
    vez (32 bytes al azar) y queda guardado en config.ini; el mismo token
    hay que cargarlo en el Dueño Remoto para que pueda conectarse."""
    with _candado_config():
        cfg = cargar_config()
        token = cfg.get("remoto", "token", fallback="")
        if not token:
            token = secrets.token_urlsafe(32)
            cfg.set("remoto", "token", token)
            guardar_config(cfg)
        return token


# --------------------------------------------------------------------- #
# [api_celular]: la regla de lectura compartida con el watchdog
# --------------------------------------------------------------------- #

PUERTO_CELULAR_POR_DEFECTO = 8766
PUERTO_REMOTO_POR_DEFECTO = 8765
_VALORES_SI = {"true", "1", "si", "sí", "yes", "on"}
_RE_PUERTO = re.compile(r"[0-9]{1,5}")


def _primer_token(valor) -> str:
    partes = (valor or "").split()
    return partes[0] if partes else ""


def _puerto_valido(texto: str, defecto: int) -> int:
    if _RE_PUERTO.fullmatch(texto or "") and 1 <= int(texto) <= 65535:
        return int(texto)
    return defecto


def leer_config_celular(ruta: str = None) -> dict:
    """[api_celular] habilitado/puerto y [remoto] puerto, leídos del ARCHIVO real.

    Es la única implementación en Python de la regla que también usa el
    watchdog (scripts/watchdog_celular.ps1, LeerConfigCelular): del valor
    se toma el PRIMER token. configparser no saca los comentarios al final
    de la línea, así que "habilitado = true   ; algo" se leía entero (apagada)
    mientras el watchdog leía "true" (prendida) y la reiniciaba sin parar.

    Sin _DEFAULTS a propósito: [api_celular] no está en los defaults (si
    estuviera, cualquier guardado de otra app escribiría habilitado = false
    y no se podría distinguir "nunca configurado" de "apagado a propósito").
    Lee con utf-8-sig, como el watchdog con -Encoding UTF8. Nunca lanza.
    """
    resultado = {"habilitado": False, "puerto": PUERTO_CELULAR_POR_DEFECTO,
                 "puerto_remoto": PUERTO_REMOTO_POR_DEFECTO, "leido": False}
    try:
        cfg = configparser.ConfigParser(interpolation=None, strict=False)
        with open(ruta or config_path(), "r", encoding="utf-8-sig") as f:
            cfg.read_file(f)
    except Exception:
        return resultado
    try:
        habilitado = _primer_token(cfg.get("api_celular", "habilitado", fallback="")).lower()
        resultado["habilitado"] = habilitado in _VALORES_SI
        resultado["puerto"] = _puerto_valido(
            _primer_token(cfg.get("api_celular", "puerto", fallback="")), PUERTO_CELULAR_POR_DEFECTO)
        resultado["puerto_remoto"] = _puerto_valido(
            _primer_token(cfg.get("remoto", "puerto", fallback="")), PUERTO_REMOTO_POR_DEFECTO)
        resultado["leido"] = True
    except Exception:
        return {"habilitado": False, "puerto": PUERTO_CELULAR_POR_DEFECTO,
                "puerto_remoto": PUERTO_REMOTO_POR_DEFECTO, "leido": False}
    return resultado
