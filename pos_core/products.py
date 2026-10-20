"""Alta manual de productos nuevos (sin pasar por un Excel), para cuando
llega un solo artículo nuevo y no vale la pena armar una planilla entera.
La usan el Panel del Dueño, el Dueño Remoto y la app del celular.
"""

import uuid
from datetime import datetime

from pos_core.db import get_connection, transaction


class ProductoExistenteError(ValueError):
    """El código ya está en Productos (activo o dado de baja). Es ValueError
    para que el Panel y el Dueño Remoto lo sigan mostrando como siempre; la
    API del celular lo distingue para contestar 409 con el producto."""


def crear_producto(*, codigo: str, nombre: str, precio_venta: float, stock_inicial: int = 0,
                    proveedor: str = None, marca: str = None, categoria: str = None,
                    usuario: str, origen: str = "MAESTRO") -> None:
    from pos_core.sales import CODIGO_SIN_BARRA

    codigo = (codigo or "").strip()
    nombre = (nombre or "").strip()
    if not codigo or not nombre:
        raise ValueError("Código y nombre son obligatorios")
    if codigo == CODIGO_SIN_BARRA:
        # La Caja usa este código para los artículos sin código de barras y
        # cerrar_ticket() nunca le descuenta stock: un producto con este código
        # quedaría con el stock clavado y chocaría con el botón de la Caja.
        raise ValueError(f"El código {CODIGO_SIN_BARRA} lo usa la Caja para los artículos sin código de "
                         f"barras: elegí otro.")
    if precio_venta < 0 or stock_inicial < 0:
        raise ValueError("Precio y stock no pueden ser negativos")

    now = datetime.now().isoformat(timespec="milliseconds")
    sincronizado = 1 if origen == "MAESTRO" else 0

    with transaction() as conn:
        existente = conn.execute("SELECT 1 FROM Productos WHERE codigo = ?", (codigo,)).fetchone()
        if existente:
            raise ProductoExistenteError(
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


def listar_stock(busqueda: str = None) -> list:
    """Para la pestaña Stock del Panel del Dueño: código/nombre/stock de
    los productos activos, opcionalmente filtrados por código o nombre."""
    conn = get_connection()
    if busqueda:
        like = f"%{busqueda}%"
        rows = conn.execute(
            "SELECT codigo, nombre, stock FROM Productos WHERE activo=1 "
            "AND (codigo LIKE ? OR nombre LIKE ?) ORDER BY nombre", (like, like)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT codigo, nombre, stock FROM Productos WHERE activo=1 ORDER BY nombre"
        ).fetchall()
    return [dict(r) for r in rows]


def listar_para_filtro(busqueda: str = None) -> list:
    """Para la pestaña Filtros/Edición Masiva: código/nombre/precio de los
    productos activos que se pueden elegir para armar un filtro."""
    conn = get_connection()
    if busqueda:
        like = f"%{busqueda}%"
        rows = conn.execute(
            "SELECT codigo, nombre, precio_venta FROM Productos WHERE activo=1 "
            "AND (codigo LIKE ? OR nombre LIKE ?) ORDER BY nombre LIMIT 200", (like, like)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT codigo, nombre, precio_venta FROM Productos WHERE activo=1 ORDER BY nombre LIMIT 200"
        ).fetchall()
    return [dict(r) for r in rows]
