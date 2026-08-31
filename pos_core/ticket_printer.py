"""Impresión de tickets en impresora térmica (58/80mm) desde la Caja.

Enfoque elegido: texto plano de ancho fijo enviado en modo RAW a la
impresora configurada en Windows, vía `win32print` — funciona con
cualquier impresora de tickets instalada como impresora estándar de
Windows (que es como vienen casi todas las térmicas USB/serie de mostrador),
sin necesitar el SDK propio de cada fabricante.

Si no hay impresora disponible (no es Windows, no está instalada, o
falla el envío), el ticket se guarda como archivo de texto en
`tickets/` para poder imprimirlo manualmente — nunca se pierde el
ticket ni se interrumpe la venta por un problema de impresora.
"""

import os
import re
from datetime import datetime

from pos_core.paths import tickets_dir
from pos_core.config import cargar_config

ANCHO = 32  # columnas típicas de una ticketera térmica de 58mm


def _centrar(texto: str) -> str:
    return texto[:ANCHO].center(ANCHO)


def _separador() -> str:
    return "-" * ANCHO


def formatear_ticket(venta: dict, detalle: list) -> str:
    lineas = []
    lineas.append(_centrar("OTTER"))
    lineas.append(_separador())
    fecha_legible = venta["fecha_hora"][:19].replace("T", " ")
    lineas.append(f"Fecha: {fecha_legible}")
    lineas.append(f"Ticket: {venta['uuid_unico'][:8]}")
    lineas.append(f"Cajero: {venta['usuario']}")
    lineas.append(_separador())

    for item in detalle:
        lineas.append(item["producto_nombre"].upper()[:ANCHO])
        izq = f"{item['cantidad']} x ${item['precio_unitario']:.2f}"
        der = f"${item['subtotal']:.2f}"
        relleno = max(ANCHO - len(izq) - len(der), 1)
        lineas.append(izq + " " * relleno + der)

    lineas.append(_separador())
    lineas.append(f"TOTAL: ${venta['total']:.2f}".rjust(ANCHO))
    lineas.append(f"Pago: {venta['metodo_pago']}")

    if venta.get("facturada"):
        lineas.append(_separador())
        lineas.append(f"Factura {venta.get('tipo_comprobante', '')} "
                       f"Nº {venta.get('numero_comprobante', '')}"[:ANCHO])
        lineas.append(f"CAE: {venta.get('cae', '')}"[:ANCHO])
        lineas.append(f"Vto. CAE: {venta.get('cae_vencimiento', '')}"[:ANCHO])

    lineas.append(_separador())
    lineas.append(_centrar("Gracias por su compra!"))
    lineas.append("\n\n\n")  # avance de papel para que el corte no tape texto
    return "\n".join(lineas)


def _guardar_respaldo(texto: str, venta_uuid: str) -> str:
    nombre = f"ticket_{venta_uuid[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    ruta = os.path.join(tickets_dir(), re.sub(r"[^\w.\-]", "_", nombre))
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(texto)
    return ruta


def listar_impresoras() -> list:
    """Nombres de las impresoras instaladas en Windows (locales y de red
    ya conectadas), para el selector de configuración de la Caja. Lista
    vacía en cualquier otro sistema operativo o si falla la consulta."""
    try:
        import win32print
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        return sorted(p[2] for p in win32print.EnumPrinters(flags))
    except Exception:
        return []


def imprimir_ticket(texto: str, venta_uuid: str = "", nombre_impresora: str = None) -> tuple:
    """Devuelve (enviado_a_impresora: bool, detalle: str).

    detalle es la ruta del archivo de respaldo si no se pudo imprimir, o
    el nombre de la impresora usada si sí se pudo.
    """
    try:
        import win32print

        cfg = cargar_config()
        impresora = nombre_impresora or cfg.get("impresora", "nombre", fallback="") \
            or win32print.GetDefaultPrinter()

        hprinter = win32print.OpenPrinter(impresora)
        try:
            win32print.StartDocPrinter(hprinter, 1, ("Ticket Otter", None, "RAW"))
            win32print.StartPagePrinter(hprinter)
            datos = texto.encode("cp437", errors="replace")  # charset típico de impresoras ESC/POS
            win32print.WritePrinter(hprinter, datos)
            win32print.EndPagePrinter(hprinter)
            win32print.EndDocPrinter(hprinter)
        finally:
            win32print.ClosePrinter(hprinter)
        return True, impresora
    except Exception:
        ruta = _guardar_respaldo(texto, venta_uuid)
        return False, ruta
