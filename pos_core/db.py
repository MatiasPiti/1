"""Capa de acceso a datos: conexión SQLite + helpers transaccionales.

Todo cambio de stock o dinero pasa por `transaction()`, que abre con
BEGIN IMMEDIATE (toma el lock de escritura de entrada, en vez de esperar a
la primera escritura real) y hace ROLLBACK automático ante cualquier
excepción. Esto es lo que nos da el "casi 0% de error" pedido: o se aplica
completo, o no se aplica nada.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager

from pos_core.paths import db_path

_SCHEMA_CACHE = None
_local = threading.local()


def _schema_sql() -> str:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        schema_file = os.path.join(here, "sql", "schema.sql")
        with open(schema_file, "r", encoding="utf-8") as f:
            _SCHEMA_CACHE = f.read()
    return _SCHEMA_CACHE


def get_connection(path: str = None) -> sqlite3.Connection:
    """Conexión SQLite por hilo (sqlite3 no es thread-safe entre hilos
    compartiendo una misma conexión sin check_same_thread=False + locks).
    El servicio oculto de stock corre en su propio hilo/proceso, así que
    cada hilo obtiene su propia conexión."""
    path = path or db_path()
    if not hasattr(_local, "conn") or getattr(_local, "conn_path", None) != path:
        conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        _local.conn = conn
        _local.conn_path = path
    return _local.conn


def init_db(path: str = None) -> None:
    """Crea el archivo .db y todas las tablas si no existen. Segura de
    llamar en cada arranque (idempotente)."""
    conn = get_connection(path)
    conn.executescript(_schema_sql())


@contextmanager
def transaction(path: str = None):
    """Context manager transaccional.

    Uso:
        with transaction() as conn:
            conn.execute("UPDATE Productos SET stock = stock - ? WHERE id = ?", (1, pid))
            conn.execute("INSERT INTO Movimientos_Stock (...) VALUES (...)")
        # commit automático al salir sin excepciones; ROLLBACK si hubo error.
    """
    conn = get_connection(path)
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def integrity_check(path: str = None) -> str:
    conn = get_connection(path)
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return row[0] if row else "unknown"
