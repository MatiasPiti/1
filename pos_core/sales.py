"""Registro de ventas (cierre de ticket) y disparo del descuento de stock.

`cerrar_ticket()` es el evento central del sistema: inserta la cabecera de
la Venta, el detalle de líneas y descuenta stock de cada producto, todo
encadenado. La cabecera + detalle se graban en una transacción; el
descuento de stock de cada línea usa su propia transacción optimista
(pos_core.stock_service) para no bloquear el resto del inventario si una
sola línea tiene conflicto de concurrencia.
"""

import uuid
from datetime import datetime

from pos_core.db import transaction
from pos_core import stock_service


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def cerrar_ticket(carrito: list, *, metodo_pago: str, usuario: str,
                   origen: str = "MAESTRO") -> dict:
    """carrito: lista de dicts {codigo, nombre, cantidad, precio_unitario}.

    Devuelve {venta_uuid, total, fecha_hora}. Cada venta lleva un UUID4
    propio: es la clave que usa la conciliación para no duplicar ventas
    que ya viajaron desde un USB (ver pos_core/reconciliation.py).
    """
    if not carrito:
        raise ValueError("El carrito está vacío")

    venta_uuid = str(uuid.uuid4())
    fecha_hora = _now()
    total = sum(item["cantidad"] * item["precio_unitario"] for item in carrito)

    # En el Maestro no hace falta "sincronizar" (ya es la fuente de verdad);
    # en un USB, la venta queda pendiente de exportar hasta el botón
    # "Preparar sincronización" (ver pos_core/sync_export.py).
    sincronizado = 1 if origen == "MAESTRO" else 0

    with transaction() as conn:
        conn.execute(
            """INSERT INTO Ventas (uuid_unico, fecha_hora, total, metodo_pago, usuario, origen, sincronizado)
               VALUES (?,?,?,?,?,?,?)""",
            (venta_uuid, fecha_hora, total, metodo_pago, usuario, origen, sincronizado),
        )
        for item in carrito:
            subtotal = item["cantidad"] * item["precio_unitario"]
            conn.execute(
                """INSERT INTO Detalle_Ventas
                   (venta_uuid, producto_codigo, producto_nombre, cantidad, precio_unitario, subtotal)
                   VALUES (?,?,?,?,?,?)""",
                (venta_uuid, item["codigo"], item["nombre"], item["cantidad"],
                 item["precio_unitario"], subtotal),
            )

    # El descuento de stock se dispara DESPUÉS de confirmar la venta, ya
    # que cada línea es una transacción propia (versionado optimista); si
    # una línea puntual fallara (p.ej. el producto fue desactivado entre
    # el cobro y este paso), la venta ya quedó registrada y el problema de
    # stock se resuelve manualmente sin perder el ticket.
    fallas_stock = []
    for item in carrito:
        try:
            stock_service.descontar_por_venta(
                item["codigo"], item["cantidad"], ticket_uuid=venta_uuid,
                usuario=usuario, origen=origen)
        except Exception as e:
            fallas_stock.append({"codigo": item["codigo"], "error": str(e)})

    return {
        "venta_uuid": venta_uuid,
        "total": total,
        "fecha_hora": fecha_hora,
        "fallas_stock": fallas_stock,
    }


def buscar_productos(termino: str, *, limite: int = 30) -> list:
    """Búsqueda de productos por código o nombre para la pantalla de
    cobro. Deliberadamente NO devuelve la columna 'stock': el cajero no
    debe ver disponibilidad (requisito 3.1 'Stock Invisible')."""
    from pos_core.db import get_connection
    conn = get_connection()
    like = f"%{termino}%"
    rows = conn.execute(
        """SELECT codigo, nombre, precio_venta FROM Productos
           WHERE activo = 1 AND (codigo LIKE ? OR nombre LIKE ?)
           ORDER BY nombre LIMIT ?""",
        (like, like, limite),
    ).fetchall()
    return [dict(r) for r in rows]
