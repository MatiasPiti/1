"""Lectura/escritura de config.ini junto al ejecutable (portable)."""

import configparser
import os
import tempfile
import time

from pos_core.paths import config_path

_DEFAULTS = {
    "telegram": {"bot_token": "", "chat_id_default": "", "habilitado": "false"},
    "general": {"nombre_local": "Mi Negocio", "modo": "MAESTRO"},
}

REINTENTOS_GUARDADO = 5


class ConfigIlegibleError(RuntimeError):
    """config.ini existe pero no se pudo abrir o parsear. Quien lo reciba
    NO debe regenerar ni pisar el archivo: se perderían los datos que tiene
    (clave de sesiones de la API, token de Telegram...)."""


def cargar_config(*, estricto: bool = False) -> configparser.ConfigParser:
    """Defaults + lo que haya en config.ini.

    Normalmente un config.ini que no se puede abrir se ignora en silencio
    (quedan los defaults). Con `estricto=True`, si el archivo EXISTE pero
    no se puede abrir, decodificar o parsear, se lanza ConfigIlegibleError:
    lo usa quien después va a reescribir el archivo o generar datos a
    partir de lo que falte."""
    cfg = configparser.ConfigParser()
    cfg.read_dict(_DEFAULTS)
    ruta = config_path()
    if not estricto:
        cfg.read(ruta, encoding="utf-8")
        return cfg
    if os.path.exists(ruta):
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                cfg.read_file(f, source=ruta)
        except (OSError, UnicodeError, configparser.Error) as exc:
            raise ConfigIlegibleError(f"No se pudo leer {ruta}: {exc}") from exc
    return cfg


def guardar_config(cfg: configparser.ConfigParser) -> None:
    """Escritura atómica: se escribe un temporal en la misma carpeta y se lo
    renombra encima de config.ini con os.replace. Así nadie (la API, el
    panel) lee nunca un config.ini a medio escribir, y un corte de luz deja
    el archivo viejo o el nuevo, nunca uno truncado."""
    ruta = config_path()
    fd, temporal = tempfile.mkstemp(prefix=".config-", suffix=".tmp",
                                    dir=os.path.dirname(ruta) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            cfg.write(f)
            f.flush()
            os.fsync(f.fileno())
        for intento in range(REINTENTOS_GUARDADO):
            try:
                os.replace(temporal, ruta)
                break
            except PermissionError:
                # Windows: un antivirus o alguien con el archivo abierto lo
                # bloquea un instante. Se reintenta antes de rendirse.
                if intento == REINTENTOS_GUARDADO - 1:
                    raise
                time.sleep(0.05 * (intento + 1))
    except BaseException:
        try:
            os.remove(temporal)
        except OSError:
            pass
        raise
