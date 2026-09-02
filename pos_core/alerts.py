"""Umbrales de alerta (stoploss / sobre-stock) personalizados POR PRODUCTO.

Reutiliza la tabla Configuracion_Alertas: una fila con producto_codigo
NULL es el umbral global por defecto (ver scripts/setup_inicial.py); una
fila con producto_codigo puntual pisa ese default SOLO para ese producto.
pos_core.telegram_bot ya hace el join con COALESCE(específico, global) —
este módulo es la capa de escritura/lectura que usa la UI del Dueño.
"""

from pos_core.db import get_connection, transaction


def set_umbral_global(stock_minimo: int, stock_maximo: int) -> None:
    """Umbral por defecto para todos los productos que no tengan uno propio.

    OJO con el UNIQUE de producto_codigo: en SQLite cada NULL cuenta como
    distinto de cualquier otro NULL, así que un ON CONFLICT(producto_codigo)
    NUNCA se dispara para la fila global (producto_codigo IS NULL) —
    insertaría una fila global nueva cada vez, con dos efectos feos: el
    umbral global "no se guardaría" (queda ganando la fila vieja) y el
    LEFT JOIN de telegram_bot._productos_fuera_de_umbral empezaría a
    multiplicar filas, mandando una alerta repetida por cada global de más.
    Por eso se hace UPDATE explícito y solo se inserta si no existía
    ninguna (mismo criterio que scripts/setup_inicial.py).
    """
    if stock_minimo < 0 or stock_maximo < 0:
        raise ValueError("Los umbrales no pueden ser negativos")

    with transaction() as conn:
        actualizadas = conn.execute(
            "UPDATE Configuracion_Alertas SET stock_minimo = ?, stock_maximo = ?, activo = 1 "
            "WHERE producto_codigo IS NULL",
            (stock_minimo, stock_maximo),
        ).rowcount
        if not actualizadas:
            conn.execute(
                """INSERT INTO Configuracion_Alertas
                   (producto_codigo, stock_minimo, stock_maximo, activo)
                   VALUES (NULL, ?, ?, 1)""",
                (stock_minimo, stock_maximo),
            )


def set_umbral_producto(codigo: str, stock_minimo: int, stock_maximo: int) -> None:
    codigo = (codigo or "").strip()
    if not codigo:
        raise ValueError("Hace falta el código del producto")
    if stock_minimo < 0 or stock_maximo < 0:
        raise ValueError("Los umbrales no pueden ser negativos")
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
