"""Parsing de facturas/remitos en PDF con pdfplumber + RegEx.

Los proveedores no tienen un formato único, así que en vez de intentar
adivinar "el" formato, definimos varios patrones de línea conocidos y
probamos cada uno; lo que no matchea ningún patrón se junta en
`lineas_no_reconocidas` para carga manual asistida (nunca se descarta
en silencio: un renglón no reconocido significa mercadería que podría
quedar sin sumar al stock).
"""

import re
from dataclasses import dataclass, field


@dataclass
class ItemFactura:
    codigo: str          # "" cuando la factura no trae código de barras
    nombre: str
    cantidad: int
    precio_compra: float


@dataclass
class ResultadoParsingPDF:
    items: list = field(default_factory=list)          # list[ItemFactura]
    lineas_no_reconocidas: list = field(default_factory=list)
    es_pdf_escaneado: bool = False   # True => probablemente imagen, sin texto extraíble


# Patrones probados en orden. Grupos con nombre: codigo, nombre, cantidad, precio
# Ejemplos que cubren:
#   "7791234567890  GALLETITAS OREO 118G     x12    $450.50"
#   "001234 | Yerba Mate 1kg | 24 | 3500,00"
#   "SKU-99  Fideos Guisero 500g   Cant: 10   P.Unit: 890.00"
_PATRONES = [
    re.compile(
        r"^(?P<codigo>\d{6,14})\s+(?P<nombre>.+?)\s+x?(?P<cantidad>\d+)\s+\$?\s*"
        r"(?P<precio>[\d.,]+)\s*$"
    ),
    re.compile(
        r"^(?P<codigo>[A-Za-z0-9\-]+)\s*\|\s*(?P<nombre>.+?)\s*\|\s*(?P<cantidad>\d+)\s*\|\s*"
        r"(?P<precio>[\d.,]+)\s*$"
    ),
    re.compile(
        r"^(?P<codigo>[A-Za-z0-9\-]+)\s+(?P<nombre>.+?)\s+Cant:\s*(?P<cantidad>\d+)\s+"
        r"P\.?\s*Unit:?\s*\$?\s*(?P<precio>[\d.,]+)\s*$",
        re.IGNORECASE,
    ),
]

# Patrones SIN código de barras: muchos remitos listan solo descripción,
# cantidad y precio. Se prueban después de los de arriba (un código, si
# está, siempre es más confiable que el nombre) y dejan `codigo` vacío para
# que quien llame resuelva por nombre — ver pos_core/matching.py.
# Ejemplos que cubren:
#   "COCA COLA 2.25 LTS          x6      $1250,00"
#   "Mayonesa Natura 500 gr    6    980,50"
_PATRONES_SIN_CODIGO = [
    re.compile(
        r"^(?P<nombre>[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][^|]*?)\s+x\s*(?P<cantidad>\d+)\s+\$?\s*"
        r"(?P<precio>[\d.,]+)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<nombre>[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][^|]*?)\s+(?P<cantidad>\d{1,4})\s+\$?\s*"
        r"(?P<precio>\d[\d.,]*)\s*$"
    ),
    re.compile(
        r"^(?P<nombre>[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][^|]*?)\s*\|\s*(?P<cantidad>\d+)\s*\|\s*\$?\s*"
        r"(?P<precio>[\d.,]+)\s*$"
    ),
]

# Encabezados, pies y renglones de totales de una factura. Importa cubrirlos
# bien: desde que se leen líneas SIN código de barras, cualquier renglón con
# forma "texto  numero  numero" puede pasar por producto — un "Descuento
# aplicado 10  500,00" se convertía en un artículo llamado "Descuento
# aplicado". No llegaba a cargarse (lo frena la pantalla de confirmación),
# pero ensucia la lista y hace perder tiempo al revisarla.
_LINEAS_A_IGNORAR = re.compile(
    r"^(total|subtotal|sub-total|iva|i\.v\.a|cuit|c\.u\.i\.t|fecha|remito|factura|"
    r"comprobante|p[aá]gina|pag\b|cliente|dom\w*|raz[oó]n|se[nñ]or|"
    r"descuento|bonificaci[oó]n|recargo|percepci[oó]n|retenci[oó]n|"
    r"neto|gravado|no gravado|exento|importe|son pesos|saldo|"
    r"transporte|flete|vencimiento|vto\b|condici[oó]n|forma de pago|"
    r"cae\b|c\.a\.e|ingresos brutos|iibb|firma|aclaraci[oó]n|"
    r"observaciones|nota|art[ií]culo\s*$|descripci[oó]n|cantidad\s+precio)\b",
    re.IGNORECASE,
)


def _normalizar_numero(texto: str) -> float:
    """Acepta tanto '3.500,00' (es-AR) como '3500.00' (en-US)."""
    texto = texto.strip()
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    return float(texto)


def _construir_item(m, *, con_codigo: bool):
    try:
        nombre = re.sub(r"\s{2,}", " ", m.group("nombre")).strip(" .-|")
        if not nombre:
            return None
        return ItemFactura(
            codigo=m.group("codigo").strip() if con_codigo else "",
            nombre=nombre,
            cantidad=int(m.group("cantidad")),
            precio_compra=_normalizar_numero(m.group("precio")),
        )
    except (ValueError, IndexError):
        return None


def _parsear_linea(linea: str):
    linea = linea.strip()
    if not linea or _LINEAS_A_IGNORAR.match(linea):
        return None

    # Primero los patrones CON código: si la factura lo trae, es la forma
    # confiable de identificar el producto y no hace falta adivinar nada.
    for patron in _PATRONES:
        m = patron.match(linea)
        if m:
            return _construir_item(m, con_codigo=True)

    # Recién si no hay código se intenta leer la línea por descripción.
    for patron in _PATRONES_SIN_CODIGO:
        m = patron.match(linea)
        if m:
            item = _construir_item(m, con_codigo=False)
            # Una descripción de una sola letra o puro número no es un
            # producto; mejor que quede como "no reconocida" y la mire una
            # persona, antes que inventar un ítem.
            if item and len(item.nombre) >= 3 and not item.nombre.isdigit():
                return item
    return None


def parsear_factura_pdf(ruta_pdf: str) -> ResultadoParsingPDF:
    import pdfplumber  # import perezoso: no hace falta si el módulo no se usa

    resultado = ResultadoParsingPDF()
    texto_total_extraido = ""

    with pdfplumber.open(ruta_pdf) as pdf:
        for pagina in pdf.pages:
            # 1) Intentar tablas estructuradas primero (más confiable que texto libre)
            for tabla in pagina.extract_tables() or []:
                for fila in tabla:
                    if not fila or len(fila) < 3:
                        continue
                    celda = " | ".join(c.strip() for c in fila if c)
                    texto_total_extraido += celda + "\n"
                    item = _parsear_linea(celda)
                    if item:
                        resultado.items.append(item)

            # 2) Texto plano línea por línea (facturas sin tabla real)
            texto = pagina.extract_text() or ""
            texto_total_extraido += texto
            for linea in texto.split("\n"):
                item = _parsear_linea(linea)
                if item:
                    resultado.items.append(item)
                elif linea.strip() and not _LINEAS_A_IGNORAR.match(linea.strip()):
                    resultado.lineas_no_reconocidas.append(linea.strip())

    if not texto_total_extraido.strip():
        # Sin texto extraíble => probablemente el PDF es una imagen escaneada.
        # No hay OCR embebido por defecto (mantiene el .exe liviano); se
        # informa al usuario para carga manual asistida, tal como pide el spec.
        resultado.es_pdf_escaneado = True

    # de-duplicar líneas no reconocidas que en realidad ya matchearon vía tabla
    codigos_reconocidos = {i.codigo for i in resultado.items if i.codigo}
    nombres_reconocidos = {i.nombre for i in resultado.items}
    resultado.lineas_no_reconocidas = [
        l for l in resultado.lineas_no_reconocidas
        if not any(c in l for c in codigos_reconocidos)
        and not any(n and n in l for n in nombres_reconocidos)
    ]
    return resultado


def resolver_por_nombre(items: list) -> list:
    """Para los ítems que quedaron SIN código (la factura no lo traía) o
    cuyo código no existe en el catálogo, busca el producto por nombre.

    Devuelve una lista paralela de dicts con lo necesario para que el
    Panel del Dueño arme la pantalla de confirmación:

        {indice, nombre_factura, cantidad, precio_compra, codigo_original,
         motivo, confianza, candidatos, elegido}

    `motivo` dice por qué hubo que buscar por nombre ("sin_codigo" o
    "codigo_desconocido"). NUNCA aplica nada por su cuenta: siempre lo
    confirma una persona, porque emparejar mal le suma el stock a otro
    producto y eso no se nota hasta que la góndola no cierra.
    """
    from pos_core import matching
    from pos_core.db import get_connection

    conn = get_connection()
    catalogo = [dict(r) for r in conn.execute(
        "SELECT codigo, nombre FROM Productos WHERE activo = 1").fetchall()]
    codigos_existentes = {p["codigo"] for p in catalogo}

    pendientes = []
    for indice, item in enumerate(items):
        codigo = (item.get("codigo") if isinstance(item, dict) else item.codigo) or ""
        nombre = item.get("nombre") if isinstance(item, dict) else item.nombre
        cantidad = item.get("cantidad") if isinstance(item, dict) else item.cantidad
        precio = item.get("precio_compra") if isinstance(item, dict) else item.precio_compra

        if codigo and codigo in codigos_existentes:
            continue   # se resuelve por código, no hace falta adivinar
        motivo = "sin_codigo" if not codigo else "codigo_desconocido"

        sugerencia = matching.sugerir(nombre, catalogo)
        pendientes.append({
            "indice": indice,
            "nombre_factura": nombre,
            "cantidad": cantidad,
            "precio_compra": precio,
            "codigo_original": codigo,
            "motivo": motivo,
            "confianza": sugerencia["confianza"],
            "candidatos": sugerencia["candidatos"],
            "elegido": sugerencia["elegido"],
        })
    return pendientes
