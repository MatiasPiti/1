from pos_core.bulk_edit import calcular_nuevo_precio, redondear_a_centena_superior


def test_regla_del_negocio_2500_mas_3_por_ciento_da_2600():
    assert calcular_nuevo_precio(2500, porcentaje=3) == 2600


def test_redondeo_siempre_hacia_arriba_y_respeta_multiplos():
    assert redondear_a_centena_superior(2501) == 2600
    assert redondear_a_centena_superior(2600) == 2600


def test_sin_redondeo_y_nunca_negativo():
    assert calcular_nuevo_precio(1000, monto_fijo=55.5, redondear=False) == 1055.5
    assert calcular_nuevo_precio(100, monto_fijo=-500) == 0
