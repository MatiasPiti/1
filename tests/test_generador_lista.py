"""El envase de siempre tiene que seguir andando en la caja cuando Leo le agrega otro código.

En el sistema viejo el código interno de casi todos los productos ES su código
de barras (1768 de 2500 en septiembre de 2026): el que está impreso en el
envase. BAR_STO guarda los códigos ADEMÁS de ese. El generador del Excel
(scripts/generar_lista_precios.py) emitía solo los de BAR_STO, así que el día
que Leo le agregaba uno a un producto (otro envase, otro sabor) el código
impreso desaparecía del Excel. Al reimportar, Otter le cambiaba el código al
producto por el nuevo, y escanear el envase de siempre daba "no registrado",
mientras el sistema viejo lo seguía conociendo. Le pasó a PUGLISI CABALLA el
11/11/2026 (7790748235064 -> 7790748235095).

La prueba arma el Excel con el generador de verdad y lo aplica con la misma
función que usa el Panel. Después le pregunta a la caja si encuentra cada
código, que es lo único que le importa al cajero.
"""
import importlib.util
import os
import sys
import tempfile
import types

AQUI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AQUI)

# El generador lee .DBF con dbfread, que no está en requirements.txt a
# propósito (solo lo usa ese script). Lo que se prueba acá no lee ningún .DBF.
try:
    import dbfread  # noqa: F401
except ImportError:
    sys.modules["dbfread"] = types.SimpleNamespace(DBF=None)

_spec = importlib.util.spec_from_file_location(
    "generar_lista_precios", os.path.join(AQUI, "scripts", "generar_lista_precios.py"))
generador = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generador)

import openpyxl

fallos = []
carpeta = tempfile.mkdtemp(prefix="generador_lista_")


def producto(interno, nombre, precio):
    """Un registro de MAS_STO, con los campos que lee el generador."""
    return {"COD_STO": interno, "NOM_STO": nombre, "PRE_STO": precio, "RUB_STO": "ALMACEN",
            "SRU_STO": "GENERAL", "COS_STO": 0, "COS_SIV": 0, "POR_STO": 0, "IVA_STO": 21}


def generar_excel(productos, barras, nombre):
    filas, _colisiones = generador.armar(productos, barras)
    ruta = os.path.join(carpeta, nombre)
    generador.escribir_importacion(filas, ruta)
    ws = openpyxl.load_workbook(ruta).active
    return ruta, [dict(zip(["codigo", "nombre", "precio", "stock", "proveedor", "rubro", "grupo", "interno"], f))
                  for f in ws.iter_rows(min_row=2, values_only=True)]


# --------------------------------------------------------------------- #
# 1. Qué códigos salen en el Excel
# --------------------------------------------------------------------- #
PUGLISI, PUGLISI_NUEVO = "7790748235064", "7790748235095"
productos = [
    producto(PUGLISI, "PUGLISI CABALLA", 7300.0),                 # interno = el del envase
    producto("1722", "COLON SELECTO", 7700.0),                    # interno corto, no se escanea
    producto("7790168903925", "COLON SELECTO MALB", 4500.0),      # sin nada en BAR_STO
    producto("20001234", "PRODUCTO CON INTERNO RARO", 999.0),     # 8 cifras, verificador mal
]
barras = {
    PUGLISI: [PUGLISI_NUEVO],
    "1722": ["7790168904328", "7790168904649", "7790168904663"],
    "20001234": ["7790000000017"],
}
_, filas = generar_excel(productos, barras, "importar.xlsx")
codigos_de = lambda interno: sorted(f["codigo"] for f in filas if f["interno"] == interno)

if codigos_de(PUGLISI) != sorted([PUGLISI, PUGLISI_NUEVO]):
    fallos.append(f"PUGLISI CABALLA sale con {codigos_de(PUGLISI)}: falta el código impreso en el envase "
                   f"({PUGLISI}), y al reimportar la caja lo da por no registrado")
elif {f["grupo"] for f in filas if f["interno"] == PUGLISI} != {"PUGLISI CABALLA"}:
    fallos.append("los dos códigos de PUGLISI CABALLA no quedan en el mismo grupo de edición masiva")
else:
    print("OK: un producto cuyo código interno es su código de barras sale con ese código Y con el que se le agregó")

if "1722" in codigos_de("1722") or len(codigos_de("1722")) != 3:
    fallos.append(f"COLON SELECTO (interno 1722, que no se escanea) sale con {codigos_de('1722')}: "
                   f"tienen que ser solo sus 3 códigos de barras")
elif codigos_de("7790168903925") != ["7790168903925"]:
    fallos.append(f"un producto sin nada en BAR_STO cambió: {codigos_de('7790168903925')}")
elif codigos_de("20001234") != ["7790000000017"]:
    fallos.append(f"un código interno que no es de barras se agregó como si lo fuera: {codigos_de('20001234')}")
else:
    print("OK: un código interno que no se escanea no se agrega, y un producto sin BAR_STO queda como antes")

casos = {"7790748235064": True, "763571733462": True, "96385074": True, "7790168903925": True,
         "7790748235065": False, "1722": False, "20001234": False, "": False, "77907482350a4": False}
es_barra = getattr(generador, "es_codigo_de_barras", None)
mal = {c: e for c, e in casos.items() if es_barra is None or es_barra(c) != e}
if es_barra is None:
    fallos.append("el generador no tiene es_codigo_de_barras: no sabe qué código interno se escanea")
elif mal:
    fallos.append(f"es_codigo_de_barras se equivoca con: {mal}")
else:
    print("OK: reconoce EAN-13, UPC-A y EAN-8 por su dígito verificador")

# --------------------------------------------------------------------- #
# 2. Lo que encuentra la caja después de reimportar
# --------------------------------------------------------------------- #
from pos_core import paths
paths.set_base_override(tempfile.mkdtemp(prefix="generador_base_"))
from pos_core import actualizacion_precios, db, filters, products, sales
db.preparar_base()


def en_la_caja(codigo):
    return any(p["codigo"] == codigo for p in sales.buscar_productos(codigo))


# Así quedó la base del local el 11/11: PUGLISI ya con el código cambiado y el
# del envase perdido. Más un producto al que Leo le agregó un código DESPUÉS de
# esa importación, todavía con el suyo.
YERBA, YERBA_NUEVO = "7790387013801", "7790387013818"     # EAN-13 válidos
products.crear_producto(codigo=PUGLISI_NUEVO, nombre="PUGLISI CABALLA", precio_venta=7300.0, usuario="test")
products.crear_producto(codigo=YERBA, nombre="YERBA PLAYADITO 1KG", precio_venta=4000.0, usuario="test")
filters.guardar_filtro_manual("Yerbas de Leo", [YERBA])

ruta, _ = generar_excel(
    [producto(PUGLISI, "PUGLISI CABALLA", 7300.0), producto(YERBA, "YERBA PLAYADITO 1KG", 4100.0)],
    {PUGLISI: [PUGLISI_NUEVO], YERBA: [YERBA_NUEVO]}, "reimportar.xlsx")
resultado = actualizacion_precios.aplicar(ruta, usuario="test")
if resultado.get("errores"):
    fallos.append(f"aplicar dio errores: {resultado['errores']}")

perdidos = [c for c in (PUGLISI, PUGLISI_NUEVO, YERBA, YERBA_NUEVO) if not en_la_caja(c)]
if perdidos:
    fallos.append(f"después de reimportar, la caja da 'no registrado' con {perdidos}")
else:
    print("OK: después de reimportar la caja encuentra el envase de siempre y el código nuevo "
          "(también el de PUGLISI que se había perdido el 11/11)")

precios = {c: (sales.buscar_productos(c) or [{}])[0].get("precio_venta") for c in (YERBA, YERBA_NUEVO)}
if precios != {YERBA: 4100.0, YERBA_NUEVO: 4100.0}:
    fallos.append(f"los dos códigos de la yerba no quedaron con el precio nuevo: {precios}")
filtro = [p["codigo"] for p in filters.aplicar_filtro(
    next(f["definicion"] for f in filters.listar_filtros_guardados() if f["nombre"] == "Yerbas de Leo"))]
if filtro != [YERBA]:
    fallos.append(f"el filtro que armó Leo quedó con {filtro}: tenía que seguir con el código de siempre")
else:
    print("OK: el filtro que armó Leo sigue con el código de siempre, con el precio nuevo")

db.cerrar_conexion()
print()
if fallos:
    print("=== FALLOS GENERADOR DE LA LISTA ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== GENERADOR DE LA LISTA OK ===")
