"""Reimportar la lista de precios no puede dejar los filtros con el precio viejo.

El caso que esta prueba cuida, que es el que pidió Matías: el dueño tiene
filtros armados en el Panel (listas de productos elegidos a mano), se vuelve
a extraer la lista de precios del sistema viejo, y después de importarla los
filtros tienen que seguir armados con los MISMOS productos, pero con los
precios nuevos.

Lo que lo rompía: el código que Otter usa sale del primer código de barras
del sistema viejo, y ese código cambia entre extracciones. La carga masiva
de siempre empareja solo por código, así que creaba un producto nuevo al
lado y el filtro se quedaba señalando al viejo, con el precio de antes.

La prueba mira lo que ve el dueño: abre el filtro guardado y lee los precios
que salen. No alcanza con mirar la tabla Productos — el filtro podría estar
actualizado y el producto correcto afuera de él, que es exactamente el bug.
"""
import atexit
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fallos = []

# ---------------------------------------------------------------- #
# Aislar la carpeta del usuario ANTES de importar nada.
#
# La pantalla guarda el informe en el Escritorio. Sin esto, correr las
# pruebas le deja dos archivos en el Escritorio REAL a quien las corre.
# En Windows expanduser("~") NO mira HOME: mira USERPROFILE, y si no
# está, HOMEDRIVE+HOMEPATH — aislar solo con HOME da verde en Linux y
# escribe en el Escritorio de verdad en Windows.
# ---------------------------------------------------------------- #
_CLAVES_CASA = ("HOME", "USERPROFILE", "HOMEPATH", "HOMEDRIVE", "OneDrive", "OneDriveConsumer")
_casa_original = {k: os.environ.get(k) for k in _CLAVES_CASA}
escritorio = tempfile.mkdtemp(prefix="escritorio_")
os.makedirs(os.path.join(escritorio, "Desktop"))
for _k in _CLAVES_CASA:
    os.environ.pop(_k, None)
os.environ["HOME"] = escritorio
os.environ["USERPROFILE"] = escritorio


@atexit.register
def _devolver_la_casa():
    for k, v in _casa_original.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


if os.path.expanduser("~") != escritorio:
    fallos.append(f"no se pudo aislar la carpeta del usuario: expanduser da "
                   f"{os.path.expanduser('~')!r} — la prueba estaría escribiendo en el "
                   f"Escritorio real de quien la corre")

base = tempfile.mkdtemp(prefix="lista_precios_")
from pos_core import paths
paths.set_base_override(base)
from pos_core import db
db.preparar_base()

import openpyxl
from pos_core import products, filters, alerts, ofertas, actualizacion_precios


def excel(filas, nombre="lista.xlsx"):
    """Arma un Excel con el mismo formato que genera scripts/generar_lista_precios.py:
    el proveedor va VACÍO, que es lo que le borraba el proveedor al catálogo."""
    ruta = os.path.join(base, nombre)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Código", "Nombre", "Precio Venta", "Stock Inicial", "Proveedor"])
    for codigo, nom, precio in filas:
        ws.append([codigo, nom, precio, 0, ""])
    wb.save(ruta)
    return ruta


def precio_de(codigo):
    row = db.get_connection().execute(
        "SELECT precio_venta FROM Productos WHERE codigo = ?", (codigo,)).fetchone()
    return None if row is None else row["precio_venta"]


def cuantos_productos():
    return db.get_connection().execute("SELECT COUNT(*) c FROM Productos").fetchone()["c"]


# --------------------------------------------------------------------- #
# El catálogo que ya está cargado, con datos que el dueño puso a mano
# --------------------------------------------------------------------- #
products.crear_producto(codigo="111", nombre="COCA COLA 2.25 LT", precio_venta=2500.0,
                        stock_inicial=12, proveedor="Distribuidora Sur", usuario="test")
products.crear_producto(codigo="222", nombre="YERBA PLAYADITO 1 KG", precio_venta=4000.0,
                        stock_inicial=5, proveedor="Mayorista Norte", usuario="test")
products.crear_producto(codigo="333", nombre="PAN LACTAL BIMBO", precio_venta=1800.0,
                        stock_inicial=3, proveedor="Bimbo", usuario="test")
products.crear_producto(codigo="444", nombre="ARTICULO SOLO DE OTTER", precio_venta=999.0,
                        stock_inicial=1, proveedor="Nadie", usuario="test")

# El dueño armó un filtro con tres de ellos, y le puso una oferta y un
# umbral de alerta al que después va a cambiar de código.
filters.guardar_filtro_manual("Bebidas y secos", ["111", "222", "333"])
alerts.set_umbral_producto("111", 7, 0)
ofertas.crear_oferta(codigo="111", tipo_descuento="PORCENTAJE", valor=10.0,
                      descripcion="Oferta Coca", dias=30, usuario="test")

# --------------------------------------------------------------------- #
# La lista nueva: a la Coca le cambió el código de barras en el sistema
# viejo (111 -> 999), los otros dos mantienen el suyo, y aparece un
# producto nuevo de verdad.
# --------------------------------------------------------------------- #
ruta = excel([
    ("999", "COCA COLA 2.25 LT",    3100.0),   # mismo producto, código distinto
    ("222", "YERBA PLAYADITO 1 KG", 4800.0),   # mismo código, precio nuevo
    ("333", "PAN LACTAL BIMBO",     1800.0),   # sin cambios
    ("555", "ALFAJOR JORGITO",       600.0),   # producto nuevo de verdad
])

# --------------------------------------------------------------------- #
# 1. El ensayo no puede escribir nada
# --------------------------------------------------------------------- #
antes_cantidad = cuantos_productos()
antes_precio = precio_de("111")
plan = actualizacion_precios.analizar(ruta)
if cuantos_productos() != antes_cantidad or precio_de("111") != antes_precio:
    fallos.append("el ENSAYO modificó la base; tiene que ser solo lectura")
else:
    print("OK: el ensayo no tocó nada")

if len(plan["recodifican"]) != 1 or plan["recodifican"][0]["codigo_nuevo"] != "999":
    fallos.append(f"el ensayo no detectó el cambio de código: {plan['recodifican']}")
else:
    print("OK: el ensayo avisa que la Coca cambia de código 111 -> 999")

if [p["codigo"] for p in plan["no_vienen"]] != ["444"]:
    fallos.append(f"el ensayo no listó bien lo que no viene en el Excel: {plan['no_vienen']}")
else:
    print("OK: el ensayo avisa que 'ARTICULO SOLO DE OTTER' no viene en el Excel")

texto = actualizacion_precios.informe_texto(plan, ruta=ruta)
if "ENSAYO" not in texto or "Bebidas y secos" not in texto:
    fallos.append("el informe del ensayo no nombra el filtro afectado")
else:
    print("OK: el informe nombra el filtro afectado")

# --------------------------------------------------------------------- #
# 2. Aplicar
# --------------------------------------------------------------------- #
resultado = actualizacion_precios.aplicar(ruta, usuario="test")

# --------------------------------------------------------------------- #
# 3. Lo que ve el dueño: abre el filtro y lee los precios
# --------------------------------------------------------------------- #
guardados = {f["nombre"]: f["definicion"] for f in filters.listar_filtros_guardados()}
if "Bebidas y secos" not in guardados:
    fallos.append("EL FILTRO SE BORRÓ")
else:
    en_filtro = filters.aplicar_filtro(guardados["Bebidas y secos"])
    nombres = [p["nombre"] for p in en_filtro]
    precios = {p["nombre"]: p["precio_venta"] for p in en_filtro}

    if len(en_filtro) != 3:
        fallos.append(f"el filtro quedó con {len(en_filtro)} productos, tenía 3: {nombres}")
    else:
        print("OK: el filtro sigue teniendo sus 3 productos")

    if precios.get("COCA COLA 2.25 LT") != 3100.0:
        fallos.append(f"EL BUG: en el filtro la Coca sigue a "
                       f"${precios.get('COCA COLA 2.25 LT')}, tenía que estar a $3100")
    else:
        print("OK: en el filtro, la Coca que cambió de código muestra el precio NUEVO")

    if precios.get("YERBA PLAYADITO 1 KG") != 4800.0:
        fallos.append(f"en el filtro la yerba quedó a ${precios.get('YERBA PLAYADITO 1 KG')}")
    else:
        print("OK: en el filtro, la yerba muestra el precio nuevo")

# --------------------------------------------------------------------- #
# 4. No se duplicó la Coca
# --------------------------------------------------------------------- #
cocas = db.get_connection().execute(
    "SELECT codigo FROM Productos WHERE nombre = 'COCA COLA 2.25 LT'").fetchall()
if len(cocas) != 1:
    fallos.append(f"se duplicó la Coca: {[c['codigo'] for c in cocas]}")
elif cocas[0]["codigo"] != "999":
    fallos.append(f"la Coca quedó con el código {cocas[0]['codigo']}, esperaba 999")
else:
    print("OK: la Coca es UN solo producto, con el código de barras nuevo")

# --------------------------------------------------------------------- #
# 5. El nombre, el proveedor y el stock no se pisan
# --------------------------------------------------------------------- #
coca = db.get_connection().execute(
    "SELECT * FROM Productos WHERE codigo = '999'").fetchone()
if coca["proveedor"] != "Distribuidora Sur":
    fallos.append(f"EL EXCEL BORRÓ EL PROVEEDOR: quedó {coca['proveedor']!r}")
else:
    print("OK: el proveedor cargado en Otter se conservó")
if coca["stock"] != 12:
    fallos.append(f"el stock se pisó: quedó en {coca['stock']}, era 12")
else:
    print("OK: el stock no se tocó")

yerba = db.get_connection().execute(
    "SELECT * FROM Productos WHERE codigo = '222'").fetchone()
if yerba["proveedor"] != "Mayorista Norte":
    fallos.append(f"se borró el proveedor de la yerba: {yerba['proveedor']!r}")

# --------------------------------------------------------------------- #
# 6. La oferta y el umbral siguieron al producto
# --------------------------------------------------------------------- #
of = db.get_connection().execute(
    "SELECT producto_codigo FROM Ofertas WHERE descripcion = 'Oferta Coca'").fetchone()
if of is None or of["producto_codigo"] != "999":
    fallos.append(f"la oferta quedó colgada del código viejo: {of and of['producto_codigo']}")
else:
    print("OK: la oferta siguió al producto al código nuevo")

um = db.get_connection().execute(
    "SELECT producto_codigo, stock_minimo FROM Configuracion_Alertas "
    "WHERE producto_codigo IN ('111','999')").fetchall()
if len(um) != 1 or um[0]["producto_codigo"] != "999" or um[0]["stock_minimo"] != 7:
    fallos.append(f"el umbral propio no siguió al producto: {[dict(u) for u in um]}")
else:
    print("OK: el umbral de alerta siguió al producto al código nuevo")

# --------------------------------------------------------------------- #
# 7. El producto que no viene en el Excel queda intacto
# --------------------------------------------------------------------- #
if precio_de("444") != 999.0:
    fallos.append(f"se tocó un producto que no venía en el Excel: ${precio_de('444')}")
else:
    print("OK: el producto que no viene en el Excel quedó intacto")

# --------------------------------------------------------------------- #
# 8. El producto nuevo de verdad sí se crea
# --------------------------------------------------------------------- #
if precio_de("555") != 600.0:
    fallos.append("no se creó el producto nuevo que sí venía en el Excel")
else:
    print("OK: el producto nuevo de verdad se creó")

# --------------------------------------------------------------------- #
# 9. Nombre repetido: no se adivina, va al informe
# --------------------------------------------------------------------- #
products.crear_producto(codigo="A1", nombre="GALLETITAS SURTIDAS", precio_venta=100.0,
                        usuario="test")
products.crear_producto(codigo="A2", nombre="GALLETITAS SURTIDAS", precio_venta=200.0,
                        usuario="test")
ruta2 = excel([("B9", "GALLETITAS SURTIDAS", 777.0)], "ambiguo.xlsx")
plan2 = actualizacion_precios.analizar(ruta2)
if len(plan2["ambiguos"]) != 1 or plan2["nuevos"]:
    fallos.append(f"con dos productos del mismo nombre tendría que dudar, no decidir: "
                   f"ambiguos={plan2['ambiguos']} nuevos={plan2['nuevos']}")
else:
    print("OK: con dos productos del mismo nombre no adivina, lo manda al informe")

actualizacion_precios.aplicar(ruta2, usuario="test")
if precio_de("A1") != 100.0 or precio_de("A2") != 200.0 or precio_de("B9") is not None:
    fallos.append("un caso ambiguo modificó la base igual")
else:
    print("OK: el caso ambiguo no tocó ningún precio")

# --------------------------------------------------------------------- #
# 10. Volver a importar el mismo Excel no cambia nada (idempotente)
# --------------------------------------------------------------------- #
segunda = actualizacion_precios.aplicar(ruta, usuario="test")
if segunda["precios"] or segunda["recodificados"] or segunda["creados"]:
    fallos.append(f"reimportar el mismo Excel volvió a tocar cosas: {segunda}")
else:
    print("OK: reimportar el mismo Excel no cambia nada")

# --------------------------------------------------------------------- #
# 11. Por la API REMOTA, que es como lo va a hacer Leo desde su laptop
# --------------------------------------------------------------------- #
# El Dueño Remoto no abre la base: manda el Excel por HTTP y el local
# decide. Si el archivo o los kwargs (usuario, origen) no viajaran bien,
# el síntoma sería el mismo que el bug original por otra causa.
import socket
from services import remote_api
from pos_core.dueno_backend import RemoteBackend

products.crear_producto(codigo="R1", nombre="FIDEOS MATARAZZO 500", precio_venta=1000.0,
                        proveedor="Mayorista Norte", usuario="test")
filters.guardar_filtro_manual("Remoto", ["R1"])
ruta3 = excel([("R9", "FIDEOS MATARAZZO 500", 1400.0)], "remoto.xlsx")

s_libre = socket.socket()
s_libre.bind(("127.0.0.1", 0))
puerto = s_libre.getsockname()[1]
s_libre.close()

servidor = remote_api.iniciar_servidor(puerto=puerto, token="token-de-prueba")
try:
    backend = RemoteBackend(f"http://127.0.0.1:{puerto}", "token-de-prueba")
    plan3 = backend.subir_y_llamar("actualizacion_precios", "analizar", ruta3)
    if len(plan3["recodifican"]) != 1:
        fallos.append(f"por la API remota el ensayo no detectó el cambio de código: "
                       f"{plan3['recodifican']}")
    elif precio_de("R1") != 1000.0:
        fallos.append("el ensayo por la API remota modificó la base")
    else:
        print("OK: el ensayo anda por la API remota y no toca nada")

    hecho3 = backend.subir_y_llamar("actualizacion_precios", "aplicar", ruta3,
                                     usuario="leo", origen="MAESTRO")
    if hecho3["recodificados"] != 1:
        fallos.append(f"por la API remota no se aplicó la recodificación: {hecho3}")
    elif precio_de("R9") != 1400.0:
        fallos.append(f"por la API remota el precio no llegó: ${precio_de('R9')}")
    else:
        print("OK: actualizar los precios anda por la API remota")

    guardados = {f["nombre"]: f["definicion"] for f in filters.listar_filtros_guardados()}
    en_filtro = filters.aplicar_filtro(guardados["Remoto"])
    if len(en_filtro) != 1 or en_filtro[0]["precio_venta"] != 1400.0:
        fallos.append(f"por la API remota el filtro quedó mal: "
                       f"{[(p['codigo'], p['precio_venta']) for p in en_filtro]}")
    else:
        print("OK: por la API remota el filtro queda armado y con el precio nuevo")

    fideos = db.get_connection().execute(
        "SELECT proveedor FROM Productos WHERE codigo = 'R9'").fetchone()
    if fideos["proveedor"] != "Mayorista Norte":
        fallos.append(f"por la API remota se borró el proveedor: {fideos['proveedor']!r}")
finally:
    servidor.shutdown()

# --------------------------------------------------------------------- #
# 12. Por la pantalla del Panel, con los botones reales
# --------------------------------------------------------------------- #
from tkinter import messagebox, filedialog
messagebox.showinfo = lambda *a, **k: None
messagebox.showwarning = lambda *a, **k: None
messagebox.showerror = lambda *a, **k: print("   ERROR DIALOG:", a)
messagebox.askyesno = lambda *a, **k: True

products.crear_producto(codigo="P1", nombre="AZUCAR LEDESMA 1 KG", precio_venta=900.0,
                        proveedor="Mayorista Norte", usuario="test")
filters.guardar_filtro_manual("Pantalla", ["P1"])
ruta4 = excel([("P9", "AZUCAR LEDESMA 1 KG", 1200.0)], "pantalla.xlsx")

paths.set_base_override(base)
db.cerrar_conexion()
from apps.master_dueno.main import AppDueno

filedialog.askopenfilename = lambda *a, **k: ruta4
app = AppDueno()
app.update()

app._ensayar_lista_precios()
app.update()
if precio_de("P1") != 900.0:
    fallos.append("el botón ENSAYAR de la pantalla modificó la base")
elif "ENSAYO" not in app.excel_log.get("1.0", "end"):
    fallos.append("el botón ENSAYAR no dejó el informe en pantalla")
else:
    print("OK: el botón ENSAYAR de la pantalla no toca nada y muestra el informe")

app._aplicar_lista_precios()
app.update()
guardados = {f["nombre"]: f["definicion"] for f in filters.listar_filtros_guardados()}
en_filtro = filters.aplicar_filtro(guardados["Pantalla"])
if len(en_filtro) != 1 or en_filtro[0]["precio_venta"] != 1200.0:
    fallos.append(f"desde la pantalla el filtro quedó mal: "
                   f"{[(p['codigo'], p['precio_venta']) for p in en_filtro]}")
else:
    print("OK: desde la pantalla el filtro queda armado y con el precio nuevo")

# El informe tiene que quedar en un archivo: lista productos uno por uno y
# no entra en una foto de la pantalla, pero el archivo se puede mandar.
guardado = os.path.join(escritorio, "Desktop", "otter_actualizacion_precios.txt")
if not os.path.isfile(guardado):
    fallos.append("la pantalla no guardó el informe en el Escritorio")
elif "Pantalla" not in open(guardado, encoding="utf-8").read():
    fallos.append("el informe guardado no nombra el filtro afectado")
else:
    print("OK: el informe queda guardado en el Escritorio, para poder mandarlo")
app.destroy()

print()
if fallos:
    print("=== FALLOS LISTA DE PRECIOS ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== LISTA DE PRECIOS OK ===")
