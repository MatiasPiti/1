"""Consultas de solo lectura del panel del dueño (dashboard, productos,
movimientos). A diferencia de `sales.buscar_productos`, acá SÍ se devuelve
el stock: el dueño ve todo, el cajero no (requisito "Stock Invisible").
"""

from datetime import date, timedelta

from pos_core import alertas
from pos_core.db import get_connection

_COLUMNAS_PRODUCTO = f"""
    p.codigo, p.nombre, p.precio_venta, p.precio_compra, p.stock,
    p.marca, p.proveedor, p.categoria, {alertas.SQL_UMBRALES_EFECTIVOS}
"""

_PERIODOS_TOP = {
    "hoy": "AND date(v.fecha_hora) = date('now','localtime')",
    "7dias": "AND date(v.fecha_hora) >= date('now','localtime','-6 days')",
    "30dias": "AND date(v.fecha_hora) >= date('now','localtime','-29 days')",
    "historico": "",
}


def resumen_dashboard(*, periodo_top: str = "historico", limite_top: int = 8) -> dict:
    if periodo_top not in _PERIODOS_TOP:
        raise ValueError(f"Período inválido: {periodo_top}")
    conn = get_connection()

    hoy = conn.execute(
        "SELECT COALESCE(SUM(total),0) total, COUNT(*) tickets FROM Ventas "
        "WHERE date(fecha_hora) = date('now','localtime') AND anulada = 0"
    ).fetchone()

    por_metodo = conn.execute(
        "SELECT metodo_pago, COUNT(*) tickets, COALESCE(SUM(total),0) total FROM Ventas "
        "WHERE date(fecha_hora) = date('now','localtime') AND anulada = 0 "
        "GROUP BY metodo_pago ORDER BY total DESC"
    ).fetchall()

    por_dia = {
        r["dia"]: r for r in conn.execute(
            "SELECT date(fecha_hora) dia, COALESCE(SUM(total),0) total, COUNT(*) tickets FROM Ventas "
            "WHERE anulada = 0 AND date(fecha_hora) >= date('now','localtime','-6 days') "
            "GROUP BY dia"
        ).fetchall()
    }
    hoy_local = date.fromisoformat(conn.execute("SELECT date('now','localtime')").fetchone()[0])
    ultimos_7 = []
    for i in range(6, -1, -1):
        dia = (hoy_local - timedelta(days=i)).isoformat()
        r = por_dia.get(dia)
        ultimos_7.append({"dia": dia, "total": r["total"] if r else 0, "tickets": r["tickets"] if r else 0})

    top = conn.execute(
        f"""SELECT dv.producto_codigo codigo, MAX(dv.producto_nombre) nombre,
                   SUM(dv.cantidad) cantidad, SUM(dv.subtotal) importe
            FROM Detalle_Ventas dv JOIN Ventas v ON v.uuid_unico = dv.venta_uuid
            WHERE v.anulada = 0 {_PERIODOS_TOP[periodo_top]}
            GROUP BY dv.producto_codigo ORDER BY cantidad DESC LIMIT ?""",
        (limite_top,),
    ).fetchall()

    tickets = hoy["tickets"]
    return {
        "hoy": {
            "total": hoy["total"],
            "tickets": tickets,
            "ticket_promedio": (hoy["total"] / tickets) if tickets else 0,
            "por_metodo": [dict(r) for r in por_metodo],
        },
        "ultimos_7_dias": ultimos_7,
        "top_productos": [dict(r) for r in top],
        "periodo_top": periodo_top,
        "alertas_activas": len(alertas.listar_alertas()),
    }


def buscar_productos(termino: str = "", *, limite: int = 200) -> list:
    """Búsqueda por código o nombre (vacío = todos, por nombre)."""
    conn = get_connection()
    like = f"%{termino.strip()}%"
    rows = conn.execute(
        f"""SELECT {_COLUMNAS_PRODUCTO}
            FROM Productos p {alertas.SQL_JOIN_UMBRAL_PRODUCTO}
            WHERE p.activo = 1 AND (p.codigo LIKE ? OR p.nombre LIKE ?)
            ORDER BY (p.codigo = ?) DESC, p.nombre LIMIT ?""",
        (like, like, termino.strip(), limite),
    ).fetchall()
    return [dict(r) for r in rows]


def obtener_producto(codigo: str):
    conn = get_connection()
    row = conn.execute(
        f"""SELECT {_COLUMNAS_PRODUCTO}
            FROM Productos p {alertas.SQL_JOIN_UMBRAL_PRODUCTO}
            WHERE p.activo = 1 AND p.codigo = ?""",
        (codigo,),
    ).fetchone()
    return dict(row) if row else None


def completar_productos(codigos: list) -> list:
    """Devuelve los productos (con stock y umbrales) de una lista de
    códigos, en el mismo orden; útil después de aplicar un filtro."""
    return [p for p in (obtener_producto(c) for c in codigos) if p]


def movimientos_recientes(*, codigo: str = None, limite: int = 30) -> list:
    conn = get_connection()
    sql = """SELECT m.fecha_hora, m.producto_codigo codigo, p.nombre, m.tipo, m.cantidad,
                    m.stock_resultante, m.motivo, m.usuario
             FROM Movimientos_Stock m LEFT JOIN Productos p ON p.codigo = m.producto_codigo"""
    params = []
    if codigo:
        sql += " WHERE m.producto_codigo = ?"
        params.append(codigo)
    sql += " ORDER BY m.fecha_hora DESC, m.id DESC LIMIT ?"
    params.append(limite)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]
