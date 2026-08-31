"""Lectura/escritura de config.ini junto al ejecutable (portable)."""

import configparser
import secrets

from pos_core.paths import config_path

_DEFAULTS = {
    "telegram": {"bot_token": "", "chat_id_default": "", "habilitado": "false"},
    "general": {"nombre_local": "Mi Negocio", "modo": "MAESTRO"},
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


def cargar_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read_dict(_DEFAULTS)
    cfg.read(config_path(), encoding="utf-8")
    return cfg


def guardar_config(cfg: configparser.ConfigParser) -> None:
    with open(config_path(), "w", encoding="utf-8") as f:
        cfg.write(f)


def obtener_config_dict() -> dict:
    """Versión JSON-serializable de toda la config, para exponerla vía la
    API remota (un configparser.ConfigParser no se puede mandar tal cual
    por HTTP)."""
    cfg = cargar_config()
    return {seccion: dict(cfg[seccion]) for seccion in cfg.sections()}


def actualizar_config_dict(cambios: dict) -> None:
    """cambios: {seccion: {clave: valor}}. Solo pisa las claves que vengan,
    el resto de la config queda como estaba."""
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
    cfg = cargar_config()
    token = cfg.get("remoto", "token", fallback="")
    if not token:
        token = secrets.token_urlsafe(32)
        cfg.set("remoto", "token", token)
        guardar_config(cfg)
    return token
