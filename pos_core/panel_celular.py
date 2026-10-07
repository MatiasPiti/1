"""Lecturas para la app del celular (API del celular, services/api_celular.py).

Todo lo de acá es de SOLO LECTURA: las escrituras siguen pasando por las
funciones de siempre (stock_service, precios, bulk_edit, alerts). Vive en
pos_core y no en la API para que la API no escriba SQL y para que las
consultas se puedan probar contra las del Panel, la Caja y el bot.

Nunca crea la base: la ruta se arma a mano (paths.db_path() y
paths.data_dir() crean la carpeta database\\) y la API abre todo en modo
"solo existente" (db.usar_solo_base_existente).
"""

import configparser
import os
import time
from datetime import date, datetime, timedelta

from pos_core import paths
from pos_core.db import columnas_faltantes, get_connection

CAMPOS_BUSQUEDA = ("codigo", "nombre", "marca", "proveedor", "categoria", "subrubro")
PERIODOS_TOP = ("hoy", "7dias", "30dias", "historico")
# Copia de services/stock_daemon_windows.DIAS_VENTANA_WATCHDOG: la API no puede
# importar el demonio (al importarse fija la carpeta base y arma su log). Una
# prueba compara los dos valores.
DIAS_VENTANA_WATCHDOG = 2
MINUTOS_FACTURA_REPETIDA = 60
MAX_EMPAREJAR = 100             # ítems sin código que se emparejan por nombre, como mucho
SEGUNDOS_EMPAREJAR = 20         # y como mucho este tiempo
MAX_PAGINAS_PDF = 10
CACHE_COLUMNAS_S = 600

_CODIGO_SIN_BARRA = "1"         # = sales.CODIGO_SIN_BARRA (la consulta del watchdog lo excluye)

_FILTRO_PERIODO = {
    "hoy": "AND date(v.fecha_hora) = date('now','localtime')",
    "7dias": "AND date(v.fecha_hora) >= date('now','localtime','-6 days')",
    "30dias": "AND date(v.fecha_hora) >= date('now','localtime','-29 days')",
    "historico": "",
}

# Umbral EFECTIVO (el propio activo, si no el global) y oferta vigente en una
# sola consulta. Verificada contra sales.buscar_productos (precio que cobra la
# Caja) y contra telegram_bot._productos_fuera_de_umbral (lo que avisa el bot).
_SQL_PRODUCTOS = """
WITH global AS (
  SELECT stock_minimo, stock_maximo FROM Configuracion_Alertas
  WHERE producto_codigo IS NULL AND activo = 1 ORDER BY id LIMIT 1),
vig AS (
  SELECT o.* FROM Ofertas o
  WHERE o.activa = 1 AND date('now','localtime') BETWEEN o.fecha_inicio AND o.fecha_fin
    AND o.id = (SELECT o2.id FROM Ofertas o2
                WHERE o2.producto_codigo = o.producto_codigo AND o2.activa = 1
                  AND date('now','localtime') BETWEEN o2.fecha_inicio AND o2.fecha_fin
                ORDER BY o2.creado_en DESC, o2.id DESC LIMIT 1))
SELECT p.codigo, p.nombre, p.marca, p.proveedor, p.categoria, p.subrubro, p.stock,
       p.precio_venta, p.precio_compra, p.costo_sin_iva, p.margen_ganancia, p.actualizado_en,
       COALESCE(a.stock_minimo, (SELECT stock_minimo FROM global), 0) AS stock_minimo,
       COALESCE(a.stock_maximo, (SELECT stock_maximo FROM global), 0) AS stock_maximo,
       (a.id IS NOT NULL) AS umbral_propio,
       v.id AS oferta_id, v.tipo_descuento, v.valor AS oferta_valor, v.descripcion AS oferta_descripcion,
       v.fecha_fin AS oferta_fecha_fin
       {extra_columnas}
FROM Productos p
LEFT JOIN Configuracion_Alertas a ON a.producto_codigo = p.codigo AND a.activo = 1
LEFT JOIN vig v ON v.producto_codigo = p.codigo
{extra_join}
WHERE p.activo = 1 AND {filtro}
{orden}
"""


class PdfInvalidoError(Exception):
    """El PDF no se puede analizar acá. El texto es para mostrar tal cual."""


# --------------------------------------------------------------------- #
# Base y config
# --------------------------------------------------------------------- #

def _cooldown() -> timedelta:
    from pos_core import telegram_bot   # import diferido: no hace falta para nada más
    return telegram_bot._COOLDOWN


def ruta_base_datos() -> str:
    return os.path.join(paths.get_base_path(), "database", "stock.db")


def estado_base() -> dict:
    """{"ok", "motivo", "detalle", "faltan"}. Nunca lanza ni crea nada."""
    ruta = ruta_base_datos()
    if not os.path.isfile(ruta):
        return {"ok": False, "motivo": "base_no_disponible",
                "detalle": "no encontré database\\stock.db al lado de la carpeta ApiCelular", "faltan": []}
    try:
        faltan = columnas_faltantes(ruta)
    except Exception as e:
        return {"ok": False, "motivo": "base_no_disponible",
                "detalle": f"no se pudo leer database\\stock.db ({type(e).__name__})", "faltan": []}
    if faltan:
        return {"ok": False, "motivo": "base_desactualizada",
                "detalle": f"faltan columnas: {', '.join(faltan)}; abrí la Caja una vez o corré el Actualizador",
                "faltan": faltan}
    return {"ok": True, "motivo": None, "detalle": "ok", "faltan": []}


_cache_columnas = {"ruta": None, "hasta": 0.0}


def columnas_ok() -> bool:
    """La base tiene todas las columnas del esquema actual.

    Se cachea SOLO el resultado bueno (10 minutos): uno con faltantes se
    vuelve a mirar en el pedido siguiente, así apenas el Actualizador o
    una app migran la base, la API anda sin reiniciar nada.
    FileNotFoundError si la base no está (quien llama lo trata como 503).
    """
    ruta = ruta_base_datos()
    if _cache_columnas["ruta"] == ruta and time.monotonic() < _cache_columnas["hasta"]:
        return True
    if columnas_faltantes(ruta):
        return False
    _cache_columnas["ruta"], _cache_columnas["hasta"] = ruta, time.monotonic() + CACHE_COLUMNAS_S
    return True


def olvidar_cache_columnas() -> None:
    _cache_columnas["ruta"], _cache_columnas["hasta"] = None, 0.0


def _config_real():
    """config.ini tal cual está en disco, SIN los valores por defecto."""
    cfg = configparser.ConfigParser(interpolation=None, strict=False)
    with open(paths.config_path(), "r", encoding="utf-8-sig") as f:
        cfg.read_file(f)
    return cfg


def nombre_local_real() -> str:
    """[general] nombre_local del config.ini REAL; "" si no está o no se lee.

    Sin los defaults a propósito: cargar_config() devuelve "El Galpón Del
    Nono" aunque no exista ningún config.ini, y una API instalada en la
    carpeta equivocada parecería la del negocio.
    """
    try:
        return _config_real().get("general", "nombre_local", fallback="").strip()
    except Exception:
        return ""


def _cargar_config_o_ilegible():
    from pos_core import config
    try:
        return config.cargar_config()
    except configparser.Error as e:
        raise config.ConfigIlegibleError(f"config.ini mal formado: {type(e).__name__}") from e


def telegram_habilitado() -> bool:
    """Misma regla que telegram_bot.enviar_mensaje: solo 'true' lo prende."""
    try:
        cfg = _cargar_config_o_ilegible()
    except Exception:
        return False
    return cfg.get("telegram", "habilitado", fallback="false").lower() == "true"


# --------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------- #

def resumen_dashboard(periodo_top: str = "historico", limite_top: int = 8) -> dict:
    from pos_core import reports
    if periodo_top not in PERIODOS_TOP:
        raise ValueError(f"Período inválido: {periodo_top}")
    conn = get_connection()
    hoy = conn.execute(
        "SELECT COALESCE(SUM(total),0) total, COUNT(*) tickets FROM Ventas "
        "WHERE date(fecha_hora) = date('now','localtime') AND anulada = 0").fetchone()
    por_metodo = sorted(
        ({"metodo_pago": r["metodo_pago"], "tickets": r["cantidad"], "total": r["total"]}
         for r in reports.totales_por_metodo_pago()),
        key=lambda r: -float(r["total"] or 0))
    por_dia = {
        r["dia"]: r for r in conn.execute(
            "SELECT date(fecha_hora) dia, COALESCE(SUM(total),0) total, COUNT(*) tickets FROM Ventas "
            "WHERE anulada = 0 AND date(fecha_hora) >= date('now','localtime','-6 days') GROUP BY dia")
    }
    hoy_local = date.fromisoformat(conn.execute("SELECT date('now','localtime')").fetchone()[0])
    ultimos_7 = []
    for i in range(6, -1, -1):
        dia = (hoy_local - timedelta(days=i)).isoformat()
        r = por_dia.get(dia)
        ultimos_7.append({"dia": dia, "total": r["total"] if r else 0, "tickets": r["tickets"] if r else 0})
    # El top excluye las ventas anuladas (reports.resumen_dashboard las cuenta;
    # hoy main no anula ninguna, así que en la práctica dan lo mismo).
    top = conn.execute(
        f"""SELECT dv.producto_codigo codigo, MAX(dv.producto_nombre) nombre,
                   SUM(dv.cantidad) cantidad, SUM(dv.subtotal) importe
            FROM Detalle_Ventas dv JOIN Ventas v ON v.uuid_unico = dv.venta_uuid
            WHERE v.anulada = 0 {_FILTRO_PERIODO[periodo_top]}
            GROUP BY dv.producto_codigo ORDER BY cantidad DESC, nombre LIMIT ?""",
        (int(limite_top),)).fetchall()
    tickets = hoy["tickets"]
    return {
        "hoy": {"total": hoy["total"], "tickets": tickets,
                "ticket_promedio": round(hoy["total"] / tickets, 2) if tickets else 0,
                "por_metodo": por_metodo},
        "ultimos_7_dias": ultimos_7,
        "top_productos": [dict(r) for r in top],
        "periodo_top": periodo_top,
        "alertas_activas": contar_alertas(),
        "telegram_habilitado": telegram_habilitado(),
    }


# --------------------------------------------------------------------- #
# Productos y movimientos
# --------------------------------------------------------------------- #

def _escapar_like(texto: str) -> str:
    # Igual que sales.buscar_productos: '%' y '_' en un nombre o en lo que
    # mete el lector no pueden volverse comodines.
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _oferta(fila):
    if fila["oferta_id"] is None:
        return None
    return {"id": fila["oferta_id"], "tipo_descuento": fila["tipo_descuento"],
            "valor": float(fila["oferta_valor"] or 0), "descripcion": fila["oferta_descripcion"] or "",
            "fecha_fin": fila["oferta_fecha_fin"]}


def _producto(fila) -> dict:
    from pos_core import ofertas
    oferta = _oferta(fila)
    precio_venta = float(fila["precio_venta"] or 0)
    efectivo = (ofertas._calcular_precio(precio_venta, oferta["tipo_descuento"], oferta["valor"])
                if oferta else precio_venta)
    return {
        "codigo": fila["codigo"], "nombre": fila["nombre"], "marca": fila["marca"],
        "proveedor": fila["proveedor"], "categoria": fila["categoria"], "subrubro": fila["subrubro"],
        "stock": fila["stock"], "stock_minimo": fila["stock_minimo"], "stock_maximo": fila["stock_maximo"],
        "umbral_propio": bool(fila["umbral_propio"]),
        "precio_venta": precio_venta, "precio_efectivo": efectivo, "oferta": oferta,
        "precio_compra": float(fila["precio_compra"] or 0), "costo_sin_iva": float(fila["costo_sin_iva"] or 0),
        "margen_ganancia": float(fila["margen_ganancia"] or 0), "actualizado_en": fila["actualizado_en"],
    }


def _consultar(filtro: str, params: list, *, orden: str = "", extra_columnas: str = "",
               extra_join: str = "") -> list:
    sql = _SQL_PRODUCTOS.format(filtro=filtro, orden=orden, extra_columnas=extra_columnas,
                                extra_join=extra_join)
    return get_connection().execute(sql, params).fetchall()


def buscar_productos(q: str = "", *, campo: str = None, valor: str = "", limite: int = 200) -> list:
    if campo:
        if campo not in CAMPOS_BUSQUEDA:
            raise ValueError(f"Campo de búsqueda no permitido: {campo}")
        termino = (valor or "").strip()
        filtro = f"p.{campo} LIKE ? ESCAPE '\\'" if termino else "1 = 1"
        params = [f"%{_escapar_like(termino)}%"] if termino else []
    else:
        termino = (q or "").strip()
        filtro = "(p.codigo LIKE ? ESCAPE '\\' OR p.nombre LIKE ? ESCAPE '\\')"
        like = f"%{_escapar_like(termino)}%"
        params = [like, like]
    filas = _consultar(filtro, params + [termino, int(limite)],
                       orden="ORDER BY (p.codigo = ?) DESC, p.nombre LIMIT ?")
    return [_producto(f) for f in filas]


def obtener_producto(codigo: str):
    filas = _consultar("p.codigo = ?", [(codigo or "").strip()])
    return _producto(filas[0]) if filas else None


def movimientos(codigo: str = None, limite: int = 30) -> list:
    sql = ("SELECT m.fecha_hora, m.producto_codigo codigo, p.nombre, m.tipo, m.cantidad, "
           "m.stock_resultante, m.motivo, m.usuario FROM Movimientos_Stock m "
           "LEFT JOIN Productos p ON p.codigo = m.producto_codigo")
    params = []
    if codigo:
        sql += " WHERE m.producto_codigo = ?"
        params.append(codigo)
    sql += " ORDER BY m.fecha_hora DESC, m.id DESC LIMIT ?"
    params.append(int(limite))
    return [dict(r) for r in get_connection().execute(sql, params).fetchall()]


# --------------------------------------------------------------------- #
# Alertas y umbrales
# --------------------------------------------------------------------- #

def tipo_alerta(stock: int, minimo: int, maximo: int):
    """Idéntico a telegram_bot.revisar_umbrales_y_alertar: 0 es "no controlar"."""
    if minimo and stock <= minimo:
        return "BAJO"
    if maximo and stock >= maximo:
        return "SOBRE"
    return None


def _fecha(texto):
    try:
        return datetime.fromisoformat(texto) if texto else None
    except (TypeError, ValueError):
        return None


def _alertas() -> list:
    filas = _consultar("1 = 1", [], extra_columnas=", e.ultima_alerta_enviada",
                       extra_join="LEFT JOIN Alertas_Enviadas e ON e.producto_codigo = p.codigo")
    ahora, cooldown = datetime.now(), _cooldown()
    resultado = []
    for f in filas:
        tipo = tipo_alerta(f["stock"], f["stock_minimo"], f["stock_maximo"])
        if tipo is None:
            continue
        ultima = _fecha(f["ultima_alerta_enviada"])
        proximo = ultima + cooldown if ultima is not None and ahora < ultima + cooldown else None
        resultado.append({
            "codigo": f["codigo"], "nombre": f["nombre"], "stock": f["stock"],
            "stock_minimo": f["stock_minimo"], "stock_maximo": f["stock_maximo"], "tipo": tipo,
            "umbral_propio": bool(f["umbral_propio"]),
            # Una fecha que no se puede leer se trata como "sin dato" (el bot
            # hace lo mismo: la toma como "sin cooldown").
            "ultima_alerta_enviada": f["ultima_alerta_enviada"] if ultima is not None else None,
            "proximo_aviso_desde": proximo.isoformat(timespec="milliseconds") if proximo else None,
        })
    resultado.sort(key=lambda a: (a["tipo"] != "BAJO", a["nombre"] or ""))
    return resultado


def listar_alertas(limite: int = 1000) -> list:
    return _alertas()[:int(limite)]


def contar_alertas() -> int:
    return len(_alertas())


def resumen_umbrales() -> dict:
    from pos_core import alerts
    conn = get_connection()
    existe = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM Configuracion_Alertas WHERE producto_codigo IS NULL AND activo = 1)"
    ).fetchone()[0]
    # Mismo JOIN que alerts.listar_umbrales_por_producto (el número del título
    # del Panel), así "inactivos" es un subconjunto de "total".
    inactivos = conn.execute(
        "SELECT COUNT(*) FROM Configuracion_Alertas ca JOIN Productos p ON p.codigo = ca.producto_codigo "
        "WHERE ca.producto_codigo IS NOT NULL AND ca.activo = 0").fetchone()[0]
    return {"umbral_global": dict(alerts.obtener_umbral_global(), existe=bool(existe)),
            "umbrales_propios": {"total": len(alerts.listar_umbrales_por_producto()), "inactivos": inactivos}}


def config_telegram_visible() -> dict:
    """Lo que el celular puede ver del bot. NUNCA un carácter del token
    (regla 4: ya se filtró una vez por una captura de pantalla)."""
    cfg = _cargar_config_o_ilegible()
    token = cfg.get("telegram", "bot_token", fallback="").strip()
    return {"habilitado": cfg.get("telegram", "habilitado", fallback="false").lower() == "true",
            "chat_id_default": cfg.get("telegram", "chat_id_default", fallback="").strip(),
            "token_configurado": bool(token), "token_mascara": "••••••••" if token else ""}


# --------------------------------------------------------------------- #
# Precios
# --------------------------------------------------------------------- #

def precios_para_pantalla(codigo: str):
    """Forma PreciosProducto del contrato; None si el producto no existe."""
    from pos_core import precios
    try:
        p = precios.obtener_para_precios(codigo)
    except ValueError:
        return None
    producto = obtener_producto(p["codigo"])
    crudos = {"costo_sin_iva": float(p["costo_sin_iva"] or 0), "precio_compra": float(p["precio_compra"] or 0),
              "margen_ganancia": float(p["margen_ganancia"] or 0), "precio_venta": float(p["precio_venta"] or 0)}
    return {"codigo": p["codigo"], "nombre": p["nombre"], "categoria": p["categoria"], "subrubro": p["subrubro"],
            "stock": p["stock"], **precios.valores_para_pantalla(p), "precio_venta": crudos["precio_venta"],
            "crudos": crudos,
            "precio_efectivo": producto["precio_efectivo"] if producto else crudos["precio_venta"],
            "oferta": producto["oferta"] if producto else None, "actualizado_en": p["actualizado_en"]}


def _en_lotes(lista: list, tamano: int = 500):
    for i in range(0, len(lista), tamano):
        yield lista[i:i + tamano]


def previsualizar_ajuste(codigos: list, *, porcentaje: float = None, monto_fijo: float = None,
                         redondear: bool = True) -> list:
    """Lo que haría aplicar_ajuste_masivo, con la MISMA calcular_nuevo_precio
    (redondeo incluido): la vista previa tiene que ser lo que se guarda."""
    from pos_core import bulk_edit
    bulk_edit.calcular_nuevo_precio(1.0, porcentaje=porcentaje, monto_fijo=monto_fijo, redondear=redondear)
    conn = get_connection()
    datos, en_oferta = {}, set()
    for lote in _en_lotes(list(codigos)):
        marcas = ",".join("?" * len(lote))
        for r in conn.execute(f"SELECT codigo, nombre, precio_venta FROM Productos "
                              f"WHERE activo = 1 AND codigo IN ({marcas})", lote):
            datos[r["codigo"]] = r
        for r in conn.execute(f"SELECT DISTINCT producto_codigo FROM Ofertas WHERE activa = 1 "
                              f"AND date('now','localtime') BETWEEN fecha_inicio AND fecha_fin "
                              f"AND producto_codigo IN ({marcas})", lote):
            en_oferta.add(r["producto_codigo"])
    resultado = []
    for codigo in codigos:
        fila = datos.get(codigo)
        if fila is None:
            resultado.append({"codigo": codigo, "ok": False, "error": "no encontrado"})
            continue
        anterior = float(fila["precio_venta"] or 0)
        nuevo = bulk_edit.calcular_nuevo_precio(anterior, porcentaje=porcentaje, monto_fijo=monto_fijo,
                                                redondear=redondear)
        resultado.append({"codigo": codigo, "nombre": fila["nombre"], "ok": True, "precio_anterior": anterior,
                          "precio_nuevo": nuevo, "queda_en_cero": anterior > 0 and nuevo <= 0,
                          "en_oferta": codigo in en_oferta})
    return resultado


# --------------------------------------------------------------------- #
# Stock: lo que el StockService todavía va a descontar
# --------------------------------------------------------------------- #

def ventas_pendientes_de_descontar(codigo: str) -> dict:
    """Ventas de este producto que el watchdog del StockService todavía va a
    descontar. Es la consulta de stock_daemon_windows._ventas_con_stock_pendiente
    COPIADA TAL CUAL (con su comparación de texto ISO 'T' contra fecha con
    espacio): lo que importa es dar lo mismo que el watchdog va a hacer.

    Con el catálogo en 0, las ventas quedan con el descuento pendiente: Leo
    carga 12, la respuesta dice 12, y a los 5 s el StockService deja 6. Si
    lo cargado era un conteo de la góndola, eso descuenta dos veces.
    """
    fila = get_connection().execute(
        """
        SELECT COUNT(*) AS lineas, COALESCE(SUM(dv.cantidad), 0) AS unidades
        FROM Detalle_Ventas dv
        JOIN Ventas v ON v.uuid_unico = dv.venta_uuid
        WHERE v.anulada = 0
          AND dv.producto_codigo <> ?
          AND v.fecha_hora >= datetime('now', 'localtime', ?)
          AND NOT EXISTS (
              SELECT 1 FROM Movimientos_Stock ms
              WHERE ms.ticket_uuid = dv.venta_uuid AND ms.producto_codigo = dv.producto_codigo
          )
          AND dv.producto_codigo = ?
        """,
        (_CODIGO_SIN_BARRA, f"-{DIAS_VENTANA_WATCHDOG} days", codigo)).fetchone()
    return {"lineas": int(fila["lineas"]), "unidades": int(fila["unidades"])}


# --------------------------------------------------------------------- #
# Facturas
# --------------------------------------------------------------------- #

def factura_ya_aplicada(nombre: str, items: list, minutos: int = MINUTOS_FACTURA_REPETIDA):
    """{"hace_min", "renglones_iguales"} si en los últimos `minutos` ya se cargó
    una factura con este nombre y al menos un renglón igual (código y
    cantidad); None si no. Pedir un renglón igual evita frenar dos facturas
    distintas que se llaman igual ("factura.pdf")."""
    # El límite se arma en Python: fecha_hora es ISO con 'T' y compararla
    # contra datetime('now', ...) de SQLite (con espacio) da mal el mismo día.
    limite = (datetime.now() - timedelta(minutes=minutos)).isoformat(timespec="milliseconds")
    filas = get_connection().execute(
        "SELECT producto_codigo, cantidad, MIN(fecha_hora) AS primera FROM Movimientos_Stock "
        "WHERE tipo = 'ENTRADA_PDF' AND motivo = ? AND fecha_hora >= ? GROUP BY producto_codigo, cantidad",
        (f"Factura PDF: {nombre}", limite)).fetchall()
    ya = {(f["producto_codigo"], int(f["cantidad"])): f["primera"] for f in filas}
    iguales = [ya[(it["codigo"], int(it["cantidad"]))] for it in items
               if (it["codigo"], int(it["cantidad"])) in ya]
    if not iguales:
        return None
    primera = _fecha(min(iguales))
    hace = int((datetime.now() - primera).total_seconds() // 60) if primera else 0
    return {"hace_min": max(hace, 0), "renglones_iguales": len(iguales)}


def es_precio_sospechoso(nuevo, actual) -> bool:
    """Un precio leído del PDF muy distinto del costo guardado. Cubre el
    "3.500" que pdf_import._normalizar_numero lee como 3,5."""
    if nuevo is None:
        return False
    if actual is not None and actual > 0:
        cociente = nuevo / actual
        return not (0.2 <= cociente <= 5)
    return nuevo < 10


def marcar_posibles_duplicados(items: list) -> list:
    """El parser puede leer el mismo renglón por la tabla y por el texto: la
    segunda aparición en adelante se marca (no se descarta)."""
    vistos, marcas = set(), []
    for it in items:
        clave = (it.get("codigo") or (it.get("nombre") or "").upper().strip(), it.get("cantidad"),
                 it.get("precio_compra"))
        marcas.append(clave in vistos)
        vistos.add(clave)
    return marcas


def analizar_factura(ruta_pdf: str, nombre_archivo: str) -> dict:
    """Lee la factura y arma lo que la app muestra para revisar. NO escribe nada."""
    from pos_core import pdf_import
    try:
        import pdfplumber
        with pdfplumber.open(ruta_pdf) as pdf:
            paginas = len(pdf.pages)
    except Exception as e:
        raise PdfInvalidoError("El PDF está dañado o incompleto.") from e
    if paginas > MAX_PAGINAS_PDF:
        raise PdfInvalidoError("El PDF tiene más de 10 páginas: cargalo desde el Panel de la PC.")
    try:
        leido = pdf_import.parsear_factura_pdf(ruta_pdf)
    except Exception as e:
        raise PdfInvalidoError("El PDF está dañado o incompleto.") from e

    items = [{"codigo": (i.codigo or "").strip(), "nombre": i.nombre, "cantidad": i.cantidad,
              "precio_compra": float(i.precio_compra)} for i in leido.items]
    conn = get_connection()
    existentes = {}
    codigos = sorted({it["codigo"] for it in items if it["codigo"]})
    for lote in _en_lotes(codigos):
        marcas = ",".join("?" * len(lote))
        for r in conn.execute(f"SELECT codigo, nombre, stock, precio_compra FROM Productos "
                              f"WHERE activo = 1 AND codigo IN ({marcas})", lote):
            existentes[r["codigo"]] = r
    duplicados = marcar_posibles_duplicados(items)

    salida = []
    for indice, (it, dup) in enumerate(zip(items, duplicados)):
        prod = existentes.get(it["codigo"]) if it["codigo"] else None
        actual = float(prod["precio_compra"] or 0) if prod else None
        salida.append({"indice": indice, "codigo": it["codigo"], "nombre": it["nombre"],
                       "cantidad": it["cantidad"], "precio_compra": it["precio_compra"],
                       "existe": prod is not None, "nombre_sistema": prod["nombre"] if prod else None,
                       "stock_actual": prod["stock"] if prod else None, "precio_compra_actual": actual,
                       "posible_duplicado": dup, "precio_sospechoso": es_precio_sospechoso(it["precio_compra"], actual),
                       "emparejamiento": None})

    # Emparejamiento por nombre CON TOPE: cuesta ~0,1 s por ítem contra el
    # catálogo real (más en la PC del local). Sin tope, una factura de 500
    # renglones sin código tardaba minutos, la app cortaba y el "un análisis
    # por vez" quedaba tomado. Los que quedan afuera NO se descartan.
    pendientes = [s for s in salida if not s["existe"]]
    inicio, emparejados, pos = time.monotonic(), 0, 0
    while (pos < len(pendientes) and emparejados < MAX_EMPAREJAR
           and time.monotonic() - inicio < SEGUNDOS_EMPAREJAR):
        lote = pendientes[pos:pos + min(10, MAX_EMPAREJAR - emparejados)]
        resueltos = pdf_import.resolver_por_nombre(
            [{"codigo": s["codigo"], "nombre": s["nombre"], "cantidad": s["cantidad"],
              "precio_compra": s["precio_compra"]} for s in lote])
        for r in resueltos:
            lote[r["indice"]]["emparejamiento"] = {
                "motivo": r["motivo"], "confianza": r["confianza"],
                "candidatos": [{"codigo": c["codigo"], "nombre": c["nombre"], "score": c["score"]}
                               for c in r["candidatos"]],
                "sugerido": ({"codigo": r["elegido"]["codigo"], "nombre": r["elegido"]["nombre"],
                              "score": r["elegido"]["score"]} if r["elegido"] else None)}
        pos += len(lote)
        emparejados += len(lote)
    for s in pendientes:
        if s["emparejamiento"] is None:
            s["emparejamiento"] = {"motivo": "demasiados", "confianza": "NINGUNA", "candidatos": [],
                                   "sugerido": None}
    return {"factura_nombre": nombre_archivo, "es_pdf_escaneado": bool(leido.es_pdf_escaneado),
            "lineas_no_reconocidas": list(leido.lineas_no_reconocidas), "items": salida}
