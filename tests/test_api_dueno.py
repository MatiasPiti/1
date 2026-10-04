import time

from pos_core import sales
from pos_core.db import get_connection


# ------------------------------- sesión -------------------------------- #

def test_salud_no_requiere_login(app_cliente):
    r = app_cliente.get("/api/salud")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_endpoints_requieren_token(app_cliente):
    assert app_cliente.get("/api/dashboard").status_code == 401
    r = app_cliente.get("/api/dashboard", headers={"Authorization": "Bearer basura.firma"})
    assert r.status_code == 401


def test_login_solo_acepta_pin_de_dueno(app_cliente):
    assert app_cliente.post("/api/auth/login", json={"pin": "9999"}).status_code == 401  # PIN de cajero
    r = app_cliente.post("/api/auth/login", json={"pin": "1234"})
    assert r.status_code == 200
    assert r.json()["usuario"] == "dueño"


def test_login_bloquea_tras_intentos_fallidos(app_cliente):
    for _ in range(5):
        assert app_cliente.post("/api/auth/login", json={"pin": "0000"}).status_code == 401
    # bloqueado: ni siquiera el PIN correcto entra
    assert app_cliente.post("/api/auth/login", json={"pin": "1234"}).status_code == 429


def test_token_vencido_es_rechazado(base):
    from services.api_dueno import DURACION_TOKEN_SEGUNDOS, emitir_token, validar_token
    token = emitir_token("dueño")["token"]
    assert validar_token(token) == "dueño"
    assert validar_token(token, ahora=time.time() + DURACION_TOKEN_SEGUNDOS + 10) is None
    payload, firma = token.split(".")
    assert validar_token(payload + "." + firma[:-2] + "xx") is None


# ------------------------------ dashboard ------------------------------ #

def test_dashboard_refleja_ventas_del_dia(cliente):
    sales.cerrar_ticket(
        [{"codigo": "7790001", "nombre": "Yerba Mate 1kg", "cantidad": 2, "precio_unitario": 2500}],
        metodo_pago="EFECTIVO", usuario="cajero")
    sales.cerrar_ticket(
        [{"codigo": "7790003", "nombre": "Galletitas Oreo", "cantidad": 1, "precio_unitario": 980}],
        metodo_pago="TARJETA", usuario="cajero")

    d = cliente.get("/api/dashboard").json()
    assert d["hoy"]["tickets"] == 2
    assert d["hoy"]["total"] == 5980
    assert d["hoy"]["ticket_promedio"] == 2990
    assert {m["metodo_pago"] for m in d["hoy"]["por_metodo"]} == {"EFECTIVO", "TARJETA"}
    assert len(d["ultimos_7_dias"]) == 7
    assert d["ultimos_7_dias"][-1]["total"] == 5980
    assert d["top_productos"][0]["codigo"] == "7790001"
    assert d["alertas_activas"] == 1  # café con stock 3 <= mínimo global 5


def test_dashboard_periodo_invalido(cliente):
    assert cliente.get("/api/dashboard", params={"periodo_top": "siglo"}).status_code == 400


# ------------------------------ productos ------------------------------ #

def test_busqueda_por_codigo_y_nombre(cliente):
    r = cliente.get("/api/productos", params={"q": "café"}).json()
    assert [p["codigo"] for p in r] == ["7790002"]
    assert r[0]["stock"] == 3 and r[0]["stock_minimo"] == 5

    r = cliente.get("/api/productos", params={"q": "7790001"}).json()
    assert r[0]["nombre"] == "Yerba Mate 1kg"


def test_filtro_por_campo_como_en_la_pc(cliente):
    r = cliente.get("/api/productos", params={"campo": "marca", "valor": "playa"}).json()
    assert [p["codigo"] for p in r] == ["7790001"]
    assert cliente.get("/api/productos", params={"campo": "stock; DROP", "valor": "x"}).status_code == 400


def test_detalle_producto_y_404(cliente):
    r = cliente.get("/api/productos/7790001")
    assert r.status_code == 200
    assert r.json()["movimientos"] == []
    assert cliente.get("/api/productos/NOEXISTE").status_code == 404


# -------------------------------- stock -------------------------------- #

def test_movimiento_manual_sumar_y_restar(cliente):
    r = cliente.post("/api/stock/movimiento", json={"codigo": "7790001", "cantidad": 5, "operacion": "sumar"})
    assert r.status_code == 200 and r.json()["stock_nuevo"] == 25
    r = cliente.post("/api/stock/movimiento", json={"codigo": "7790001", "cantidad": 10, "operacion": "restar"})
    assert r.json()["stock_nuevo"] == 15

    movs = cliente.get("/api/movimientos").json()
    assert movs[0]["tipo"] == "SALIDA_MANUAL" and movs[0]["usuario"] == "dueño (app)"
    assert movs[1]["tipo"] == "ENTRADA_MANUAL"


def test_no_se_puede_dejar_stock_negativo(cliente):
    r = cliente.post("/api/stock/movimiento", json={"codigo": "7790002", "cantidad": 99, "operacion": "restar"})
    assert r.status_code == 409
    stock = get_connection().execute("SELECT stock FROM Productos WHERE codigo='7790002'").fetchone()[0]
    assert stock == 3


def test_lector_mueve_una_unidad(cliente):
    r = cliente.post("/api/stock/lector", json={"codigo": "7790003"})
    assert r.json() == {"codigo": "7790003", "nombre": "Galletitas Oreo", "stock_nuevo": 49,
                        "stock_minimo": 5, "stock_maximo": 0}
    r = cliente.post("/api/stock/lector", json={"codigo": "7790003", "operacion": "sumar"})
    assert r.json()["stock_nuevo"] == 50
    assert cliente.post("/api/stock/lector", json={"codigo": "000"}).status_code == 404


# ------------------------------- precios ------------------------------- #

def test_previsualizar_no_escribe_y_redondea_a_centena(cliente):
    r = cliente.post("/api/precios/previsualizar", json={"codigos": ["7790001"], "porcentaje": 3}).json()
    assert r[0]["precio_anterior"] == 2500 and r[0]["precio_nuevo"] == 2600
    assert cliente.get("/api/productos/7790001").json()["precio_venta"] == 2500


def test_aplicar_ajuste_masivo(cliente):
    r = cliente.post("/api/precios/aplicar",
                     json={"codigos": ["7790001", "7790003"], "monto_fijo": 100}).json()
    assert [x["precio_nuevo"] for x in r] == [2600, 1100]
    assert cliente.get("/api/productos/7790003").json()["precio_venta"] == 1100


def test_ajuste_requiere_un_solo_criterio(cliente):
    assert cliente.post("/api/precios/aplicar", json={"codigos": ["7790001"]}).status_code == 422
    assert cliente.post("/api/precios/aplicar",
                        json={"codigos": ["7790001"], "porcentaje": 1, "monto_fijo": 1}).status_code == 422


def test_precio_individual(cliente):
    r = cliente.put("/api/productos/7790002/precio", json={"precio_venta": 4450.5})
    assert r.json() == {"codigo": "7790002", "precio_anterior": 4300, "precio_nuevo": 4450.5}
    assert cliente.put("/api/productos/NOEXISTE/precio", json={"precio_venta": 1}).status_code == 404
    assert cliente.put("/api/productos/7790002/precio", json={"precio_venta": -1}).status_code == 422


# ------------------------------- alertas ------------------------------- #

def test_alertas_bajo_y_sobre_stock(cliente):
    cliente.put("/api/config/umbrales", json={"stock_minimo": 5, "stock_maximo": 40})
    r = cliente.get("/api/alertas").json()
    assert [(a["codigo"], a["tipo"]) for a in r] == [("7790002", "BAJO"), ("7790003", "SOBRE")]


def test_umbral_global_no_duplica_filas(cliente):
    for minimo in (7, 8, 9):
        assert cliente.put("/api/config/umbrales", json={"stock_minimo": minimo, "stock_maximo": 0}).status_code == 200
    n = get_connection().execute(
        "SELECT COUNT(*) FROM Configuracion_Alertas WHERE producto_codigo IS NULL").fetchone()[0]
    assert n == 1
    assert cliente.get("/api/config/alertas").json()["umbral_global"] == {"stock_minimo": 9, "stock_maximo": 0}


def test_alertas_toleran_globales_duplicados_viejos(cliente):
    # versiones anteriores del panel de la PC insertaban filas globales repetidas
    get_connection().execute(
        "INSERT INTO Configuracion_Alertas (producto_codigo, stock_minimo, stock_maximo) VALUES (NULL, 5, 0)")
    assert len(cliente.get("/api/alertas").json()) == 1


def test_config_telegram_no_expone_token(cliente):
    r = cliente.put("/api/config/telegram",
                    json={"habilitado": True, "chat_id_default": "123", "bot_token": "123456:ABCDEFGHIJ"}).json()
    assert r["telegram"]["token_configurado"] is True
    assert "ABCDEFGHIJ" not in str(r)
    # sin bot_token no se pisa el guardado
    r = cliente.put("/api/config/telegram", json={"habilitado": False, "chat_id_default": "456"}).json()
    assert r["telegram"]["token_configurado"] is True and r["telegram"]["habilitado"] is False


def test_probar_telegram_deshabilitado_no_envia(cliente):
    assert cliente.post("/api/config/telegram/probar").json() == {"enviado": False}


# ------------------------------- facturas ------------------------------ #

def test_analizar_factura_rechaza_no_pdf(cliente):
    r = cliente.post("/api/facturas/analizar", files={"archivo": ("x.pdf", b"hola", "application/pdf")})
    assert r.status_code == 415


def test_analizar_factura_marca_existentes_y_duplicados(cliente, monkeypatch):
    from pos_core import pdf_import
    resultado = pdf_import.ResultadoParsingPDF(
        items=[pdf_import.ItemFactura("7790001", "YERBA", 12, 1800.0),
               pdf_import.ItemFactura("7790001", "YERBA", 12, 1800.0),
               pdf_import.ItemFactura("NUEVO1", "PRODUCTO NUEVO", 3, 100.0)],
        lineas_no_reconocidas=["linea rara"])
    monkeypatch.setattr(pdf_import, "parsear_factura_pdf", lambda ruta: resultado)

    r = cliente.post("/api/facturas/analizar",
                     files={"archivo": ("remito.pdf", b"%PDF-1.4 ...", "application/pdf")}).json()
    assert r["factura_nombre"] == "remito.pdf"
    assert [i["posible_duplicado"] for i in r["items"]] == [False, True, False]
    assert r["items"][0]["existe"] and r["items"][0]["stock_actual"] == 20
    assert not r["items"][2]["existe"]
    assert r["lineas_no_reconocidas"] == ["linea rara"]


def test_aplicar_factura_suma_stock_y_reporta_fallos(cliente):
    r = cliente.post("/api/facturas/aplicar", json={
        "factura_nombre": "remito.pdf",
        "items": [{"codigo": "7790001", "cantidad": 12, "precio_compra": 1800},
                  {"codigo": "NUEVO1", "cantidad": 3}]}).json()
    assert r[0] == {"codigo": "7790001", "ok": True, "stock_nuevo": 32}
    assert r[1]["ok"] is False
    movs = cliente.get("/api/movimientos").json()
    assert movs[0]["tipo"] == "ENTRADA_PDF"
