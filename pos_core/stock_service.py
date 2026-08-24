"""Servicio de Stock: el único punto por el que el stock cambia de valor.

En el Sistema Maestro corre "oculto" dentro de un hilo del proceso de
servicio de Windows (ver services/stock_daemon_windows.py) y escucha el
evento "ticket cerrado" emitido por la Caja. En los USBs, estas mismas
funciones se llaman directamente desde el hilo principal de Tkinter
porque no hace falta separar procesos (uso mono-usuario).

Cada función es una transacción atómica: si algo falla a mitad de camino,
no queda stock "descontado a medias".
"""

import uuid
from datetime import datetime

from pos_core.db import transaction


class StockInsuficienteError(Exception):
    pass


class ProductoNoEncontradoError(Exception):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _get_producto_for_update(conn, codigo: str):
    row = conn.execute(
        "SELECT id, codigo, nombre, stock, version FROM Productos WHERE codigo = ? AND activo = 1",
        (codigo,),
    ).fetchone()
    if row is None:
        raise ProductoNoEncontradoError(f"Producto con código '{codigo}' no existe o está inactivo")
    return row


def _registrar_movimiento(conn, *, producto_codigo, tipo, cantidad, stock_resultante,
                           motivo, ticket_uuid, usuario, origen, mov_uuid=None, fecha_hora=None):
    # Igual criterio que en Ventas: en el Maestro se considera ya "al día";
    # en un USB queda pendiente de exportar/conciliar.
    sincronizado = 1 if origen == "MAESTRO" else 0
    conn.execute(
        """INSERT INTO Movimientos_Stock
           (uuid_unico, producto_codigo, tipo, cantidad, stock_resultante,
            motivo, ticket_uuid, usuario, origen, fecha_hora, sincronizado)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (mov_uuid or str(uuid.uuid4()), producto_codigo, tipo, cantidad, stock_resultante,
         motivo, ticket_uuid, usuario, origen, fecha_hora or _now(), sincronizado),
    )


def _aplicar_delta_con_version(conn, producto_row, delta: int):
    """UPDATE optimista: solo aplica si la 'version' no cambió desde el
    SELECT (nadie más la tocó en el medio). Si otro proceso concurrente
    ganó la carrera, se reintenta automáticamente (hasta 5 veces) en vez
    de corromper el conteo. Esto reemplaza a `SELECT ... FOR UPDATE`
    (que SQLite no soporta) manteniendo la misma garantía práctica:
    ninguna venta pisa el resultado de otra."""
    nuevo_stock = producto_row["stock"] + delta
    cur = conn.execute(
        "UPDATE Productos SET stock = ?, version = version + 1, "
        "actualizado_en = ? WHERE id = ? AND version = ?",
        (nuevo_stock, _now(), producto_row["id"], producto_row["version"]),
    )
    return cur.rowcount == 1, nuevo_stock


def _con_reintento_optimista(codigo: str, delta: int, aplicar_movimiento, max_intentos=5):
    """Reintenta la transacción completa si el versionado optimista
    detecta una escritura concurrente. Cada intento es su propia
    transacción SQL (BEGIN IMMEDIATE ... COMMIT/ROLLBACK)."""
    ultimo_error = None
    for _ in range(max_intentos):
        try:
            with transaction() as conn:
                producto = _get_producto_for_update(conn, codigo)
                if producto["stock"] + delta < 0:
                    raise StockInsuficienteError(
                        f"Stock insuficiente para '{codigo}': disponible {producto['stock']}, "
                        f"se pidió descontar {-delta}")
                ok, nuevo_stock = _aplicar_delta_con_version(conn, producto, delta)
                if not ok:
                    raise _ConcurrenciaError()
                aplicar_movimiento(conn, producto, nuevo_stock)
                return nuevo_stock
        except _ConcurrenciaError as e:
            ultimo_error = e
            continue
    raise RuntimeError(f"No se pudo aplicar el movimiento de stock tras {max_intentos} "
                        f"reintentos por escritura concurrente") from ultimo_error


class _ConcurrenciaError(Exception):
    pass


# --------------------------------------------------------------------- #
# API pública
# --------------------------------------------------------------------- #

def descontar_por_venta(codigo: str, cantidad: int, *, ticket_uuid: str, usuario: str,
                         origen: str = "MAESTRO") -> int:
    """Descuenta stock dentro de LA MISMA transacción que registra el
    movimiento. Se llama desde el evento 'ticket cerrado' de la Caja,
    nunca deja stock a medio actualizar."""
    def _mov(conn, producto, nuevo_stock):
        _registrar_movimiento(
            conn, producto_codigo=codigo, tipo="SALIDA_VENTA", cantidad=cantidad,
            stock_resultante=nuevo_stock, motivo=f"Venta ticket {ticket_uuid}",
            ticket_uuid=ticket_uuid, usuario=usuario, origen=origen)
    return _con_reintento_optimista(codigo, -cantidad, _mov)


def sumar_stock_manual(codigo: str, cantidad: int, *, usuario: str, motivo: str = "Alta manual",
                        origen: str = "MAESTRO") -> int:
    def _mov(conn, producto, nuevo_stock):
        _registrar_movimiento(
            conn, producto_codigo=codigo, tipo="ENTRADA_MANUAL", cantidad=cantidad,
            stock_resultante=nuevo_stock, motivo=motivo, ticket_uuid=None,
            usuario=usuario, origen=origen)
    return _con_reintento_optimista(codigo, cantidad, _mov)


def restar_stock_manual(codigo: str, cantidad: int, *, usuario: str, motivo: str = "Baja manual",
                         origen: str = "MAESTRO") -> int:
    def _mov(conn, producto, nuevo_stock):
        _registrar_movimiento(
            conn, producto_codigo=codigo, tipo="SALIDA_MANUAL", cantidad=cantidad,
            stock_resultante=nuevo_stock, motivo=motivo, ticket_uuid=None,
            usuario=usuario, origen=origen)
    return _con_reintento_optimista(codigo, -cantidad, _mov)


def restar_stock_por_lector(codigo: str, *, usuario: str, cantidad: int = 1,
                             origen: str = "MAESTRO") -> int:
    """Atajo para el flujo 'enfocar input oculto -> leer EAN -> descontar
    N unidades', usado en el panel del dueño para salidas por lector."""
    return restar_stock_manual(codigo, cantidad, usuario=usuario,
                                motivo="Lector de código de barras", origen=origen)


def sumar_stock_por_factura_pdf(items: list, *, usuario: str, factura_nombre: str,
                                 origen: str = "MAESTRO") -> list:
    """items: lista de dicts {codigo, cantidad, precio_compra}. Aplica
    todo el remito en una única corrida de transacciones (una por línea,
    para no bloquear el resto del stock por una línea con error) y
    devuelve el detalle de qué se aplicó y qué falló."""
    resultados = []
    for item in items:
        try:
            nuevo_stock = _con_reintento_optimista(
                item["codigo"], item["cantidad"],
                lambda conn, producto, ns, it=item: _registrar_movimiento(
                    conn, producto_codigo=it["codigo"], tipo="ENTRADA_PDF",
                    cantidad=it["cantidad"], stock_resultante=ns,
                    motivo=f"Factura PDF: {factura_nombre}", ticket_uuid=None,
                    usuario=usuario, origen=origen),
            )
            if item.get("precio_compra"):
                with transaction() as conn:
                    conn.execute(
                        "UPDATE Productos SET precio_compra = ?, actualizado_en = ?, "
                        "version = version + 1 WHERE codigo = ?",
                        (item["precio_compra"], _now(), item["codigo"]),
                    )
            resultados.append({"codigo": item["codigo"], "ok": True, "stock_nuevo": nuevo_stock})
        except (ProductoNoEncontradoError, StockInsuficienteError) as e:
            resultados.append({"codigo": item["codigo"], "ok": False, "error": str(e)})
    return resultados
