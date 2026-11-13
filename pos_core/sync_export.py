"""Exportación de datos de emergencia desde los USBs.

Genera un JSON en USB_X/SYNC_DATA/ con TODO lo que ese USB produjo,
identificado por UUID (nunca por id autoincremental: el id de un USB no
significa nada en la DB maestra, donde otro registro puede tener ya ese
mismo número).

Cada exportación es COMPLETA, no "lo nuevo desde la anterior". Antes era
incremental: lo exportado se marcaba y la vez siguiente no se incluía. Pero
el USB no tiene forma de saber si el archivo anterior llegó a conciliarse
en el Maestro, y el panel de conciliación solo lista el archivo fijo
(export_caja.json), que se pisaba con lo nuevo. Resultado, verificado: con
dos días de emergencia y dos "Preparar sincronización", al Maestro
llegaban solo las ventas del segundo día — y apretar el botón dos veces
seguidas dejaba el archivo VACÍO. Las ventas quedaban en el pendrive, en un
archivo con fecha que nadie miraba.

Exportar todo siempre no duplica nada: el Maestro reconoce por UUID lo que
ya importó y lo saltea (ver pos_core/reconciliation.py).
"""

import json
import os
from datetime import datetime

from pos_core.db import get_connection, transaction
from pos_core.paths import sync_dir


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _escribir_json(ruta: str, payload: dict) -> None:
    """Escribe el JSON entero o no escribe nada.

    Va a un temporal y recién después reemplaza al archivo: si el pendrive
    se saca a mitad de la escritura, queda el archivo anterior completo y no
    uno cortado con el nombre del bueno. El fsync es para que el contenido
    esté en el pendrive, y no en la memoria de Windows, antes de decir
    "Sincronización preparada" (lo siguiente que hace el cajero es sacarlo).
    """
    temporal = ruta + ".tmp"
    with open(temporal, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporal, ruta)


def exportar_caja() -> str:
    """USB Caja: exporta TODAS las ventas de este USB y sus movimientos de
    stock (ver el encabezado del módulo: completa, no incremental).

    No viajan los movimientos ENTRADA_EXCEL: son la carga del catálogo en el
    propio pendrive ("Cargar productos (Excel)"), no mercadería que haya
    entrado al negocio. Reproducirlos en el Maestro le sumaba de nuevo el
    stock del catálogo entero.
    """
    with transaction() as conn:
        ventas = conn.execute(
            "SELECT * FROM Ventas WHERE origen = 'USB_CAJA' ORDER BY fecha_hora"
        ).fetchall()
        # Con JOIN y no con un IN (...) de todos los uuid: con muchas ventas
        # el IN pasa el tope de variables de SQLite ("too many SQL variables").
        detalle = conn.execute(
            """SELECT dv.* FROM Detalle_Ventas dv
               JOIN Ventas v ON v.uuid_unico = dv.venta_uuid
               WHERE v.origen = 'USB_CAJA' ORDER BY dv.id"""
        ).fetchall()
        movimientos = conn.execute(
            "SELECT * FROM Movimientos_Stock WHERE origen = 'USB_CAJA' AND tipo <> 'ENTRADA_EXCEL' "
            "ORDER BY id"
        ).fetchall()

        payload = {
            "tipo": "export_caja",
            "completo": True,     # trae todo lo del USB, no solo lo nuevo
            "generado_en": datetime.now().isoformat(timespec="milliseconds"),
            "ventas": [dict(r) for r in ventas],
            "detalle_ventas": [dict(r) for r in detalle],
            "movimientos_stock": [dict(r) for r in movimientos],
        }

        destino = os.path.join(sync_dir(), f"export_caja_{_now_tag()}.json")
        _escribir_json(destino, payload)

        # el archivo 'latest' fijo facilita que el módulo de conciliación
        # siempre sepa dónde buscar sin tener que ordenar por fecha
        _escribir_json(os.path.join(sync_dir(), "export_caja.json"), payload)

        # La marca queda solo como dato ("ya salió en alguna exportación"):
        # no decide qué se exporta la próxima vez.
        conn.execute("UPDATE Ventas SET sincronizado = 1 WHERE origen = 'USB_CAJA' AND sincronizado = 0")
        conn.execute(
            "UPDATE Movimientos_Stock SET sincronizado = 1 WHERE origen = 'USB_CAJA' AND sincronizado = 0"
        )

    return destino


def exportar_dueno() -> str:
    """USB Dueño: exporta los productos y TODOS los movimientos de stock de
    este USB (manuales/PDF/Excel), más la configuración de alertas.

    Los productos van en dos listas:
      - "productos": los que están pendientes (creados o cargados en este
        USB y todavía no exportados). Son los únicos sobre los que el Maestro
        compara precios, igual que siempre.
      - "productos_ya_exportados": el resto. El Maestro solo los usa para
        crear el que le falte (un producto nuevo del USB cuya exportación
        anterior nunca se concilió) y para reconocer cuáles nacieron acá.
    """
    with transaction() as conn:
        productos = conn.execute(
            "SELECT * FROM Productos WHERE sincronizado = 0"
        ).fetchall()
        ya_exportados = conn.execute(
            "SELECT * FROM Productos WHERE sincronizado <> 0"
        ).fetchall()
        movimientos = conn.execute(
            "SELECT * FROM Movimientos_Stock WHERE origen = 'USB_DUENO' ORDER BY id"
        ).fetchall()
        alertas = conn.execute("SELECT * FROM Configuracion_Alertas").fetchall()

        payload = {
            "tipo": "export_dueno",
            "completo": True,     # trae todo lo del USB, no solo lo nuevo
            "generado_en": datetime.now().isoformat(timespec="milliseconds"),
            "productos": [dict(r) for r in productos],
            "productos_ya_exportados": [dict(r) for r in ya_exportados],
            "movimientos_stock": [dict(r) for r in movimientos],
            "configuracion_alertas": [dict(r) for r in alertas],
        }

        destino = os.path.join(sync_dir(), f"export_dueño_{_now_tag()}.json")
        _escribir_json(destino, payload)
        _escribir_json(os.path.join(sync_dir(), "export_dueño.json"), payload)

        conn.execute("UPDATE Productos SET sincronizado = 1 WHERE sincronizado = 0")
        conn.execute(
            "UPDATE Movimientos_Stock SET sincronizado = 1 WHERE origen = 'USB_DUENO' AND sincronizado = 0"
        )

    return destino
