import time

import pytest

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


# ------------------------- seguridad del login ------------------------- #

def _cliente_desde(app, ip):
    from fastapi.testclient import TestClient
    return TestClient(app, client=(ip, 50000))


def test_main_levanta_uvicorn_sin_proxy_headers(base, uvicorn_falso):
    from services import api_dueno
    assert api_dueno.main(["--base", str(base)]) == 0
    assert len(uvicorn_falso) == 1
    assert uvicorn_falso[0]["proxy_headers"] is False


def test_main_arranca_el_monitor_de_alertas_de_telegram(base, uvicorn_falso):
    # Ninguna app de escritorio lo arranca: si la API no lo hace, los avisos
    # automáticos de stock bajo nunca salen (y la app dice que están activos).
    from services import api_dueno
    assert api_dueno.main(["--base", str(base)]) == 0
    assert _MonitorFalso.arrancados == 1


def test_main_con_base_invalida_no_arranca_el_monitor(tmp_path, uvicorn_falso, carpeta_programa):
    from services import api_dueno
    assert api_dueno.main(["--base", str(tmp_path / "no_existe")]) == 2
    assert _MonitorFalso.arrancados == 0


def test_x_forwarded_for_no_evita_el_bloqueo(base, uvicorn_falso):
    # La app tal cual la arma uvicorn con lo que le pasa main(), conectando
    # desde 127.0.0.1 (el caso en que uvicorn le creería a X-Forwarded-For).
    import uvicorn
    from fastapi.testclient import TestClient
    from services import api_dueno
    assert api_dueno.main(["--base", str(base)]) == 0
    kw = dict(uvicorn_falso[0])
    app = kw.pop("app")
    cfg = uvicorn.Config(app, **{**kw, "log_config": None})
    cfg.load()
    c = TestClient(cfg.loaded_app, client=("127.0.0.1", 50000))
    # Cada intento dice venir de otra IP: igual cuenta la IP de la conexión.
    for i in range(5):
        r = c.post("/api/auth/login", json={"pin": "0000"}, headers={"X-Forwarded-For": f"10.0.0.{i}"})
        assert r.status_code == 401
    r = c.post("/api/auth/login", json={"pin": "1234"}, headers={"X-Forwarded-For": "10.9.9.9"})
    assert r.status_code == 429


def test_tope_global_de_fallos_bloquea_todas_las_ips(base):
    from services.api_dueno import create_app
    app = create_app()
    # 4 fallos por IP (no llega al bloqueo por IP) desde 5 IPs = 20 fallos
    for n in range(5):
        c = _cliente_desde(app, f"100.64.0.{n}")
        for _ in range(4):
            assert c.post("/api/auth/login", json={"pin": "0000"}).status_code == 401
    # 20 todavía no es "más de 20": el dueño entra
    assert _cliente_desde(app, "100.64.1.1").post("/api/auth/login", json={"pin": "1234"}).status_code == 200
    # el fallo 21 bloquea a todos, aunque vengan de una IP nueva
    assert _cliente_desde(app, "100.64.0.50").post("/api/auth/login", json={"pin": "0000"}).status_code == 401
    r = _cliente_desde(app, "100.64.1.2").post("/api/auth/login", json={"pin": "1234"})
    assert r.status_code == 429


def test_login_concurrente_no_supera_el_limite_de_intentos(app_cliente, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from pos_core import usuarios

    def verificar_lento(pin, **kw):
        time.sleep(0.05)   # agranda la ventana entre el chequeo y el registro
        return None
    monkeypatch.setattr(usuarios, "verificar_pin", verificar_lento)

    largada = threading.Barrier(15)

    def intentar(_):
        largada.wait()
        return app_cliente.post("/api/auth/login", json={"pin": "0000"}).status_code

    with ThreadPoolExecutor(max_workers=15) as ex:
        estados = list(ex.map(intentar, range(15)))
    assert estados.count(401) == 5
    assert estados.count(429) == 10


# ----------------------- secreto de los tokens ------------------------ #

def test_secreto_se_lee_una_vez_y_queda_en_memoria(base, monkeypatch):
    from pos_core import config
    from services import api_dueno
    secreto = api_dueno._secreto()
    assert "secreto" in (base / "config.ini").read_text(encoding="utf-8")

    lecturas = []
    original = config.cargar_config
    monkeypatch.setattr(config, "cargar_config", lambda **kw: lecturas.append(kw) or original(**kw))
    # aunque config.ini quede ilegible (otro proceso escribiéndolo), no se relee
    (base / "config.ini").write_text("[api\nsecr", encoding="utf-8")
    for _ in range(3):
        assert api_dueno._secreto() == secreto
    assert lecturas == []
    assert (base / "config.ini").read_text(encoding="utf-8") == "[api\nsecr"


def test_config_ini_ilegible_da_500_y_no_regenera_el_secreto(base):
    from fastapi.testclient import TestClient
    from services.api_dueno import create_app
    roto = "[telegram]\nbot_token = 123:ABC\n[api\nsecreto = a1b2"
    (base / "config.ini").write_text(roto, encoding="utf-8")
    c = TestClient(create_app())
    r = c.post("/api/auth/login", json={"pin": "1234"})
    assert r.status_code == 500
    assert "config.ini" in r.json()["detail"]
    assert (base / "config.ini").read_text(encoding="utf-8") == roto


def test_secreto_existente_se_respeta(base):
    from services import api_dueno
    (base / "config.ini").write_text("[api]\nsecreto = abc123\n", encoding="utf-8")
    assert api_dueno._secreto() == b"abc123"
    assert (base / "config.ini").read_text(encoding="utf-8") == "[api]\nsecreto = abc123\n"


# --------------------- el token sigue al usuario ---------------------- #

def test_token_se_invalida_al_cambiar_el_pin(cliente):
    from pos_core import usuarios
    assert cliente.get("/api/auth/yo").status_code == 200
    usuarios.definir_pin_dueno("5678")
    assert cliente.get("/api/auth/yo").status_code == 401
    r = cliente.post("/api/auth/login", json={"pin": "5678"})
    assert r.status_code == 200
    r = cliente.get("/api/auth/yo", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert r.status_code == 200


def test_token_se_invalida_si_el_dueno_se_desactiva_o_pierde_el_rol(cliente):
    conn = get_connection()
    conn.execute("UPDATE Usuarios SET activo = 0 WHERE nombre = 'dueño'")
    assert cliente.get("/api/dashboard").status_code == 401
    conn.execute("UPDATE Usuarios SET activo = 1, rol = 'CAJERO' WHERE nombre = 'dueño'")
    assert cliente.get("/api/dashboard").status_code == 401


def test_token_sin_huella_del_pin_es_rechazado(base):
    # Un token firmado bien pero sin la huella (formato viejo) ya no vale.
    import base64, hashlib, hmac, json
    from services import api_dueno
    payload = base64.urlsafe_b64encode(
        json.dumps({"u": "dueño", "exp": int(time.time()) + 60}).encode()).rstrip(b"=").decode()
    firma = base64.urlsafe_b64encode(
        hmac.new(api_dueno._secreto(), payload.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    assert api_dueno.validar_token(f"{payload}.{firma}") is None


# --------------------- tamaño de los pedidos -------------------------- #

def _asgi(app, path, *, headers=(), trozos=()):
    """Llama a la app ASGI a mano, para ver cuántos bytes del cuerpo llegó
    a leer. Devuelve (status, bytes_leidos, cuerpo_respuesta)."""
    import asyncio
    pendientes = list(trozos)
    leidos = []
    enviados = []

    async def receive():
        if pendientes:
            trozo = pendientes.pop(0)
            leidos.append(len(trozo))
            return {"type": "http.request", "body": trozo, "more_body": bool(pendientes)}
        return {"type": "http.disconnect"}

    async def send(mensaje):
        enviados.append(mensaje)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "client": ("100.64.0.9", 1234), "server": ("testserver", 80),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
    }
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in enviados if m["type"] == "http.response.start")
    cuerpo = b"".join(m.get("body", b"") for m in enviados if m["type"] == "http.response.body")
    return status, sum(leidos), cuerpo


def test_json_de_mas_de_1_mb_da_413(cliente):
    codigos = ["7790001" + "x" * 100] * 10000   # ~1,1 MB
    r = cliente.post("/api/precios/previsualizar", json={"codigos": codigos, "porcentaje": 1})
    assert r.status_code == 413


def test_cuerpo_chunked_sin_content_length_tambien_se_corta(base):
    from services.api_dueno import MAX_CUERPO_BYTES, create_app
    trozo = b" " * 65536
    status, leidos, _ = _asgi(create_app(), "/api/precios/previsualizar",
                              headers=[("content-type", "application/json")], trozos=[trozo] * 40)
    assert status == 413
    assert leidos <= MAX_CUERPO_BYTES + len(trozo)   # dejó de leer apenas pasó el límite


def test_content_length_declarado_grande_se_rechaza_sin_leer(base):
    from services.api_dueno import create_app
    status, leidos, cuerpo = _asgi(create_app(), "/api/facturas/analizar",
                                   headers=[("content-length", str(50 * 1024 * 1024)),
                                            ("content-type", "multipart/form-data; boundary=x")],
                                   trozos=[b"x" * 1024])
    assert status == 413 and leidos == 0
    assert b"20 MB" in cuerpo


def test_pdf_de_mas_de_20_mb_da_413(cliente):
    # 20,5 MB: pasa el tope del cuerpo (21 MB) y lo frena el chequeo del PDF
    grande = b"%PDF-1.4 " + b"0" * (20 * 1024 * 1024 + 512 * 1024)
    r = cliente.post("/api/facturas/analizar", files={"archivo": ("grande.pdf", grande, "application/pdf")})
    assert r.status_code == 413
    assert r.json()["detail"] == "El PDF supera los 20 MB"


def test_pdf_chunked_que_supera_el_limite_se_corta_al_leer_el_formulario(cliente, monkeypatch):
    # Límite chico para no mandar 21 MB: el corte pasa adentro de request.form()
    from services import api_dueno
    monkeypatch.setattr(api_dueno, "MAX_CUERPO_FACTURA_BYTES", 200 * 1024)
    app = api_dueno.create_app()
    cuerpo = (b"--lim\r\nContent-Disposition: form-data; name=\"archivo\"; filename=\"a.pdf\"\r\n"
              b"Content-Type: application/pdf\r\n\r\n%PDF-1.4 " + b"0" * (400 * 1024) + b"\r\n--lim--\r\n")
    trozos = [cuerpo[i:i + 32768] for i in range(0, len(cuerpo), 32768)]
    status, leidos, _ = _asgi(app, "/api/facturas/analizar", trozos=trozos, headers=[
        ("authorization", cliente.headers["Authorization"]),
        ("content-type", "multipart/form-data; boundary=lim")])
    assert status == 413
    assert leidos <= 200 * 1024 + 32768


def test_analizar_sin_token_da_401_sin_leer_el_cuerpo(base):
    from services.api_dueno import create_app
    trozos = [b"x" * 65536] * 80   # 5 MB, por debajo del límite de 21 MB
    status, leidos, _ = _asgi(create_app(), "/api/facturas/analizar", trozos=trozos, headers=[
        ("content-length", str(65536 * 80)), ("content-type", "multipart/form-data; boundary=x")])
    assert status == 401
    assert leidos == 0


def test_analizar_sin_archivo_da_422(cliente):
    r = cliente.post("/api/facturas/analizar", data={"otro": "campo"})
    assert r.status_code == 422


def test_pdf_danado_da_422_y_borra_el_temporal(cliente, monkeypatch):
    import os
    import tempfile
    creados = []
    mkstemp_real = tempfile.mkstemp

    def espia(*a, **kw):
        fd, ruta = mkstemp_real(*a, **kw)
        creados.append(ruta)
        return fd, ruta
    monkeypatch.setattr(tempfile, "mkstemp", espia)

    r = cliente.post("/api/facturas/analizar",
                     files={"archivo": ("roto.pdf", b"%PDF-1.4\n1 0 obj << /Type", "application/pdf")})
    assert r.status_code == 422
    assert r.json()["detail"] == "El PDF está dañado o incompleto"
    assert creados and not any(os.path.exists(p) for p in creados)


# ------------------ montos absurdos en los modelos -------------------- #

def _post_crudo(cliente, metodo, url, texto_json):
    return cliente.request(metodo, url, content=texto_json, headers={"Content-Type": "application/json"})


def test_precio_infinity_o_nan_se_rechaza(cliente):
    for valor in ("Infinity", "-Infinity", "NaN", "1e13"):
        r = _post_crudo(cliente, "PUT", "/api/productos/7790001/precio", f'{{"precio_venta": {valor}}}')
        assert r.status_code == 422, (valor, r.text)
    assert cliente.get("/api/productos/7790001").json()["precio_venta"] == 2500


def test_ajuste_masivo_infinity_o_nan_se_rechaza(cliente):
    for cuerpo in ('{"codigos": ["7790001"], "porcentaje": NaN}',
                   '{"codigos": ["7790001"], "porcentaje": Infinity}',
                   '{"codigos": ["7790001"], "monto_fijo": Infinity}',
                   '{"codigos": ["7790001"], "monto_fijo": -Infinity}',
                   '{"codigos": ["7790001"], "monto_fijo": 2e12}'):
        for url in ("/api/precios/previsualizar", "/api/precios/aplicar"):
            r = _post_crudo(cliente, "POST", url, cuerpo)
            assert r.status_code == 422, (url, cuerpo, r.text)
    assert cliente.get("/api/productos/7790001").json()["precio_venta"] == 2500


def test_factura_con_precio_compra_infinity_se_rechaza(cliente):
    r = _post_crudo(cliente, "POST", "/api/facturas/aplicar",
                    '{"factura_nombre": "r.pdf", "items": [{"codigo": "7790001", "cantidad": 1, '
                    '"precio_compra": Infinity}]}')
    assert r.status_code == 422
    assert cliente.get("/api/productos/7790001").json()["stock"] == 20


# ------------------------ arranque de la API -------------------------- #

class _MonitorFalso:
    arrancados = 0

    def start(self):
        _MonitorFalso.arrancados += 1


@pytest.fixture
def uvicorn_falso(monkeypatch):
    """Reemplaza uvicorn.run: anota con qué se lo llamó (y la app) sin
    levantar el servidor. También reemplaza el monitor de alertas de
    Telegram, para no dejar un hilo real corriendo entre tests."""
    import uvicorn
    from pos_core import telegram_bot
    llamadas = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: llamadas.append({**kw, "app": app}))
    _MonitorFalso.arrancados = 0
    monkeypatch.setattr(telegram_bot, "MonitorAlertas", _MonitorFalso)
    return llamadas


@pytest.fixture
def carpeta_programa(tmp_path, monkeypatch):
    """Carpeta de ApiDueno.exe: donde va el log si la base ni existe."""
    from services import api_dueno
    carpeta = tmp_path / "ApiDueno"
    carpeta.mkdir()
    monkeypatch.setattr(api_dueno, "_carpeta_programa", lambda: str(carpeta))
    return carpeta


def test_main_con_base_inexistente_sale_con_2(tmp_path, monkeypatch, uvicorn_falso, carpeta_programa):
    from services import api_dueno
    monkeypatch.setenv("SISTEMA_DUAL_BASE", str(tmp_path))   # para que monkeypatch la restaure
    inexistente = tmp_path / "MaestroDuen"   # mal escrita
    assert api_dueno.main(["--base", str(inexistente)]) == 2
    assert uvicorn_falso == []
    assert not inexistente.exists()   # no se crea una base vacía de la nada
    log = (carpeta_programa / "logs" / "api_dueno.log").read_text(encoding="utf-8")
    assert "no existe la base de datos" in log


def test_main_con_carpeta_sin_database_sale_con_2(tmp_path, monkeypatch, uvicorn_falso):
    from services import api_dueno
    monkeypatch.setenv("SISTEMA_DUAL_BASE", str(tmp_path))
    base = tmp_path / "MaestroDueno"
    base.mkdir()
    assert api_dueno.main(["--base", str(base)]) == 2
    assert uvicorn_falso == []
    assert not (base / "database").exists()
    assert "no existe la base de datos" in (base / "logs" / "api_dueno.log").read_text(encoding="utf-8")


def test_main_con_base_sin_dueno_activo_sale_con_2(tmp_path, monkeypatch, uvicorn_falso):
    from pos_core.db import init_db, transaction
    from pos_core.usuarios import hash_pin
    from services import api_dueno
    base = tmp_path / "MaestroDueno"
    monkeypatch.setenv("SISTEMA_DUAL_BASE", str(base))
    init_db()
    with transaction() as conn:
        conn.execute("INSERT INTO Usuarios (nombre, pin_hash, rol) VALUES ('cajero', ?, 'CAJERO')",
                     (hash_pin("9999"),))
        conn.execute("INSERT INTO Usuarios (nombre, pin_hash, rol, activo) VALUES ('dueño', ?, 'DUEÑO', 0)",
                     (hash_pin("1234"),))
    assert api_dueno.main(["--base", str(base)]) == 2
    assert uvicorn_falso == []
    log = (base / "logs" / "api_dueno.log").read_text(encoding="utf-8")
    assert "DUEÑO activo" in log and "--definir-pin" in log


def test_definir_pin_crea_y_actualiza_el_dueno_sin_levantar_el_servidor(tmp_path, monkeypatch, uvicorn_falso):
    from pos_core import usuarios
    from services import api_dueno
    monkeypatch.setenv("SISTEMA_DUAL_BASE", str(tmp_path))
    base = tmp_path / "MaestroDueno"
    base.mkdir()

    assert api_dueno.main(["--base", str(base), "--definir-pin", "4321"]) == 0
    assert usuarios.verificar_pin("4321", rol="DUEÑO") == "dueño"
    assert api_dueno.main(["--base", str(base), "--definir-pin", "87654321"]) == 0
    assert usuarios.verificar_pin("4321", rol="DUEÑO") is None
    assert usuarios.verificar_pin("87654321", rol="DUEÑO") == "dueño"
    assert uvicorn_falso == []

    log = (base / "logs" / "api_dueno.log").read_text(encoding="utf-8")
    assert "creado" in log and "actualizado" in log
    # el PIN nunca queda escrito (sin la ruta, que puede traer dígitos: pytest-4321)
    sin_rutas = log.replace(str(base), "")
    assert "4321" not in sin_rutas   # tampoco el 87654321, que lo contiene

    # ahora sí arranca
    assert api_dueno.main(["--base", str(base)]) == 0
    assert len(uvicorn_falso) == 1


def test_definir_pin_invalido_o_base_inexistente_sale_con_2(tmp_path, monkeypatch, uvicorn_falso,
                                                            carpeta_programa):
    from services import api_dueno
    monkeypatch.setenv("SISTEMA_DUAL_BASE", str(tmp_path))
    base = tmp_path / "MaestroDueno"
    base.mkdir()
    for pin in ("12", "12ab", "1234567890123"):
        assert api_dueno.main(["--base", str(base), "--definir-pin", pin]) == 2
    assert not (base / "database").exists()
    assert api_dueno.main(["--base", str(tmp_path / "NoExiste"), "--definir-pin", "1234"]) == 2
    assert not (tmp_path / "NoExiste").exists()
    assert uvicorn_falso == []
