"""Lectura/escritura de config.ini junto al ejecutable (portable)."""

import configparser

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
}


def cargar_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read_dict(_DEFAULTS)
    cfg.read(config_path(), encoding="utf-8")
    return cfg


def guardar_config(cfg: configparser.ConfigParser) -> None:
    with open(config_path(), "w", encoding="utf-8") as f:
        cfg.write(f)
