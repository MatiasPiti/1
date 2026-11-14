"""El servicio de stock (lo único que corre 24/7) no puede descontar dos veces ni fallar en silencio.

Nunca se había probado lo que hace el StockService por su cuenta, y tenía
cuatro fallas que no se ven desde ninguna pantalla:

1. STOCK DESCONTADO DOS VECES AL CONCILIAR. Cada 5 segundos el servicio busca
   ventas recientes sin su salida de stock y la aplica él. Conciliar un
   pendrive de emergencia grababa primero las ventas y después sus
   movimientos: en el medio, el servicio las veía "sin descontar", las
   descontaba, y enseguida llegaba el movimiento del pendrive por la misma
   venta. 3 unidades vendidas en la emergencia, 6 menos en el Maestro.

2. LA API REMOTA NO DEJABA NADA ESCRITO. Su logger no tenía archivo: "No se
   pudo iniciar la API remota" (por qué Leo no puede conectar) y el error de
   cada función que fallaba se perdían.

3. EL MONITOR DE ALERTAS FALLABA MUDO, y un config.ini mal formado mostraba
   en el error el TEXTO de la línea rota — que puede ser la del token.

4. LA MISMA ALERTA DOS VECES. Las alertas las revisan dos programas (el
   servicio, y el Panel mientras está abierto), cada uno con el "ya se
   mandó" que leyó al empezar su vuelta.

Acá se corre el ciclo de verdad del servicio contra bases de verdad (el
Maestro y dos pendrives) y se mira el stock que queda, el archivo de log y
los mensajes que salen.
"""
import json
import logging
import os
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RAIZ = tempfile.mkdtemp(prefix="servicio_stock_")
MAESTRO = os.path.join(RAIZ, "SistemaDual")
USB_1 = os.path.join(RAIZ, "usb_1")
USB_2 = os.path.join(RAIZ, "usb_2")
os.makedirs(os.path.join(MAESTRO, "StockService"))
os.makedirs(USB_1)
os.makedirs(USB_2)
# El servicio fija su base en "la carpeta padre de donde está el programa"
# (C:\SistemaDual\StockService\StockService.exe -> C:\SistemaDual). Se lo
# ubica igual, para que su log y su base caigan en la carpeta de la prueba.
sys.argv[0] = os.path.join(MAESTRO, "StockService", "StockService.py")

from pos_core import paths, db

fallos = []


def usar(base):
    """Cambia de máquina: cierra la base anterior y abre la de `base`."""
    db.cerrar_conexion()
    paths.set_base_override(base)
    db.preparar_base()


from pos_core import (alerts, config, excel_import, products, reconciliation, sales,
                      stock_service)
from pos_core import sync_export
from pos_core.sales import CODIGO_SIN_BARRA

usar(MAESTRO)
import services.stock_daemon_windows as demonio

if os.path.normpath(paths.get_base_path()) != os.path.normpath(MAESTRO):
    fallos.append(f"la prueba no ubicó al servicio en su carpeta: {paths.get_base_path()}")
LOG = os.path.join(MAESTRO, "logs", "stock_daemon.log")


def leer_log():
    for h in demonio.log.handlers:
        h.flush()
    with open(LOG, encoding="utf-8") as f:
        return f.read()


def stock(codigo):
    return db.get_connection().execute(
        "SELECT stock FROM Productos WHERE codigo = ?", (codigo,)).fetchone()["stock"]


def salidas(codigo):
    return [dict(f) for f in db.get_connection().execute(
        "SELECT cantidad, origen, ticket_uuid FROM Movimientos_Stock "
        "WHERE producto_codigo = ? AND tipo = 'SALIDA_VENTA' ORDER BY id", (codigo,))]


def vender(codigo, cantidad, origen="MAESTRO", precio=1000.0):
    return sales.cerrar_ticket(
        [{"codigo": codigo, "nombre": codigo, "cantidad": cantidad, "precio_unitario": precio}],
        metodo_pago="EFECTIVO", usuario="cajero", origen=origen)


for codigo, inicial in (("111", 20), ("222", 0), ("333", 10), ("444", 10)):
    products.crear_producto(codigo=codigo, nombre=f"PRODUCTO {codigo}", precio_venta=1000.0,
                            stock_inicial=inicial, usuario="test")
lista = os.path.join(RAIZ, "lista.xlsx")
excel_import.exportar_lista_precios(lista)

# --------------------------------------------------------------------- #
# 1. Una venta cuyo descuento se perdió se descuenta sola, UNA vez
# --------------------------------------------------------------------- #
# La Caja graba la venta y recién después descuenta. Si se cierra de golpe
# en el medio, la venta queda sin su salida: para eso está el servicio.
_descontar = stock_service.descontar_por_venta


def _se_corta_la_luz(*a, **k):
    raise RuntimeError("la Caja se cerró antes de descontar")


stock_service.descontar_por_venta = _se_corta_la_luz
venta = vender("111", 2)
stock_service.descontar_por_venta = _descontar
if stock("111") != 20 or not venta["fallas_stock"]:
    fallos.append("la prueba no logró dejar una venta sin su descuento de stock")

for _ in range(3):
    demonio.ciclo_watchdog()
if stock("111") != 18 or len(salidas("111")) != 1:
    fallos.append(f"una venta de 2 con el descuento pendiente dejó el stock en {stock('111')} "
                   f"(tenía 20) con {len(salidas('111'))} salidas: tenía que quedar 18 con una sola")
elif leer_log().count("Descuento diferido aplicado") != 1:
    fallos.append("el log no dice (una vez) que el servicio aplicó el descuento pendiente")
else:
    print("OK: una venta que quedó sin descontar la descuenta el servicio, una sola vez")

# --------------------------------------------------------------------- #
# 2. El "artículo sin código" no es un descuento pendiente
# --------------------------------------------------------------------- #
sales.cerrar_ticket([{"codigo": CODIGO_SIN_BARRA, "nombre": "FIAMBRE", "cantidad": 1,
                      "precio_unitario": 900.0}],
                    metodo_pago="EFECTIVO", usuario="cajero")
if demonio._ventas_con_stock_pendiente():
    fallos.append("una línea de 'artículo sin código' figura como descuento pendiente: "
                   "el servicio la reintentaría cada 5 segundos para siempre")
else:
    print("OK: el artículo sin código no queda como descuento pendiente")

# --------------------------------------------------------------------- #
# 3. Sin stock: avisa UNA vez, no se olvida, y descuenta cuando hay
# --------------------------------------------------------------------- #
vender("222", 1)                       # stock 0: la venta se cobra, el descuento no sale
antes = leer_log().count("Fallo aplicando descuento diferido")
for _ in range(4):
    demonio.ciclo_watchdog()
despues = leer_log().count("Fallo aplicando descuento diferido")
if despues - antes != 1:
    fallos.append(f"una venta sin stock dejó {despues - antes} errores en el log en 4 vueltas: "
                   f"tiene que quedar uno solo (son 17.000 vueltas por día)")
elif stock("222") != 0:
    fallos.append("sin stock, el servicio dejó el stock en negativo")
else:
    print("OK: una venta sin stock deja UN error en el log, no uno por vuelta")

stock_service.sumar_stock_manual("222", 5, usuario="dueño")
demonio.ciclo_watchdog()
demonio.ciclo_watchdog()
if stock("222") != 4:
    fallos.append(f"llegó mercadería (5) con una venta de 1 pendiente y el stock quedó en {stock('222')}")
elif demonio._fallas_ya_registradas:
    fallos.append(f"el servicio se queda con fallas viejas en memoria para siempre: "
                   f"{demonio._fallas_ya_registradas}")
else:
    print("OK: cuando entra la mercadería, la venta pendiente se descuenta (5 - 1 = 4) y se olvida la falla")

# --------------------------------------------------------------------- #
# 4. Conciliar un pendrive con el servicio trabajando en el medio
# --------------------------------------------------------------------- #
usar(USB_1)
excel_import.cargar_masivo(lista, usuario="cajero", origen="USB_CAJA")
vender("333", 3, origen="USB_CAJA")
vender("333", 1, origen="USB_CAJA")
sync_export.exportar_caja()
archivo_1 = os.path.join(USB_1, "SYNC_DATA", "export_caja.json")

usar(MAESTRO)
_aplicar = reconciliation._aplicar_movimiento_reproducido
vueltas = []


def _con_el_servicio_en_el_medio(mov, delta, usuario_dev):
    # El servicio despierta antes y después de cada movimiento.
    demonio.ciclo_watchdog()
    try:
        return _aplicar(mov, delta, usuario_dev)
    finally:
        demonio.ciclo_watchdog()
        vueltas.append(1)


reconciliation._aplicar_movimiento_reproducido = _con_el_servicio_en_el_medio
previa = reconciliation.analizar_export_caja(archivo_1)
resumen = reconciliation.aplicar_export_caja(archivo_1, usuario_dev="dev")
reconciliation._aplicar_movimiento_reproducido = _aplicar
demonio.ciclo_watchdog()               # y una vuelta más, ya con todo conciliado

if not vueltas:
    fallos.append("la prueba no llegó a correr el servicio en medio de la conciliación")
if resumen.errores:
    fallos.append(f"la conciliación dio errores: {resumen.errores}")
if stock("333") != 6:
    fallos.append(f"en la emergencia se vendieron 4 del producto 333 (tenía 10) y el Maestro quedó en "
                   f"{stock('333')}: el servicio y la conciliación descontaron los dos la misma venta")
elif len(salidas("333")) != 2:
    fallos.append(f"dos ventas del 333 dejaron {len(salidas('333'))} salidas de stock: {salidas('333')}")
elif (resumen.ventas_nuevas, resumen.movimientos_aplicados, resumen.movimientos_omitidos_duplicados) != (2, 2, 0):
    fallos.append(f"el resumen de la conciliación no dice lo que pasó: {resumen.ventas_nuevas} ventas, "
                   f"{resumen.movimientos_aplicados} movimientos aplicados, "
                   f"{resumen.movimientos_omitidos_duplicados} duplicados (eran 2, 2 y 0)")
elif (previa.ventas_nuevas, previa.movimientos_aplicados) != (2, 2):
    fallos.append("el resumen previo no coincide con lo que después se aplicó")
else:
    print("OK: conciliar con el servicio trabajando en el medio descuenta UNA vez (10 - 4 = 6)")

# --------------------------------------------------------------------- #
# 5. La conciliación falla a medias, el servicio descuenta, y se repite
# --------------------------------------------------------------------- #
usar(USB_2)
excel_import.cargar_masivo(lista, usuario="cajero", origen="USB_CAJA")
vender("444", 2, origen="USB_CAJA")
sync_export.exportar_caja()
archivo_2 = os.path.join(USB_2, "SYNC_DATA", "export_caja.json")

usar(MAESTRO)


def _falla_de_ese_momento(mov, delta, usuario_dev):
    raise RuntimeError("database is locked")


reconciliation._aplicar_movimiento_reproducido = _falla_de_ese_momento
primera = reconciliation.aplicar_export_caja(archivo_2, usuario_dev="dev")
reconciliation._aplicar_movimiento_reproducido = _aplicar
if not primera.errores or primera.ventas_nuevas != 1 or stock("444") != 10:
    fallos.append("la prueba no logró dejar una conciliación a medias (venta adentro, movimiento afuera)")

demonio.ciclo_watchdog()               # el servicio ve la venta sin su salida y la descuenta
if stock("444") != 8:
    fallos.append(f"el servicio no descontó la venta que quedó a medias: stock {stock('444')}")

previa_2 = reconciliation.analizar_export_caja(archivo_2)
segunda = reconciliation.aplicar_export_caja(archivo_2, usuario_dev="dev")
if stock("444") != 8:
    fallos.append(f"repetir la conciliación después de que el servicio descontó dejó el stock en "
                   f"{stock('444')}: tenía 10 y se vendieron 2")
elif segunda.movimientos_aplicados != 0 or segunda.movimientos_omitidos_duplicados != 1:
    fallos.append(f"al repetir, el resumen dice {segunda.movimientos_aplicados} aplicados y "
                   f"{segunda.movimientos_omitidos_duplicados} duplicados (era 0 y 1)")
elif previa_2.movimientos_aplicados != 0:
    fallos.append("el resumen previo anunciaba que iba a aplicar un movimiento que ya estaba descontado")
else:
    print("OK: repetir una conciliación que quedó a medias no vuelve a descontar lo que ya descontó el servicio")

# Y al revés: el servicio armó su lista de pendientes, y antes de que llegue
# a aplicarla ya la aplicó otro. La pregunta se repite al descontar.
stock_service.descontar_por_venta = _se_corta_la_luz
vender("111", 1)
stock_service.descontar_por_venta = _descontar
lista_vieja = demonio._ventas_con_stock_pendiente()
demonio.ciclo_watchdog()
_pendientes = demonio._ventas_con_stock_pendiente
demonio._ventas_con_stock_pendiente = lambda: lista_vieja
demonio.ciclo_watchdog()
demonio._ventas_con_stock_pendiente = _pendientes
if len(lista_vieja) != 1:
    fallos.append("la prueba no armó una lista de pendientes para repetir")
elif stock("111") != 17:
    fallos.append(f"con una lista de pendientes que ya no vale, el servicio descontó de nuevo: "
                   f"stock {stock('111')} (tenía que quedar 17)")
else:
    print("OK: si otro ya aplicó la salida, el servicio no la repite aunque la tuviera anotada")

# --------------------------------------------------------------------- #
# 6. Lo que le pasa a la API remota queda escrito
# --------------------------------------------------------------------- #
from services import remote_api

TOKEN = "galpon-de-prueba-0123456789abcdef"
TOKEN_BOT = "123456789:AAFakeTokenDePrueba_nuncaReal-xyz"
config.actualizar_config_dict({
    "remoto": {"habilitado": "true", "puerto": "8765", "token": TOKEN,
               # Una dirección que esta PC no tiene: es lo que pasa cuando en
               # escuchar_en quedó una IP de Tailscale que cambió.
               "escuchar_en": "203.0.113.7"},
    "telegram": {"bot_token": TOKEN_BOT, "chat_id_default": "999", "habilitado": "true"}})

if remote_api.iniciar_si_esta_habilitado() is not None:
    fallos.append("la prueba esperaba que la API remota no pudiera arrancar")
texto = leer_log()
if "No se pudo iniciar la API remota" not in texto:
    fallos.append("la API remota no pudo arrancar y en stock_daemon.log no quedó escrito por qué: "
                   "es lo primero que hay que leer cuando el Dueño Remoto no conecta")
else:
    print("OK: si la API remota no arranca, el motivo queda en stock_daemon.log")

servidor = remote_api.iniciar_servidor(puerto=0, token=TOKEN, escuchar_en="127.0.0.1")
puerto = servidor.server_address[1]


def rpc(modulo, funcion, **args):
    pedido = urllib.request.Request(
        f"http://127.0.0.1:{puerto}/rpc",
        data=json.dumps({"modulo": modulo, "funcion": funcion, "args": args}).encode("utf-8"),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(pedido, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


respuesta = rpc("stock_service", "sumar_stock_manual", codigo="NO-EXISTE", cantidad=1, usuario="dueño")
if respuesta.get("ok") is not False:
    fallos.append(f"la prueba esperaba un error de la API remota y contestó {respuesta}")
if "Error ejecutando stock_service.sumar_stock_manual" not in leer_log():
    fallos.append("una función de la API remota falló y el error no quedó en stock_daemon.log")
else:
    print("OK: el error de una función pedida por el Dueño Remoto queda en el log")

# Los pedidos que salen bien NO llenan el log (el celular pregunta /health
# cada 20 segundos, todo el día).
antes = len(leer_log())
for _ in range(5):
    if rpc("alerts", "obtener_umbral_global").get("ok") is not True:
        fallos.append("un pedido normal a la API remota falló")
if len(leer_log()) != antes:
    fallos.append("cada pedido que sale bien escribe en stock_daemon.log: lo llena en días")
servidor.shutdown()
servidor.server_close()

# Ningún secreto en el log, venga por donde venga.
logging.getLogger("remote_api").error(
    "pedido rechazado con Authorization: Bearer %s en https://api.telegram.org/bot%s/sendMessage",
    TOKEN, TOKEN_BOT)
texto = leer_log()
if TOKEN in texto or TOKEN_BOT in texto or "AAFakeToken" in texto:
    fallos.append("el log del servicio escribió un token entero: ese archivo se copia al Escritorio y se manda")
elif "[TAPADO]" not in texto:
    fallos.append("la prueba no llegó a escribir el renglón con los tokens")
else:
    print("OK: el log del servicio tapa el token de [remoto] y el del bot")

# --------------------------------------------------------------------- #
# 7. config.ini mal formado: se dice la línea, nunca lo que dice
# --------------------------------------------------------------------- #
ruta_config = paths.config_path()
with open(ruta_config, encoding="utf-8") as f:
    config_bueno = f.read()
SECRETO = "token-roto-NO-TIENE-QUE-VERSE-4f9a"
with open(ruta_config, "w", encoding="utf-8") as f:
    # Alguien editó el archivo a mano y se comió el "=" de la línea del token.
    f.write(f"[remoto]\nhabilitado = true\ntoken {SECRETO}\n")
try:
    config.cargar_config()
    fallos.append("un config.ini mal formado se leyó sin avisar")
except Exception as e:
    mensaje = f"{e!r} {e}"
    if SECRETO in mensaje:
        fallos.append("el error de un config.ini mal formado muestra el TEXTO de la línea rota "
                       "(acá, la del token): ese mensaje sale en el cartel de arranque de la Caja")
    elif "línea 3" not in str(e):
        fallos.append(f"el error de un config.ini mal formado no dice en qué línea está: {e}")
    elif e.__cause__ is not None or not e.__suppress_context__:
        fallos.append("el error original (con el texto de la línea) sigue encadenado: sale en la traza")
    else:
        print("OK: un config.ini mal formado dice la línea (3) pero no lo que dice la línea")

if remote_api.iniciar_si_esta_habilitado() is not None:
    fallos.append("con el config.ini mal formado la API remota dijo que arrancó")
import pos_core.telegram_bot as tb

monitor = tb.MonitorAlertas(intervalo_segundos=0.01)
vueltas_monitor = []
_revisar = tb.revisar_umbrales_y_alertar


def _revisar_y_contar():
    vueltas_monitor.append(1)
    if len(vueltas_monitor) >= 4:
        monitor.detener()
    return _revisar()


tb.revisar_umbrales_y_alertar = _revisar_y_contar
antes = leer_log().count("No se pudieron revisar las alertas de stock")
monitor.start()
monitor.join(timeout=20)
tb.revisar_umbrales_y_alertar = _revisar
despues = leer_log().count("No se pudieron revisar las alertas de stock")
texto = leer_log()
if len(vueltas_monitor) < 4:
    fallos.append("el monitor de alertas se murió con el primer error en vez de seguir intentando")
elif despues - antes != 1:
    fallos.append(f"el monitor de alertas falló {len(vueltas_monitor)} vueltas seguidas y dejó "
                   f"{despues - antes} avisos en el log: tiene que dejar UNO (antes no dejaba ninguno)")
elif SECRETO in texto:
    fallos.append("el log del servicio guardó el texto de la línea rota del config.ini (el token)")
else:
    print("OK: si las alertas no se pueden revisar, queda UN aviso en el log, sin el contenido del config.ini")

with open(ruta_config, "w", encoding="utf-8") as f:
    f.write(config_bueno)

# --------------------------------------------------------------------- #
# 8. Dos programas revisando alertas no mandan el mismo aviso dos veces
# --------------------------------------------------------------------- #
for n in range(5):
    products.crear_producto(codigo=f"A{n}", nombre=f"ALERTA {n}", precio_venta=100.0,
                            stock_inicial=1, usuario="test")
for codigo in ("111", "222", "333", "444"):
    alerts.set_umbral_producto(codigo, 0, 0)        # estos no avisan
alerts.set_umbral_global(5, 0)

enviados = []
estado = {"ok": True, "otro_programa": False}


class _Respuesta:
    def __init__(self, ok):
        self.ok = ok
        self.status_code = 200 if ok else 429


def _post_simulado(*a, **k):
    enviados.append(k.get("json", {}).get("text", ""))
    if estado["otro_programa"]:
        # Mientras este aviso está saliendo, el OTRO programa (el Panel
        # abierto, o el servicio) hace su propia vuelta entera.
        estado["otro_programa"] = False
        tb.revisar_umbrales_y_alertar()
    return _Respuesta(estado["ok"])


tb.requests = type("R", (), {"post": staticmethod(_post_simulado),
                              "RequestException": Exception})()


def marcas():
    return db.get_connection().execute("SELECT COUNT(*) c FROM Alertas_Enviadas").fetchone()["c"]


estado["otro_programa"] = True
tb.revisar_umbrales_y_alertar()
repetidos = len(enviados) - len(set(enviados))
if len(set(enviados)) != 5:
    fallos.append(f"con 5 productos bajo el mínimo salieron {len(set(enviados))} avisos distintos")
elif repetidos:
    fallos.append(f"con dos programas revisando a la vez, {repetidos} avisos llegaron repetidos")
else:
    print("OK: con el servicio y el Panel revisando a la vez, cada aviso llega una sola vez")

# Si el envío no sale, no queda anotado como enviado (se reintenta), y con
# Telegram caído no se insiste producto por producto.
with db.transaction() as conn:
    conn.execute("DELETE FROM Alertas_Enviadas")
enviados.clear()
estado["ok"] = False
tb.revisar_umbrales_y_alertar()
if marcas():
    fallos.append("un aviso que Telegram rechazó quedó anotado como enviado: no se reintenta por 4 horas")
elif len(enviados) != 3:
    fallos.append(f"con Telegram rechazando todo se intentaron {len(enviados)} envíos en una vuelta "
                   f"(tienen que ser 3: con el catálogo entero bajo el mínimo serían miles)")
else:
    print("OK: si Telegram no acepta el envío, no se anota como enviado y la vuelta se corta a los 3 intentos")
estado["ok"] = True
enviados.clear()
tb.revisar_umbrales_y_alertar()
if len(enviados) != 5 or marcas() != 5:
    fallos.append(f"cuando Telegram volvió salieron {len(enviados)} de los 5 avisos pendientes")

# Con el bot apagado no se manda ni se anota nada.
with db.transaction() as conn:
    conn.execute("DELETE FROM Alertas_Enviadas")
enviados.clear()
config.actualizar_config_dict({"telegram": {"habilitado": "false"}})
tb.revisar_umbrales_y_alertar()
if enviados or marcas():
    fallos.append("con el bot destildado igual se intentó mandar o se anotó un envío")
else:
    print("OK: con el bot destildado no sale ni se anota nada")

db.cerrar_conexion()
for h in list(demonio.log.handlers):
    h.close()
print()
if fallos:
    print("=== FALLOS SERVICIO DE STOCK ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== SERVICIO DE STOCK OK ===")
