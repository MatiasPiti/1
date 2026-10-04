"""Edición masiva de precios (Bulk Edit).

Regla estricta del negocio: un aumento porcentual siempre redondea hacia
ARRIBA a la centena más cercana (techo, no redondeo "al más cercano").
Ejemplo obligatorio: 2500 + 3% = 2575  ->  2600 (no 2500, no 2575).
"""

import math
from datetime import datetime

from pos_core.db import get_connection, transaction
from pos_core.stock_service import ProductoNoEncontradoError


def redondear_a_centena_superior(valor: float) -> int:
    """math.ceil a la centena: redondea siempre hacia arriba, incluso si
    ya es un múltiplo exacto de 100 no lo modifica (ceil(2600/100)=26)."""
    return int(math.ceil(valor / 100.0) * 100)


def calcular_nuevo_precio(precio_actual: float, *, porcentaje: float = None,
                           monto_fijo: float = None, redondear: bool = True) -> int:
    """Aplica UNO de los dos ajustes (porcentaje o monto fijo). El
    porcentaje puede ser negativo (ej. -5% en una promoción). El
    redondeo a centena superior es el default pedido por el negocio, pero
    queda parametrizado por si el dueño pide un ajuste sin redondear."""
    if porcentaje is not None and monto_fijo is not None:
        raise ValueError("Especificá porcentaje o monto_fijo, no ambos")
    if porcentaje is not None:
        nuevo = precio_actual * (1 + porcentaje / 100.0)
    elif monto_fijo is not None:
        nuevo = precio_actual + monto_fijo
    else:
        raise ValueError("Especificá porcentaje o monto_fijo")

    if nuevo < 0:
        nuevo = 0
    return redondear_a_centena_superior(nuevo) if redondear else round(nuevo, 2)


def previsualizar_ajuste_masivo(codigos: list, *, porcentaje: float = None, monto_fijo: float = None,
                                 redondear: bool = True) -> list:
    """Mismo cálculo que `aplicar_ajuste_masivo` pero sin escribir nada:
    lo usa la app del dueño para mostrar "antes -> después" y pedir
    confirmación antes de tocar precios."""
    conn = get_connection()
    resultados = []
    for codigo in codigos:
        row = conn.execute(
            "SELECT nombre, precio_venta FROM Productos WHERE codigo = ? AND activo = 1", (codigo,)
        ).fetchone()
        if row is None:
            resultados.append({"codigo": codigo, "ok": False, "error": "no encontrado"})
            continue
        resultados.append({
            "codigo": codigo, "ok": True, "nombre": row["nombre"],
            "precio_anterior": row["precio_venta"],
            "precio_nuevo": calcular_nuevo_precio(
                row["precio_venta"], porcentaje=porcentaje, monto_fijo=monto_fijo, redondear=redondear),
        })
    return resultados


def fijar_precio(codigo: str, precio: float, *, usuario: str, origen: str = "MAESTRO") -> dict:
    """Pone un precio de venta exacto a un producto (sin redondeo: el
    dueño escribió el número que quiere)."""
    if precio < 0:
        raise ValueError("El precio no puede ser negativo")
    now = datetime.now().isoformat(timespec="milliseconds")
    with transaction() as conn:
        row = conn.execute(
            "SELECT precio_venta FROM Productos WHERE codigo = ? AND activo = 1", (codigo,)
        ).fetchone()
        if row is None:
            raise ProductoNoEncontradoError(f"Producto con código '{codigo}' no existe o está inactivo")
        conn.execute(
            "UPDATE Productos SET precio_venta = ?, actualizado_en = ?, version = version + 1 "
            "WHERE codigo = ?",
            (precio, now, codigo),
        )
    return {"codigo": codigo, "precio_anterior": row["precio_venta"], "precio_nuevo": precio}


def aplicar_ajuste_masivo(codigos: list, *, porcentaje: float = None, monto_fijo: float = None,
                           redondear: bool = True, usuario: str, origen: str = "MAESTRO") -> list:
    """Aplica el ajuste a una lista de códigos de producto (resultado de
    un filtro guardado o de una selección manual en la grilla). Cada
    producto se actualiza en su propia transacción para no bloquear toda
    la tabla mientras se procesan cientos de artículos."""
    resultados = []
    now = datetime.now().isoformat(timespec="milliseconds")
    for codigo in codigos:
        with transaction() as conn:
            row = conn.execute(
                "SELECT precio_venta FROM Productos WHERE codigo = ? AND activo = 1", (codigo,)
            ).fetchone()
            if row is None:
                resultados.append({"codigo": codigo, "ok": False, "error": "no encontrado"})
                continue
            precio_anterior = row["precio_venta"]
            precio_nuevo = calcular_nuevo_precio(
                precio_anterior, porcentaje=porcentaje, monto_fijo=monto_fijo, redondear=redondear)
            conn.execute(
                "UPDATE Productos SET precio_venta = ?, actualizado_en = ?, version = version + 1 "
                "WHERE codigo = ?",
                (precio_nuevo, now, codigo),
            )
            resultados.append({
                "codigo": codigo, "ok": True,
                "precio_anterior": precio_anterior, "precio_nuevo": precio_nuevo,
            })
    return resultados
