"""Genera la lista de precios del cliente a partir de los .DBF del POS viejo (FoxPro).

Por qué existe: el dueño pide cada tanto "la lista de precios" y el catálogo real sigue
viviendo en el sistema viejo (C:\\mmarket\\bases). Este script hace siempre lo mismo, con
las mismas reglas, para que dos listas generadas en fechas distintas sean comparables.

Uso:
    python scripts/generar_lista_precios.py <carpeta_con_los_dbf> <carpeta_de_salida>

Archivos que necesita en la carpeta de entrada (el nombre puede tener prefijos, se
buscan por como terminan): MAS_STO.DBF, BAR_STO.DBF. ARC_RUB.DBF y mas_pro.DBF son
opcionales (los rubros ya vienen escritos en MAS_STO y mas_pro viene vacio).

Salida:
    Lista_Precios_El_Galpon_<fecha>.xlsx  -> para que la mire el dueno (4 hojas)
    Otter_importar_<fecha>.xlsx           -> para la carga masiva del Panel del Dueno

Reglas (no cambiarlas sin avisar, la lista se compara contra las anteriores):
  - Un producto = un registro vivo de MAS_STO. Los borrados de FoxPro se ignoran.
  - El codigo que se carga en Otter es el PRIMER codigo de barras del producto; si no
    tiene ninguno, se usa el codigo interno del sistema viejo.
  - En el Excel de importacion va una fila por cada codigo con el que el producto se
    escanea: los de BAR_STO y, si el codigo interno ES un codigo de barras (EAN/UPC con
    su digito verificador), tambien ese. Ver codigos_para_otter().
  - Si dos productos caen en el mismo codigo, al segundo se le agrega "-2". Quedan
    listados aparte para arreglarlos a mano: un codigo repetido significa que uno de los
    dos no se va a poder escanear.
  - El stock entra en 0 y el proveedor vacio, a pedido de Matias: la existencia del
    sistema viejo no es confiable (hay negativos) y el inventario fisico esta pendiente.
"""

import os
import re
import sys
from collections import defaultdict
from datetime import date

from dbfread import DBF
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

CODIGO_SIN_BARRAS = "sin codigo de barras"


def _buscar(carpeta: str, sufijo: str) -> str:
    """Encuentra el .DBF por como termina el nombre: las subidas por la web de GitHub
    a veces llegan con prefijos tipo 'bases__MAS_STO.DBF'."""
    candidatos = [
        n for n in sorted(os.listdir(carpeta))
        if n.upper().endswith(sufijo.upper())
    ]
    if not candidatos:
        raise SystemExit(f"No encontre ningun archivo que termine en {sufijo} dentro de {carpeta}")
    return os.path.join(carpeta, candidatos[0])


def _leer(ruta: str) -> list:
    # latin-1 + replace: los .DBF del sistema viejo tienen acentos y algun byte suelto;
    # que una letra salga mal es infinitamente mejor que no poder leer la lista.
    return list(DBF(ruta, encoding="latin-1", char_decode_errors="replace", load=True))


def _texto(valor) -> str:
    return ("" if valor is None else str(valor)).strip()


def _numero(valor) -> float:
    try:
        return round(float(valor or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def cargar(carpeta: str):
    productos = _leer(_buscar(carpeta, "MAS_STO.DBF"))
    barras_por_codigo = defaultdict(list)
    for fila in _leer(_buscar(carpeta, "BAR_STO.DBF")):
        interno = _texto(fila.get("COD_STO"))
        barra = _texto(fila.get("BAR_STO"))
        if interno and barra and barra not in barras_por_codigo[interno]:
            barras_por_codigo[interno].append(barra)
    return productos, barras_por_codigo


def armar(productos: list, barras_por_codigo: dict):
    """Devuelve (filas, colisiones). Cada fila es un producto ya con su codigo de Otter."""
    filas, colisiones, usados = [], [], {}
    for p in productos:
        interno = _texto(p.get("COD_STO"))
        nombre = _texto(p.get("NOM_STO"))
        if not interno or not nombre:
            continue
        barras = barras_por_codigo.get(interno, [])
        base = barras[0] if barras else interno
        codigo, n = base, 1
        while codigo in usados:
            n += 1
            codigo = f"{base}-{n}"
        usados[codigo] = True

        fila = {
            "codigo_otter": codigo,
            "interno": interno,
            "nombre": nombre,
            "detalle": _texto(p.get("DET_STO")),
            "rubro": _texto(p.get("RUB_STO")),
            "subrubro": _texto(p.get("SRU_STO")),
            "costo_siva": _numero(p.get("COS_SIV")),
            "precio_costo": _numero(p.get("COS_STO")),
            "ganancia": _numero(p.get("POR_STO")),
            "iva": _numero(p.get("IVA_STO")),
            "precio_venta": _numero(p.get("PRE_STO")),
            "barras": barras,
            "ultima_act": p.get("ULT_ACT"),
        }
        filas.append(fila)
        if n > 1:
            colisiones.append(fila)
    return filas, colisiones


# --- escritura del Excel -------------------------------------------------------------

_ENCABEZADO = Font(bold=True, color="FFFFFF")
_FONDO = PatternFill("solid", fgColor="2F5597")
_ALERTA = PatternFill("solid", fgColor="FFE0E0")


def _hoja(wb, titulo: str, columnas: list, anchos: list):
    ws = wb.create_sheet(titulo)
    ws.append(columnas)
    for celda in ws[1]:
        celda.font = _ENCABEZADO
        celda.fill = _FONDO
        celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for i, ancho in enumerate(anchos, start=1):
        ws.column_dimensions[get_column_letter(i)].width = ancho
    ws.freeze_panes = "A2"
    return ws


def escribir_lista(filas: list, colisiones: list, destino: str) -> None:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    sin_barras = [f for f in filas if not f["barras"]]
    codigos_escaneables = sum(len(f["barras"]) or 1 for f in filas)

    ws = _hoja(wb, "Resumen", ["Dato", "Valor"], [46, 20])
    for etiqueta, valor in [
        ("Fecha de esta lista", date.today().isoformat()),
        ("Productos distintos", len(filas)),
        ("Productos con codigo de barras", len(filas) - len(sin_barras)),
        ("Productos SIN codigo de barras", len(sin_barras)),
        ("Codigos escaneables en total", codigos_escaneables),
        ("Codigos repetidos (arreglar a mano)", len(colisiones)),
        ("Grupos de edicion masiva", sum(1 for f in filas if len(f["barras"]) > 1)),
        ("Origen", "MAS_STO.DBF + BAR_STO.DBF del sistema viejo"),
        ("Stock", "entra en 0: el inventario fisico esta pendiente"),
    ]:
        ws.append([etiqueta, valor])

    ws = _hoja(
        wb, "Lista de precios",
        ["Codigo para Otter", "Codigo interno viejo", "Nombre", "Rubro", "Subrubro",
         "Costo S/IVA", "Precio Costo", "% Ganancia", "% IVA", "PRECIO VENTA",
         "Codigos de barras", "Ultima actualizacion"],
        [22, 18, 42, 16, 16, 13, 13, 12, 9, 14, 34, 20],
    )
    for f in sorted(filas, key=lambda x: (x["rubro"], x["nombre"])):
        ws.append([
            f["codigo_otter"], f["interno"], f["nombre"], f["rubro"], f["subrubro"],
            f["costo_siva"], f["precio_costo"], f["ganancia"], f["iva"], f["precio_venta"],
            " / ".join(f["barras"]), f["ultima_act"],
        ])

    ws = _hoja(
        wb, "Para cargar en Otter",
        ["Codigo (uno por fila)", "Nombre", "Precio Venta", "Es codigo de barras"],
        [24, 44, 14, 20],
    )
    for f in sorted(filas, key=lambda x: x["nombre"]):
        if f["barras"]:
            for barra in f["barras"]:
                ws.append([barra, f["nombre"], f["precio_venta"], "si"])
        else:
            ws.append([f["codigo_otter"], f["nombre"], f["precio_venta"], "no (codigo interno)"])

    ws = _hoja(
        wb, "Sin codigo de barras",
        ["Codigo interno viejo", "Nombre", "Rubro", "Precio Venta"],
        [22, 44, 18, 14],
    )
    for f in sorted(sin_barras, key=lambda x: x["nombre"]):
        ws.append([f["interno"], f["nombre"], f["rubro"], f["precio_venta"]])

    if colisiones:
        ws = _hoja(
            wb, "Codigos repetidos",
            ["Codigo que quedo", "Nombre", "Precio Venta", "Que hacer"],
            [24, 44, 14, 52],
        )
        for f in colisiones:
            ws.append([
                f["codigo_otter"], f["nombre"], f["precio_venta"],
                "Dos productos comparten el codigo de barras: cambiarle el codigo a uno "
                "desde la pantalla de Precios, si no uno de los dos no se escanea.",
            ])
            for celda in ws[ws.max_row]:
                celda.fill = _ALERTA

    wb.save(destino)


def es_codigo_de_barras(codigo: str) -> bool:
    """¿Se puede escanear? EAN-8, UPC-A, EAN-13 o GTIN-14, con el digito
    verificador bien. Un codigo interno corto ("1722") no lo es."""
    if not re.fullmatch(r"\d{8}|\d{12,14}", codigo or ""):
        return False
    digitos = [int(c) for c in codigo]
    suma = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digitos[:-1])))
    return (10 - suma % 10) % 10 == digitos[-1]


def codigos_para_otter(f: dict) -> list:
    """Los codigos con los que el producto se escanea en el sistema viejo: uno
    por fila del Excel de importacion.

    En el sistema viejo el codigo interno de casi todos los productos ES su
    codigo de barras (1768 de los 2500, en septiembre de 2026): el que esta
    impreso en el envase. BAR_STO guarda los codigos ADEMAS de ese. Mientras un
    producto no tiene ninguno en BAR_STO, su codigo interno es el que se carga
    en Otter. El problema era cuando Leo le agregaba uno (otro envase, otro
    sabor): desde ese momento el Excel traia solo los de BAR_STO y el codigo
    impreso desaparecia. Al reimportar, Otter le cambiaba el codigo al producto
    por el nuevo, y escanear el envase de siempre daba "no registrado" en la
    caja, mientras el sistema viejo lo seguia conociendo. Le paso el 11/11/2026
    a PUGLISI CABALLA (7790748235064 -> 7790748235095).

    Con el interno en la lista, el envase de siempre sigue andando, y el que se
    perdio vuelve en la proxima importacion (lo crea actualizacion_precios como
    un codigo que le faltaba a un producto que ya estaba).

    Un producto sin nada en BAR_STO queda exactamente como antes (su codigo de
    Otter, con el "-2" de las colisiones si lo tiene), y un codigo interno que
    no es de barras ("1722") no se agrega: con ese no se escanea nada.
    """
    if not f["barras"]:
        return [f["codigo_otter"]]
    codigos = list(f["barras"])
    if f["interno"] not in codigos and es_codigo_de_barras(f["interno"]):
        codigos.append(f["interno"])
    return codigos


def nombre_de_grupo(filas: list) -> dict:
    """Como se llama el GRUPO de cada producto viejo, indexado por codigo interno.

    Un producto del sistema viejo es UNA entrada con varios codigos de barras:
    "SAPHIRUS TEXTIL 250ML." son 53 codigos distintos que valen todos lo mismo, y
    el dueno les cambia el precio UNA vez. En Otter cada codigo es un producto
    aparte, asi que sin el grupo hay que cambiar 53 precios a mano.

    Solo forman grupo los que tienen MAS DE UN codigo (contando el interno cuando
    se escanea, ver codigos_para_otter): uno solo no es un grupo.

    Si dos productos viejos se llaman igual (pasa 8 veces: MATARAZZO FIDEOS, BC
    MERMELADA...) se les agrega el precio al nombre, porque son productos
    distintos y juntarlos haria que un ajuste masivo les ponga el mismo precio.
    """
    from collections import Counter
    con_grupo = [f for f in filas if len(codigos_para_otter(f)) > 1]
    repetidos = {n for n, c in Counter(f["nombre"] for f in con_grupo).items() if c > 1}
    return {f["interno"]: (f'{f["nombre"]} (${f["precio_venta"]:.0f})'
                            if f["nombre"] in repetidos else f["nombre"])
            for f in con_grupo}


def escribir_importacion(filas: list, destino: str) -> None:
    """El Excel que come pos_core.excel_import: una sola hoja y los encabezados exactos.
    El importador lee wb.active, asi que no puede haber otra hoja adelante.

    UNA FILA POR CODIGO DE BARRAS, no por producto. En Otter cada codigo
    escaneable es un producto propio (asi entro el catalogo: 4587 filas), y una
    fila por producto viejo dejaria sin actualizar los ~2000 codigos restantes.

    Seis columnas que la carga masiva de siempre ignora y que usa
    pos_core.actualizacion_precios:
      - Rubro: el rubro del sistema viejo, para la columna del producto y para
        los 16 grupos por rubro.
      - Grupo: el producto del sistema viejo, para los 683 grupos de edicion
        masiva (los que valen todos lo mismo).
      - Codigo interno: el del sistema viejo. Es lo UNICO estable del grupo: el
        nombre puede llevar el precio (los 8 repetidos) y el precio cambia, asi
        que sin esto cada importacion crearia un filtro nuevo al lado del viejo.
      - Costo S/IVA, Precio Costo y % Ganancia: los de MAS_STO (COS_SIV, COS_STO,
        POR_STO), para que la pantalla de Precios abra con el costo cargado.
        Leo los veia vacios. En los .DBF de septiembre los tres son coherentes
        con el precio y todos con IVA 21 %, el mismo de Otter. Un costo en 0 va
        VACIO: en Otter 0 es "nunca se cargo", y asi no pisa uno que haya.
    """
    grupos = nombre_de_grupo(filas)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Productos"
    ws.append(["Código", "Nombre", "Precio Venta", "Stock Inicial", "Proveedor",
                "Rubro", "Grupo", "Codigo interno", "Costo S/IVA", "Precio Costo", "% Ganancia"])
    for celda in ws[1]:
        celda.font = _ENCABEZADO
        celda.fill = _FONDO
    for i, ancho in enumerate([22, 44, 14, 14, 18, 18, 44, 16, 14, 14, 12], start=1):
        ws.column_dimensions[get_column_letter(i)].width = ancho
    ws.freeze_panes = "A2"
    for f in sorted(filas, key=lambda x: x["nombre"]):
        grupo = grupos.get(f["interno"], "")
        # Sin costo no hay ganancia que mostrar: los tres van juntos o ninguno.
        if f["precio_costo"] > 0:
            costos = [f["costo_siva"] or None, f["precio_costo"], f["ganancia"]]
        else:
            costos = [None, None, None]
        for codigo in codigos_para_otter(f):
            ws.append([codigo, f["nombre"], f["precio_venta"], 0, "",
                        f["rubro"], grupo, f["interno"], *costos])
    wb.save(destino)


def main(argv: list) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    entrada, salida = argv[1], argv[2]
    os.makedirs(salida, exist_ok=True)
    productos, barras = cargar(entrada)
    filas, colisiones = armar(productos, barras)
    sello = date.today().isoformat()
    lista = os.path.join(salida, f"Lista_Precios_El_Galpon_{sello}.xlsx")
    importar = os.path.join(salida, f"Otter_importar_{sello}.xlsx")
    escribir_lista(filas, colisiones, lista)
    escribir_importacion(filas, importar)
    print(f"Productos: {len(filas)}")
    print(f"Sin codigo de barras: {sum(1 for f in filas if not f['barras'])}")
    print(f"Codigos repetidos: {len(colisiones)}")
    for f in colisiones:
        print(f"   {f['codigo_otter']}  {f['nombre']}  ${f['precio_venta']}")
    print(f"Escritos:\n  {lista}\n  {importar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
