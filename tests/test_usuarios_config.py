import configparser
import importlib.util
import os

import pytest

from pos_core import config, usuarios
from pos_core.db import get_connection

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _temporales(carpeta):
    return [n for n in os.listdir(carpeta) if n.endswith(".tmp")]


# ------------------------------ config.ini ------------------------------ #

def test_guardar_config_escribe_y_no_deja_temporales(base):
    cfg = config.cargar_config()
    cfg["general"]["nombre_local"] = "Almacén Leo"
    config.guardar_config(cfg)
    assert config.cargar_config()["general"]["nombre_local"] == "Almacén Leo"
    assert _temporales(base) == []


def test_guardar_config_reintenta_si_windows_bloquea_el_archivo(base, monkeypatch):
    (base / "config.ini").write_text("[general]\nnombre_local = Viejo\n", encoding="utf-8")
    replace_real = os.replace
    intentos = []

    def replace_bloqueado(origen, destino):
        intentos.append(destino)
        if len(intentos) <= 2:
            raise PermissionError("el archivo está siendo usado por otro proceso")
        replace_real(origen, destino)
    monkeypatch.setattr(config.os, "replace", replace_bloqueado)
    monkeypatch.setattr(config.time, "sleep", lambda s: None)

    cfg = config.cargar_config()
    cfg["general"]["nombre_local"] = "Nuevo"
    config.guardar_config(cfg)
    assert len(intentos) == 3
    assert config.cargar_config()["general"]["nombre_local"] == "Nuevo"
    assert _temporales(base) == []


def test_guardar_config_que_falla_deja_el_archivo_viejo_entero(base, monkeypatch):
    original = "[general]\nnombre_local = Viejo\n\n[api]\nsecreto = abc\n"
    (base / "config.ini").write_text(original, encoding="utf-8")

    def siempre_bloqueado(origen, destino):
        raise PermissionError("bloqueado")
    monkeypatch.setattr(config.os, "replace", siempre_bloqueado)
    monkeypatch.setattr(config.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        config.guardar_config(config.cargar_config())
    assert (base / "config.ini").read_text(encoding="utf-8") == original
    assert _temporales(base) == []


def test_corte_a_mitad_de_escritura_no_trunca_config_ini(base):
    original = "[general]\nnombre_local = Viejo\n\n[api]\nsecreto = abc\n"
    (base / "config.ini").write_text(original, encoding="utf-8")

    class CorteDeLuz(configparser.ConfigParser):
        def write(self, f, space_around_delimiters=True):
            f.write("[general]\nnombre_lo")
            raise OSError("se cortó la luz")

    with pytest.raises(OSError):
        config.guardar_config(CorteDeLuz())
    assert (base / "config.ini").read_text(encoding="utf-8") == original
    assert _temporales(base) == []


def test_cargar_config_estricto_falla_si_no_se_puede_parsear(base):
    (base / "config.ini").write_text("[api\nsecreto = x", encoding="utf-8")
    with pytest.raises(config.ConfigIlegibleError):
        config.cargar_config(estricto=True)
    (base / "config.ini").write_bytes(b"[api]\nsecreto = \xff\xfe\n")   # no es UTF-8
    with pytest.raises(config.ConfigIlegibleError):
        config.cargar_config(estricto=True)


def test_cargar_config_estricto_sin_archivo_da_defaults(base):
    assert not (base / "config.ini").exists()
    assert config.cargar_config(estricto=True)["general"]["nombre_local"] == "Mi Negocio"


# ------------------------------- usuarios ------------------------------- #

def test_obtener_pin_hash_solo_de_usuario_activo_con_ese_rol(base):
    assert usuarios.obtener_pin_hash("dueño", rol="DUEÑO") == usuarios.hash_pin("1234")
    assert usuarios.obtener_pin_hash("cajero", rol="DUEÑO") is None
    assert usuarios.obtener_pin_hash("nadie", rol="DUEÑO") is None
    get_connection().execute("UPDATE Usuarios SET activo = 0 WHERE nombre = 'dueño'")
    assert usuarios.obtener_pin_hash("dueño", rol="DUEÑO") is None
    assert not usuarios.hay_usuario_activo(rol="DUEÑO")
    assert usuarios.hay_usuario_activo(rol="CAJERO")


def test_definir_pin_dueno_actualiza_y_reactiva(base):
    get_connection().execute("UPDATE Usuarios SET activo = 0, rol = 'CAJERO' WHERE nombre = 'dueño'")
    assert usuarios.definir_pin_dueno(" 24680 ") == "actualizado"
    assert usuarios.verificar_pin("24680", rol="DUEÑO") == "dueño"
    assert usuarios.verificar_pin("1234", rol="DUEÑO") is None
    n = get_connection().execute("SELECT COUNT(*) FROM Usuarios WHERE nombre = 'dueño'").fetchone()[0]
    assert n == 1


def test_definir_pin_dueno_crea_si_no_existe(base):
    get_connection().execute("DELETE FROM Usuarios WHERE nombre = 'dueño'")
    assert usuarios.definir_pin_dueno("123456789012") == "creado"
    assert usuarios.verificar_pin("123456789012", rol="DUEÑO") == "dueño"


@pytest.mark.parametrize("pin", ["", "123", "1234567890123", "12a4", "١٢٣٤", "12 34", None])
def test_definir_pin_dueno_rechaza_pin_invalido(base, pin):
    with pytest.raises(ValueError):
        usuarios.definir_pin_dueno(pin)
    assert usuarios.verificar_pin("1234", rol="DUEÑO") == "dueño"   # no tocó nada


# ---------------------------- setup_inicial ----------------------------- #

def _cargar_setup_inicial():
    spec = importlib.util.spec_from_file_location(
        "setup_inicial", os.path.join(RAIZ, "scripts", "setup_inicial.py"))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_setup_inicial_con_base_crea_la_db_ahi(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SISTEMA_DUAL_BASE", str(tmp_path))   # para que monkeypatch la restaure
    destino = tmp_path / "MaestroDueno"
    respuestas = iter(["12", "2468"])   # el primero no es válido: lo vuelve a pedir
    monkeypatch.setattr("builtins.input", lambda _msg="": next(respuestas))

    _cargar_setup_inicial().main(["--base", str(destino)])

    assert (destino / "database" / "stock.db").exists()
    assert os.environ["SISTEMA_DUAL_BASE"] == str(destino)
    assert usuarios.verificar_pin("2468", rol="DUEÑO") == "dueño"
    n = get_connection().execute(
        "SELECT COUNT(*) FROM Configuracion_Alertas WHERE producto_codigo IS NULL").fetchone()[0]
    assert n == 1
    assert "4 y 12" in capsys.readouterr().out
