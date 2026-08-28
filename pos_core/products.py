"""Alta manual de productos nuevos desde el Panel del Dueño (sin pasar
por un Excel), para cuando llega un solo artículo nuevo y no vale la
pena armar una planilla entera.
"""

import uuid
from datetime import datetime

from pos_core.db import transaction


def crear_producto(*, codigo: str, nombre: str, precio_venta: float, stock_inicial: int = 0,
                    proveedor: str = None, marca: str = None, categoria: str = None,
                    usuario: str, origen: str = "MAESTRO") -> None:
    codigo = (codigo or "").strip()
    nombre = (nombre or "").strip()
    if not codigo or not nombre:
        raise ValueError("Código y nombre son obligatorios")
    if precio_venta < 0 or stock_inicial < 0:
        raise ValueError("Precio y stock no pueden ser negativos")

    now = datetime.now().isoformat(timespec="milliseconds")
    sincronizado = 1 if origen == "MAESTRO" else 0

    with transaction() as conn:
        existente = conn.execute("SELECT 1 FROM Productos WHERE codigo = ?", (codigo,)).fetchone()
        if existente:
            raise ValueError(
                f"Ya existe un producto con código '{codigo}'. "
                f"Usá 'Carga Excel' o la edición masiva si querés actualizarlo.")

        conn.execute(
            """INSERT INTO Productos
               (uuid_unico, codigo, nombre, precio_venta, stock, proveedor, marca, categoria,
                origen, sincronizado)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), codigo, nombre, precio_venta, stock_inicial,
             proveedor or None, marca or None, categoria or None, origen, sincronizado),
        )

        if stock_inicial:
            conn.execute(
                """INSERT INTO Movimientos_Stock
                   (uuid_unico, producto_codigo, tipo, cantidad, stock_resultante, motivo,
                    usuario, origen, fecha_hora, sincronizado)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), codigo, "ENTRADA_MANUAL", stock_inicial, stock_inicial,
                 "Alta de producto nuevo", usuario, origen, now, sincronizado),
            )
