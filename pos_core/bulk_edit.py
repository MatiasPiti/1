"""Edición masiva de precios (Bulk Edit).

Regla estricta del negocio: un aumento porcentual siempre redondea hacia
ARRIBA a la centena más cercana (techo, no redondeo "al más cercano").
Ejemplo obligatorio: 2500 + 3% = 2575  ->  2600 (no 2500, no 2575).
"""

import math
from datetime import datetime

from pos_core.db import transaction


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


def aplicar_ajuste_masivo(codigos: list, *, porcentaje: float = None, monto_fijo: float = None,
                           redondear: bool = True, usuario: str, origen: str = "MAESTRO",
                           esperados: dict = None) -> list:
    """Aplica el ajuste a una lista de códigos de producto (resultado de
    un filtro guardado o de una selección manual en la grilla). Cada
    producto se actualiza en su propia transacción para no bloquear toda
    la tabla mientras se procesan cientos de artículos.

    `esperados` (opcional, lo manda la app del celular) es {codigo: precio
    que mostró la vista previa}. Un producto cuyo precio ya no es ese NO se
    toca y sale con ok=False. Es lo que vuelve seguro "aplicar de nuevo"
    después de un corte con datos móviles: si el primer intento llegó, todos
    tienen ya el precio nuevo y el segundo no suma el aumento otra vez. Con
    None (el Panel no lo manda) el comportamiento es el de siempre.
    """
    # El ajuste se valida ANTES de tocar el primer producto: si los
    # parámetros están mal, la excepción tiene que salir con cero precios
    # modificados. Validándolo recién adentro del bucle (como estaba), el
    # error saltaba en el primer producto y abortaba la función entera, pero
    # en una segunda pasada podía dejar parte del lote ya actualizado y
    # parte no, sin devolverle a la UI ningún resumen de lo aplicado.
    calcular_nuevo_precio(1.0, porcentaje=porcentaje, monto_fijo=monto_fijo, redondear=redondear)

    resultados = []
    now = datetime.now().isoformat(timespec="milliseconds")
    for codigo in codigos:
        # Cada producto en su propia transacción Y con su propio manejo de
        # error: uno que falle (fue borrado en el medio, quedó bloqueado)
        # no debe abortar el resto del lote ni dejar a la UI sin saber qué
        # se llegó a aplicar.
        try:
            with transaction() as conn:
                row = conn.execute(
                    "SELECT precio_venta FROM Productos WHERE codigo = ? AND activo = 1", (codigo,)
                ).fetchone()
                if row is None:
                    resultados.append({"codigo": codigo, "ok": False, "error": "no encontrado"})
                    continue
                precio_anterior = row["precio_venta"]
                if esperados is not None:
                    if codigo not in esperados:
                        resultados.append({"codigo": codigo, "ok": False, "error": "sin precio esperado"})
                        continue
                    visto = float(esperados[codigo] or 0)
                    if abs(float(precio_anterior or 0) - visto) > 0.005:
                        resultados.append({
                            "codigo": codigo, "ok": False,
                            "error": f"el precio cambió mientras tanto (era {visto}, ahora es "
                                     f"{float(precio_anterior or 0)}): no se tocó"})
                        continue
                precio_nuevo = calcular_nuevo_precio(
                    precio_anterior, porcentaje=porcentaje, monto_fijo=monto_fijo,
                    redondear=redondear)
                conn.execute(
                    "UPDATE Productos SET precio_venta = ?, actualizado_en = ?, version = version + 1 "
                    "WHERE codigo = ?",
                    (precio_nuevo, now, codigo),
                )
            resultados.append({
                "codigo": codigo, "ok": True,
                "precio_anterior": precio_anterior, "precio_nuevo": precio_nuevo,
            })
        except Exception as e:
            resultados.append({"codigo": codigo, "ok": False, "error": str(e)})
    return resultados
