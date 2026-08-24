"""Exportación de datos de emergencia desde los USBs.

Genera un JSON en USB_X/SYNC_DATA/ con TODO lo que ese USB produjo desde
la última exportación exitosa, identificado por UUID (nunca por id
autoincremental: el id de un USB no significa nada en la DB maestra,
donde otro registro puede tener ya ese mismo número).
"""

import json
import os
from datetime import datetime

from pos_core.db import get_connection, transaction
from pos_core.paths import sync_dir


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def exportar_caja() -> str:
    """USB Caja: exporta ventas + movimientos de stock aún no incluidos
    en una exportación previa (sincronizado = 0). Idempotente: si el
    archivo anterior nunca llegó a aplicarse en el Maestro, sus registros
    se vuelven a incluir la próxima vez porque la marca de "exportado"
    solo se escribe DESPUÉS de generar el JSON con éxito.
    """
    with transaction() as conn:
        ventas = conn.execute(
            "SELECT * FROM Ventas WHERE sincronizado = 0"
        ).fetchall()
        uuids_venta = [r["uuid_unico"] for r in ventas]
        detalle = []
        if uuids_venta:
            placeholders = ",".join("?" for _ in uuids_venta)
            detalle = conn.execute(
                f"SELECT * FROM Detalle_Ventas WHERE venta_uuid IN ({placeholders})",
                uuids_venta,
            ).fetchall()
        movimientos = conn.execute(
            "SELECT * FROM Movimientos_Stock WHERE origen = 'USB_CAJA' AND sincronizado = 0"
        ).fetchall()

        payload = {
            "tipo": "export_caja",
            "generado_en": datetime.now().isoformat(timespec="milliseconds"),
            "ventas": [dict(r) for r in ventas],
            "detalle_ventas": [dict(r) for r in detalle],
            "movimientos_stock": [dict(r) for r in movimientos],
        }

        destino = os.path.join(sync_dir(), f"export_caja_{_now_tag()}.json")
        with open(destino, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

        # el archivo 'latest' fijo facilita que el módulo de conciliación
        # siempre sepa dónde buscar sin tener que ordenar por fecha
        latest = os.path.join(sync_dir(), "export_caja.json")
        with open(latest, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

        # recién ahora, con el archivo ya en disco, se marcan como exportados
        if uuids_venta:
            conn.executemany("UPDATE Ventas SET sincronizado = 1 WHERE uuid_unico = ?",
                              [(u,) for u in uuids_venta])
        conn.execute(
            "UPDATE Movimientos_Stock SET sincronizado = 1 WHERE origen = 'USB_CAJA' AND sincronizado = 0"
        )

    return destino


def exportar_dueno() -> str:
    """USB Dueño: exporta productos nuevos/modificados, movimientos de
    stock manuales/PDF/Excel y configuración de alertas, todo aún no
    marcado como exportado (sincronizado = 0)."""
    with transaction() as conn:
        productos = conn.execute(
            "SELECT * FROM Productos WHERE sincronizado = 0"
        ).fetchall()
        movimientos = conn.execute(
            "SELECT * FROM Movimientos_Stock WHERE origen = 'USB_DUENO' AND sincronizado = 0"
        ).fetchall()
        alertas = conn.execute("SELECT * FROM Configuracion_Alertas").fetchall()

        payload = {
            "tipo": "export_dueno",
            "generado_en": datetime.now().isoformat(timespec="milliseconds"),
            "productos": [dict(r) for r in productos],
            "movimientos_stock": [dict(r) for r in movimientos],
            "configuracion_alertas": [dict(r) for r in alertas],
        }

        destino = os.path.join(sync_dir(), f"export_dueño_{_now_tag()}.json")
        with open(destino, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

        latest = os.path.join(sync_dir(), "export_dueño.json")
        with open(latest, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

        conn.execute("UPDATE Productos SET sincronizado = 1 WHERE sincronizado = 0")
        conn.execute(
            "UPDATE Movimientos_Stock SET sincronizado = 1 WHERE origen = 'USB_DUENO' AND sincronizado = 0"
        )

    return destino
