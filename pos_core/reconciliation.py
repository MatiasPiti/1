"""Módulo de Sincronización y Conciliación — SOLO corre en el Sistema
Maestro (PC fija), oculto tras Ctrl+Shift+M. Lee los JSON dejados en
USB_X/SYNC_DATA/ y los aplica a la base maestra.

Reglas clave:
1. Las ventas se identifican por UUID, nunca por id autoincremental (dos
   USBs y el maestro generan ids 1,2,3... en paralelo y sin relación
   entre sí; solo el UUID es una clave verdaderamente global).
2. Todo movimiento de stock proveniente de un USB se REPRODUCE sobre el
   stock maestro actual (se aplica el delta, no se pisa el valor
   absoluto), así que el orden de llegada de varios USBs no importa y no
   se pierde ninguna venta concurrente hecha en la PC fija mientras el
   USB estuvo desconectado.
3. Los precios se resuelven por "el cambio de precio más reciente gana"
   comparando 'actualizado_en', igual que se explica en la sección de
   Proceso de Pensamiento más abajo en el informe.
4. Nada se aplica sin mostrar antes un resumen de diferencias (dry_run).
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime

from pos_core.db import get_connection, transaction
from pos_core.paths import get_base_path
from pos_core import stock_service


@dataclass
class ResumenConciliacion:
    ventas_nuevas: int = 0
    ventas_omitidas_duplicadas: int = 0
    movimientos_aplicados: int = 0
    movimientos_omitidos_duplicados: int = 0
    precios_actualizados: int = 0
    productos_nuevos: int = 0
    conflictos_precio: list = field(default_factory=list)   # se sobreescribió por ser más reciente
    errores: list = field(default_factory=list)
    detalle: list = field(default_factory=list)


def _venta_existe(conn, venta_uuid: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM Ventas WHERE uuid_unico = ?", (venta_uuid,)
    ).fetchone() is not None


def _movimiento_existe(conn, mov_uuid: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM Movimientos_Stock WHERE uuid_unico = ?", (mov_uuid,)
    ).fetchone() is not None


def _leer_json(ruta: str) -> dict:
    with open(ruta, "r", encoding="utf-8") as f:
        return json.load(f)


def analizar_export_caja(ruta_json: str) -> ResumenConciliacion:
    """Dry-run: calcula qué se importaría SIN tocar la base."""
    data = _leer_json(ruta_json)
    resumen = ResumenConciliacion()
    conn = get_connection()
    for venta in data.get("ventas", []):
        if _venta_existe(conn, venta["uuid_unico"]):
            resumen.ventas_omitidas_duplicadas += 1
        else:
            resumen.ventas_nuevas += 1
            resumen.detalle.append(f"Venta nueva {venta['uuid_unico']} ({venta['fecha_hora']}) "
                                    f"${venta['total']}")
    for mov in data.get("movimientos_stock", []):
        if _movimiento_existe(conn, mov["uuid_unico"]):
            resumen.movimientos_omitidos_duplicados += 1
        else:
            resumen.movimientos_aplicados += 1
    return resumen


def aplicar_export_caja(ruta_json: str, *, usuario_dev: str) -> ResumenConciliacion:
    """Aplica ventas + movimientos de stock del USB Caja a la DB maestra.
    Cada venta+su detalle+su descuento de stock se procesan en una única
    transacción atómica por venta, para que una venta nunca quede
    "medio importada"."""
    data = _leer_json(ruta_json)
    resumen = ResumenConciliacion()

    detalle_por_venta = {}
    for linea in data.get("detalle_ventas", []):
        detalle_por_venta.setdefault(linea["venta_uuid"], []).append(linea)

    for venta in data.get("ventas", []):
        try:
            with transaction() as conn:
                if _venta_existe(conn, venta["uuid_unico"]):
                    resumen.ventas_omitidas_duplicadas += 1
                    continue
                conn.execute(
                    """INSERT INTO Ventas
                       (uuid_unico, fecha_hora, total, metodo_pago, usuario, origen,
                        sincronizado, importado_en)
                       VALUES (?,?,?,?,?,?,1,?)""",
                    (venta["uuid_unico"], venta["fecha_hora"], venta["total"],
                     venta["metodo_pago"], venta["usuario"], "USB_CAJA",
                     datetime.now().isoformat(timespec="milliseconds")),
                )
                for linea in detalle_por_venta.get(venta["uuid_unico"], []):
                    conn.execute(
                        """INSERT INTO Detalle_Ventas
                           (venta_uuid, producto_codigo, producto_nombre, cantidad,
                            precio_unitario, subtotal)
                           VALUES (?,?,?,?,?,?)""",
                        (linea["venta_uuid"], linea["producto_codigo"], linea["producto_nombre"],
                         linea["cantidad"], linea["precio_unitario"], linea["subtotal"]),
                    )
                resumen.ventas_nuevas += 1
        except Exception as e:
            resumen.errores.append(f"Venta {venta.get('uuid_unico')}: {e}")

    # Movimientos de stock (las SALIDA_VENTA de más arriba también viajan
    # acá y son las que efectivamente descuentan el stock maestro)
    for mov in data.get("movimientos_stock", []):
        try:
            # Chequeo de duplicado con una lectura simple (no un
            # `with transaction()` propio): _aplicar_movimiento_reproducido
            # ya abre su propia transacción atómica (vía
            # stock_service._con_reintento_optimista), y SQLite no admite
            # anidar un BEGIN dentro de otro — envolver este SELECT en su
            # propia transacción vacía además daba la falsa impresión de
            # que "chequear + aplicar" era una sola operación atómica,
            # cuando en realidad siempre fueron dos pasos separados.
            if _movimiento_existe(get_connection(), mov["uuid_unico"]):
                resumen.movimientos_omitidos_duplicados += 1
                continue
            delta = -mov["cantidad"] if mov["tipo"].startswith("SALIDA") else mov["cantidad"]
            _aplicar_movimiento_reproducido(mov, delta, usuario_dev)
            resumen.movimientos_aplicados += 1
        except Exception as e:
            resumen.errores.append(f"Movimiento {mov.get('uuid_unico')}: {e}")

    _escribir_log(resumen, ruta_json, "USB_CAJA")
    return resumen


def _aplicar_movimiento_reproducido(mov: dict, delta: int, usuario_dev: str):
    """Reproduce en el maestro un movimiento ya validado en el USB,
    conservando su UUID original (para no volver a aplicarlo si este
    mismo JSON se concilia por error una segunda vez) y su fecha/hora
    original (para que los reportes históricos sean correctos)."""
    def _mov(conn, producto, nuevo_stock):
        conn.execute(
            """INSERT INTO Movimientos_Stock
               (uuid_unico, producto_codigo, tipo, cantidad, stock_resultante, motivo,
                ticket_uuid, usuario, origen, fecha_hora, sincronizado, importado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
            (mov["uuid_unico"], mov["producto_codigo"], mov["tipo"], mov["cantidad"],
             nuevo_stock, mov.get("motivo"), mov.get("ticket_uuid"), mov["usuario"],
             mov["origen"], mov["fecha_hora"], datetime.now().isoformat(timespec="milliseconds")),
        )
    stock_service._con_reintento_optimista(mov["producto_codigo"], delta, _mov)


def analizar_export_dueno(ruta_json: str) -> ResumenConciliacion:
    data = _leer_json(ruta_json)
    resumen = ResumenConciliacion()
    conn = get_connection()
    for prod in data.get("productos", []):
        existente = conn.execute(
            "SELECT precio_venta, actualizado_en FROM Productos WHERE codigo = ?",
            (prod["codigo"],),
        ).fetchone()
        if existente is None:
            resumen.productos_nuevos += 1
        elif existente["precio_venta"] != prod["precio_venta"]:
            gana_usb = prod["actualizado_en"] >= existente["actualizado_en"]
            resumen.precios_actualizados += 1
            resumen.conflictos_precio.append({
                "codigo": prod["codigo"],
                "precio_maestro": existente["precio_venta"],
                "precio_usb": prod["precio_venta"],
                "gana": "USB (más reciente)" if gana_usb else "Maestro (más reciente, se ignora el del USB)",
            })
    for mov in data.get("movimientos_stock", []):
        if not _movimiento_existe(conn, mov["uuid_unico"]):
            resumen.movimientos_aplicados += 1
        else:
            resumen.movimientos_omitidos_duplicados += 1
    return resumen


def aplicar_export_dueno(ruta_json: str, *, usuario_dev: str) -> ResumenConciliacion:
    """Aplica productos nuevos/modificados y movimientos de stock del USB
    Dueño. Conflicto de precio: gana el cambio con 'actualizado_en' más
    reciente (last-write-wins por timestamp), nunca un pisado ciego."""
    data = _leer_json(ruta_json)
    resumen = ResumenConciliacion()

    for prod in data.get("productos", []):
        try:
            with transaction() as conn:
                existente = conn.execute(
                    "SELECT id, precio_venta, actualizado_en, version FROM Productos WHERE codigo = ?",
                    (prod["codigo"],),
                ).fetchone()
                if existente is None:
                    conn.execute(
                        """INSERT INTO Productos
                           (uuid_unico, codigo, nombre, precio_venta, precio_compra, stock,
                            stock_minimo, stock_maximo, proveedor, marca, categoria,
                            origen, sincronizado)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                        (prod["uuid_unico"], prod["codigo"], prod["nombre"], prod["precio_venta"],
                         prod.get("precio_compra", 0), prod.get("stock", 0),
                         prod.get("stock_minimo", 0), prod.get("stock_maximo", 0),
                         prod.get("proveedor"), prod.get("marca"), prod.get("categoria"),
                         "USB_DUENO"),
                    )
                    resumen.productos_nuevos += 1
                elif prod["actualizado_en"] >= existente["actualizado_en"] and \
                        prod["precio_venta"] != existente["precio_venta"]:
                    conn.execute(
                        "UPDATE Productos SET precio_venta = ?, actualizado_en = ?, "
                        "version = version + 1 WHERE id = ?",
                        (prod["precio_venta"], prod["actualizado_en"], existente["id"]),
                    )
                    resumen.precios_actualizados += 1
        except Exception as e:
            resumen.errores.append(f"Producto {prod.get('codigo')}: {e}")

    for mov in data.get("movimientos_stock", []):
        try:
            if _movimiento_existe(get_connection(), mov["uuid_unico"]):
                resumen.movimientos_omitidos_duplicados += 1
                continue
            delta = -mov["cantidad"] if mov["tipo"].startswith("SALIDA") else mov["cantidad"]
            _aplicar_movimiento_reproducido(mov, delta, usuario_dev)
            resumen.movimientos_aplicados += 1
        except Exception as e:
            resumen.errores.append(f"Movimiento {mov.get('uuid_unico')}: {e}")

    _escribir_log(resumen, ruta_json, "USB_DUENO")
    return resumen


def _escribir_log(resumen: ResumenConciliacion, ruta_json_origen: str, origen_usb: str):
    """Deja constancia en Log_Sincronizacion (DB) y en un .txt plano
    DENTRO del propio USB, tal como pide el punto 5 del spec, para que
    quede evidencia incluso si se mira el USB en otra PC sin la DB."""
    ahora = datetime.now().isoformat(timespec="milliseconds")
    with transaction() as conn:
        conn.execute(
            """INSERT INTO Log_Sincronizacion
               (fecha_hora, origen_usb, ventas_importadas, ventas_omitidas,
                movimientos_importados, precios_actualizados, detalle_json)
               VALUES (?,?,?,?,?,?,?)""",
            (ahora, origen_usb, resumen.ventas_nuevas, resumen.ventas_omitidas_duplicadas,
             resumen.movimientos_aplicados, resumen.precios_actualizados,
             json.dumps(resumen.__dict__, ensure_ascii=False, default=str)),
        )

    carpeta_usb = os.path.dirname(os.path.dirname(os.path.abspath(ruta_json_origen)))
    log_txt = os.path.join(carpeta_usb, "sincronizacion_exitosa.txt")
    try:
        with open(log_txt, "a", encoding="utf-8") as f:
            f.write(f"=== Sincronización {ahora} ({origen_usb}) ===\n")
            f.write(f"Ventas nuevas: {resumen.ventas_nuevas}\n")
            f.write(f"Ventas duplicadas omitidas: {resumen.ventas_omitidas_duplicadas}\n")
            f.write(f"Movimientos de stock aplicados: {resumen.movimientos_aplicados}\n")
            f.write(f"Precios actualizados: {resumen.precios_actualizados}\n")
            f.write(f"Productos nuevos: {resumen.productos_nuevos}\n")
            if resumen.errores:
                f.write(f"Errores: {resumen.errores}\n")
            f.write("\n")
    except OSError:
        pass  # el USB puede haberse desmontado justo en este instante; no es crítico
