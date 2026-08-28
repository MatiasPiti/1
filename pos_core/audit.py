"""Auditoría anti-robo: registro de cada línea quitada del carrito antes
de cobrar. Solo se lee desde el Panel del Dueño — la Caja nunca muestra
este historial, solo lo alimenta.

El caso que esto detecta: un cajero escanea un producto (el cliente lo
ve, lo paga en efectivo) y después, ya con el cliente afuera, lo quita
del carrito antes de cerrar el ticket para quedarse con esa plata. Cada
"Quitar línea" queda registrado con el detalle completo y quién/cuándo.
"""

import uuid
from datetime import datetime

from pos_core.db import get_connection, transaction


def registrar_linea_eliminada(*, codigo: str, nombre: str, cantidad: int, precio_unitario: float,
                               usuario: str, origen: str = "MAESTRO") -> None:
    subtotal = cantidad * precio_unitario
    with transaction() as conn:
        conn.execute(
            """INSERT INTO Lineas_Eliminadas
               (uuid_unico, producto_codigo, producto_nombre, cantidad, precio_unitario, subtotal,
                usuario, origen, fecha_hora)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), codigo, nombre, cantidad, precio_unitario, subtotal,
             usuario, origen, datetime.now().isoformat(timespec="milliseconds")),
        )


def listar_lineas_eliminadas() -> list:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM Lineas_Eliminadas ORDER BY fecha_hora DESC").fetchall()
    return [dict(r) for r in rows]


def contar_lineas_eliminadas() -> int:
    conn = get_connection()
    return conn.execute("SELECT COUNT(*) AS c FROM Lineas_Eliminadas").fetchone()["c"]
