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


def resumen_dashboard() -> dict:
    """Lo que muestra el Dashboard del Panel del Dueño: ventas de hoy y
    los productos más vendidos históricamente (para el gráfico)."""
    conn = get_connection()
    total_hoy = conn.execute(
        "SELECT COALESCE(SUM(total),0) t, COUNT(*) c FROM Ventas "
        "WHERE date(fecha_hora) = date('now','localtime') AND anulada = 0"
    ).fetchone()
    top_productos = conn.execute(
        """SELECT producto_nombre, SUM(cantidad) cant FROM Detalle_Ventas
           GROUP BY producto_codigo ORDER BY cant DESC LIMIT 8"""
    ).fetchall()
    return {
        "ventas_hoy": total_hoy["c"],
        "total_hoy": total_hoy["t"],
        "top_productos": [dict(r) for r in top_productos],
    }
