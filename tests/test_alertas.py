"""Umbrales de alerta y cooldown del bot de Telegram (pos_core/alertas.py,
pos_core/telegram_bot.py). Usan la instalación aislada de conftest.py:
global 5/0 y productos 7790001 (stock 20), 7790002 (stock 3), 7790003 (stock 50)."""

import importlib.util
import os
import sys
import types
from datetime import datetime, timedelta

import pytest

from pos_core import alertas, telegram_bot
from pos_core.db import get_connection, init_db, transaction

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def enviados(monkeypatch):
    """Reemplaza el envío real a Telegram por uno que siempre sale bien."""
    lista = []

    def falso(texto, *, chat_id=None, timeout=10):
        lista.append({"texto": texto, "chat_id": chat_id})
        return True

    monkeypatch.setattr(telegram_bot, "enviar_mensaje", falso)
    return lista


def _fila(codigo):
    return get_connection().execute(
        "SELECT * FROM Configuracion_Alertas WHERE producto_codigo = ?", (codigo,)).fetchone()


def _umbrales_de(codigo):
    return {a["codigo"]: (a["stock_minimo"], a["stock_maximo"]) for a in alertas.listar_alertas()}.get(codigo)


def _correr_cooldown(codigo, hace):
    with transaction() as conn:
        conn.execute("UPDATE Configuracion_Alertas SET ultima_alerta_enviada = ? WHERE producto_codigo = ?",
                     ((datetime.now() - hace).isoformat(timespec="milliseconds"), codigo))


# ------------------------------- cooldown ------------------------------- #

def test_cooldown_no_cambia_el_umbral_efectivo(base, enviados):
    alertas.guardar_umbral_global(10, 100)

    telegram_bot.revisar_umbrales_y_alertar()
    assert len(enviados) == 1 and "7790002" in enviados[0]["texto"] and "mínimo: 10" in enviados[0]["texto"]

    # la fila de cooldown queda inactiva y el umbral sigue siendo el global
    fila = _fila("7790002")
    assert fila["activo"] == 0 and fila["ultima_alerta_enviada"]
    assert _umbrales_de("7790002") == (10, 100)
    assert [a["codigo"] for a in alertas.listar_alertas()] == ["7790002"]

    # antes de 4 h no se reenvía
    telegram_bot.revisar_umbrales_y_alertar()
    _correr_cooldown("7790002", timedelta(hours=3, minutes=59))
    telegram_bot.revisar_umbrales_y_alertar()
    assert len(enviados) == 1

    # pasadas las 4 h sí, y sigue con el umbral global
    _correr_cooldown("7790002", timedelta(hours=4, minutes=1))
    telegram_bot.revisar_umbrales_y_alertar()
    assert len(enviados) == 2 and "mínimo: 10" in enviados[1]["texto"]
    assert _umbrales_de("7790002") == (10, 100)
    n = get_connection().execute(
        "SELECT COUNT(*) FROM Configuracion_Alertas WHERE producto_codigo = '7790002'").fetchone()[0]
    assert n == 1


def test_sin_envio_no_se_registra_cooldown(base, monkeypatch):
    monkeypatch.setattr(telegram_bot, "enviar_mensaje", lambda *a, **k: False)
    telegram_bot.revisar_umbrales_y_alertar()
    assert _fila("7790002") is None


def test_cooldown_respeta_una_fila_de_umbral_real(base, enviados):
    with transaction() as conn:
        conn.execute("INSERT INTO Configuracion_Alertas (producto_codigo, stock_minimo, stock_maximo, activo) "
                     "VALUES ('7790002', 4, 0, 1)")
    telegram_bot.revisar_umbrales_y_alertar()
    assert len(enviados) == 1 and "mínimo: 4" in enviados[0]["texto"]
    fila = _fila("7790002")
    assert (fila["activo"], fila["stock_minimo"], fila["stock_maximo"]) == (1, 4, 0)
    assert fila["ultima_alerta_enviada"]
    telegram_bot.revisar_umbrales_y_alertar()
    assert len(enviados) == 1


def test_telegram_usa_el_chat_del_global(base, enviados):
    with transaction() as conn:
        conn.execute("UPDATE Configuracion_Alertas SET telegram_chat_id = '555' WHERE producto_codigo IS NULL")
    telegram_bot.revisar_umbrales_y_alertar()
    assert [e["chat_id"] for e in enviados] == ["555"]


# ------------------------------- migración ------------------------------ #

def test_migracion_desactiva_filas_de_cooldown_viejas(base, enviados):
    alertas.guardar_umbral_global(10, 100)
    reciente = datetime.now().isoformat(timespec="milliseconds")
    with transaction() as conn:
        # lo que insertaba el bot viejo: DEFAULT 5/0, activo = 1
        conn.execute("INSERT INTO Configuracion_Alertas (producto_codigo, ultima_alerta_enviada, activo) "
                     "VALUES ('7790002', ?, 1)", (reciente,))
        # umbral real por producto: no tiene la firma de cooldown, se respeta
        conn.execute("INSERT INTO Configuracion_Alertas (producto_codigo, stock_minimo, stock_maximo, "
                     "ultima_alerta_enviada, activo) VALUES ('7790003', 60, 0, ?, 1)", (reciente,))
    assert _umbrales_de("7790002") == (5, 0)  # el bug: la fila de cooldown tapaba el global

    init_db()  # la migración corre en cada arranque

    assert _fila("7790002")["activo"] == 0
    assert _fila("7790003")["activo"] == 1
    assert _umbrales_de("7790002") == (10, 100)
    assert _umbrales_de("7790003") == (60, 0)
    assert alertas.migrar_cooldown_viejo() == 0  # idempotente

    # el cooldown que ya estaba registrado se sigue respetando
    telegram_bot.revisar_umbrales_y_alertar()
    assert enviados == []


def test_migracion_no_toca_globales_ni_filas_sin_envio(base):
    with transaction() as conn:
        conn.execute("INSERT INTO Configuracion_Alertas (producto_codigo, activo) VALUES ('7790001', 1)")
    assert alertas.migrar_cooldown_viejo() == 0
    assert _fila("7790001")["activo"] == 1
    assert alertas.obtener_umbral_global() == {"stock_minimo": 5, "stock_maximo": 0}


# --------------------------- globales duplicados ------------------------ #

def _duplicar_global(minimo, maximo, activo=1):
    with transaction() as conn:
        return conn.execute(
            "INSERT INTO Configuracion_Alertas (producto_codigo, stock_minimo, stock_maximo, activo) "
            "VALUES (NULL, ?, ?, ?)", (minimo, maximo, activo)).lastrowid


def test_global_duplicado_toma_el_mas_nuevo(base, enviados):
    _duplicar_global(12, 80)  # la del dueño, posterior al default 5/0 del setup
    assert alertas.obtener_umbral_global() == {"stock_minimo": 12, "stock_maximo": 80}
    assert _umbrales_de("7790002") == (12, 80)

    filas = telegram_bot._productos_fuera_de_umbral()
    assert len(filas) == 3  # sin repetir productos por cada global duplicado
    assert {(r["stock_minimo"], r["stock_maximo"]) for r in filas} == {(12, 80)}
    telegram_bot.revisar_umbrales_y_alertar()
    assert len(enviados) == 1 and "mínimo: 12" in enviados[0]["texto"]


def test_obtener_umbral_global_ignora_inactivos(base):
    _duplicar_global(30, 300, activo=0)
    assert alertas.obtener_umbral_global() == {"stock_minimo": 5, "stock_maximo": 0}
    assert _umbrales_de("7790002") == (5, 0)


def test_guardar_umbral_global_conserva_el_mas_nuevo(base):
    nuevo = _duplicar_global(12, 80)
    alertas.guardar_umbral_global(7, 70)
    filas = get_connection().execute(
        "SELECT id, stock_minimo, stock_maximo FROM Configuracion_Alertas WHERE producto_codigo IS NULL"
    ).fetchall()
    assert [tuple(f) for f in filas] == [(nuevo, 7, 70)]
    assert alertas.obtener_umbral_global() == {"stock_minimo": 7, "stock_maximo": 70}


# ------------------------- panel de escritorio -------------------------- #

class _WidgetFalso:
    """Widget de tkinter de mentira: alcanza para armar la pestaña sin pantalla."""

    def __init__(self, *args, **kwargs):
        self.texto = ""

    def insert(self, indice, texto):
        self.texto = str(texto) + self.texto

    def get(self):
        return self.texto

    def __getattr__(self, nombre):  # grid, pack, etc.: no hacen nada
        return lambda *args, **kwargs: None


def _panel_sin_pantalla(monkeypatch):
    """Carga apps/master_dueno/main.py con un tkinter falso (acá no hay
    pantalla, ni siquiera tkinter). Devuelve el módulo y los messagebox."""
    mensajes = []
    tk = types.ModuleType("tkinter")
    ttk = types.ModuleType("tkinter.ttk")
    messagebox = types.ModuleType("tkinter.messagebox")
    filedialog = types.ModuleType("tkinter.filedialog")
    tk.__getattr__ = ttk.__getattr__ = filedialog.__getattr__ = lambda nombre: _WidgetFalso
    messagebox.showinfo = lambda titulo, texto: mensajes.append(("showinfo", titulo))
    messagebox.showerror = lambda titulo, texto: mensajes.append(("showerror", titulo, texto))
    tk.ttk, tk.messagebox, tk.filedialog = ttk, messagebox, filedialog
    for nombre, modulo in [("tkinter", tk), ("tkinter.ttk", ttk),
                           ("tkinter.messagebox", messagebox), ("tkinter.filedialog", filedialog)]:
        monkeypatch.setitem(sys.modules, nombre, modulo)
    monkeypatch.setattr(sys, "path", list(sys.path))  # main.py agrega la raíz al path
    spec = importlib.util.spec_from_file_location(
        "_panel_dueno_sin_pantalla", os.path.join(RAIZ, "apps", "master_dueno", "main.py"))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo, mensajes


def test_panel_escritorio_precarga_el_umbral_global(base, monkeypatch):
    alertas.guardar_umbral_global(10, 100)  # p. ej. desde la app del celular
    modulo, mensajes = _panel_sin_pantalla(monkeypatch)
    panel = modulo.AppDueno.__new__(modulo.AppDueno)
    panel._armar_alertas(_WidgetFalso())
    assert (panel.um_min.get(), panel.um_max.get()) == ("10", "100")

    # guardar sin tocar los campos no pisa lo configurado
    panel._guardar_umbrales()
    assert mensajes == [("showinfo", "Guardado")]
    assert alertas.obtener_umbral_global() == {"stock_minimo": 10, "stock_maximo": 100}


def test_panel_escritorio_no_pisa_un_config_ilegible_al_guardar_telegram(base, monkeypatch):
    # Si lo reescribiera con los valores por defecto se perdería [api] secreto
    # y se cerraría la sesión de todos los celulares.
    ruta = base / "config.ini"
    corrupto = "[api]\nsecreto = abc123\nlinea suelta sin clave\n"
    ruta.write_text(corrupto, encoding="utf-8")
    modulo, mensajes = _panel_sin_pantalla(monkeypatch)
    panel = modulo.AppDueno.__new__(modulo.AppDueno)
    panel.tg_token, panel.tg_chat, panel.tg_habilitado = _WidgetFalso(), _WidgetFalso(), _WidgetFalso()
    panel.tg_token.insert(0, "123456:NUEVO")

    panel._guardar_config_telegram()

    assert [m[0] for m in mensajes] == ["showerror"]
    assert ruta.read_text(encoding="utf-8") == corrupto
