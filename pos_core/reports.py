"""Reportes simples para el Panel del Dueño."""

from pos_core.db import get_connection


def totales_por_metodo_pago(fecha: str = None) -> list:
    """fecha en formato 'YYYY-MM-DD'; None = hoy. Devuelve una fila por
    método de pago con cantidad de ventas y total, para que el dueño vea
    cuánto dinero se movió en efectivo/débito/transferencia/mixto."""
    conn = get_connection()
    if fecha:
        rows = conn.execute(
            """SELECT metodo_pago, COUNT(*) AS cantidad, COALESCE(SUM(total),0) AS total
               FROM Ventas WHERE date(fecha_hora) = ? AND anulada = 0
               GROUP BY metodo_pago ORDER BY metodo_pago""",
            (fecha,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT metodo_pago, COUNT(*) AS cantidad, COALESCE(SUM(total),0) AS total
               FROM Ventas WHERE date(fecha_hora) = date('now','localtime') AND anulada = 0
               GROUP BY metodo_pago ORDER BY metodo_pago"""
        ).fetchall()
    return [dict(r) for r in rows]
