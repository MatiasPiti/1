"""ApiCelular.exe autoprueba: el exe recién compilado se prueba a sí mismo.

Arma una base de prueba en una carpeta temporal NUEVA (nunca toca la real:
se niega si la carpeta ya tiene database\\stock.db), levanta el servidor de
verdad en 127.0.0.1 y le hace pedidos HTTP reales. Así fuerza todas las
importaciones tardías (uvicorn, python_multipart, pdfplumber/pdfminer,
anyio): un --hidden-import que falte se descubre en el build y no en el
local con el negocio esperando.

Es el ÚNICO módulo de la API que puede llamar preparar_base y crear_producto,
y solo sobre su carpeta temporal.
"""

import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request

PIN_DE_PRUEBA = "482915"


def pdf_de_texto(lineas: list, paginas: int = 1) -> bytes:
    """Un PDF de texto armado a mano (sin librerías): alcanza para el parser.
    Con paginas > 1 repite las líneas en cada página."""
    contenido = "BT /F1 10 Tf 40 800 Td 14 TL " + " ".join(
        "(" + l.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj T*" for l in lineas) + " ET"
    kids = " ".join(f"{3 + i} 0 R" for i in range(paginas))
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", f"<< /Type /Pages /Kids [{kids}] /Count {paginas} >>"]
    n_contenido, n_fuente = 3 + paginas, 4 + paginas
    for _ in range(paginas):
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {n_contenido} 0 R "
                    f"/Resources << /Font << /F1 {n_fuente} 0 R >> >> >>")
    objs.append(f"<< /Length {len(contenido.encode('latin-1'))} >>\nstream\n{contenido}\nendstream")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    salida, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(salida))
        salida += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(salida)
    salida += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    salida += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    salida += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return salida


def multipart(campo: str, nombre_archivo: str, contenido: bytes, tipo: str = "application/pdf") -> tuple:
    """(cuerpo, content-type) de un formulario con un solo archivo."""
    limite = "----otter" + secrets.token_hex(8)
    cuerpo = (f"--{limite}\r\nContent-Disposition: form-data; name=\"{campo}\"; filename=\"{nombre_archivo}\"\r\n"
              f"Content-Type: {tipo}\r\n\r\n").encode("utf-8") + contenido + f"\r\n--{limite}--\r\n".encode()
    return cuerpo, f"multipart/form-data; boundary={limite}"


class _Fallo(Exception):
    pass


def _pedir(base_url: str, metodo: str, ruta: str, *, cuerpo=None, token=None, tipo="application/json"):
    datos = None
    if cuerpo is not None:
        datos = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode("utf-8")
    pedido = urllib.request.Request(base_url + ruta, data=datos, method=metodo)
    if datos is not None:
        pedido.add_header("Content-Type", tipo)
    if token:
        pedido.add_header("Authorization", f"Bearer {token}")
    abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with abridor.open(pedido, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, None


def _esperar(cond: bool, que: str) -> None:
    if not cond:
        raise _Fallo(que)


def autoprueba(carpeta: str = None, conservar: bool = False) -> tuple:
    """(ok, texto). `carpeta` es para las pruebas: si ya tiene una base, se niega."""
    creada = carpeta is None
    carpeta = carpeta or tempfile.mkdtemp(prefix="otter_autoprueba_")
    if os.path.exists(os.path.join(carpeta, "database", "stock.db")):
        return False, f"la carpeta {carpeta} ya tiene una base: la autoprueba nunca trabaja sobre una base existente"

    from pos_core import paths
    # ANTES de cualquier logs_dir(): si no, quedaría un dist\logs en el build.
    paths.set_base_override(carpeta)
    servidor = None
    from services import api_celular
    try:
        from pos_core import acceso_celular, db, products
        db.usar_solo_base_existente(None)
        db.cerrar_conexion()
        db.preparar_base()
        db.cerrar_conexion()
        db.usar_solo_base_existente(os.path.join(carpeta, "database", "stock.db"))
        products.crear_producto(codigo="7790001000011", nombre="YERBA MATE PLAYADITO 1KG", precio_venta=3500,
                                stock_inicial=5, usuario="autoprueba")
        products.crear_producto(codigo="7790002000028", nombre="COCA COLA 2.25 LTS", precio_venta=2500,
                                stock_inicial=5, usuario="autoprueba")
        with open(os.path.join(carpeta, "config.ini"), "w", encoding="utf-8") as f:
            f.write("[general]\nnombre_local = Autoprueba\n\n[api_celular]\nhabilitado = true\n")
        acceso_celular.definir_pin_dueno(PIN_DE_PRUEBA)
        db.cerrar_conexion()
        api_celular.configurar_logs("servicio")

        estado = api_celular.EstadoApi()
        servidor = api_celular.iniciar_servidor(0, host="127.0.0.1", app=api_celular.crear_app(
            estado=estado, permitir_loopback_total=True))
        _esperar(servidor.esperar_inicio(30), "el servidor HTTP no arrancó")
        url = f"http://127.0.0.1:{servidor.sock.getsockname()[1]}"

        st, salud = _pedir(url, "GET", "/api/salud")
        _esperar(st == 200 and salud and salud.get("servicio") == api_celular.FIRMA, f"/api/salud dio {st}")
        _esperar(salud.get("contrato") == api_celular.CONTRATO, "/api/salud no trae el contrato esperado")
        _esperar(salud.get("base", {}).get("ok") is True, f"la API no ve la base ({salud.get('base')})")

        st, login = _pedir(url, "POST", "/api/auth/login", cuerpo={"pin": PIN_DE_PRUEBA})
        _esperar(st == 200 and login and login.get("token"), f"el login dio {st}")
        token = login["token"]

        st, lista = _pedir(url, "GET", "/api/productos", token=token)
        _esperar(st == 200 and isinstance(lista, list) and len(lista) == 2, f"/api/productos dio {st}")

        st, mov = _pedir(url, "POST", "/api/stock/movimiento", token=token,
                         cuerpo={"codigo": "7790001000011", "cantidad": 1, "operacion": "sumar", "motivo": None})
        _esperar(st == 200 and mov and mov.get("stock_nuevo") == 6, f"sumar stock dio {st} ({mov})")

        st, valores = _pedir(url, "POST", "/api/precios/recalcular", token=token,
                             cuerpo={"cambio": "precio_final", "costo_sin_iva": None, "precio_costo": "2.420",
                                     "margen": None, "precio_final": "3.800"})
        _esperar(st == 200 and valores and valores.get("margen") == 57.02, f"recalcular dio {st} ({valores})")

        pdf = pdf_de_texto(["FACTURA A 0001-00001234", "7790001000011  YERBA MATE PLAYADITO 1KG   x10   $3.500,00",
                            "COCA COLA 2.25 LTS   x6   $1250,00", "TOTAL 99999"])
        cuerpo, tipo = multipart("archivo", "autoprueba.pdf", pdf)
        st, factura = _pedir(url, "POST", "/api/facturas/analizar", token=token, cuerpo=cuerpo, tipo=tipo)
        _esperar(st == 200 and factura and len(factura.get("items", [])) >= 2, f"analizar factura dio {st}")
        _esperar(any(i["existe"] for i in factura["items"]), "la factura no reconoció el renglón con código")
        _esperar(any(i["emparejamiento"] for i in factura["items"]), "la factura no emparejó por nombre")

        inicio = time.perf_counter()
        hashlib.pbkdf2_hmac("sha256", b"x" * 32, b"y" * 16, acceso_celular.ITERACIONES, 32)
        ms = (time.perf_counter() - inicio) * 1000
        return True, f"pbkdf2 {ms:.0f} ms"
    except _Fallo as e:
        return False, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        if servidor is not None:
            servidor.detener()
        try:
            from pos_core import db
            db.cerrar_conexion()
            db.usar_solo_base_existente(None)
        except Exception:
            pass
        api_celular.cerrar_logs()
        if creada and not conservar:
            for _ in range(10):
                shutil.rmtree(carpeta, ignore_errors=True)
                if not os.path.exists(carpeta):
                    break
                time.sleep(0.5)


def main(argv: list = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    conservar = "--conservar" in argv
    ok, texto = autoprueba(conservar=conservar)
    if ok:
        print(f"AUTOPRUEBA OK ({texto})")
        return 0
    print(f"AUTOPRUEBA FALLÓ: {texto}")
    return 1
