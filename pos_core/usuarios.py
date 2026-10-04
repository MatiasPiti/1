"""Usuarios del sistema (cajeros / dueño / dev) y verificación de PIN."""

import hashlib
import hmac

from pos_core.db import get_connection


def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()


def verificar_pin(pin: str, *, rol: str, nombre: str = None):
    """Devuelve el nombre del usuario activo con ese rol cuyo PIN coincide,
    o None. Si se pasa `nombre`, solo se compara contra ese usuario. La
    comparación de hashes es en tiempo constante."""
    conn = get_connection()
    sql = "SELECT nombre, pin_hash FROM Usuarios WHERE rol = ? AND activo = 1"
    params = [rol]
    if nombre:
        sql += " AND nombre = ?"
        params.append(nombre)
    esperado = hash_pin(pin)
    for row in conn.execute(sql + " ORDER BY id", params).fetchall():
        if hmac.compare_digest(row["pin_hash"], esperado):
            return row["nombre"]
    return None
