"""Ofertas y promociones temporales.

Decisión de diseño clave: el precio "de lista" (Productos.precio_venta)
NUNCA se toca por una oferta. El descuento se calcula al vuelo cada vez
que se pide el precio de un producto (ver precio_con_oferta, usado desde
pos_core.sales.buscar_productos), comparando la fecha de hoy contra
[fecha_inicio, fecha_fin] de la oferta. Por eso "volver la lista de
precios a la normalidad" al vencer no requiere ninguna tarea en segundo
plano ni riesgo de perder el precio original: simplemente, un día
después de fecha_fin, la consulta deja de encontrar una oferta vigente y
listo.
"""

import uuid
from datetime import datetime, timedelta

from pos_core.db import get_connection, transaction

TIPOS_DESCUENTO = ("PORCENTAJE", "MONTO_FIJO", "PRECIO_FIJO")


def crear_oferta(*, codigo: str, tipo_descuento: str, valor: float, descripcion: str,
                  dias: int, usuario: str) -> str:
    if tipo_descuento not in TIPOS_DESCUENTO:
        raise ValueError(f"Tipo de descuento inválido: {tipo_descuento}")
    if dias <= 0:
        raise ValueError("La duración tiene que ser mayor a 0 días")
    if valor <= 0:
        raise ValueError("El valor del descuento/precio tiene que ser mayor a 0")
    if tipo_descuento == "PORCENTAJE" and valor >= 100:
        # Un 100% (o más) dejaba el precio efectivo en $0 y la Caja cobraba
        # $0 por ese producto sin ningún aviso.
        raise ValueError("Un descuento por porcentaje tiene que ser menor a 100%")

    conn = get_connection()
    producto = conn.execute(
        "SELECT nombre, precio_venta FROM Productos WHERE codigo = ? AND activo = 1", (codigo,)
    ).fetchone()
    if producto is None:
        raise ValueError(f"No existe el producto con código '{codigo}'")
    if tipo_descuento == "MONTO_FIJO" and valor >= float(producto["precio_venta"] or 0):
        # El mismo agujero que el 100 % de arriba, por el otro lado: un
        # descuento de $ 5.000 sobre un producto de $ 4.500 dejaba el precio
        # efectivo en $ 0 y la Caja lo cobraba $ 0 sin ningún aviso.
        raise ValueError(
            f"El descuento (${valor:.2f}) es igual o mayor que el precio del producto "
            f"(${float(producto['precio_venta'] or 0):.2f}): quedaría en $0. "
            f"Si querías poner el precio de la promoción, elegí 'Precio fijo promocional'.")

    hoy = datetime.now().date()
    fecha_fin = hoy + timedelta(days=dias)

    with transaction() as conn:
        conn.execute(
            """INSERT INTO Ofertas
               (uuid_unico, producto_codigo, tipo_descuento, valor, descripcion,
                fecha_inicio, fecha_fin, activa, creado_por, creado_en)
               VALUES (?,?,?,?,?,?,?,1,?,?)""",
            (str(uuid.uuid4()), codigo, tipo_descuento, valor, descripcion or "",
             hoy.isoformat(), fecha_fin.isoformat(), usuario,
             datetime.now().isoformat(timespec="milliseconds")),
        )
    return fecha_fin.isoformat()


def _calcular_precio(precio_base: float, tipo_descuento: str, valor: float) -> float:
    if tipo_descuento == "PORCENTAJE":
        nuevo = precio_base * (1 - valor / 100.0)
    elif tipo_descuento == "MONTO_FIJO":
        nuevo = precio_base - valor
    else:  # PRECIO_FIJO
        nuevo = valor
    return max(round(nuevo, 2), 0.0)


def oferta_activa_para(codigo: str):
    """Devuelve el dict de la oferta vigente hoy para ese código, o None."""
    conn = get_connection()
    row = conn.execute(
        """SELECT * FROM Ofertas WHERE producto_codigo = ? AND activa = 1
           AND date('now','localtime') BETWEEN fecha_inicio AND fecha_fin
           ORDER BY creado_en DESC LIMIT 1""",
        (codigo,),
    ).fetchone()
    return dict(row) if row else None


def precio_con_oferta(codigo: str, precio_base: float):
    """Devuelve (precio_efectivo, oferta_o_None). Si no hay oferta vigente,
    precio_efectivo == precio_base sin ninguna modificación."""
    oferta = oferta_activa_para(codigo)
    if not oferta:
        return precio_base, None
    precio = _calcular_precio(precio_base, oferta["tipo_descuento"], oferta["valor"])
    if precio <= 0 and precio_base > 0:
        # La oferta dejaría el producto gratis. No pasa al crearla (se
        # rechaza), pero sí si después le bajaron el precio al producto por
        # debajo del descuento. Entre regalar la mercadería y cobrar el
        # precio de lista, la Caja cobra el de lista: es lo mismo que pasa
        # cuando la oferta vence. En el Panel la oferta se sigue viendo, con
        # su "-> $0.00", para que el dueño la corrija.
        return precio_base, None
    return precio, oferta


def listar_ofertas() -> list:
    conn = get_connection()
    rows = conn.execute(
        """SELECT o.*, p.nombre AS producto_nombre, p.precio_venta AS precio_normal
           FROM Ofertas o JOIN Productos p ON p.codigo = o.producto_codigo
           ORDER BY o.creado_en DESC"""
    ).fetchall()
    resultado = []
    for r in rows:
        oferta = dict(r)
        if not oferta["activa"]:
            oferta["estado"] = "CANCELADA"
        elif oferta["fecha_fin"] < datetime.now().date().isoformat():
            oferta["estado"] = "VENCIDA"
        elif oferta["fecha_inicio"] > datetime.now().date().isoformat():
            oferta["estado"] = "PROGRAMADA"
        else:
            oferta["estado"] = "ACTIVA"
        oferta["precio_con_descuento"] = _calcular_precio(
            oferta["precio_normal"], oferta["tipo_descuento"], oferta["valor"])
        resultado.append(oferta)
    return resultado


def cancelar_oferta(oferta_id: int) -> None:
    with transaction() as conn:
        conn.execute("UPDATE Ofertas SET activa = 0 WHERE id = ?", (oferta_id,))
