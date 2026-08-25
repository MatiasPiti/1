"""Umbrales de alerta (stoploss / sobre-stock) personalizados POR PRODUCTO.

Reutiliza la tabla Configuracion_Alertas: una fila con producto_codigo
NULL es el umbral global por defecto (ver scripts/setup_inicial.py); una
fila con producto_codigo puntual pisa ese default SOLO para ese producto.
pos_core.telegram_bot ya hace el join con COALESCE(específico, global) —
este módulo es la capa de escritura/lectura que usa la UI del Dueño.
"""

from pos_core.db import get_connection, transaction


def set_umbral_producto(codigo: str, stock_minimo: int, stock_maximo: int) -> None:
    with transaction() as conn:
        conn.execute(
            """INSERT INTO Configuracion_Alertas (producto_codigo, stock_minimo, stock_maximo, activo)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(producto_codigo) DO UPDATE SET
                   stock_minimo = excluded.stock_minimo,
                   stock_maximo = excluded.stock_maximo,
                   activo = 1""",
            (codigo, stock_minimo, stock_maximo),
        )


def quitar_umbral_producto(codigo: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM Configuracion_Alertas WHERE producto_codigo = ?", (codigo,))


def listar_umbrales_por_producto() -> list:
    conn = get_connection()
    rows = conn.execute(
        """SELECT ca.producto_codigo AS codigo, p.nombre, ca.stock_minimo, ca.stock_maximo
           FROM Configuracion_Alertas ca
           JOIN Productos p ON p.codigo = ca.producto_codigo
           WHERE ca.producto_codigo IS NOT NULL
           ORDER BY p.nombre"""
    ).fetchall()
    return [dict(r) for r in rows]
