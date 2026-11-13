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
import json
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


def excel_grupos(filas, nombre):
    """Excel con las columnas del generador nuevo: una fila por CÓDIGO DE
    BARRAS, con su grupo y el código interno del sistema viejo.
    Cada fila: (codigo, nombre, precio, rubro, grupo, cod_viejo)."""
    ruta = os.path.join(base, nombre)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Código", "Nombre", "Precio Venta", "Stock Inicial", "Proveedor",
                "Rubro", "Grupo", "Codigo interno"])
    for cod, nom, precio, rubro, grupo, cv in filas:
        ws.append([cod, nom, precio, 0, "", rubro, grupo, cv])
    wb.save(ruta)
    return ruta


def excel(filas, nombre="lista.xlsx", con_rubro=False):
    """Arma un Excel con el mismo formato que genera scripts/generar_lista_precios.py:
    el proveedor va VACÍO, que es lo que le borraba el proveedor al catálogo.
    Con `con_rubro`, cada fila lleva además su grupo (4º elemento)."""
    ruta = os.path.join(base, nombre)
    wb = openpyxl.Workbook()
    ws = wb.active
    encabezados = ["Código", "Nombre", "Precio Venta", "Stock Inicial", "Proveedor"]
    if con_rubro:
        encabezados.append("Rubro")
    ws.append(encabezados)
    for f in filas:
        fila = [f[0], f[1], f[2], 0, ""]
        if con_rubro:
            fila.append(f[3])
        ws.append(fila)
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

# --------------------------------------------------------------------- #
# 13. LOS GRUPOS: los rubros del sistema viejo pasan a ser filtros
# --------------------------------------------------------------------- #
# Es lo que pidió Matías. En el sistema viejo hay 16 rubros (ARC_RUB.DBF)
# y el dueño los usa para cambiarle el precio a todo un grupo de una.

for cod, nom, precio in [("G1", "QUESO CREMOSO", 5000.0), ("G2", "YOGUR ENTERO", 1200.0),
                          ("G3", "LAVANDINA 1 LT", 800.0), ("G4", "JABON EN POLVO", 2000.0)]:
    products.crear_producto(codigo=cod, nombre=nom, precio_venta=precio, usuario="test")

# El dueño ya tiene un filtro propio que se llama igual que un rubro.
filters.guardar_filtro_manual("LIMPIEZA", ["G3"])

ruta_g = excel([
    ("G1", "QUESO CREMOSO",  5500.0, "LACTEOS"),
    ("G2", "YOGUR ENTERO",   1300.0, "LACTEOS"),
    ("G3", "LAVANDINA 1 LT",  900.0, "LIMPIEZA"),   # choca con el filtro del dueño
    ("G9", "JABON EN POLVO", 2200.0, "LIMPIEZA"),   # cambió de código: G4 -> G9
], "grupos.xlsx", con_rubro=True)

plan_g = actualizacion_precios.analizar(ruta_g)
acciones = {g["nombre"]: g["accion"] for g in plan_g["grupos"]}
if acciones.get("LACTEOS") != "crea" or acciones.get("LIMPIEZA") != "respeta":
    fallos.append(f"el ensayo de grupos decidió mal: {acciones}")
else:
    print("OK: el ensayo dice que crea LACTEOS y RESPETA el LIMPIEZA del dueño")

if "GRUPOS" not in plan_g["informe"] or "LACTEOS" not in plan_g["informe"]:
    fallos.append("el informe del ensayo no muestra los grupos")
else:
    print("OK: el informe muestra los grupos")

actualizacion_precios.aplicar(ruta_g, usuario="test")

guardados = {f["nombre"]: f["definicion"] for f in filters.listar_filtros_guardados()}

# 13a. El grupo nuevo quedó armado y sirve para la edición masiva
if "LACTEOS" not in guardados:
    fallos.append("no se creó el grupo LACTEOS")
else:
    en_lacteos = filters.aplicar_filtro(guardados["LACTEOS"])
    nombres = sorted(p["nombre"] for p in en_lacteos)
    precios = {p["nombre"]: p["precio_venta"] for p in en_lacteos}
    if nombres != ["QUESO CREMOSO", "YOGUR ENTERO"]:
        fallos.append(f"el grupo LACTEOS quedó con {nombres}")
    elif precios.get("QUESO CREMOSO") != 5500.0:
        fallos.append(f"en el grupo LACTEOS el precio no se actualizó: {precios}")
    else:
        print("OK: el grupo LACTEOS quedó armado, con sus 2 productos y los precios nuevos")

# 13b. El filtro del dueño NO se pisó
if "LIMPIEZA" not in guardados:
    fallos.append("SE BORRÓ el filtro LIMPIEZA del dueño")
else:
    en_limpieza = filters.aplicar_filtro(guardados["LIMPIEZA"])
    if [p["codigo"] for p in en_limpieza] != ["G3"]:
        fallos.append(f"SE PISÓ el filtro LIMPIEZA del dueño: quedó con "
                       f"{[p['codigo'] for p in en_limpieza]}, tenía solo G3")
    elif guardados["LIMPIEZA"].get("origen"):
        fallos.append("el filtro del dueño quedó marcado como si lo hubiéramos armado nosotros")
    else:
        print("OK: el filtro LIMPIEZA que armó el dueño quedó intacto")

# 13c. Un producto que cambió de código entra al grupo con el código NUEVO
if "LACTEOS" in guardados and "G9" in str(guardados.get("LIMPIEZA", {})):
    fallos.append("el producto recodificado entró al filtro del dueño")
jabon = db.get_connection().execute(
    "SELECT codigo, categoria FROM Productos WHERE nombre = 'JABON EN POLVO'").fetchone()
if jabon["codigo"] != "G9":
    fallos.append(f"el producto del grupo no se recodificó: quedó en {jabon['codigo']}")
else:
    print("OK: el producto que cambió de código se recodificó estando en un grupo")

# 13d. El rubro se carga también en la columna Rubro del producto
queso = db.get_connection().execute(
    "SELECT categoria FROM Productos WHERE codigo = 'G1'").fetchone()
if queso["categoria"] != "LACTEOS":
    fallos.append(f"no se cargó la columna Rubro del producto: {queso['categoria']!r}")
else:
    print("OK: el rubro quedó cargado en la columna Rubro del producto")

# 13e. Reimportar no duplica ni rompe: el grupo se actualiza
segunda_g = actualizacion_precios.aplicar(ruta_g, usuario="test")
if segunda_g["grupos_creados"]:
    fallos.append(f"reimportar volvió a CREAR grupos: {segunda_g['grupos_creados']}")
elif "LACTEOS" not in segunda_g["grupos_actualizados"]:
    fallos.append(f"reimportar no reconoció el grupo que ya había creado: {segunda_g}")
else:
    print("OK: reimportar actualiza el grupo en vez de duplicarlo")

# 13f. Si el dueño lo edita (y pierde la marca), deja de pisarse
filters.guardar_filtro_manual("LACTEOS", ["G1"])      # como lo guarda el Panel: sin marca
tercera = actualizacion_precios.aplicar(ruta_g, usuario="test")
despues = {f["nombre"]: f["definicion"] for f in filters.listar_filtros_guardados()}
if [c for c in despues["LACTEOS"].get("codigos", [])] != ["G1"]:
    fallos.append(f"se pisó un grupo que el dueño había editado: "
                   f"{despues['LACTEOS'].get('codigos')}")
elif "LACTEOS" not in tercera["grupos_respetados"]:
    fallos.append("el grupo editado por el dueño no figura como respetado en el informe")
else:
    print("OK: si el dueño edita un grupo, pasa a ser suyo y no se vuelve a pisar")

# 13g. La columna Rubro que puso el dueño a mano NO se pisa
db.get_connection().execute("UPDATE Productos SET categoria = 'MI RUBRO' WHERE codigo = 'G2'")
db.get_connection().commit()
actualizacion_precios.aplicar(ruta_g, usuario="test")
yogur = db.get_connection().execute(
    "SELECT categoria FROM Productos WHERE codigo = 'G2'").fetchone()
if yogur["categoria"] != "MI RUBRO":
    fallos.append(f"se pisó el rubro que cargó el dueño: quedó {yogur['categoria']!r}")
else:
    print("OK: el rubro que cargó el dueño a mano no se pisa")

# 13h. Un Excel SIN columna Rubro no toca ningún grupo (archivos viejos)
antes_filtros = {f["nombre"]: json.dumps(f["definicion"], sort_keys=True)
                  for f in filters.listar_filtros_guardados()}
ruta_sin = excel([("G1", "QUESO CREMOSO", 5600.0)], "sin_rubro.xlsx")
plan_sin = actualizacion_precios.analizar(ruta_sin)
if plan_sin["grupos"] or plan_sin["trae_rubros"]:
    fallos.append("un Excel sin columna Rubro planificó grupos igual")
actualizacion_precios.aplicar(ruta_sin, usuario="test")
despues_filtros = {f["nombre"]: json.dumps(f["definicion"], sort_keys=True)
                    for f in filters.listar_filtros_guardados()}
if antes_filtros != despues_filtros:
    fallos.append("un Excel sin columna Rubro cambió los grupos")
else:
    print("OK: un Excel sin columna Rubro no toca ningún grupo")

# --------------------------------------------------------------------- #
# 14. LOS GRUPOS POR PRODUCTO: los que valen todos lo mismo
# --------------------------------------------------------------------- #
# Es lo que pidió Matías: en el sistema viejo "SAPHIRUS TEXTIL 250ML." es UN
# producto con 53 códigos de barras que valen todos $4601, y el dueño les
# cambia el precio una vez. En Otter cada código es un producto suelto, así
# que sin el grupo hay que cambiar 53 precios a mano.

ruta_p = excel_grupos([
    ("J1", "ARCOR JUGO NARANJA", 400.0, "ALMACEN", "ARCOR JUGOS", "1000"),
    ("J2", "ARCOR JUGO MANZANA", 400.0, "ALMACEN", "ARCOR JUGOS", "1000"),
    ("J3", "ARCOR JUGO UVA",     400.0, "ALMACEN", "ARCOR JUGOS", "1000"),
    ("S1", "SHAMPOO X",          900.0, "HIGIENE", "",            "2000"),
], "productos.xlsx")

actualizacion_precios.aplicar(ruta_p, usuario="test")
g = {f["nombre"]: f["definicion"] for f in filters.listar_filtros_guardados()}

if "ARCOR JUGOS" not in g:
    fallos.append("no se creó el grupo por producto ARCOR JUGOS")
else:
    en_grupo = filters.aplicar_filtro(g["ARCOR JUGOS"])
    codigos = sorted(p["codigo"] for p in en_grupo)
    precios = {p["precio_venta"] for p in en_grupo}
    if codigos != ["J1", "J2", "J3"]:
        fallos.append(f"el grupo ARCOR JUGOS quedó con {codigos}")
    elif precios != {400.0}:
        fallos.append(f"los del grupo no valen todos lo mismo: {precios}")
    else:
        print("OK: los 3 códigos de ARCOR JUGOS quedaron en un grupo, todos al mismo precio")

if g["ARCOR JUGOS"].get("cod_viejo") != "1000":
    fallos.append("el grupo no guardó el código interno del sistema viejo")
if "SHAMPOO X" in g:
    fallos.append("se creó un grupo para un producto con un solo código")
else:
    print("OK: un producto con un solo código no forma grupo")

# 14a. Cambiar el precio del grupo es UNA operación
nuevo_precio = 520.0
for cod in ("J1", "J2", "J3"):
    db.get_connection().execute("UPDATE Productos SET precio_venta = ? WHERE codigo = ?",
                                 (nuevo_precio, cod))
db.get_connection().commit()
if {p["precio_venta"] for p in filters.aplicar_filtro(g["ARCOR JUGOS"])} != {nuevo_precio}:
    fallos.append("el grupo no refleja un cambio de precio masivo")
else:
    print("OK: cambiar el precio sobre el grupo alcanza los 3 de una")

# 14b. Si el grupo cambia de NOMBRE, se renombra en vez de duplicarse.
# Pasa de verdad: cuando dos productos viejos se llaman igual, al nombre se
# le agrega el precio, y el precio cambia.
ruta_p2 = excel_grupos([
    ("J1", "ARCOR JUGO NARANJA", 520.0, "ALMACEN", "ARCOR JUGOS ($520)", "1000"),
    ("J2", "ARCOR JUGO MANZANA", 520.0, "ALMACEN", "ARCOR JUGOS ($520)", "1000"),
    ("J3", "ARCOR JUGO UVA",     520.0, "ALMACEN", "ARCOR JUGOS ($520)", "1000"),
], "productos2.xlsx")
r_ren = actualizacion_precios.aplicar(ruta_p2, usuario="test")
g2 = {f["nombre"]: f["definicion"] for f in filters.listar_filtros_guardados()}
if "ARCOR JUGOS" in g2:
    fallos.append("al cambiarle el nombre al grupo quedó el viejo huérfano al lado del nuevo")
elif "ARCOR JUGOS ($520)" not in g2:
    fallos.append(f"el grupo renombrado no aparece: {sorted(g2)}")
elif not r_ren["grupos_renombrados"]:
    fallos.append("el informe no dice que se renombró un grupo")
else:
    print("OK: si al grupo le cambia el nombre se renombra, no se duplica")

# 14c. Un grupo que el dueño haya armado con ese nombre no se pisa
filters.guardar_filtro_manual("MIS JUGOS", ["J1"])
ruta_p3 = excel_grupos([
    ("J1", "ARCOR JUGO NARANJA", 600.0, "ALMACEN", "MIS JUGOS", "3000"),
    ("J2", "ARCOR JUGO MANZANA", 600.0, "ALMACEN", "MIS JUGOS", "3000"),
], "productos3.xlsx")
actualizacion_precios.aplicar(ruta_p3, usuario="test")
g3 = {f["nombre"]: f["definicion"] for f in filters.listar_filtros_guardados()}
if [p["codigo"] for p in filters.aplicar_filtro(g3["MIS JUGOS"])] != ["J1"]:
    fallos.append("se pisó un filtro del dueño con un grupo por producto")
else:
    print("OK: un grupo por producto no pisa un filtro que armó el dueño")

# --------------------------------------------------------------------- #
# 14d. Un código de barras que el sistema viejo le AGREGÓ a un producto
# --------------------------------------------------------------------- #
# Pasó el 11/11/2026: en el sistema viejo a "AQUARIUS 1,5L." le cargaron un
# sabor nuevo. Todos sus códigos se llaman igual, los que Otter ya tenía se
# reclamaban por código, y el nuevo quedaba "SIN RESOLVER" porque el nombre
# ya estaba tomado. Resultado: la caja decía "no registrado" con un código
# que el sistema viejo sí conocía (~86 códigos). Si el que lo tomó es del
# MISMO producto viejo (mismo código interno), no hay nada que adivinar.
actualizacion_precios.aplicar(excel_grupos([
    ("AQ1", "AQUARIUS 1,5L.", 1500.0, "BEBIDAS", "AQUARIUS 1,5L.", "5000"),
    ("AQ2", "AQUARIUS 1,5L.", 1500.0, "BEBIDAS", "AQUARIUS 1,5L.", "5000"),
], "aquarius1.xlsx"), usuario="test")
ruta_aq = excel_grupos([
    ("AQ1", "AQUARIUS 1,5L.", 1600.0, "BEBIDAS", "AQUARIUS 1,5L.", "5000"),
    ("AQ2", "AQUARIUS 1,5L.", 1600.0, "BEBIDAS", "AQUARIUS 1,5L.", "5000"),
    ("AQ3", "AQUARIUS 1,5L.", 1600.0, "BEBIDAS", "AQUARIUS 1,5L.", "5000"),
    # Mismo nombre pero OTRO producto viejo: ahí sí no se sabe, no se toca.
    ("AQ9", "AQUARIUS 1,5L.", 9999.0, "BEBIDAS", "", "6000"),
], "aquarius2.xlsx")
plan_aq = actualizacion_precios.analizar(ruta_aq)
if [n["codigo"] for n in plan_aq["nuevos"]] != ["AQ3"]:
    fallos.append(f"el código agregado no se planifica como nuevo: nuevos={plan_aq['nuevos']}")
elif [a["codigo"] for a in plan_aq["ambiguos"]] != ["AQ9"]:
    fallos.append(f"el de otro producto viejo con el mismo nombre no quedó sin resolver: "
                   f"{plan_aq['ambiguos']}")
elif "le faltaban a un producto" not in actualizacion_precios.informe_texto(plan_aq):
    fallos.append("el informe no dice que hay códigos que le faltaban a un producto")
else:
    print("OK: el ensayo crea el código agregado y deja sin resolver el de otro producto")
actualizacion_precios.aplicar(ruta_aq, usuario="test")
g_aq = {f["nombre"]: f["definicion"] for f in filters.listar_filtros_guardados()}
if precio_de("AQ3") != 1600.0:
    fallos.append(f"el código agregado no quedó registrado con su precio: {precio_de('AQ3')}")
elif precio_de("AQ9") is not None:
    fallos.append("se creó el producto de otro código interno con el mismo nombre")
elif sorted(p["codigo"] for p in filters.aplicar_filtro(g_aq["AQUARIUS 1,5L."])) \
        != ["AQ1", "AQ2", "AQ3"]:
    fallos.append("el código agregado no entró al grupo de su producto")
else:
    print("OK: el código que le faltaba se registra con su precio y entra a su grupo")

# --------------------------------------------------------------------- #
# 14e. Un código de barras que en el sistema viejo usan DOS productos
# --------------------------------------------------------------------- #
# Pasó el 11/11/2026 con doce códigos: MILKA OREO 155G. y MILKA 155G.
# "precio oferta" comparten el mismo código de barras. El Excel viene
# ordenado por nombre, se aplicaba la PRIMERA fila y solo la segunda iba al
# informe como "no se toca": el producto de Otter quedaba con el precio del
# otro, y el informe decía que no se había tocado.
products.crear_producto(codigo="DUP1", nombre="MILKA OREO 155G.", precio_venta=6000.0, usuario="test")
ruta_dup = excel_grupos([
    ("DUP1", "MILKA 155G. ( precio oferta )", 10000.0, "KIOSCO", "", "7000"),
    ("DUP1", "MILKA OREO 155G.",              6500.0,  "KIOSCO", "", "7001"),
], "duplicado.xlsx")
plan_dup = actualizacion_precios.analizar(ruta_dup)
informe_dup = actualizacion_precios.informe_texto(plan_dup)
if plan_dup["actualizan"] or plan_dup["nuevos"] or plan_dup["recodifican"]:
    fallos.append(f"con un código que usan dos productos se aplicó una de las filas: "
                   f"{plan_dup['actualizan']} {plan_dup['nuevos']} {plan_dup['recodifican']}")
elif sorted(a["nombre"] for a in plan_dup["ambiguos"]) != ["MILKA 155G. ( precio oferta )",
                                                            "MILKA OREO 155G."]:
    fallos.append(f"las DOS filas del código repetido tienen que ir al informe: {plan_dup['ambiguos']}")
elif "$6500.00" not in informe_dup or "$10000.00" not in informe_dup:
    fallos.append("el informe no muestra los dos precios en disputa, que es con lo que se decide a mano")
elif any(n["codigo"] == "DUP1" for n in plan_dup["no_vienen"]):
    fallos.append("el producto del código en disputa figura como 'no viene en el Excel'")
else:
    print("OK: un código que usan dos productos no se aplica: van las dos filas al informe, con sus precios")
actualizacion_precios.aplicar(ruta_dup, usuario="test")
if precio_de("DUP1") != 6000.0:
    fallos.append(f"el producto de un código en disputa cambió de precio: quedó en {precio_de('DUP1')}")
else:
    print("OK: el producto de un código en disputa conserva su precio")

# Si las dos filas traen el MISMO precio no hay nada que adivinar: se aplica.
ruta_dup2 = excel_grupos([
    ("DUP1", "MILKA 155G. ( precio oferta )", 6800.0, "KIOSCO", "", "7000"),
    ("DUP1", "MILKA OREO 155G.",              6800.0, "KIOSCO", "", "7001"),
], "duplicado_mismo_precio.xlsx")
r_dup2 = actualizacion_precios.aplicar(ruta_dup2, usuario="test")
if precio_de("DUP1") != 6800.0 or r_dup2["plan"]["ambiguos"]:
    fallos.append(f"dos filas del mismo código con el MISMO precio tenían que aplicarse sin dudar: "
                   f"{precio_de('DUP1')} {r_dup2['plan']['ambiguos']}")
else:
    print("OK: si las filas repetidas traen el mismo precio, se aplica una sola vez")

# --------------------------------------------------------------------- #
# 14f. Dos productos viejos DISTINTOS que se llaman igual y cambiaron de código
# --------------------------------------------------------------------- #
# El caso de PUGLISI CABALLA el 11/11/2026: en Otter había UN producto con
# ese nombre (su código ya no venía), y en el Excel dos filas con ese nombre,
# de dos productos distintos del sistema viejo. Se le daba a la primera: el
# producto pasó de $3500 a $7300 con el código de quién sabe cuál de los dos.
products.crear_producto(codigo="PUG-VIEJO", nombre="PUGLISI CABALLA", precio_venta=3500.0, usuario="test")
filters.guardar_filtro_manual("Conservas", ["PUG-VIEJO"])
ruta_pug = excel_grupos([
    ("PUG-A", "PUGLISI CABALLA", 7300.0, "ALMACEN", "", "8000"),
    ("PUG-B", "PUGLISI CABALLA", 3600.0, "ALMACEN", "", "8001"),
], "puglisi.xlsx")
actualizacion_precios.aplicar(ruta_pug, usuario="test")
en_filtro = filters.aplicar_filtro({f["nombre"]: f["definicion"]
                                     for f in filters.listar_filtros_guardados()}["Conservas"])
if precio_de("PUG-VIEJO") != 3500.0 or precio_de("PUG-A") is not None or precio_de("PUG-B") is not None:
    fallos.append("con dos productos viejos del mismo nombre peleando por uno de Otter, se adivinó: "
                   f"PUG-VIEJO={precio_de('PUG-VIEJO')} PUG-A={precio_de('PUG-A')} PUG-B={precio_de('PUG-B')}")
elif [(p["codigo"], p["precio_venta"]) for p in en_filtro] != [("PUG-VIEJO", 3500.0)]:
    fallos.append(f"el filtro del dueño cambió por una fila que no se podía resolver: {en_filtro}")
else:
    print("OK: dos productos viejos del mismo nombre no se disputan el de Otter: queda como estaba")

# El caso de siempre sigue andando: UNA sola fila con ese nombre es el mismo
# producto con el código cambiado.
ruta_pug2 = excel_grupos([("PUG-A", "PUGLISI CABALLA", 7300.0, "ALMACEN", "", "8000")], "puglisi2.xlsx")
actualizacion_precios.aplicar(ruta_pug2, usuario="test")
if precio_de("PUG-A") != 7300.0 or precio_de("PUG-VIEJO") is not None:
    fallos.append("una sola fila con el nombre ya no recodifica el producto (se rompió el caso normal)")
else:
    print("OK: con una sola fila de ese nombre, el producto se recodifica como siempre")

# --------------------------------------------------------------------- #
# 14g. La columna Código guardada como NÚMERO en el Excel
# --------------------------------------------------------------------- #
# Si la planilla pasa por Google Sheets (o alguien la toca a mano), el código
# puede quedar como número y la celda llega como 7790895651755.0. Leído tal
# cual era "7790895651755.0", un código que no existe: la actualización no
# encontraba ningún producto y trataba de cambiarles el código a todos los
# que emparejaba por nombre.
from pos_core import excel_import
products.crear_producto(codigo="7790895651755", nombre="AQUARIUS POMELO 1,5L.", precio_venta=1500.0,
                        usuario="test")
# Así entrega openpyxl una celda numérica guardada con notación científica
# (7.790895651755E12), que es como la escribe Google Sheets: como float. Se
# reemplaza la lectura del archivo por esas filas, que es lo único que cambia.
ruta_num = os.path.join(base, "codigo_numerico.xlsx")
FILAS_NUM = [["Código", "Nombre", "Precio Venta", "Stock Inicial", "Proveedor", "Rubro", "Grupo",
              "Codigo interno"],
             [7790895651755.0, "AQUARIUS POMELO 1,5L.", 1700.0, 0, "", "BEBIDAS", "", 5100.0]]
_leer_original = excel_import._leer_filas
excel_import._leer_filas = lambda ruta: [list(f) for f in FILAS_NUM]
actualizacion_precios._leer_filas = excel_import._leer_filas
try:
    plan_num = actualizacion_precios.analizar(ruta_num)
    if plan_num["recodifican"] or plan_num["nuevos"] or plan_num["ambiguos"]:
        fallos.append(f"con el código guardado como número, no se reconoció el producto: "
                       f"recodifican={plan_num['recodifican']} nuevos={plan_num['nuevos']} "
                       f"ambiguos={plan_num['ambiguos']}")
    actualizacion_precios.aplicar(ruta_num, usuario="test")
    if precio_de("7790895651755") != 1700.0 or precio_de("7790895651755.0") is not None:
        fallos.append(f"con el código como número el precio no se actualizó (o se creó un producto "
                       f"'...0'): {precio_de('7790895651755')} / {precio_de('7790895651755.0')}")
    else:
        print("OK: un código guardado como número en el Excel se lee bien (sin el '.0')")
    antes_carga = cuantos_productos()
    r_carga = excel_import.cargar_masivo(ruta_num, usuario="test")
    if cuantos_productos() != antes_carga or r_carga["actualizados"] != 1:
        fallos.append(f"la carga masiva con el código como número creó un producto de más: {r_carga}")
    else:
        print("OK: la carga masiva de siempre tampoco crea un producto '...0'")
finally:
    excel_import._leer_filas = _leer_original
    actualizacion_precios._leer_filas = _leer_original
if excel_import.texto_de_codigo(" 0707273535528 ") != "0707273535528" \
        or excel_import.texto_de_codigo(1410) != "1410" or excel_import.texto_de_codigo("12-2") != "12-2":
    fallos.append("texto_de_codigo cambió un código que venía bien (ceros a la izquierda, sufijo -2)")

# --------------------------------------------------------------------- #
# 15. El buscador de la lista de filtros (con ~700 no se puede desplazar)
# --------------------------------------------------------------------- #
app2 = AppDueno()
app2.update()
total_en_lista = app2.lista_filtros.size()
if total_en_lista < 3:
    fallos.append(f"la lista de filtros abrió con {total_en_lista} entradas")
app2.buscar_filtro.insert(0, "jugos")
app2._refrescar_lista_filtros()
app2.update()
visibles = [app2.lista_filtros.get(i) for i in range(app2.lista_filtros.size())]
if not visibles or any("jugos" not in v.lower() for v in visibles):
    fallos.append(f"el buscador de filtros no filtró: {visibles}")
elif len(visibles) >= total_en_lista:
    fallos.append("el buscador no redujo la lista")
elif " de " not in app2.cuenta_filtros.cget("text"):
    fallos.append(f"el buscador no dice cuántos de cuántos: {app2.cuenta_filtros.cget('text')!r}")
else:
    print(f"OK: el buscador de filtros deja {len(visibles)} de {total_en_lista} "
           f"y lo dice en pantalla")
app2.destroy()

print()
if fallos:
    print("=== FALLOS LISTA DE PRECIOS ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== LISTA DE PRECIOS OK ===")
