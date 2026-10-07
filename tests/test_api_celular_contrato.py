"""La API del celular hace lo mismo que el Panel, y contesta lo que la app espera.

La app del celular ("Panel Dueño") habla con la API del 8766 (ApiCelular,
services/api_celular.py). Esta prueba pega a CADA endpoint como lo haría el
celular (desde una IP de Tailscale) y mira el efecto con las funciones de
siempre de main, como lo vería el Panel de la PC:

- que la forma de cada respuesta sea la del contrato compartido con la app
  (apps/movil_dueno/test/fixtures/contrato_api_celular_v2.json): si la API
  cambia un campo y la app no, la app se rompe en el celular de Leo y acá
  no se ve;
- que los precios guarden lo que vio el dueño y NO inventen costos, y que
  una factura cargada mientras la hoja estaba abierta no se pise (el caso
  c4: el margen quedaba en 57,02 cuando el real era 26,67);
- que el precio que muestra sea el que cobra HOY la Caja (ofertas);
- que las alertas sean las mismas que manda el bot de Telegram;
- que cargar stock avise lo que el StockService todavía va a descontar;
- que el ajuste masivo y la factura no se apliquen dos veces tras un
  "incierto" con datos móviles;
- que nunca devuelva el token del bot ni el de [remoto] (regla 4);
- que con la base vieja o sin base no rompa nada ni cree una base vacía.
"""
import configparser
import os
import re
import shutil
import sqlite3
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import _base_celular as B  # noqa: E402  (agrega la raíz del repo al path)

from pos_core import paths  # noqa: E402

fallos = []


def ok(texto):
    print("OK:", texto)


def falla(texto):
    print("FALLA:", texto)
    fallos.append(texto)


def verificar(condicion, texto_ok, texto_falla):
    if condicion:
        ok(texto_ok)
    else:
        falla(texto_falla)
    return condicion


# --------------------------------------------------------------------- #
# Comparación contra el fixture (reglas de la espec, §8.4)
# --------------------------------------------------------------------- #

def tipo_json(valor) -> str:
    if valor is None:
        return "null"
    if isinstance(valor, bool):
        return "bool"
    if isinstance(valor, (int, float)):
        return "number"          # int y float valen los dos como número; bool no
    if isinstance(valor, str):
        return "string"
    if isinstance(valor, list):
        return "list"
    if isinstance(valor, dict):
        return "object"
    return type(valor).__name__


def _recolectar(valor, ruta: str, muestras: dict) -> None:
    muestras.setdefault(ruta, []).append(valor)
    if isinstance(valor, dict):
        for clave, v in valor.items():
            _recolectar(v, f"{ruta}.{clave}", muestras)
    elif isinstance(valor, list):
        for elemento in valor:
            _recolectar(elemento, f"{ruta}[]", muestras)


class Contrato:
    """La forma esperada de cada respuesta es la UNIÓN de todos los ejemplos
    del fixture (todas las respuestas y todos los elementos de cada lista).
    Un objeto real tiene que tener exactamente las claves de alguna de las
    variantes que muestra el fixture en ese lugar (p. ej. un renglón
    {"ok": true, ...} o uno {"ok": false, "error"}), sin faltantes ni
    sobrantes. Un null solo vale donde tipos_anulables lo permite."""

    def __init__(self, fixture: dict):
        self.fixture = fixture
        self.anulables = {k: v for k, v in fixture.get("tipos_anulables", {}).items() if k != "..."}
        self.muestras = {}
        for nombre, ejemplo in fixture["ejemplos"].items():
            for respuesta in ejemplo["respuestas"]:
                _recolectar(respuesta, nombre, self.muestras)
        self.tipos = {ruta: {tipo_json(v) for v in valores} for ruta, valores in self.muestras.items()}
        self.variantes = {ruta: {frozenset(v) for v in valores if isinstance(v, dict)}
                          for ruta, valores in self.muestras.items()}

    def errores_del_fixture(self) -> list:
        errores = []
        for ruta, tipos in sorted(self.tipos.items()):
            llenos = tipos - {"null"}
            if len(llenos) > 1:
                errores.append(f"{ruta}: el fixture mezcla tipos {sorted(llenos)}")
            if not llenos:
                errores.append(f"{ruta}: nunca aparece lleno en el fixture (no se puede validar)")
            if "null" in tipos and ruta not in self.anulables:
                errores.append(f"{ruta}: aparece null en el fixture pero no está en tipos_anulables")
        for ruta, declarado in sorted(self.anulables.items()):
            tipos = self.tipos.get(ruta)
            if tipos is None:
                errores.append(f"tipos_anulables nombra {ruta}, que no aparece en ningún ejemplo")
                continue
            if "null" not in tipos:
                errores.append(f"{ruta}: es anulable pero ningún ejemplo lo muestra en null")
            lleno = declarado.split("|")[0]
            if lleno not in tipos:
                errores.append(f"{ruta}: es anulable '{declarado}' pero ningún ejemplo lo muestra lleno")
        return errores

    def comparar(self, valor, ruta: str) -> list:
        tipos = self.tipos.get(ruta)
        if tipos is None:
            return [f"{ruta}: la API lo devuelve y el contrato no lo tiene"]
        t = tipo_json(valor)
        if t == "null":
            return [] if ruta in self.anulables else [f"{ruta}: vino null y el contrato no lo permite"]
        llenos = tipos - {"null"}
        if t not in llenos:
            return [f"{ruta}: vino {t} y el contrato dice {'/'.join(sorted(llenos))}"]
        errores = []
        if t == "object":
            claves = frozenset(valor)
            variantes = self.variantes.get(ruta, set())
            if claves not in variantes:
                cercana = min(variantes, key=lambda v: len(v ^ claves))
                faltan, sobran = sorted(cercana - claves), sorted(claves - cercana)
                errores.append(f"{ruta}: claves distintas del contrato (faltan {faltan}, sobran {sobran})")
            for clave, v in valor.items():
                if f"{ruta}.{clave}" in self.tipos:
                    errores.extend(self.comparar(v, f"{ruta}.{clave}"))
        elif t == "list":
            for elemento in valor:
                errores.extend(self.comparar(elemento, f"{ruta}[]"))
        return errores


def _ruta_normalizada(ruta: str) -> str:
    return re.sub(r"/\d{5,}$", "/{codigo}", ruta.split("?")[0])


# --------------------------------------------------------------------- #
# Preparación
# --------------------------------------------------------------------- #

from pos_core import acceso_celular, alerts, config, db, panel_celular, precios  # noqa: E402
from pos_core import sales, stock_service  # noqa: E402
from pos_core import telegram_bot  # noqa: E402

RAIZ_TMP = tempfile.mkdtemp(prefix="api_celular_contrato_")
TMP_A = os.path.join(RAIZ_TMP, "instalacion")          # la de todos los días
TMP_SIN_PIN = os.path.join(RAIZ_TMP, "sin_pin")        # la de producción: sha256('1234')
for carpeta in (TMP_A, TMP_SIN_PIN):
    os.makedirs(carpeta)
B.armar_base(TMP_SIN_PIN)
B.armar_base(TMP_A)
acceso_celular.definir_pin_dueno(B.PIN)


def usar_instalacion(tmp: str) -> str:
    """Apunta el proceso a otra carpeta, como si el servicio arrancara ahí."""
    db.cerrar_conexion()
    paths.set_base_override(tmp)
    ruta = os.path.join(tmp, "database", "stock.db")
    db.usar_solo_base_existente(ruta)
    return ruta


RUTA_DB = usar_instalacion(TMP_A)

# Trampas: la API no puede crear ni migrar la base ni arrancar un monitor de
# Telegram (eso lo hace el StockService; dos monitores = alertas duplicadas).
llamadas_prohibidas = []
_originales = {"init_db": db.init_db, "preparar_base": db.preparar_base,
               "aplicar_migraciones": db.aplicar_migraciones, "MonitorAlertas": telegram_bot.MonitorAlertas}


def _trampa(nombre):
    def _llamada(*a, **k):
        llamadas_prohibidas.append(nombre)
        raise AssertionError(f"la API llamó a {nombre}")
    return _llamada


def poner_trampas():
    db.init_db, db.preparar_base, db.aplicar_migraciones = (
        _trampa("init_db"), _trampa("preparar_base"), _trampa("aplicar_migraciones"))
    telegram_bot.MonitorAlertas = _trampa("MonitorAlertas")


def sacar_trampas():
    db.init_db, db.preparar_base = _originales["init_db"], _originales["preparar_base"]
    db.aplicar_migraciones = _originales["aplicar_migraciones"]
    telegram_bot.MonitorAlertas = _originales["MonitorAlertas"]


poner_trampas()

enviados = []
B.simular_telegram(enviados)

from services import api_celular  # noqa: E402

contrato = Contrato(B.cargar_fixture())
respuestas = {}          # nombre de ejemplo -> [(status, json, metodo, ruta)]
textos = []              # todo lo que devolvió la API, para buscar secretos


def pedir(c, metodo, ruta, *, ejemplo=None, esperado=200, **kw):
    r = c.request(metodo, ruta, **kw)
    textos.append(r.text)
    if esperado is not None and r.status_code != esperado:
        falla(f"{metodo} {ruta} dio {r.status_code} (se esperaba {esperado}): {r.text[:300]}")
    if ejemplo:
        try:
            cuerpo = r.json()
        except ValueError:
            cuerpo = None
        respuestas.setdefault(ejemplo, []).append((r.status_code, cuerpo, metodo, ruta))
    return r


def fila(sql, params=()):
    con = sqlite3.connect(RUTA_DB)
    con.row_factory = sqlite3.Row
    try:
        r = con.execute(sql, params).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


def filas(sql, params=()):
    con = sqlite3.connect(RUTA_DB)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def config_por_seccion(ruta_ini: str) -> dict:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(ruta_ini, encoding="utf-8")
    return {s: dict(cfg[s]) for s in cfg.sections()}


def alertas_del_bot() -> set:
    """Lo que manda el bot de verdad (envío simulado, sin cooldown)."""
    sacar_trampas()
    try:
        config.actualizar_config_dict({"telegram": {"habilitado": "true"}})
        with db.transaction() as conn:
            conn.execute("DELETE FROM Alertas_Enviadas")
        enviados.clear()
        telegram_bot.revisar_umbrales_y_alertar()
        db.cerrar_conexion()
    finally:
        poner_trampas()
    salida = set()
    for texto in enviados:
        m = re.search(r"\((?P<codigo>[^()]+)\) tiene", texto)
        if m:
            salida.add((m.group("codigo"), "BAJO" if "STOCK BAJO" in texto else "SOBRE"))
    return salida


ULTIMA_FACTURA = {}

# ===================================================================== #
# Fase A: la instalación de todos los días
# ===================================================================== #
app = api_celular.crear_app()
with B.cliente(app) as c:
    app.state.estado.refrescar()

    # ---------------- Salud ----------------
    r = pedir(c, "GET", "/api/salud", ejemplo="salud")
    s = r.json()
    verificar(s.get("servicio") == "otter-api-celular" and s.get("contrato") == 2,
              "salud trae la firma otter-api-celular y el contrato 2", f"salud no trae la firma: {s}")
    verificar(s.get("nombre_local") == B.NOMBRE_LOCAL, "salud muestra el nombre del local del config.ini real",
              f"salud mostró nombre_local={s.get('nombre_local')!r}")
    verificar(s.get("compilado") == "desarrollo", "sin congelar, salud dice compilado = desarrollo",
              f"compilado = {s.get('compilado')!r}")
    verificar(s.get("pin_configurado") is True and s.get("login_disponible") is True and s.get("motivo") is None
              and s.get("base") == {"ok": True, "detalle": "ok"},
              "con base y PIN, salud dice que el login está disponible", f"salud: {s}")

    # ---------------- Login ----------------
    r = pedir(c, "POST", "/api/auth/login", ejemplo="login", json={"pin": B.PIN})
    token = r.json()["token"]
    H = B.auth(token)
    verificar(token.startswith("v2.") and r.json().get("usuario") == "dueño", "el login con el PIN da una sesión v2",
              f"login: {r.json()}")
    pedir(c, "GET", "/api/auth/yo", ejemplo="yo", headers=H)
    r = pedir(c, "GET", "/api/auth/yo", esperado=401)
    verificar(r.json().get("codigo") == "sesion_invalida", "sin token, 401 sesion_invalida", f"sin token: {r.text}")
    r = pedir(c, "GET", "/api/no-existe", headers=H, esperado=404)
    verificar(r.json().get("codigo") == "ruta_inexistente", "una ruta que no existe da 404 ruta_inexistente",
              f"ruta inexistente: {r.text}")
    r = pedir(c, "PUT", f"/api/productos/{B.YERBA}/precio", headers=H, json={"precio": 1}, esperado=404)
    verificar(r.json().get("codigo") == "ruta_inexistente",
              "PUT /api/productos/{codigo}/precio ya no existe (se reemplazó por la hoja de precios)",
              f"PUT viejo de precio: {r.status_code} {r.text}")

    # ---------------- Dashboard ----------------
    r = pedir(c, "GET", "/api/dashboard?periodo_top=historico", ejemplo="dashboard", headers=H)
    d = r.json()
    # Hoy: 2 tickets de 14.370 (yerba + café con oferta), uno de fideos 6.000,
    # 3 de coca 4.000 y la venta anulada de 90.000 que NO cuenta.
    verificar(round(d["hoy"]["total"], 2) == 46740 and d["hoy"]["tickets"] == 6,
              "el dashboard da el total de hoy de las ventas cobradas, sin la anulada",
              f"dashboard hoy: {d['hoy']}")
    metodos = {m["metodo_pago"]: (m["tickets"], round(m["total"], 2)) for m in d["hoy"]["por_metodo"]}
    verificar(metodos == {"EFECTIVO": (5, 40740), "TRANSFERENCIA": (1, 6000)}
              and d["hoy"]["por_metodo"][0]["metodo_pago"] == "EFECTIVO",
              "por método de pago, ordenado de mayor a menor", f"por método: {d['hoy']['por_metodo']}")
    dias = d["ultimos_7_dias"]
    verificar(len(dias) == 7 and dias == sorted(dias, key=lambda x: x["dia"])
              and round(dias[-1]["total"], 2) == 46740 and all(x["total"] == 0 and x["tickets"] == 0 for x in dias[:-1]),
              "los últimos 7 días vienen siempre 7, del más viejo a hoy, con ceros", f"7 días: {dias}")
    codigos_top = [t["codigo"] for t in d["top_productos"]]
    verificar(B.RAROS not in codigos_top, "la anulada no aparece en el top", f"top con la anulada: {codigos_top}")
    for periodo in ("hoy", "7dias", "30dias"):
        r = pedir(c, "GET", f"/api/dashboard?periodo_top={periodo}", headers=H)
        if r.status_code == 200 and [t["codigo"] for t in r.json()["top_productos"]] != codigos_top:
            falla(f"el top de {periodo} no coincide con el histórico (todas las ventas son de hoy)")
    r = pedir(c, "GET", "/api/dashboard?periodo_top=semana", headers=H, esperado=400)
    verificar(r.json().get("codigo") == "dato_invalido", "un período inválido da 400 dato_invalido",
              f"período inválido: {r.text}")

    # ---------------- Productos ----------------
    r = pedir(c, "GET", "/api/productos?q=&limite=200", ejemplo="productos", headers=H)
    lista = r.json()
    por_codigo = {p["codigo"]: p for p in lista}
    verificar(B.INACTIVO not in por_codigo and len(lista) == 8, "la lista trae los activos y no el dado de baja",
              f"productos: {sorted(por_codigo)}")
    distintos = []
    for p in lista:
        caja = [x for x in sales.buscar_productos(p["codigo"]) if x["codigo"] == p["codigo"]]
        if not caja or abs(caja[0]["precio_venta"] - p["precio_efectivo"]) > 0.005:
            distintos.append((p["codigo"], p["precio_efectivo"], caja and caja[0]["precio_venta"]))
    db.cerrar_conexion()
    verificar(not distintos, "precio_efectivo es lo que cobra HOY la Caja (sales.buscar_productos), con ofertas",
              f"precio distinto del de la Caja: {distintos}")
    verificar(por_codigo[B.CAFE]["oferta"] and por_codigo[B.CAFE]["oferta"]["tipo_descuento"] == "PORCENTAJE"
              and por_codigo[B.CAFE]["precio_efectivo"] == 3870 and por_codigo[B.YERBA]["oferta"] is None,
              "el café muestra su oferta del 10 % (4300 -> 3870) y la yerba ninguna",
              f"ofertas: café {por_codigo[B.CAFE]}")
    verificar(por_codigo[B.CAFE]["umbral_propio"] is True and por_codigo[B.CAFE]["stock_minimo"] == 5
              and por_codigo[B.YERBA]["umbral_propio"] is False and por_codigo[B.YERBA]["stock_minimo"] == 20,
              "cada producto trae su umbral EFECTIVO (el propio, o si no el global)",
              f"umbrales: café {por_codigo[B.CAFE]['stock_minimo']}, yerba {por_codigo[B.YERBA]['stock_minimo']}")
    verificar(por_codigo[B.RAROS]["umbral_propio"] is False and por_codigo[B.RAROS]["stock_minimo"] == 20,
              "una fila de umbral propia INACTIVA no cuenta como propia (el bot tampoco la usa)",
              f"galletitas: {por_codigo[B.RAROS]}")
    r = pedir(c, "GET", "/api/productos?q=50%25_", headers=H)
    verificar([p["codigo"] for p in r.json()] == [B.RAROS], "buscar '50%_' encuentra solo eso (no es un comodín)",
              f"buscar '50%_': {[p['codigo'] for p in r.json()]}")
    r = pedir(c, "GET", "/api/productos?campo=marca&valor=Colombia", headers=H)
    verificar([p["codigo"] for p in r.json()] == [B.CAFE], "buscar por marca", f"por marca: {r.text[:200]}")
    r = pedir(c, "GET", "/api/productos?campo=precio_venta&valor=1", headers=H, esperado=400)
    verificar(r.json().get("codigo") == "dato_invalido" and "no permitido" in r.json().get("detail", ""),
              "buscar por un campo no permitido da 400", f"campo no permitido: {r.text}")
    pedir(c, "GET", "/api/productos?limite=0", headers=H, esperado=422)
    pedir(c, "GET", "/api/productos?limite=2001", headers=H, esperado=422)

    pedir(c, "GET", f"/api/productos/{B.CAFE}", ejemplo="producto", headers=H)
    r = pedir(c, "GET", f"/api/productos/{B.YERBA}", ejemplo="producto", headers=H)
    verificar(len(r.json().get("movimientos", [])) >= 1, "el detalle del producto trae sus últimos movimientos",
              f"detalle sin movimientos: {r.text[:200]}")
    r = pedir(c, "GET", "/api/productos/0000000", ejemplo="error_404_no_existe", headers=H, esperado=404)
    verificar(r.json() == {"detail": "Producto con código '0000000' no existe o está inactivo", "codigo": "no_existe"},
              "un código que no existe da 404 no_existe", f"404: {r.text}")
    pedir(c, "GET", f"/api/productos/{B.INACTIVO}", headers=H, esperado=404)
    pedir(c, "GET", "/api/movimientos?limite=30", ejemplo="movimientos", headers=H)
    pedir(c, "GET", "/api/movimientos?limite=501", headers=H, esperado=422)

    # ---------------- Stock ----------------
    antes = fila("SELECT stock, version FROM Productos WHERE codigo = ?", (B.CAFE,))
    r = pedir(c, "POST", "/api/stock/movimiento", ejemplo="stock_movimiento", headers=H,
              json={"codigo": B.CAFE, "cantidad": 12, "operacion": "sumar", "motivo": "Reposición"})
    despues = fila("SELECT stock, version FROM Productos WHERE codigo = ?", (B.CAFE,))
    mov = fila("SELECT * FROM Movimientos_Stock WHERE producto_codigo = ? ORDER BY id DESC LIMIT 1", (B.CAFE,))
    verificar(despues["stock"] == antes["stock"] + 12 and despues["version"] == antes["version"] + 1
              and r.json()["stock_nuevo"] == despues["stock"],
              "sumar 12 desde el celular suma 12 con el versionado de siempre (versión +1)",
              f"stock {antes} -> {despues}, respuesta {r.json()}")
    verificar(mov["tipo"] == "ENTRADA_MANUAL" and mov["usuario"] == "dueño (celular)" and mov["origen"] == "MAESTRO"
              and mov["motivo"] == "Reposición" and mov["cantidad"] == 12,
              "el movimiento queda firmado 'dueño (celular)', origen MAESTRO", f"movimiento: {mov}")

    # D22: la coca está en 0 con 3 ventas cuyo descuento quedó pendiente (6 u.)
    r = pedir(c, "POST", "/api/stock/movimiento", ejemplo="stock_movimiento", headers=H,
              json={"codigo": B.COCA, "cantidad": 12, "operacion": "sumar", "motivo": None})
    res = r.json()
    verificar(res.get("stock_nuevo") == 12 and res.get("ventas_pendientes") == {"lineas": 3, "unidades": 6},
              "cargar 12 de un producto en 0 avisa que el StockService todavía va a descontar 6 (3 ventas)",
              f"respuesta: {res}")
    mov = fila("SELECT motivo FROM Movimientos_Stock WHERE producto_codigo = ? ORDER BY id DESC LIMIT 1", (B.COCA,))
    verificar(mov["motivo"] == "Alta manual", "sin motivo, queda 'Alta manual' (como el Panel)", f"motivo: {mov}")
    # Lo mismo que va a hacer el demonio de verdad (la prueba SÍ puede
    # importarlo; la API no). Al importarse fija la carpeta padre como base.
    sacar_trampas()
    try:
        paths.set_base_override(os.path.join(TMP_A, "StockService"))
        from services import stock_daemon_windows as demonio
        paths.set_base_override(TMP_A)
        pendientes = [p for p in demonio._ventas_con_stock_pendiente() if p["producto_codigo"] == B.COCA]
        verificar((len(pendientes), sum(p["cantidad"] for p in pendientes)) == (3, 6),
                  "ventas_pendientes coincide con lo que ve el watchdog del StockService",
                  f"watchdog: {len(pendientes)} líneas")
        verificar(panel_celular.DIAS_VENTANA_WATCHDOG == demonio.DIAS_VENTANA_WATCHDOG,
                  "la ventana de días es la misma que la del demonio",
                  f"{panel_celular.DIAS_VENTANA_WATCHDOG} vs {demonio.DIAS_VENTANA_WATCHDOG}")
        demonio.ciclo_watchdog()
        db.cerrar_conexion()
    finally:
        poner_trampas()
        usar_instalacion(TMP_A)
    verificar(fila("SELECT stock FROM Productos WHERE codigo = ?", (B.COCA,))["stock"] == 6,
              "y efectivamente, tras la vuelta del watchdog la coca queda en 6",
              f"coca después del watchdog: {fila('SELECT stock FROM Productos WHERE codigo = ?', (B.COCA,))}")

    r = pedir(c, "POST", "/api/stock/lector", ejemplo="stock_lector", headers=H,
              json={"codigo": B.CAFE, "operacion": "restar"})
    mov = fila("SELECT tipo, motivo, usuario FROM Movimientos_Stock WHERE producto_codigo = ? ORDER BY id DESC LIMIT 1",
               (B.CAFE,))
    verificar(mov == {"tipo": "SALIDA_MANUAL", "motivo": "Lector de código de barras", "usuario": "dueño (celular)"},
              "el lector resta 1 como el lector del Panel", f"lector: {mov}")
    pedir(c, "POST", "/api/stock/lector", headers=H, json={"codigo": B.CAFE, "operacion": "sumar"})

    movs_antes = fila("SELECT COUNT(*) n FROM Movimientos_Stock")["n"]
    r = pedir(c, "POST", "/api/stock/movimiento", ejemplo="error_409_stock", headers=H, esperado=409,
              json={"codigo": B.BOLSA, "cantidad": 1, "operacion": "restar", "motivo": None})
    verificar(r.json().get("codigo") == "stock_insuficiente"
              and fila("SELECT COUNT(*) n FROM Movimientos_Stock")["n"] == movs_antes,
              "restar de más da 409 stock_insuficiente y no deja ningún movimiento", f"restar de más: {r.text}")
    r = pedir(c, "POST", "/api/stock/movimiento", headers=H, esperado=404,
              json={"codigo": "0000000", "cantidad": 1, "operacion": "sumar", "motivo": None})
    verificar(r.json().get("codigo") == "no_existe", "sumar a un código inexistente da 404 no_existe",
              f"inexistente: {r.text}")
    pedir(c, "POST", "/api/stock/movimiento", headers=H, esperado=422,
          json={"codigo": B.CAFE, "cantidad": 0, "operacion": "sumar"})
    pedir(c, "POST", "/api/stock/movimiento", headers=H, esperado=422,
          json={"codigo": B.CAFE, "cantidad": -5, "operacion": "sumar"})
    pedir(c, "POST", "/api/stock/movimiento", headers=H, esperado=422,
          json={"codigo": B.CAFE, "cantidad": 1, "operacion": "multiplicar"})

    # ---------------- Precios ----------------
    hoja = pedir(c, "GET", f"/api/precios/{B.YERBA}", ejemplo="precios_ver", headers=H).json()
    verificar(hoja["crudos"] == {"costo_sin_iva": 2000.0, "precio_compra": 2420.0, "margen_ganancia": 44.63,
                                 "precio_venta": 3500.0},
              "la hoja de precios trae los 4 valores crudos guardados", f"crudos: {hoja.get('crudos')}")
    hoja_alfajor = pedir(c, "GET", f"/api/precios/{B.ALFAJOR}", ejemplo="precios_ver", headers=H).json()
    verificar(hoja_alfajor["costo_sin_iva"] is None and hoja_alfajor["precio_costo"] is None
              and hoja_alfajor["margen"] is None and hoja_alfajor["crudos"]["precio_compra"] == 0,
              "un costo nunca cargado (0) llega como null para la pantalla y como 0 en crudos",
              f"alfajor: {hoja_alfajor}")
    pedir(c, "GET", f"/api/precios/{B.BOLSA}", ejemplo="precios_ver", headers=H)
    pedir(c, "GET", "/api/precios/0000000", headers=H, esperado=404)

    r = pedir(c, "POST", "/api/precios/recalcular", ejemplo="precios_recalcular", headers=H,
              json={"cambio": "precio_final", "costo_sin_iva": hoja["costo_sin_iva"],
                    "precio_costo": hoja["precio_costo"], "margen": hoja["margen"], "precio_final": "3.800"})
    valores = r.json()
    verificar(valores == {"costo_sin_iva": 2000.0, "precio_costo": 2420.0, "margen": 57.02, "precio_final": 3800.0},
              "recalcular con el final en '3.800' da el margen 57,02 (lo calcula el servidor, como el Panel)",
              f"recalcular: {valores}")
    r = pedir(c, "POST", "/api/precios/recalcular", ejemplo="precios_recalcular", headers=H,
              json={"cambio": "precio_final", "costo_sin_iva": None, "precio_costo": None, "margen": None,
                    "precio_final": "1.500"})
    verificar(r.json().get("precio_final") == 1500 and r.json().get("costo_sin_iva") is None,
              "'1.500' se lee como mil quinientos y no inventa costos", f"recalcular 1.500: {r.json()}")
    pedir(c, "POST", "/api/precios/recalcular", ejemplo="precios_recalcular", headers=H,
          json={"cambio": "precio_final", "costo_sin_iva": None, "precio_costo": None, "margen": None,
                "precio_final": None})
    pedir(c, "POST", "/api/precios/recalcular", headers=H, esperado=422,
          json={"cambio": "otro", "precio_final": 1})

    r = pedir(c, "PUT", f"/api/precios/{B.YERBA}", ejemplo="precios_guardar", headers=H,
              json={**valores, "esperado": hoja["crudos"]})
    guardado = precios.obtener_para_precios(B.YERBA)
    db.cerrar_conexion()
    verificar(abs(guardado["margen_ganancia"] - 57.02) < 0.001 and guardado["precio_venta"] == 3800
              and guardado["costo_sin_iva"] == 2000 and guardado["precio_compra"] == 2420,
              "guardar la yerba deja exactamente lo que vio el dueño (margen 57,02, final 3800)",
              f"yerba guardada: {guardado}")
    verificar(r.json()["antes"]["precio_venta"] == 3500 and r.json()["despues"]["precio_venta"] == 3800,
              "la respuesta trae el antes y el después", f"antes/después: {r.text[:300]}")

    calc = pedir(c, "POST", "/api/precios/recalcular", headers=H,
                 json={"cambio": "precio_final", "costo_sin_iva": hoja_alfajor["costo_sin_iva"],
                       "precio_costo": hoja_alfajor["precio_costo"], "margen": hoja_alfajor["margen"],
                       "precio_final": 1200}).json()
    pedir(c, "PUT", f"/api/precios/{B.ALFAJOR}", ejemplo="precios_guardar", headers=H,
          json={**calc, "esperado": hoja_alfajor["crudos"]})
    guardado = precios.obtener_para_precios(B.ALFAJOR)
    db.cerrar_conexion()
    verificar(guardado["precio_venta"] == 1200 and not guardado["costo_sin_iva"] and not guardado["precio_compra"]
              and not guardado["margen_ganancia"],
              "guardar un producto sin costo NO inventa costo ni margen (quedan en 0)", f"alfajor: {guardado}")

    # El caso c4: la hoja quedó abierta y mientras tanto se cargó una factura
    # en la PC que cambió el costo. Guardar encima pisaba el costo.
    hoja = pedir(c, "GET", f"/api/precios/{B.YERBA}", headers=H).json()
    sacar_trampas()
    try:
        stock_service.sumar_stock_por_factura_pdf([{"codigo": B.YERBA, "cantidad": 1, "precio_compra": 3000}],
                                                  usuario="Panel", factura_nombre="cargada_en_la_pc.pdf")
        db.cerrar_conexion()
    finally:
        poner_trampas()
    r = pedir(c, "PUT", f"/api/precios/{B.YERBA}", ejemplo="error_409_precio", headers=H, esperado=409,
              json={"costo_sin_iva": None, "precio_costo": None, "margen": None, "precio_final": 3900,
                    "esperado": hoja["crudos"]})
    cuerpo = r.json()
    guardado = precios.obtener_para_precios(B.YERBA)
    db.cerrar_conexion()
    verificar(cuerpo.get("codigo") == "precio_cambio" and cuerpo.get("campo") == "precio_compra"
              and cuerpo.get("detail") == "El precio de costo cambió mientras tanto: era $ 2.420, ahora es $ 3.000. "
                                          "Revisalo de nuevo.",
              "con la factura cargada en el medio, guardar da 409 precio_cambio sobre el costo", f"409: {cuerpo}")
    verificar(guardado["precio_compra"] == 3000 and guardado["precio_venta"] == 3800
              and abs(guardado["margen_ganancia"] - 57.02) < 0.001,
              "y la fila queda como la dejó la factura (costo 3000, nada inventado ni pisado)",
              f"yerba después del 409: {guardado}")
    hoja_mal = dict(hoja["crudos"])
    del hoja_mal["margen_ganancia"]
    r = pedir(c, "PUT", f"/api/precios/{B.YERBA}", headers=H, esperado=422,
              json={"precio_final": 3900, "esperado": hoja_mal})
    verificar(r.json().get("codigo") == "dato_invalido", "un 'esperado' sin una de las 4 claves da 422",
              f"esperado incompleto: {r.text}")
    pedir(c, "PUT", f"/api/precios/{B.YERBA}", headers=H, esperado=422, json={"precio_final": 3900})
    pedir(c, "PUT", f"/api/precios/{B.YERBA}", headers=H, esperado=422,
          json={"precio_final": 0, "esperado": hoja["crudos"]})
    pedir(c, "PUT", f"/api/precios/{B.YERBA}", headers=H, esperado=422,
          json={"precio_final": 100, "margen": -100, "esperado": hoja["crudos"]})
    pedir(c, "PUT", "/api/precios/0000000", headers=H, esperado=404,
          json={"precio_final": 100, "esperado": hoja["crudos"]})

    # ---------------- Ajuste masivo ----------------
    pedir(c, "POST", "/api/precios/previsualizar", ejemplo="precios_previsualizar", headers=H,
          json={"codigos": [B.YERBA, B.CAFE, B.BOLSA, "0000000"], "porcentaje": 10, "monto_fijo": None,
                "redondear": True})
    vista = pedir(c, "POST", "/api/precios/previsualizar", headers=H,
                  json={"codigos": [B.FIDEOS, B.BOLSA], "porcentaje": 10}).json()
    esperados = {v["codigo"]: v["precio_anterior"] for v in vista}
    r = pedir(c, "POST", "/api/precios/aplicar", ejemplo="precios_aplicar", headers=H,
              json={"codigos": [B.FIDEOS, B.BOLSA], "porcentaje": 10, "esperados": esperados})
    aplicado = {x["codigo"]: x for x in r.json()}
    fideos = fila("SELECT precio_venta FROM Productos WHERE codigo = ?", (B.FIDEOS,))["precio_venta"]
    verificar(vista[0]["precio_nuevo"] == 3400 and aplicado[B.FIDEOS]["precio_nuevo"] == 3400 and fideos == 3400,
              "la vista previa es lo que se aplica (3000 + 10 % = 3400 en las dos, con el redondeo de main)",
              f"vista {vista[0]}, aplicado {aplicado.get(B.FIDEOS)}, guardado {fideos}")
    verificar(aplicado[B.BOLSA]["ok"] is True, "un producto que ya estaba en $ 0 no frena el ajuste",
              f"bolsa: {aplicado.get(B.BOLSA)}")
    # Aplicar dos veces (un "incierto" con datos móviles y el dueño que toca
    # "aplicar de nuevo"): la segunda no puede sumar el aumento otra vez.
    vista = pedir(c, "POST", "/api/precios/previsualizar", headers=H,
                  json={"codigos": [B.CAFE, B.YERBA], "porcentaje": 10}).json()
    esperados2 = {v["codigo"]: v["precio_anterior"] for v in vista}
    pedir(c, "POST", "/api/precios/aplicar", headers=H,
          json={"codigos": [B.CAFE, B.YERBA], "porcentaje": 10, "esperados": esperados2})
    foto = filas("SELECT codigo, precio_venta, version FROM Productos ORDER BY codigo")
    r = pedir(c, "POST", "/api/precios/aplicar", headers=H,
              json={"codigos": [B.CAFE, B.YERBA], "porcentaje": 10, "esperados": esperados2})
    verificar(foto == filas("SELECT codigo, precio_venta, version FROM Productos ORDER BY codigo")
              and len(r.json()) == 2 and all(x["ok"] is False and "cambió" in x["error"] for x in r.json()),
              "aplicar dos veces el mismo ajuste no suma el aumento otra vez: la segunda no toca nada",
              f"segunda vez: {r.json()}")
    # Un producto en $ 0 queda en $ 0 (0 + 10 % = 0): repetirlo lo deja igual.
    precios_antes = filas("SELECT codigo, precio_venta FROM Productos ORDER BY codigo")
    r = pedir(c, "POST", "/api/precios/aplicar", headers=H,
              json={"codigos": [B.FIDEOS, B.BOLSA], "porcentaje": 10, "esperados": esperados})
    verificar(precios_antes == filas("SELECT codigo, precio_venta FROM Productos ORDER BY codigo")
              and [x["ok"] for x in r.json() if x["codigo"] == B.FIDEOS] == [False],
              "repetir un ajuste que incluye un producto en $ 0 tampoco cambia ningún precio",
              f"repetido con la bolsa: {r.json()}")
    foto = filas("SELECT codigo, precio_venta, version FROM Productos ORDER BY codigo")
    vista = pedir(c, "POST", "/api/precios/previsualizar", headers=H,
                  json={"codigos": [B.ALFAJOR, B.FIDEOS], "monto_fijo": -2000}).json()
    r = pedir(c, "POST", "/api/precios/aplicar", headers=H, esperado=400,
              json={"codigos": [B.ALFAJOR, B.FIDEOS], "monto_fijo": -2000,
                    "esperados": {v["codigo"]: v["precio_anterior"] for v in vista}})
    verificar(r.json().get("codigo") == "queda_en_cero"
              and r.json().get("detail", "").startswith("1 producto(s) quedarían en $ 0")
              and foto == filas("SELECT codigo, precio_venta, version FROM Productos ORDER BY codigo"),
              "un ajuste que deja en $ 0 un producto con precio da 400 queda_en_cero sin tocar nada",
              f"queda en cero: {r.text}")
    pedir(c, "POST", "/api/precios/previsualizar", headers=H, esperado=422,
          json={"codigos": [B.FIDEOS], "porcentaje": -100})
    pedir(c, "POST", "/api/precios/previsualizar", headers=H, esperado=422,
          json={"codigos": [B.FIDEOS], "porcentaje": 10, "monto_fijo": 100})
    pedir(c, "POST", "/api/precios/previsualizar", headers=H, esperado=422, json={"codigos": [B.FIDEOS]})
    pedir(c, "POST", "/api/precios/previsualizar", headers=H, esperado=422, json={"codigos": [], "porcentaje": 1})
    r = pedir(c, "POST", "/api/precios/aplicar", headers=H, esperado=422,
              json={"codigos": [B.FIDEOS, B.CHICLE], "porcentaje": 10, "esperados": {B.FIDEOS: 3400}})
    verificar(r.json().get("codigo") == "dato_invalido", "'esperados' con otros códigos que la lista da 422",
              f"esperados distintos: {r.text}")
    # Un precio que cambió entre la vista previa y el aplicar: no se toca; los demás sí.
    vista = pedir(c, "POST", "/api/precios/previsualizar", headers=H,
                  json={"codigos": [B.CHICLE, B.ALFAJOR], "porcentaje": 10}).json()
    sacar_trampas()
    try:
        precios.actualizar_precios(codigo=B.CHICLE, precio_final=350, usuario="Panel")
        db.cerrar_conexion()
    finally:
        poner_trampas()
    r = pedir(c, "POST", "/api/precios/aplicar", ejemplo="precios_aplicar", headers=H,
              json={"codigos": [B.CHICLE, B.ALFAJOR], "porcentaje": 10,
                    "esperados": {v["codigo"]: v["precio_anterior"] for v in vista}})
    res = {x["codigo"]: x for x in r.json()}
    chicle = fila("SELECT precio_venta FROM Productos WHERE codigo = ?", (B.CHICLE,))["precio_venta"]
    alfajor = fila("SELECT precio_venta FROM Productos WHERE codigo = ?", (B.ALFAJOR,))["precio_venta"]
    verificar(res[B.CHICLE]["ok"] is False and "cambió" in res[B.CHICLE]["error"] and chicle == 350
              and res[B.ALFAJOR]["ok"] is True and alfajor == 1400,
              "un precio que cambió entre la vista previa y el aplicar no se toca; el resto sí",
              f"aplicar: {r.json()}, chicle {chicle}, alfajor {alfajor}")

    # ---------------- Alertas y umbrales ----------------
    pedir(c, "GET", "/api/alertas?limite=1000", ejemplo="alertas", headers=H)
    del_bot = alertas_del_bot()
    r = pedir(c, "GET", "/api/alertas?limite=1000", ejemplo="alertas", headers=H)
    de_la_api = {(a["codigo"], a["tipo"]) for a in r.json()}
    verificar(de_la_api == del_bot and del_bot,
              f"con el global en 20/20, las alertas son las mismas que manda el bot ({len(del_bot)})",
              f"API {sorted(de_la_api)} vs bot {sorted(del_bot)}")
    verificar(all(a["ultima_alerta_enviada"] and a["proximo_aviso_desde"] for a in r.json()),
              "después de que el bot avisó, cada alerta dice cuándo se mandó y desde cuándo puede volver a avisar",
              f"alertas: {r.json()[:2]}")
    verificar([a["tipo"] for a in r.json()] == sorted([a["tipo"] for a in r.json()], key=lambda t: t != "BAJO"),
              "las de stock bajo van primero", "orden de alertas")
    raros = [a for a in r.json() if a["codigo"] == B.RAROS]
    verificar(raros and raros[0]["umbral_propio"] is False, "la fila inactiva de las galletitas no cuenta como propia",
              f"galletitas en alertas: {raros}")
    sacar_trampas()
    try:
        with db.transaction() as conn:   # una alerta mandada hace 5 horas: ya pasó el "no repetir"
            conn.execute("UPDATE Alertas_Enviadas SET ultima_alerta_enviada = datetime('now', 'localtime', "
                         "'-5 hours') WHERE producto_codigo = ?", (B.YERBA,))
        db.cerrar_conexion()
    finally:
        poner_trampas()
    r = pedir(c, "GET", "/api/alertas?limite=1000", ejemplo="alertas", headers=H)
    yerba = [a for a in r.json() if a["codigo"] == B.YERBA]
    verificar(yerba and yerba[0]["ultima_alerta_enviada"] and yerba[0]["proximo_aviso_desde"] is None,
              "con más de 4 horas desde el último aviso, proximo_aviso_desde viene vacío", f"yerba: {yerba}")
    r = pedir(c, "GET", "/api/alertas?limite=2", headers=H)
    verificar(len(r.json()) == 2, "el límite de alertas se respeta", f"límite: {len(r.json())}")
    pedir(c, "GET", "/api/alertas?limite=5001", headers=H, esperado=422)
    dash = pedir(c, "GET", "/api/dashboard", headers=H).json()
    verificar(dash["alertas_activas"] == len(de_la_api), "el dashboard cuenta las mismas alertas",
              f"alertas_activas {dash['alertas_activas']} vs {len(de_la_api)}")

    r = pedir(c, "GET", "/api/config/alertas", ejemplo="config_alertas", headers=H)
    ca = r.json()
    total_panel = len(alerts.listar_umbrales_por_producto())
    db.cerrar_conexion()
    verificar(ca["umbrales_propios"] == {"total": total_panel, "inactivos": 1},
              f"umbrales propios: el mismo número que el título del Panel ({total_panel}), 1 inactivo",
              f"umbrales propios: {ca['umbrales_propios']} (Panel {total_panel})")
    verificar(ca["umbral_global"] == {"stock_minimo": 20, "stock_maximo": 20, "existe": True},
              "el umbral global se ve como está", f"global: {ca['umbral_global']}")
    pedazos = {B.BOT_TOKEN[i:i + 4] for i in range(len(B.BOT_TOKEN) - 3)}
    verificar(not any(p in r.text for p in pedazos) and ca["telegram"]["token_mascara"] == "••••••••"
              and ca["telegram"]["token_configurado"] is True,
              "la config del bot no trae NI UN pedazo del token (va tapado entero)", f"telegram: {ca['telegram']}")

    r = pedir(c, "PUT", "/api/config/umbrales", ejemplo="umbrales_guardar", headers=H,
              json={"stock_minimo": 0, "stock_maximo": 0})
    del_bot = alertas_del_bot()
    de_la_api = {(a["codigo"], a["tipo"]) for a in pedir(c, "GET", "/api/alertas", headers=H).json()}
    propios = {u["codigo"] for u in alerts.listar_umbrales_por_producto()}
    db.cerrar_conexion()
    verificar(de_la_api == del_bot and {cod for cod, _ in de_la_api} <= propios,
              "con el global en 0/0 se apagan los avisos de los que no tienen umbral propio (igual que el bot)",
              f"API {sorted(de_la_api)} vs bot {sorted(del_bot)}")
    antes_propios = total_panel
    r = pedir(c, "DELETE", "/api/config/umbral-global", ejemplo="umbral_global_quitar", headers=H)
    despues_propios = len(alerts.listar_umbrales_por_producto())
    db.cerrar_conexion()
    verificar(r.json()["borradas"] >= 1 and r.json()["umbral_global"]["existe"] is False
              and despues_propios == antes_propios,
              "quitar el umbral global NO toca los umbrales propios", f"quitar global: {r.json()}")
    del_bot = alertas_del_bot()
    de_la_api = {(a["codigo"], a["tipo"]) for a in pedir(c, "GET", "/api/alertas", headers=H).json()}
    verificar(de_la_api == del_bot, "sin global, las alertas siguen siendo las mismas que las del bot",
              f"API {sorted(de_la_api)} vs bot {sorted(del_bot)}")
    pedir(c, "PUT", "/api/config/umbrales", headers=H, json={"stock_minimo": 20, "stock_maximo": 20})
    pedir(c, "PUT", "/api/config/umbrales", headers=H, esperado=422, json={"stock_minimo": -1, "stock_maximo": 0})
    pedir(c, "PUT", "/api/config/umbrales", headers=H, esperado=422, json={"stock_minimo": 100001, "stock_maximo": 0})

    # ---------------- Telegram ----------------
    ruta_ini = os.path.join(TMP_A, "config.ini")
    config.actualizar_config_dict({"telegram": {"habilitado": "true"}})
    antes_ini = config_por_seccion(ruta_ini)
    r = pedir(c, "PUT", "/api/config/telegram", ejemplo="config_telegram", headers=H, json={"habilitado": False})
    despues_ini = config_por_seccion(ruta_ini)
    esperado_ini = {s: dict(v) for s, v in antes_ini.items()}
    esperado_ini["telegram"]["habilitado"] = "false"
    verificar(despues_ini == esperado_ini and r.json()["telegram"]["habilitado"] is False,
              "apagar el bot desde el celular cambia SOLO [telegram] habilitado (el resto de config.ini igual)",
              f"config.ini cambió de más: {despues_ini}")
    verificar(despues_ini["remoto"] == antes_ini["remoto"], "y el [remoto] (token de la laptop de Leo) queda intacto",
              "se tocó [remoto]")
    with open(ruta_ini, "rb") as f:
        bytes_antes = f.read()
    r = pedir(c, "PUT", "/api/config/telegram", headers=H, esperado=422,
              json={"habilitado": True, "bot_token": "999:OTRO"})
    with open(ruta_ini, "rb") as f:
        bytes_despues = f.read()
    verificar(r.json().get("codigo") == "solo_en_la_pc" and bytes_antes == bytes_despues,
              "mandar el token del bot desde el celular da 422 solo_en_la_pc y no toca nada", f"bot_token: {r.text}")
    r = pedir(c, "POST", "/api/config/telegram/probar", ejemplo="telegram_probar", headers=H, json={})
    verificar(r.json()["ok"] is False and r.json()["enviado"] is False and "DESTILDADO" in r.json()["detalle"],
              "probar con el bot apagado dice por qué no manda", f"probar: {r.json()}")
    r = pedir(c, "POST", "/api/config/telegram/probar", headers=H, json={}, esperado=429)
    verificar(r.json().get("codigo") == "ocupado", "probar dos veces seguidas da 429 ocupado (uno cada 30 s)",
              f"probar repetido: {r.text}")
    pedir(c, "PUT", "/api/config/telegram", headers=H, json={"habilitado": True})
    config.actualizar_config_dict({"telegram": {"bot_token": ""}})
    r = pedir(c, "GET", "/api/config/alertas", ejemplo="config_alertas", headers=H)
    verificar(r.json()["telegram"]["token_mascara"] == "" and r.json()["telegram"]["token_configurado"] is False,
              "sin token cargado, la máscara viene vacía", f"sin token: {r.json()['telegram']}")
    config.actualizar_config_dict({"telegram": {"bot_token": B.BOT_TOKEN}})

    # ---------------- Facturas ----------------
    LINEAS = ["FACTURA A 0001-00004321", f"{B.CAFE}  CAFE MOLIDO X 500   x12   $3.000,00",
              "COCA COLA 2.25 LTS   x6   $1250,00", "ALFAJOR TRIPLE X 12   x2   $9000,00",
              "Yerba suelta  4  3.500", "TOTAL 99999"]
    pdf = B.pdf_de_texto(LINEAS)
    huella = B.volcado_base(RUTA_DB)
    r = B.subir_pdf(c, token, pdf, "remito.pdf")
    textos.append(r.text)
    respuestas.setdefault("factura_analizar", []).append((r.status_code, r.json(), "POST", "/api/facturas/analizar"))
    fa = r.json()
    verificar(r.status_code == 200 and B.volcado_base(RUTA_DB) == huella,
              "analizar una factura no escribe nada en la base", f"analizar: {r.status_code}")
    items = fa.get("items", [])
    coca = [i for i in items if i["nombre"].startswith("COCA")]
    verificar(coca and coca[0]["emparejamiento"] and coca[0]["emparejamiento"]["candidatos"]
              and coca[0]["emparejamiento"]["candidatos"][0]["codigo"] == B.COCA,
              "la línea sin código trae candidatos por nombre (y nada se aplica solo)", f"coca: {coca}")
    sueltas = [i for i in items if i["nombre"].lower().startswith("yerba")]
    verificar(sueltas and sueltas[0]["precio_sospechoso"] is True,
              "'3.500' leído como 3,5 sale marcado como precio sospechoso", f"yerba suelta: {sueltas}")
    cafe = [i for i in items if i["codigo"] == B.CAFE]
    verificar(cafe and cafe[0]["existe"] and cafe[0]["emparejamiento"] is None and cafe[0]["nombre_sistema"],
              "la línea con código conocido se reconoce por código", f"café: {cafe}")
    r = B.subir_pdf(c, token, B.pdf_de_texto([]), "escaneado.pdf")
    textos.append(r.text)
    respuestas.setdefault("factura_analizar", []).append((r.status_code, r.json(), "POST", "/api/facturas/analizar"))
    verificar(r.json().get("es_pdf_escaneado") is True and r.json().get("items") == [],
              "un PDF sin texto se marca como escaneado", f"escaneado: {r.text[:200]}")
    r = B.subir_pdf(c, token, b"x" * 300 + pdf)
    verificar(r.status_code == 200, "un PDF con 300 bytes de basura antes de %PDF- se acepta", f"basura: {r.status_code}")
    r = B.subir_pdf(c, token, b"x" * 1100 + pdf)
    verificar(r.status_code == 415 and r.json().get("codigo") == "no_es_pdf",
              "sin %PDF- en los primeros 1024 bytes, 415 no_es_pdf", f"no es pdf: {r.status_code} {r.text}")
    r = B.subir_pdf(c, token, B.pdf_de_texto(LINEAS, paginas=11))
    verificar(r.status_code == 422 and r.json() == {"detail": "El PDF tiene más de 10 páginas: cargalo desde el "
                                                              "Panel de la PC.", "codigo": "pdf_danado"},
              "un PDF de 11 páginas da 422 pdf_danado", f"11 páginas: {r.status_code} {r.text}")
    r = B.subir_pdf(c, token, b"%PDF-1.4\nesto no es un pdf de verdad")
    verificar(r.status_code == 422 and r.json().get("codigo") == "pdf_danado", "un PDF roto da 422 pdf_danado",
              f"pdf roto: {r.status_code} {r.text}")
    maximo, segundos = panel_celular.MAX_EMPAREJAR, panel_celular.SEGUNDOS_EMPAREJAR
    try:
        panel_celular.MAX_EMPAREJAR = 2
        r = B.subir_pdf(c, token, pdf)
        sin_codigo = [i for i in r.json()["items"] if not i["existe"]]
        verificar(len(sin_codigo) == 3 and [i["emparejamiento"]["motivo"] for i in sin_codigo][2] == "demasiados"
                  and all(i["emparejamiento"]["motivo"] == "sin_codigo" for i in sin_codigo[:2]),
                  "con el tope en 2, el tercer ítem sin código sale 'demasiados' y NO se descarta",
                  f"tope 2: {[(i['nombre'], i['emparejamiento']) for i in sin_codigo]}")
        panel_celular.MAX_EMPAREJAR, panel_celular.SEGUNDOS_EMPAREJAR = maximo, 0
        r = B.subir_pdf(c, token, pdf)
        sin_codigo = [i for i in r.json()["items"] if not i["existe"]]
        verificar(len(sin_codigo) == 3 and all(i["emparejamiento"]["motivo"] == "demasiados" for i in sin_codigo),
                  "con 0 segundos, todos los sin código salen 'demasiados' y siguen en la lista",
                  f"0 s: {[i['emparejamiento'] for i in sin_codigo]}")
    finally:
        panel_celular.MAX_EMPAREJAR, panel_celular.SEGUNDOS_EMPAREJAR = maximo, segundos

    pedir(c, "POST", "/api/facturas/aplicar", headers=H, esperado=422,
          json={"factura_nombre": "remito.pdf", "items": [{"codigo": B.CAFE, "cantidad": -5}]})
    pedir(c, "POST", "/api/facturas/aplicar", headers=H, esperado=422,
          json={"factura_nombre": "remito.pdf", "items": []})
    stock_cafe = fila("SELECT stock FROM Productos WHERE codigo = ?", (B.CAFE,))["stock"]
    r = pedir(c, "POST", "/api/facturas/aplicar", ejemplo="factura_aplicar", headers=H,
              json={"factura_nombre": "remito.pdf",
                    "items": [{"codigo": B.CAFE, "cantidad": 12, "precio_compra": 3100},
                              {"codigo": "7790999", "cantidad": 1, "precio_compra": None}], "forzar": False})
    mov = fila("SELECT * FROM Movimientos_Stock WHERE producto_codigo = ? ORDER BY id DESC LIMIT 1", (B.CAFE,))
    verificar(mov["tipo"] == "ENTRADA_PDF" and mov["usuario"] == "dueño (celular)"
              and mov["motivo"] == "Factura PDF: remito.pdf"
              and fila("SELECT stock FROM Productos WHERE codigo = ?", (B.CAFE,))["stock"] == stock_cafe + 12,
              "aplicar la factura suma el stock con un ENTRADA_PDF firmado desde el celular", f"movimiento: {mov}")
    verificar([x["ok"] for x in r.json()] == [True, False], "un renglón con código inexistente no frena el resto",
              f"aplicar: {r.json()}")
    stock_cafe = fila("SELECT stock FROM Productos WHERE codigo = ?", (B.CAFE,))["stock"]
    r = pedir(c, "POST", "/api/facturas/aplicar", ejemplo="error_409_factura", headers=H, esperado=409,
              json={"factura_nombre": "remito.pdf", "items": [{"codigo": B.CAFE, "cantidad": 12, "precio_compra": None}]})
    cuerpo = r.json()
    verificar(cuerpo.get("codigo") == "factura_ya_aplicada" and cuerpo.get("renglones_iguales") == 1
              and isinstance(cuerpo.get("hace_min"), int)
              and fila("SELECT stock FROM Productos WHERE codigo = ?", (B.CAFE,))["stock"] == stock_cafe,
              "aplicar dos veces la misma factura da 409 factura_ya_aplicada y no suma el stock otra vez",
              f"segunda vez: {cuerpo}")
    pedir(c, "POST", "/api/facturas/aplicar", headers=H,
          json={"factura_nombre": "remito.pdf", "items": [{"codigo": B.CAFE, "cantidad": 12}], "forzar": True})
    verificar(fila("SELECT stock FROM Productos WHERE codigo = ?", (B.CAFE,))["stock"] == stock_cafe + 12,
              "con «Cargar igual» (forzar) se aplica", "forzar no aplicó")
    r = pedir(c, "POST", "/api/facturas/aplicar", headers=H,
              json={"factura_nombre": "remito.pdf", "items": [{"codigo": B.FIDEOS, "cantidad": 3}]})
    verificar(r.status_code == 200, "otra factura con el mismo nombre y renglones distintos no da 409",
              f"otra factura: {r.status_code}")

    # ---------------- Red ----------------
    with B.cliente(app, ip="192.168.1.5", servidor="192.168.1.2") as lan:
        r = pedir(lan, "GET", "/api/productos", ejemplo="error_403_red", headers=H, esperado=403)
    verificar(r.json().get("codigo") == "red_no_permitida", "desde la LAN, 403 aunque traiga un token válido",
              f"LAN: {r.text}")

# Probar Telegram con éxito (otra app: la anterior quedó con el "uno cada 30 s")
config.actualizar_config_dict({"telegram": {"habilitado": "true"}})
enviados.clear()
app2 = api_celular.crear_app()
with B.cliente(app2) as c:
    app2.state.estado.refrescar()
    token = B.login(c)
    r = pedir(c, "POST", "/api/config/telegram/probar", ejemplo="telegram_probar", headers=B.auth(token), json={})
    verificar(r.json()["ok"] is True and r.json()["enviado"] is True and len(enviados) == 1,
              "probar con el bot prendido manda un mensaje de verdad (simulado) y dice ok", f"probar: {r.json()}")

# ===================================================================== #
# Fase B: la base de producción, con el sha256('1234') del setup viejo
# ===================================================================== #
usar_instalacion(TMP_SIN_PIN)
os.remove(os.path.join(TMP_SIN_PIN, "config.ini"))
app = api_celular.crear_app()
with B.cliente(app) as c:
    app.state.estado.refrescar()
    r = pedir(c, "GET", "/api/salud", ejemplo="salud")
    s = r.json()
    verificar(s["pin_configurado"] is False and s["motivo"] == "pin_no_definido" and s["login_disponible"] is False,
              "con el hash viejo, salud dice que falta el PIN", f"salud sin PIN: {s}")
    verificar(s["nombre_local"] == "", "sin config.ini, nombre_local viene vacío (no el de los valores por defecto)",
              f"nombre_local sin config.ini: {s['nombre_local']!r}")
    r = pedir(c, "POST", "/api/auth/login", ejemplo="error_503_pin", json={"pin": "1234"}, esperado=503)
    verificar(r.json() == {"detail": "El PIN viejo quedó anulado: hay que definir uno nuevo en la PC del local.",
                           "codigo": "pin_no_definido"},
              "el 1234 de producción nunca entra: 503 pin_no_definido", f"login 1234: {r.text}")

# ===================================================================== #
# Fase C: base con una columna menos (un ejecutable nuevo sobre una base vieja)
# ===================================================================== #
TMP_VIEJA = os.path.join(RAIZ_TMP, "vieja")
db.cerrar_conexion()
B.copiar_instalacion(TMP_A, TMP_VIEJA)
con = sqlite3.connect(os.path.join(TMP_VIEJA, "database", "stock.db"))
try:
    con.execute("ALTER TABLE Productos DROP COLUMN subrubro")
    con.commit()
finally:
    con.close()
RUTA_VIEJA = usar_instalacion(TMP_VIEJA)
app = api_celular.crear_app()
with B.cliente(app) as c:
    app.state.estado.refrescar()
    r = pedir(c, "GET", "/api/salud", ejemplo="salud")
    s = r.json()
    verificar(s["base"]["ok"] is False and "Productos.subrubro" in s["base"]["detalle"]
              and s["motivo"] == "base_desactualizada",
              "con una columna menos, salud lo dice y nombra la columna", f"salud base vieja: {s}")
    TEXTO_VIEJA = ("La base del negocio tiene una versión vieja: abrí la Caja una vez o corré el Actualizador. "
                   "No se tocó nada.")
    H = B.auth(token)
    PEDIDOS = [("GET", "/api/productos", None), ("GET", "/api/auth/yo", None), ("GET", "/api/dashboard", None),
               ("GET", f"/api/productos/{B.CAFE}", None), ("GET", "/api/movimientos", None),
               ("POST", "/api/stock/movimiento", {"codigo": B.CAFE, "cantidad": 1, "operacion": "sumar"}),
               ("POST", "/api/stock/lector", {"codigo": B.CAFE, "operacion": "sumar"}),
               ("GET", f"/api/precios/{B.CAFE}", None),
               ("POST", "/api/precios/recalcular", {"cambio": "precio_final", "precio_final": 1}),
               ("POST", "/api/precios/previsualizar", {"codigos": [B.CAFE], "porcentaje": 1}),
               ("GET", "/api/alertas", None), ("GET", "/api/config/alertas", None),
               ("PUT", "/api/config/telegram", {"habilitado": True}),
               ("PUT", "/api/config/umbrales", {"stock_minimo": 1, "stock_maximo": 1}),
               ("DELETE", "/api/config/umbral-global", None)]
    huella = B.volcado_base(RUTA_VIEJA)
    malos = []
    for metodo, ruta, cuerpo in PEDIDOS:
        r = c.request(metodo, ruta, headers=H, json=cuerpo)
        textos.append(r.text)
        if r.status_code != 503 or r.json() != {"detail": TEXTO_VIEJA, "codigo": "base_desactualizada"}:
            malos.append((metodo, ruta, r.status_code, r.text[:120]))
    r = pedir(c, "GET", "/api/productos", ejemplo="error_503_base_desactualizada", headers=H, esperado=503)
    r = pedir(c, "POST", "/api/auth/login", json={"pin": B.PIN}, esperado=503)
    if r.json().get("codigo") != "base_desactualizada":
        malos.append(("POST", "/api/auth/login", r.status_code, r.text[:120]))
    verificar(not malos and B.volcado_base(RUTA_VIEJA) == huella,
              f"con la base vieja, los {len(PEDIDOS) + 1} endpoints dan 503 base_desactualizada y no tocan nada",
              f"endpoints que no dieron 503 base_desactualizada: {malos}")
    # Lo migra la Caja o el Actualizador (acá, la prueba): la API anda sin reiniciar.
    sacar_trampas()
    try:
        db.cerrar_conexion()
        db.aplicar_migraciones()
        db.cerrar_conexion()
    finally:
        poner_trampas()
    r = pedir(c, "GET", "/api/productos", headers=H)
    verificar(r.status_code == 200 and len(r.json()) >= 1,
              "apenas se migra la base, la API anda sin reiniciar nada (el resultado malo no se cachea)",
              f"después de migrar: {r.status_code}")

# ===================================================================== #
# Fase D: carpeta sin base (la API instalada en la carpeta equivocada)
# ===================================================================== #
TMP_VACIA = os.path.join(RAIZ_TMP, "sin_base")
os.makedirs(TMP_VACIA)
shutil.copytree(os.path.join(TMP_A, "api_celular"), os.path.join(TMP_VACIA, "api_celular"))


def listado(carpeta):
    return sorted(os.path.relpath(os.path.join(r, n), carpeta) for r, ds, fs in os.walk(carpeta) for n in ds + fs)


antes = listado(TMP_VACIA)
usar_instalacion(TMP_VACIA)
app = api_celular.crear_app()
with B.cliente(app) as c:
    app.state.estado.refrescar()
    r = pedir(c, "GET", "/api/salud", ejemplo="salud")
    s = r.json()
    verificar(s["base"]["ok"] is False and s["motivo"] == "base_no_disponible",
              "sin base, salud sigue contestando y dice base_no_disponible", f"salud sin base: {s}")
    r = pedir(c, "POST", "/api/auth/login", json={"pin": B.PIN}, esperado=503)
    verificar(r.json().get("codigo") == "base_no_disponible", "sin base, el login da 503 base_no_disponible",
              f"login sin base: {r.text}")
    malos = []
    for metodo, ruta, cuerpo in PEDIDOS:
        r = c.request(metodo, ruta, headers=B.auth(token), json=cuerpo)
        textos.append(r.text)
        if r.status_code != 503 or r.json().get("codigo") != "base_no_disponible":
            malos.append((metodo, ruta, r.status_code))
    verificar(not malos, "sin base, todos los endpoints dan 503 base_no_disponible (también con un token válido)",
              f"endpoints sin 503: {malos}")
db.cerrar_conexion()
nuevos = [x for x in listado(TMP_VACIA) if x not in antes]
creados = [x for x in nuevos if "database" in x or "stock.db" in x]
verificar(not creados, "sin base, la API NO crea database\\ ni stock.db ni -wal ni -shm",
          f"la API creó: {creados}")

# ===================================================================== #
# Lo que vale para todo
# ===================================================================== #
verificar(not llamadas_prohibidas,
          "la API nunca llamó a init_db, preparar_base, aplicar_migraciones ni MonitorAlertas",
          f"llamadas prohibidas: {llamadas_prohibidas}")
filtrados = [t for t in textos if "TOKEN-REMOTO-DE-PRUEBA" in t or "BOT-DE-PRUEBA" in t]
verificar(not filtrados, f"ninguna de las {len(textos)} respuestas trae el token de [remoto] ni el del bot (regla 4)",
          f"respuestas con un token: {[t[:200] for t in filtrados]}")

# Forma de cada respuesta contra el contrato compartido con la app
errores_fixture = contrato.errores_del_fixture()
verificar(not errores_fixture, "el fixture del contrato es válido (cada anulable aparece lleno y vacío)",
          f"errores del fixture: {errores_fixture}")
fx = contrato.fixture
verificar(fx.get("contrato") == api_celular.CONTRATO and fx.get("firma") == api_celular.FIRMA,
          "el fixture es del contrato 2 y de la firma otter-api-celular", f"fixture: {fx.get('contrato')} {fx.get('firma')}")
sin_probar = sorted(set(fx["ejemplos"]) - set(respuestas))
verificar(not sin_probar, f"se compararon de verdad los {len(fx['ejemplos'])} ejemplos del contrato",
          f"ejemplos del fixture que la prueba no ejercitó: {sin_probar}")
errores_forma = []
for nombre, lista in sorted(respuestas.items()):
    ejemplo = fx["ejemplos"].get(nombre)
    if ejemplo is None:
        errores_forma.append(f"{nombre}: no está en el fixture")
        continue
    for status, cuerpo, metodo, ruta in lista:
        if status != ejemplo["status"]:
            errores_forma.append(f"{nombre}: status {status}, el contrato dice {ejemplo['status']}")
        if metodo != ejemplo["metodo"] or _ruta_normalizada(ruta) != _ruta_normalizada(ejemplo["ruta"]):
            errores_forma.append(f"{nombre}: se pidió {metodo} {ruta}, el contrato dice {ejemplo['metodo']} "
                                 f"{ejemplo['ruta']}")
        errores_forma.extend(contrato.comparar(cuerpo, nombre))
verificar(not errores_forma,
          f"las {sum(len(v) for v in respuestas.values())} respuestas reales tienen la forma del contrato",
          "respuestas que no cumplen el contrato:\n    " + "\n    ".join(sorted(set(errores_forma))))

# Y la comparación tiene dientes: una respuesta con un campo de más, uno de
# menos, un tipo cambiado o un null donde no va, se detecta.
salud_ok = respuestas["salud"][0][1]
pruebas_dientes = {
    "campo de más": dict(salud_ok, nuevo=1),
    "campo de menos": {k: v for k, v in salud_ok.items() if k != "compilado"},
    "tipo cambiado": dict(salud_ok, contrato="2"),
    "bool como número": dict(salud_ok, contrato=True),
    "null donde no va": dict(salud_ok, servicio=None),
}
sin_dientes = [n for n, v in pruebas_dientes.items() if not contrato.comparar(v, "salud")]
verificar(not sin_dientes and not contrato.comparar(dict(salud_ok, motivo=None), "salud"),
          "la comparación detecta campos de más, de menos, tipos cambiados y nulls donde no van",
          f"la comparación no detectó: {sin_dientes}")

db.cerrar_conexion()
db.usar_solo_base_existente(None)
shutil.rmtree(RAIZ_TMP, ignore_errors=True)
print()
if fallos:
    print("=== FALLOS API CELULAR (CONTRATO) ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== API CELULAR (CONTRATO) OK ===")
