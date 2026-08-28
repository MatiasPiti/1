"""Lectura/escritura de config.ini junto al ejecutable (portable)."""

import configparser

from pos_core.paths import config_path

_DEFAULTS = {
    "telegram": {"bot_token": "", "chat_id_default": "", "habilitado": "false"},
    "general": {"nombre_local": "Mi Negocio", "modo": "MAESTRO"},
    "impresora": {"nombre": ""},  # vacío = usar la impresora predeterminada de Windows
}


def cargar_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read_dict(_DEFAULTS)
    cfg.read(config_path(), encoding="utf-8")
    return cfg


def guardar_config(cfg: configparser.ConfigParser) -> None:
    with open(config_path(), "w", encoding="utf-8") as f:
        cfg.write(f)
