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
5. La CARGA DEL CATÁLOGO en un pendrive no es mercadería que entró al
   negocio. Al USB se le carga la lista exportada del Maestro, con el stock
   de ese día, y cada producto queda con un movimiento ENTRADA_EXCEL por
   esa cantidad. Reproducir esos movimientos acá le sumaba al Maestro el
   stock del catálogo ENTERO otra vez (verificado: 20 cocas pasaban a 36
   después de vender 4 desde el USB). Se saltean siempre, salvo para un
   producto que NACIÓ en ese USB (no existía en el Maestro): ese entra con
   stock 0 y sus movimientos lo llevan al stock que tenía en el pendrive.

Limitación conocida, sin resolver: 'actualizado_en' lo pisa cualquier
movimiento de stock, no solo un cambio de precio. Un USB con el catálogo
viejo en el que se movió stock puede figurar como "más reciente" y pisar un
precio más nuevo del Maestro. El resumen previo muestra cada conflicto de
precio y quién gana: hay que leerlo antes de aplicar.
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
    movimientos_omitidos_carga_inicial: int = 0   # la carga del catálogo en el USB (regla 5)
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


def _uuid_de_productos(conn, codigos) -> dict:
    """{codigo: uuid_unico} de los productos del Maestro con esos códigos."""
    codigos = sorted({c for c in codigos if c})
    encontrados = {}
    for i in range(0, len(codigos), 500):
        tanda = codigos[i:i + 500]
        marcas = ",".join("?" for _ in tanda)
        for r in conn.execute(
                f"SELECT codigo, uuid_unico FROM Productos WHERE codigo IN ({marcas})", tanda):
            encontrados[r["codigo"]] = r["uuid_unico"]
    return encontrados


def _productos_del_export(data: dict) -> list:
    """Todos los productos que trae un export del Dueño: los pendientes y,
    en los archivos nuevos, los que ya habían salido en otra exportación."""
    return list(data.get("productos") or []) + list(data.get("productos_ya_exportados") or [])


def _es_carga_inicial_del_usb(mov: dict, uuid_en_maestro: dict, uuid_en_usb: dict) -> bool:
    """¿Este movimiento es la carga del catálogo en el pendrive (regla 5)?

    Lo es un ENTRADA_EXCEL sobre un producto que el Maestro ya tiene POR SU
    CUENTA. Si el producto del Maestro es el mismo registro que el del USB
    (mismo uuid: lo creó una conciliación de este pendrive, ahora o antes),
    nació en el USB y su ENTRADA_EXCEL sí es stock de verdad. Si el producto
    no existe en el Maestro no se saltea: tiene que salir el error de siempre.
    """
    if mov.get("tipo") != "ENTRADA_EXCEL":
        return False
    codigo = mov.get("producto_codigo")
    en_maestro = uuid_en_maestro.get(codigo)
    if en_maestro is None:
        return False
    return en_maestro != uuid_en_usb.get(codigo)


def es_export_completo(ruta_json: str) -> bool:
    """¿El archivo trae TODO lo del USB (los de ahora) o solo lo nuevo desde
    la exportación anterior (los de antes de noviembre 2026)? Con uno de los
    viejos puede faltar lo que salió en un archivo anterior."""
    try:
        return bool(_leer_json(ruta_json).get("completo"))
    except ValueError:
        return False


def _leer_json(ruta: str) -> dict:
    """Lee el JSON de exportación de un USB.

    Un archivo cortado a la mitad (el USB se sacó mientras se exportaba) es
    un caso realista: se traduce a un mensaje claro en vez de dejar salir un
    JSONDecodeError crudo a la pantalla de conciliación.
    """
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"El archivo de sincronización está dañado o incompleto ({os.path.basename(ruta)}): {e}. "
            f"Volvé a generar la exportación desde el USB ('Preparar sincronización').") from e
    except OSError as e:
        raise ValueError(f"No se pudo leer el archivo de sincronización {ruta}: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"El archivo {os.path.basename(ruta)} no tiene el formato esperado.")
    return data


def _es_mas_reciente(fecha_usb, fecha_maestro) -> bool:
    """¿El dato del USB es igual o más nuevo que el del maestro?

    `actualizado_en` puede venir NULL en bases viejas migradas (ver
    pos_core.db.aplicar_migraciones: SQLite no deja agregar una columna con
    DEFAULT calculado, así que las filas existentes quedan en NULL).
    Comparar str >= None revienta con TypeError, así que se resuelve acá:
    si el maestro no tiene fecha, cualquier dato con fecha se considera más
    nuevo; si ninguno la tiene, no se pisa nada.
    """
    if fecha_maestro is None:
        return fecha_usb is not None
    if fecha_usb is None:
        return False
    return str(fecha_usb) >= str(fecha_maestro)


def detectar_tipo_export(ruta_json: str) -> str:
    """Devuelve 'export_caja' o 'export_dueno' leyendo el campo 'tipo' que
    el propio archivo trae adentro (ver pos_core/sync_export.py).

    Antes esto se decidía mirando si el NOMBRE del archivo contenía "caja",
    lo cual se equivoca apenas alguien renombra el archivo o lo elige a
    mano: un export del Dueño tomado como export de Caja no importa nada y
    encima informa "0 cambios", que parece un USB vacío en vez de un error.
    Si el archivo no declara el tipo (formato viejo), se cae al nombre.
    """
    try:
        tipo = (_leer_json(ruta_json).get("tipo") or "").strip().lower()
    except ValueError:
        tipo = ""
    if tipo in ("export_caja", "export_dueno"):
        return tipo
    return "export_caja" if "caja" in os.path.basename(ruta_json).lower() else "export_dueno"


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
    movimientos = data.get("movimientos_stock", [])
    uuid_en_maestro = _uuid_de_productos(conn, [m.get("producto_codigo") for m in movimientos])
    for mov in movimientos:
        if _movimiento_existe(conn, mov["uuid_unico"]):
            resumen.movimientos_omitidos_duplicados += 1
        elif _es_carga_inicial_del_usb(mov, uuid_en_maestro, {}):
            resumen.movimientos_omitidos_carga_inicial += 1
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
    movimientos = data.get("movimientos_stock", [])
    uuid_en_maestro = _uuid_de_productos(get_connection(),
                                         [m.get("producto_codigo") for m in movimientos])
    for mov in movimientos:
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
            # La Caja del USB no crea productos en el Maestro: cualquier
            # ENTRADA_EXCEL suya es la carga del catálogo en el pendrive.
            if _es_carga_inicial_del_usb(mov, uuid_en_maestro, {}):
                resumen.movimientos_omitidos_carga_inicial += 1
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
            resumen.detalle.append(f"Producto nuevo {prod['codigo']} {prod.get('nombre', '')}")
        elif existente["precio_venta"] != prod["precio_venta"]:
            gana_usb = _es_mas_reciente(prod.get("actualizado_en"), existente["actualizado_en"])
            resumen.precios_actualizados += 1
            resumen.conflictos_precio.append({
                "codigo": prod["codigo"],
                "precio_maestro": existente["precio_venta"],
                "precio_usb": prod["precio_venta"],
                "gana": "USB (más reciente)" if gana_usb else "Maestro (más reciente, se ignora el del USB)",
            })
    # Los que ya habían salido en otra exportación: solo se crea el que falte.
    ya_exportados = data.get("productos_ya_exportados") or []
    en_maestro = _uuid_de_productos(conn, [p.get("codigo") for p in ya_exportados])
    for prod in ya_exportados:
        if prod.get("codigo") not in en_maestro:
            resumen.productos_nuevos += 1
            resumen.detalle.append(f"Producto nuevo {prod['codigo']} {prod.get('nombre', '')}")

    # Lo que nace en esta misma conciliación cuenta como del USB: su carga
    # inicial sí se aplica (ver _es_carga_inicial_del_usb).
    uuid_en_usb = {p.get("codigo"): p.get("uuid_unico") for p in _productos_del_export(data)}
    movimientos = data.get("movimientos_stock", [])
    uuid_en_maestro = _uuid_de_productos(conn, [m.get("producto_codigo") for m in movimientos])
    for codigo, uuid_usb in uuid_en_usb.items():
        uuid_en_maestro.setdefault(codigo, uuid_usb)
    for mov in movimientos:
        if _movimiento_existe(conn, mov["uuid_unico"]):
            resumen.movimientos_omitidos_duplicados += 1
        elif _es_carga_inicial_del_usb(mov, uuid_en_maestro, uuid_en_usb):
            resumen.movimientos_omitidos_carga_inicial += 1
        else:
            resumen.movimientos_aplicados += 1
    return resumen


def aplicar_export_dueno(ruta_json: str, *, usuario_dev: str) -> ResumenConciliacion:
    """Aplica productos nuevos/modificados y movimientos de stock del USB
    Dueño. Conflicto de precio: gana el cambio con 'actualizado_en' más
    reciente (last-write-wins por timestamp), nunca un pisado ciego."""
    data = _leer_json(ruta_json)
    resumen = ResumenConciliacion()

    def _crear_producto_del_usb(conn, prod: dict) -> None:
        # Entra con stock 0 y NO con el stock que tenía en el pendrive: ese
        # stock ya viene explicado por sus movimientos (el alta, la carga del
        # Excel, las entradas a mano), que se reproducen más abajo. Con las
        # dos cosas quedaba contado dos veces: un producto creado en el USB
        # con 10 unidades y 5 más después llegaba al Maestro con 30.
        conn.execute(
            """INSERT INTO Productos
               (uuid_unico, codigo, nombre, precio_venta, precio_compra, stock,
                stock_minimo, stock_maximo, proveedor, marca, categoria, subrubro,
                costo_sin_iva, margen_ganancia, origen, sincronizado)
               VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,1)""",
            (prod["uuid_unico"], prod["codigo"], prod["nombre"], prod["precio_venta"],
             prod.get("precio_compra") or 0,
             prod.get("stock_minimo") or 0, prod.get("stock_maximo") or 0,
             prod.get("proveedor"), prod.get("marca"), prod.get("categoria"), prod.get("subrubro"),
             prod.get("costo_sin_iva") or 0, prod.get("margen_ganancia") or 0,
             "USB_DUENO"),
        )

    for prod in data.get("productos", []):
        try:
            with transaction() as conn:
                existente = conn.execute(
                    "SELECT id, precio_venta, actualizado_en, version FROM Productos WHERE codigo = ?",
                    (prod["codigo"],),
                ).fetchone()
                if existente is None:
                    _crear_producto_del_usb(conn, prod)
                    resumen.productos_nuevos += 1
                elif prod["precio_venta"] != existente["precio_venta"] and \
                        _es_mas_reciente(prod.get("actualizado_en"), existente["actualizado_en"]):
                    conn.execute(
                        "UPDATE Productos SET precio_venta = ?, actualizado_en = ?, "
                        "version = version + 1 WHERE id = ?",
                        (prod["precio_venta"],
                         prod.get("actualizado_en") or datetime.now().isoformat(timespec="milliseconds"),
                         existente["id"]),
                    )
                    resumen.precios_actualizados += 1
        except Exception as e:
            resumen.errores.append(f"Producto {prod.get('codigo')}: {e}")

    # Los que ya habían salido en otra exportación: solo se crea el que
    # falte (su exportación anterior nunca se concilió). Nunca se les toca
    # el precio: eso es solo para los pendientes de arriba.
    for prod in data.get("productos_ya_exportados") or []:
        try:
            with transaction() as conn:
                if conn.execute("SELECT 1 FROM Productos WHERE codigo = ?",
                                (prod["codigo"],)).fetchone() is None:
                    _crear_producto_del_usb(conn, prod)
                    resumen.productos_nuevos += 1
        except Exception as e:
            resumen.errores.append(f"Producto {prod.get('codigo')}: {e}")

    uuid_en_usb = {p.get("codigo"): p.get("uuid_unico") for p in _productos_del_export(data)}
    movimientos = data.get("movimientos_stock", [])
    uuid_en_maestro = _uuid_de_productos(get_connection(),
                                         [m.get("producto_codigo") for m in movimientos])
    for mov in movimientos:
        try:
            if _movimiento_existe(get_connection(), mov["uuid_unico"]):
                resumen.movimientos_omitidos_duplicados += 1
                continue
            if _es_carga_inicial_del_usb(mov, uuid_en_maestro, uuid_en_usb):
                resumen.movimientos_omitidos_carga_inicial += 1
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
            f.write(f"Movimientos de carga del catálogo del USB (no se aplican): "
                    f"{resumen.movimientos_omitidos_carga_inicial}\n")
            f.write(f"Precios actualizados: {resumen.precios_actualizados}\n")
            f.write(f"Productos nuevos: {resumen.productos_nuevos}\n")
            if resumen.errores:
                f.write(f"Errores: {resumen.errores}\n")
            f.write("\n")
    except OSError:
        pass  # el USB puede haberse desmontado justo en este instante; no es crítico
