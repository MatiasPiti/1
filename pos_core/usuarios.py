"""Usuarios del sistema (cajeros / dueño / dev) y verificación de PIN."""

import hashlib
import hmac
import re

from pos_core.db import get_connection, transaction

# PIN del dueño: solo dígitos ASCII (el teclado numérico del celular y de la
# PC), entre 4 y 12.
_FORMATO_PIN_DUENO = re.compile(r"[0-9]{4,12}")


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


def obtener_pin_hash(nombre: str, *, rol: str):
    """pin_hash del usuario `nombre` si sigue activo y con ese rol; si no,
    None. La API lo usa para que un token deje de valer cuando al dueño le
    cambian el PIN, lo desactivan o le sacan el rol."""
    row = get_connection().execute(
        "SELECT pin_hash FROM Usuarios WHERE nombre = ? AND rol = ? AND activo = 1",
        (nombre, rol),
    ).fetchone()
    return row["pin_hash"] if row else None


def hay_usuario_activo(*, rol: str) -> bool:
    return get_connection().execute(
        "SELECT 1 FROM Usuarios WHERE rol = ? AND activo = 1 LIMIT 1", (rol,)
    ).fetchone() is not None


def validar_pin_dueno(pin: str) -> str:
    """El PIN sin espacios alrededor; ValueError si no son 4 a 12 dígitos."""
    pin = (pin or "").strip()
    if not _FORMATO_PIN_DUENO.fullmatch(pin):
        raise ValueError("El PIN tiene que tener entre 4 y 12 dígitos (solo números)")
    return pin


def definir_pin_dueno(pin: str, nombre: str = "dueño") -> str:
    """Crea el usuario `nombre` con rol DUEÑO y ese PIN, o si ya existe le
    actualiza el PIN y lo deja activo y con rol DUEÑO. Devuelve "creado" o
    "actualizado". Lanza ValueError si el PIN no son 4 a 12 dígitos.

    La usan `scripts/setup_inicial.py` y `ApiDueno --definir-pin`. Cambiar
    el PIN invalida las sesiones abiertas de los celulares."""
    pin = validar_pin_dueno(pin)
    with transaction() as conn:
        existe = conn.execute("SELECT 1 FROM Usuarios WHERE nombre = ?", (nombre,)).fetchone()
        conn.execute(
            """INSERT INTO Usuarios (nombre, pin_hash, rol, activo)
               VALUES (?, ?, 'DUEÑO', 1)
               ON CONFLICT(nombre) DO UPDATE SET
                   pin_hash = excluded.pin_hash, rol = 'DUEÑO', activo = 1""",
            (nombre, hash_pin(pin)),
        )
    return "actualizado" if existe else "creado"
