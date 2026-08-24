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
    codigo: str
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

_LINEAS_A_IGNORAR = re.compile(
    r"^(total|subtotal|iva|cuit|fecha|remito|factura|p[aá]gina|cliente|dom\w*|raz[oó]n)\b",
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


def _parsear_linea(linea: str):
    linea = linea.strip()
    if not linea or _LINEAS_A_IGNORAR.match(linea):
        return None
    for patron in _PATRONES:
        m = patron.match(linea)
        if m:
            try:
                return ItemFactura(
                    codigo=m.group("codigo").strip(),
                    nombre=re.sub(r"\s{2,}", " ", m.group("nombre")).strip(),
                    cantidad=int(m.group("cantidad")),
                    precio_compra=_normalizar_numero(m.group("precio")),
                )
            except (ValueError, IndexError):
                return None
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
    codigos_reconocidos = {i.codigo for i in resultado.items}
    resultado.lineas_no_reconocidas = [
        l for l in resultado.lineas_no_reconocidas
        if not any(c in l for c in codigos_reconocidos)
    ]
    return resultado
