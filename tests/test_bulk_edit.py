import math

import pytest

from pos_core import bulk_edit
from pos_core.bulk_edit import PRECIO_MAX, calcular_nuevo_precio, redondear_a_centena_superior
from pos_core.db import get_connection


def test_regla_del_negocio_2500_mas_3_por_ciento_da_2600():
    assert calcular_nuevo_precio(2500, porcentaje=3) == 2600


def test_redondeo_siempre_hacia_arriba_y_respeta_multiplos():
    assert redondear_a_centena_superior(2501) == 2600
    assert redondear_a_centena_superior(2600) == 2600


def test_sin_redondeo_y_nunca_negativo():
    assert calcular_nuevo_precio(1000, monto_fijo=55.5, redondear=False) == 1055.5
    assert calcular_nuevo_precio(100, monto_fijo=-500) == 0


# ---------------------------- límites de precio ---------------------------- #

def _precio_y_version(codigo):
    r = get_connection().execute(
        "SELECT precio_venta, version FROM Productos WHERE codigo = ?", (codigo,)).fetchone()
    return r["precio_venta"], r["version"]


@pytest.mark.parametrize("kwargs", [
    {"precio_actual": math.inf, "porcentaje": 3},
    {"precio_actual": math.nan, "porcentaje": 3},
    {"precio_actual": 100, "porcentaje": math.nan},
    {"precio_actual": 100, "monto_fijo": math.inf, "redondear": False},
    {"precio_actual": 100, "monto_fijo": -math.inf},
    {"precio_actual": 0, "porcentaje": math.inf},
    {"precio_actual": PRECIO_MAX, "porcentaje": 1},
    {"precio_actual": 100, "monto_fijo": 1e300, "redondear": False},
])
def test_calcular_rechaza_precios_no_finitos_o_enormes(kwargs):
    precio = kwargs.pop("precio_actual")
    with pytest.raises(ValueError):
        calcular_nuevo_precio(precio, **kwargs)


def test_calcular_acepta_hasta_el_maximo():
    assert calcular_nuevo_precio(PRECIO_MAX - 100, monto_fijo=100) == PRECIO_MAX


@pytest.mark.parametrize("precio", [math.inf, -math.inf, math.nan, -1, PRECIO_MAX + 1, 1e300])
def test_fijar_precio_rechaza_invalidos(base, precio):
    antes = _precio_y_version("7790001")
    with pytest.raises(ValueError):
        bulk_edit.fijar_precio("7790001", precio, usuario="test")
    assert _precio_y_version("7790001") == antes


def test_fijar_precio_acepta_el_maximo(base):
    r = bulk_edit.fijar_precio("7790001", PRECIO_MAX, usuario="test")
    assert r["precio_nuevo"] == PRECIO_MAX
    assert _precio_y_version("7790001")[0] == PRECIO_MAX


def test_ajuste_masivo_no_aplica_a_medias(base):
    bulk_edit.fijar_precio("7790003", PRECIO_MAX * 0.6, usuario="test")
    codigos = ["7790001", "7790002", "7790003"]  # el inválido al final
    antes = {c: _precio_y_version(c) for c in codigos}
    with pytest.raises(ValueError, match="7790003"):
        bulk_edit.aplicar_ajuste_masivo(codigos, porcentaje=100, usuario="test")
    assert {c: _precio_y_version(c) for c in codigos} == antes


def test_ajuste_masivo_valido_sigue_informando_no_encontrados(base):
    r = bulk_edit.aplicar_ajuste_masivo(["7790001", "NOEXISTE"], porcentaje=3, usuario="test")
    assert r == [
        {"codigo": "7790001", "ok": True, "precio_anterior": 2500, "precio_nuevo": 2600},
        {"codigo": "NOEXISTE", "ok": False, "error": "no encontrado"},
    ]
    assert _precio_y_version("7790001")[0] == 2600
