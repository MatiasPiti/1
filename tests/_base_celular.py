"""Ayudante de las pruebas de la API del celular (no es una prueba: no empieza
con test_, así correr_todos.py no lo corre solo).

armar_base(tmp) arma una base de prueba con el código de main, con todos los
casos que las pruebas necesitan. Donde main no tiene función (desactivar un
producto, anular una venta, una fila de umbral inactiva) se hace con un
UPDATE/INSERT a mano, marcado "simula". Ningún valor tiene forma de secreto
real: el repo es público.
"""
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import warnings

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

warnings.simplefilter("ignore", DeprecationWarning)
# starlette 1.x avisa (con un UserWarning propio) que el TestClient sobre httpx
# está "deprecado": es solo ruido en la salida de las pruebas.
warnings.filterwarnings("ignore", message=r".*httpx.*")

PIN = "482915"
TOKEN_REMOTO = "TOKEN-REMOTO-DE-PRUEBA"
BOT_TOKEN = "123456:BOT-DE-PRUEBA"
CHAT_ID = "-987650"
NOMBRE_LOCAL = "El Galpón Del Nono (prueba)"
IP_CELULAR = "100.101.102.103"
IP_PC = "100.80.1.2"
FIXTURE = os.path.join(RAIZ, "apps", "movil_dueno", "test", "fixtures", "contrato_api_celular_v2.json")

# Códigos de la base de prueba
YERBA = "7790001"       # con costo 2000/2420/44,63/3500
CAFE = "7790002"        # oferta PORCENTAJE 10 %, umbral propio 5/0
COCA = "7790003"        # stock 0 y 3 ventas pendientes de descontar (6 unidades), oferta PRECIO_FIJO
ALFAJOR = "7790004"     # SIN costo y con margen 0 (si tuviera margen, recalcular inventaría un costo)
RAROS = "7790005"       # '_' y '%' en el nombre, venta anulada, fila de umbral propia INACTIVA
INACTIVO = "7790006"    # desactivado a mano
BOLSA = "7790007"       # precio 0 y stock 0
FIDEOS = "7790008"      # precio 3000 (3000 + 10 % = 3400 con el redondeo de main)
CHICLE = "7790009"      # umbral propio 0/20 (activo)

CONFIG_INI = f"""[general]
nombre_local = {NOMBRE_LOCAL}

[remoto]
habilitado = true
puerto = 8765
token = {TOKEN_REMOTO}

[telegram]
bot_token = {BOT_TOKEN}
chat_id_default = {CHAT_ID}
habilitado = true

[api_celular]
habilitado = true
puerto = 8766
"""


def armar_base(tmp: str) -> dict:
    from pos_core import paths
    paths.set_base_override(tmp)
    from pos_core import alerts, db, ofertas, precios, products, sales
    db.usar_solo_base_existente(None)
    db.cerrar_conexion()
    db.preparar_base()
    usuario = "prueba"
    alta = [
        (YERBA, "Yerba Mate 1kg", 3500, 50, "Marolio", "Distribuidora Sur", "ALMACEN"),
        (CAFE, "Café Molido 500g", 4300, 30, "Colombia", None, "ALMACEN"),
        (COCA, "Coca Cola 2,25 L", 2500, 0, "Coca-Cola", None, "BEBIDAS"),
        (ALFAJOR, "Alfajor Triple", 1000, 12, None, None, None),
        (RAROS, "Galletitas 50%_off", 900, 200, None, None, "ALMACEN"),
        (INACTIVO, "Producto dado de baja", 500, 3, None, None, None),
        (BOLSA, "Bolsa de regalo", 0, 0, None, None, None),
        (FIDEOS, "Fideos Guisero 500g", 3000, 40, None, None, "ALMACEN"),
        (CHICLE, "Chicle de menta", 300, 25, None, None, "KIOSCO"),
    ]
    for codigo, nombre, precio, stock, marca, proveedor, categoria in alta:
        products.crear_producto(codigo=codigo, nombre=nombre, precio_venta=precio, stock_inicial=stock,
                                marca=marca, proveedor=proveedor, categoria=categoria, usuario=usuario)
    precios.actualizar_precios(codigo=YERBA, categoria="ALMACEN", subrubro="INFUSIONES", costo_sin_iva=2000,
                               precio_costo=2420, margen=44.63, precio_final=3500)
    precios.actualizar_precios(codigo=CAFE, precio_costo=3000, precio_final=4300)
    with db.transaction() as conn:   # simula: main no tiene función para desactivar un producto
        conn.execute("UPDATE Productos SET activo = 0 WHERE codigo = ?", (INACTIVO,))

    # Ventas de hoy (descuentan bien) ...
    for _ in range(2):
        sales.cerrar_ticket([{"codigo": YERBA, "nombre": "Yerba Mate 1kg", "cantidad": 3, "precio_unitario": 3500},
                             {"codigo": CAFE, "nombre": "Café Molido 500g", "cantidad": 1, "precio_unitario": 3870}],
                            metodo_pago="EFECTIVO", usuario="El Galpón Del Nono")
    sales.cerrar_ticket([{"codigo": FIDEOS, "nombre": "Fideos Guisero 500g", "cantidad": 2, "precio_unitario": 3000}],
                        metodo_pago="TRANSFERENCIA", usuario="El Galpón Del Nono")
    # ... 3 de Coca con stock 0: la venta queda y el descuento queda PENDIENTE (D22) ...
    for _ in range(3):
        sales.cerrar_ticket([{"codigo": COCA, "nombre": "Coca Cola 2,25 L", "cantidad": 2, "precio_unitario": 2000}],
                            metodo_pago="EFECTIVO", usuario="El Galpón Del Nono")
    # ... y una grande que se anula (simula: main no anula ventas nunca).
    anulada = sales.cerrar_ticket([{"codigo": RAROS, "nombre": "Galletitas 50%_off", "cantidad": 100,
                                    "precio_unitario": 900}], metodo_pago="EFECTIVO", usuario="El Galpón Del Nono")
    with db.transaction() as conn:
        conn.execute("UPDATE Ventas SET anulada = 1 WHERE uuid_unico = ?", (anulada["venta_uuid"],))

    ofertas.crear_oferta(codigo=CAFE, tipo_descuento="PORCENTAJE", valor=10, descripcion="Semana del café",
                         dias=7, usuario=usuario)
    ofertas.crear_oferta(codigo=COCA, tipo_descuento="PRECIO_FIJO", valor=2000, descripcion="",
                         dias=3, usuario=usuario)

    alerts.set_umbral_global(20, 20)
    alerts.set_umbral_producto(CAFE, 5, 0)
    alerts.set_umbral_producto(CHICLE, 0, 20)
    with db.transaction() as conn:
        # simula: una fila propia inactiva (huella del ApiDueno viejo); el bot la ignora
        conn.execute("INSERT INTO Configuracion_Alertas (producto_codigo, stock_minimo, stock_maximo, activo) "
                     "VALUES (?, 1, 0, 0)", (RAROS,))
        # la fila de producción: sha256('1234') que dejó el setup viejo
        conn.execute("INSERT INTO Usuarios (nombre, pin_hash, rol, activo) VALUES ('dueño', ?, 'DUEÑO', 1)",
                     (hashlib.sha256(b"1234").hexdigest(),))
    with open(os.path.join(tmp, "config.ini"), "w", encoding="utf-8") as f:
        f.write(CONFIG_INI)
    db.cerrar_conexion()
    ruta = os.path.join(tmp, "database", "stock.db")
    db.usar_solo_base_existente(ruta)
    return {"tmp": tmp, "ruta_db": ruta}


def cliente(app, ip: str = IP_CELULAR, servidor: str = IP_PC, puerto: int = 8766):
    from fastapi.testclient import TestClient
    return TestClient(app, client=(ip, 50000), base_url=f"http://{servidor}:{puerto}")


def login(c, pin: str = PIN) -> str:
    r = c.post("/api/auth/login", json={"pin": pin})
    if r.status_code != 200:
        raise AssertionError(f"login dio {r.status_code}: {r.text}")
    return r.json()["token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class _RespuestaOk:
    ok = True
    status_code = 200

    def json(self):
        return {"ok": True}


def simular_telegram(enviados: list, error=None):
    """Telegram sin red, como test_umbral_global.py: requests.post reemplazado."""
    import requests
    from pos_core import telegram_bot as tb

    def _post(*a, **k):
        if error is not None:
            raise error
        enviados.append(k.get("json", {}).get("text", ""))
        return _RespuestaOk()

    tb.requests = type("R", (), {"post": staticmethod(_post), "RequestException": requests.RequestException})()


def volcado_base(ruta: str) -> str:
    """Huella del CONTENIDO de la base (no del archivo: con WAL un checkpoint
    cambia el archivo sin que nadie escriba nada)."""
    con = sqlite3.connect(ruta)
    try:
        return hashlib.sha256("\n".join(con.iterdump()).encode("utf-8")).hexdigest()
    finally:
        con.close()


def pdf_de_texto(lineas, paginas: int = 1) -> bytes:
    from services.api_celular_autoprueba import pdf_de_texto as _pdf
    return _pdf(lineas, paginas)


def subir_pdf(c, token: str, contenido: bytes, nombre: str = "remito.pdf"):
    return c.post("/api/facturas/analizar", headers=auth(token),
                  files={"archivo": (nombre, io.BytesIO(contenido), "application/pdf")})


def copiar_instalacion(origen: str, destino: str) -> None:
    """Copia base + secreto + config (para variantes de una misma instalación)."""
    os.makedirs(os.path.join(destino, "database"), exist_ok=True)
    con = sqlite3.connect(os.path.join(origen, "database", "stock.db"))
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    shutil.copyfile(os.path.join(origen, "database", "stock.db"), os.path.join(destino, "database", "stock.db"))
    if os.path.isdir(os.path.join(origen, "api_celular")):
        shutil.copytree(os.path.join(origen, "api_celular"), os.path.join(destino, "api_celular"),
                        dirs_exist_ok=True)
    shutil.copyfile(os.path.join(origen, "config.ini"), os.path.join(destino, "config.ini"))


def cargar_fixture() -> dict:
    with open(FIXTURE, "r", encoding="utf-8") as f:
        return json.load(f)
