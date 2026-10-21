"""Dar de alta productos y armar filtros desde el celular, por los bordes.

test_api_celular_contrato.py mira la forma de cada respuesta y el camino
feliz. Esta prueba busca lo que se rompe en la práctica:

- el código "1" es el de la Caja para los artículos sin código de barras
  (nunca descuenta stock): no se puede dar de alta un producto con él;
- un código con espacios, un precio mandado como texto ("1.500" se leería
  1,5) o un stock con coma no entran;
- dos altas del mismo código a la vez (el reintento de un "incierto" que
  sale junto con el original) dejan UN producto y UN movimiento;
- las marcas sugeridas no tratan '%' ni '_' como comodines, salen sin
  espacios de más y no incluyen productos dados de baja;
- un filtro con un nombre con '/', '%' o espacios se abre, se cambia y se
  borra por su nombre exacto;
- un código que no existe no se guarda en silencio en un filtro, y un
  filtro guardado en el Panel con el JSON dañado o con condiciones viejas
  no tumba la lista;
- un filtro de 5000 productos (lo máximo de un ajuste masivo) se guarda y
  se abre entero, y uno más grande armado en el Panel se abre recortado y
  avisando;
- cada alta y cada cambio de filtro queda en api_celular_cambios.log.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import _base_celular as B  # noqa: E402  (agrega la raíz del repo al path)

from pos_core import acceso_celular, db, filters, products  # noqa: E402

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


RAIZ_TMP = tempfile.mkdtemp(prefix="api_celular_alta_filtros_")
TMP = os.path.join(RAIZ_TMP, "instalacion")
os.makedirs(TMP)
B.armar_base(TMP)
RUTA_DB = os.path.join(TMP, "database", "stock.db")
acceso_celular.definir_pin_dueno(B.PIN)
db.cerrar_conexion()
B.simular_telegram([])

from services import api_celular  # noqa: E402

api_celular.configurar_logs("servicio")


def fila(sql, params=()):
    con = sqlite3.connect(RUTA_DB)
    con.row_factory = sqlite3.Row
    try:
        r = con.execute(sql, params).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


def contar(sql, params=()):
    con = sqlite3.connect(RUTA_DB)
    try:
        return con.execute(sql, params).fetchone()[0]
    finally:
        con.close()


def a_mano(sql, params=()):
    """simula: lo que haría el Panel, un Excel o una base vieja (fuera de la API)."""
    with db.transaction() as conn:
        conn.execute(sql, params)
    db.cerrar_conexion()


app = api_celular.crear_app()
with B.cliente(app) as c:
    app.state.estado.refrescar()
    H = B.auth(B.login(c))

    def alta(**datos):
        cuerpo = {"codigo": "7790500", "nombre": "Producto de prueba", "precio_venta": 1000}
        cuerpo.update(datos)
        return c.post("/api/productos", json={k: v for k, v in cuerpo.items() if v is not ...}, headers=H)

    # ===================================================================== #
    # Alta: lo que no puede entrar
    # ===================================================================== #
    productos_antes = contar("SELECT COUNT(*) FROM Productos")
    movimientos_antes = contar("SELECT COUNT(*) FROM Movimientos_Stock")

    for codigo in ("1", " 1 "):
        r = alta(codigo=codigo, stock_inicial=5)
        verificar(r.status_code == 400 and r.json().get("codigo") == "dato_invalido"
                  and "sin código de barras" in r.json().get("detail", ""),
                  f"el código {codigo!r} (el de la Caja para los artículos sin código) da 400 y lo explica",
                  f"código {codigo!r}: {r.status_code} {r.text}")
    verificar(contar("SELECT COUNT(*) FROM Productos WHERE codigo = '1'") == 0,
              "y no queda ningún producto con el código 1", "se creó un producto con el código 1")

    malos = [
        ("un código con un espacio adentro", dict(codigo="7790 500"), "codigo", "no puede tener espacios"),
        ("un código con un tab adentro", dict(codigo="7790\t500"), "codigo", "no puede tener espacios"),
        ("un código de 65 caracteres", dict(codigo="7" * 65), "codigo", "largo inválido"),
        ("un código vacío", dict(codigo="   "), "codigo", "largo inválido"),
        ("un nombre vacío", dict(nombre="   "), "nombre", "largo inválido"),
        ("un nombre de 121 letras", dict(nombre="x" * 121), "nombre", "largo inválido"),
        ("sin precio", dict(precio_venta=...), "precio_venta", "falta"),
        ("un precio negativo", dict(precio_venta=-1), "precio_venta", "mayor o igual a 0"),
        ("un precio como texto ('1.500' se leería 1,5)", dict(precio_venta="1.500"), "precio_venta",
         "tiene que ser un número"),
        ("un precio como texto sin puntos", dict(precio_venta="1500"), "precio_venta", "tiene que ser un número"),
        ("un precio verdadero/falso", dict(precio_venta=True), "precio_venta", "tiene que ser un número"),
        ("un precio de mil millones y uno", dict(precio_venta=1e9 + 1), "precio_venta", "menor o igual"),
        ("un stock negativo", dict(stock_inicial=-1), "stock_inicial", "mayor o igual a 0"),
        ("un stock con coma", dict(stock_inicial=2.5), "stock_inicial", "número entero"),
        ("un stock como texto", dict(stock_inicial="3"), "stock_inicial", "número entero"),
        ("un stock de más de 100.000", dict(stock_inicial=100001), "stock_inicial", "menor o igual a 100000"),
        ("una marca de 81 letras", dict(marca="m" * 81), "marca", "largo inválido"),
        ("un proveedor que no es texto", dict(proveedor=5), "proveedor", "valor inválido"),
    ]
    for que, datos, campo, mensaje in malos:
        r = alta(**datos)
        cuerpo = r.json()
        verificar(r.status_code == 422 and cuerpo.get("codigo") == "dato_invalido" and cuerpo.get("campo") == campo
                  and mensaje in cuerpo.get("detail", ""),
                  f"{que}: 422 en «{campo}» ({mensaje})", f"{que}: {r.status_code} {r.text}")
    verificar(contar("SELECT COUNT(*) FROM Productos") == productos_antes
              and contar("SELECT COUNT(*) FROM Movimientos_Stock") == movimientos_antes,
              "ninguno de los rechazados dejó un producto o un movimiento", "un alta rechazada escribió algo")
    r = c.post("/api/productos", json={"codigo": "7790501", "nombre": "Sin sesión", "precio_venta": 1})
    verificar(r.status_code == 401 and contar("SELECT COUNT(*) FROM Productos WHERE codigo = '7790501'") == 0,
              "sin sesión, 401 y no se crea nada", f"sin sesión: {r.status_code}")

    # ===================================================================== #
    # Alta: lo que sí entra, tal cual
    # ===================================================================== #
    r = alta(codigo="0001234", nombre="Fernet 750 ml", precio_venta=9800.5, stock_inicial=6,
             marca="Branca", proveedor=None, categoria="BEBIDAS")
    p = fila("SELECT * FROM Productos WHERE codigo = '0001234'")
    verificar(r.status_code == 200 and r.json().get("codigo") == "0001234" and p and p["precio_venta"] == 9800.5,
              "un código con ceros adelante se guarda como texto, tal cual (no como el número 1234)",
              f"0001234: {r.status_code} {r.text[:200]}")
    m = fila("SELECT * FROM Movimientos_Stock WHERE producto_codigo = '0001234'")
    verificar(contar("SELECT COUNT(*) FROM Movimientos_Stock WHERE producto_codigo = '0001234'") == 1
              and m["cantidad"] == 6 and m["stock_resultante"] == p["stock"] == 6 and m["sincronizado"] == 1
              and m["origen"] == "MAESTRO",
              "el stock del producto es el del movimiento de alta (origen MAESTRO, ya sincronizado: nace en la PC)",
              f"movimiento: {m}, stock {p and p['stock']}")
    r = c.get("/api/productos?q=fernet", headers=H)
    verificar([x["codigo"] for x in r.json()] == ["0001234"], "el producto nuevo aparece en la búsqueda de la app",
              f"búsqueda: {r.text[:200]}")
    r = alta(codigo="ÑANDÚ-7", nombre="Código con letras", precio_venta=10)
    verificar(r.status_code == 200 and fila("SELECT 1 x FROM Productos WHERE codigo = 'ÑANDÚ-7'"),
              "un código con letras, eñes o guiones (sin espacios) se acepta como en el Panel",
              f"código con letras: {r.status_code} {r.text}")
    r = c.get("/api/productos/%C3%91AND%C3%9A-7", headers=H)
    verificar(r.status_code == 200 and r.json().get("nombre") == "Código con letras",
              "y se abre por su código desde la app (con la URL codificada)", f"abrir: {r.status_code}")

    # Dos altas del mismo código a la vez: el original y el reintento de un
    # "incierto" que sale antes de que llegue la respuesta.
    resultados = []
    largada = threading.Barrier(2)

    def alta_en_paralelo():
        with B.cliente(app) as otro:
            largada.wait()
            resultados.append(otro.post("/api/productos", headers=H, json={
                "codigo": "7790600", "nombre": "Doble", "precio_venta": 500, "stock_inicial": 10}).status_code)

    hilos = [threading.Thread(target=alta_en_paralelo) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(60)
    verificar(sorted(resultados) == [200, 409] and contar("SELECT COUNT(*) FROM Productos WHERE codigo = '7790600'") == 1
              and contar("SELECT COUNT(*) FROM Movimientos_Stock WHERE producto_codigo = '7790600'") == 1
              and fila("SELECT stock FROM Productos WHERE codigo = '7790600'")["stock"] == 10,
              "dos altas del mismo código a la vez: una entra, la otra da 409, un solo producto y un solo movimiento",
              f"en paralelo: {resultados}")

    # ===================================================================== #
    # Valores sugeridos (marca, proveedor, rubro)
    # ===================================================================== #
    for codigo, marca in (("7790701", "A_B"), ("7790702", "AxB"), ("7790703", "100%"), ("7790704", "1000")):
        products.crear_producto(codigo=codigo, nombre=f"Marca {marca}", precio_venta=1, marca=marca, usuario="prueba")
    products.crear_producto(codigo="7790705", nombre="Dado de baja", precio_venta=1, marca="SoloBaja",
                            usuario="prueba")
    a_mano("UPDATE Productos SET activo = 0 WHERE codigo = '7790705'")
    a_mano("UPDATE Productos SET marca = '  Marolio ' WHERE codigo = ?", (B.FIDEOS,))   # como la deja un Excel
    db.cerrar_conexion()

    def valores(query):
        r = c.get(f"/api/catalogo/valores?{query}", headers=H)
        return r.json() if r.status_code == 200 else (r.status_code, r.text)

    verificar(valores("campo=marca&q=_") == ["A_B"], "buscar '_' encuentra solo esa marca (no es un comodín)",
              f"'_': {valores('campo=marca&q=_')}")
    verificar(valores("campo=marca&q=%25") == ["100%"], "buscar '%' encuentra solo esa marca (no es un comodín)",
              f"'%': {valores('campo=marca&q=%25')}")
    verificar(valores("campo=marca&q=solobaja") == [], "la marca de un producto dado de baja no se sugiere",
              f"de baja: {valores('campo=marca&q=solobaja')}")
    verificar(valores("campo=marca&q=marolio") == ["Marolio"],
              "' Marolio ' (con espacios, de un Excel) y 'Marolio' se sugieren una sola vez y sin espacios",
              f"marolio: {valores('campo=marca&q=marolio')}")
    verificar(valores("campo=categoria&limite=1") == ["ALMACEN"], "el límite se respeta y va la más usada primero",
              f"límite: {valores('campo=categoria&limite=1')}")
    verificar(valores("campo=proveedor") == ["Distribuidora Sur"], "proveedores: los que ya se usan",
              f"proveedores: {valores('campo=proveedor')}")
    for query, status in (("campo=codigo", 400), ("campo=marca&limite=0", 422), ("campo=marca&limite=101", 422),
                          ("q=a", 422), ("campo=marca&q=" + "x" * 81, 422)):
        r = c.get(f"/api/catalogo/valores?{query}", headers=H)
        verificar(r.status_code == status and r.json().get("codigo") == "dato_invalido",
                  f"/api/catalogo/valores?{query[:30]} da {status}", f"{query[:30]}: {r.status_code} {r.text}")

    # ===================================================================== #
    # Filtros: lo que no puede entrar
    # ===================================================================== #
    def crear_filtro(nombre, codigos):
        return c.post("/api/filtros", headers=H, json={"nombre": nombre, "codigos": codigos})

    filtros_antes = contar("SELECT COUNT(*) FROM Filtros_Guardados")
    for que, nombre, codigos, status, texto in (
            ("sin productos", "Vacío", [], 422, "largo inválido"),
            ("con un código que no existe", "Con fantasma", [B.CAFE, "9999999"], 400, "9999999"),
            ("con un producto dado de baja", "Con baja", [B.INACTIVO], 400, B.INACTIVO),
            ("sin nombre", "   ", [B.CAFE], 422, "largo inválido"),
            ("con un nombre de 81 letras", "n" * 81, [B.CAFE], 422, "largo inválido"),
            ("con un caracter de control en el nombre", "Bebi\x07das", [B.CAFE], 400, "caracteres"),
            ("con 5001 códigos", "Enorme", [f"9{i:07d}" for i in range(5001)], 422, "largo inválido"),
            ("con un código vacío", "Con vacío", [B.CAFE, ""], 422, "largo inválido")):
        r = crear_filtro(nombre, codigos)
        verificar(r.status_code == status and r.json().get("codigo") == "dato_invalido"
                  and texto in r.json().get("detail", ""),
                  f"crear un filtro {que} da {status}", f"filtro {que}: {r.status_code} {r.text[:300]}")
    r = crear_filtro("Muchos fantasmas", [f"8{i:06d}" for i in range(7)])
    verificar(r.status_code == 400 and "y 2 más" in r.json().get("detail", ""),
              "con muchos códigos que no existen, nombra 5 y dice cuántos más", f"fantasmas: {r.text}")
    verificar(contar("SELECT COUNT(*) FROM Filtros_Guardados") == filtros_antes,
              "ninguno de los rechazados guardó un filtro", "un filtro rechazado se guardó")

    # ===================================================================== #
    # Filtros: nombres raros, por su nombre exacto
    # ===================================================================== #
    for nombre, ruta in (("Bebidas/Gaseosas", "Bebidas%2FGaseosas"), ("Promo 50%", "Promo%2050%25"),
                         ("¿Qué onda? #1", "%C2%BFQu%C3%A9%20onda%3F%20%231"), ("Café & Té", "Caf%C3%A9%20%26%20T%C3%A9")):
        r = crear_filtro(nombre, [B.CAFE, B.YERBA])
        creado = r.json()
        r2 = c.get(f"/api/filtros/{ruta}", headers=H)
        r3 = c.put(f"/api/filtros/{ruta}", headers=H, json={"codigos": [B.YERBA], "huella": creado.get("huella", "")})
        guardado = filters.obtener_filtro(nombre)
        db.cerrar_conexion()
        r4 = c.delete(f"/api/filtros/{ruta}?huella={r3.json().get('huella', '')}", headers=H)
        verificar(r.status_code == 200 and r2.status_code == 200 and r2.json().get("nombre") == nombre
                  and r3.status_code == 200 and guardado and guardado["definicion"]["codigos"] == [B.YERBA]
                  and r4.status_code == 200 and filters.obtener_filtro(nombre) is None,
                  f"el filtro «{nombre}» se crea, se abre, se cambia y se borra por su nombre exacto",
                  f"«{nombre}»: {r.status_code} {r2.status_code} {r3.status_code} {r4.status_code} {r2.text[:150]}")
        db.cerrar_conexion()
    crear_filtro("Bebidas", [B.COCA])
    r = crear_filtro("bebidas", [B.CAFE])
    verificar(r.status_code == 200 and {f["nombre"] for f in c.get("/api/filtros", headers=H).json()} >= {"Bebidas", "bebidas"},
              "'bebidas' y 'Bebidas' son dos filtros distintos, como en el Panel", f"mayúsculas: {r.text}")
    nombres = [f["nombre"] for f in c.get("/api/filtros", headers=H).json()]
    verificar(nombres == sorted(nombres, key=lambda n: (n.casefold(), n)),
              "la lista va por nombre sin importar mayúsculas", f"orden: {nombres}")

    # ===================================================================== #
    # Filtros: cambiar y borrar
    # ===================================================================== #
    r = crear_filtro("Cambios", [B.CAFE])
    huella = r.json()["huella"]
    for que, cuerpo, status in (("sin huella", {"codigos": [B.YERBA]}, 422),
                                ("con una huella corta", {"codigos": [B.YERBA], "huella": "abc"}, 422),
                                ("sin productos", {"codigos": [], "huella": huella}, 422),
                                ("con un código que no existe", {"codigos": ["9999999"], "huella": huella}, 400)):
        r = c.put("/api/filtros/Cambios", headers=H, json=cuerpo)
        verificar(r.status_code == status and filters.obtener_filtro("Cambios")["definicion"]["codigos"] == [B.CAFE],
                  f"cambiar un filtro {que} da {status} y lo deja como estaba", f"cambiar {que}: {r.status_code} {r.text}")
        db.cerrar_conexion()
    r = c.delete("/api/filtros/Cambios", headers=H)
    verificar(r.status_code == 422 and filters.obtener_filtro("Cambios") is not None,
              "borrar sin huella da 422 y no borra", f"borrar sin huella: {r.status_code}")
    db.cerrar_conexion()

    # Uno de condiciones del Panel viejo: cambiarlo desde el celular lo deja como lista fija.
    filters.guardar_filtro("Kiosco viejo", {"op": "OR", "conditions": [
        {"campo": "categoria", "operador": "=", "valor": "KIOSCO"}]})
    db.cerrar_conexion()
    viejo = c.get("/api/filtros/Kiosco%20viejo", headers=H).json()
    r = c.put("/api/filtros/Kiosco%20viejo", headers=H,
              json={"codigos": [p["codigo"] for p in viejo["productos"]] + [B.CAFE], "huella": viejo["huella"]})
    guardado = filters.obtener_filtro("Kiosco viejo")
    db.cerrar_conexion()
    verificar(viejo["tipo"] == "condiciones" and r.status_code == 200
              and guardado["definicion"] == {"tipo": "manual", "codigos": [B.CHICLE, B.CAFE]},
              "cambiar un filtro viejo de condiciones lo guarda como lista fija con lo elegido",
              f"condiciones -> manual: {r.status_code} {guardado}")

    # ===================================================================== #
    # Filtros guardados raros (Panel viejo, JSON dañado, condiciones inválidas)
    # ===================================================================== #
    a_mano("INSERT INTO Filtros_Guardados (nombre, definicion_json) VALUES ('Roto', '{no es json')")
    a_mano("INSERT INTO Filtros_Guardados (nombre, definicion_json) VALUES ('Lista suelta', '[1, 2]')")
    a_mano("INSERT INTO Filtros_Guardados (nombre, definicion_json) VALUES ('Campo raro', ?)",
           (json.dumps({"campo": "precio_compra", "operador": "=", "valor": 1}),))
    a_mano("INSERT INTO Filtros_Guardados (nombre, definicion_json) VALUES ('Números', ?)",
           (json.dumps({"tipo": "manual", "codigos": [7790002, None, "", B.YERBA]}),))
    r = c.get("/api/filtros", headers=H)
    lista = {f["nombre"]: f for f in r.json()} if r.status_code == 200 else {}
    verificar(r.status_code == 200 and lista.get("Roto", {}).get("cantidad") == 0
              and lista.get("Lista suelta", {}).get("cantidad") == 0 and lista.get("Campo raro", {}).get("cantidad") == 0,
              "un filtro con el JSON dañado o con condiciones que no se pueden aplicar no tumba la lista (sale vacío)",
              f"lista con filtros rotos: {r.status_code} {r.text[:300]}")
    verificar(lista.get("Números", {}).get("cantidad") == 2,
              "un filtro manual con códigos guardados como número también se lee (7790002 = el café)",
              f"códigos numéricos: {lista.get('Números')}")
    r = c.get("/api/filtros/Roto", headers=H)
    verificar(r.status_code == 200 and r.json()["productos"] == [] and r.json()["faltantes"] == [],
              "el detalle de uno roto se abre vacío", f"detalle roto: {r.status_code} {r.text[:200]}")
    r = c.delete(f"/api/filtros/Roto?huella={r.json().get('huella', '')}", headers=H)
    verificar(r.status_code == 200 and filters.obtener_filtro("Roto") is None,
              "y se puede borrar desde el celular con su huella", f"borrar roto: {r.status_code} {r.text}")
    db.cerrar_conexion()

    # ===================================================================== #
    # Filtros grandes
    # ===================================================================== #
    with db.transaction() as conn:   # simula: un catálogo cargado por Excel
        conn.executemany("INSERT INTO Productos (uuid_unico, codigo, nombre, precio_venta, stock, origen, sincronizado) "
                         "VALUES (lower(hex(randomblob(16))), ?, ?, 100, 1, 'MAESTRO', 1)",
                         [(f"5{i:07d}", f"Masivo {i}") for i in range(5001)])
    db.cerrar_conexion()
    todos = [f"5{i:07d}" for i in range(5001)]
    r = crear_filtro("Cinco mil", todos[:5000])
    verificar(r.status_code == 200 and r.json().get("cantidad") == 5000, "un filtro de 5000 productos se guarda entero",
              f"5000: {r.status_code} {r.text[:200]}")
    r = c.get("/api/filtros/Cinco%20mil", headers=H)
    verificar(r.status_code == 200 and [p["codigo"] for p in r.json()["productos"]] == todos[:5000]
              and r.json()["recortado"] is False, "y se abre entero, en el orden elegido", f"abrir 5000: {r.status_code}")
    filters.guardar_filtro_manual("Del Panel, enorme", todos)
    db.cerrar_conexion()
    r = c.get("/api/filtros/Del%20Panel%2C%20enorme", headers=H)
    verificar(r.status_code == 200 and len(r.json()["productos"]) == 5000 and r.json()["recortado"] is True
              and r.json()["cantidad"] == 5000,
              "uno de 5001 armado en el Panel se abre con los primeros 5000 y avisa que está recortado",
              f"5001: {r.status_code} {r.text[:200]}")
    resumen = {f["nombre"]: f for f in c.get("/api/filtros", headers=H).json()}.get("Del Panel, enorme", {})
    verificar(resumen.get("cantidad") == 5000, "la lista cuenta lo mismo que trae el detalle (5000)",
              f"lista: {resumen}")

# ===================================================================== #
# Lo que quedó anotado
# ===================================================================== #
api_celular.cerrar_logs()
ruta_cambios = os.path.join(TMP, "logs", "api_celular_cambios.log")
cambios = []
if os.path.exists(ruta_cambios):
    with open(ruta_cambios, encoding="utf-8") as f:
        cambios = [json.loads(linea) for linea in f if linea.strip()]
acciones = {x["accion"] for x in cambios}
verificar({"producto_alta", "filtro_crear", "filtro_cambiar", "filtro_borrar"} <= acciones,
          "cada alta y cada cambio de filtro queda en api_celular_cambios.log", f"acciones anotadas: {sorted(acciones)}")
alta_fernet = [x for x in cambios if x["accion"] == "producto_alta" and x["codigo"] == "0001234"]
verificar(alta_fernet and alta_fernet[0]["usuario"] == "dueño" and alta_fernet[0]["ip"] == B.IP_CELULAR
          and alta_fernet[0]["despues"]["precio_venta"] == 9800.5,
          "el alta anotada dice quién, desde qué IP y con qué datos", f"alta anotada: {alta_fernet}")
repetida = [x for x in cambios if x["accion"] == "producto_alta" and x["resultado"] == "ya_existia"]
verificar(len(repetida) == 1 and repetida[0]["codigo"] == "7790600",
          "el alta repetida queda anotada como 'ya_existia' (no como un alta)", f"repetidas: {repetida}")

db.cerrar_conexion()
db.usar_solo_base_existente(None)
shutil.rmtree(RAIZ_TMP, ignore_errors=True)
print()
if fallos:
    print("=== FALLOS API CELULAR (ALTA Y FILTROS) ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== API CELULAR (ALTA Y FILTROS) OK ===")
