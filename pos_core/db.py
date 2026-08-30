"""Capa de acceso a datos: conexión SQLite + helpers transaccionales.

Todo cambio de stock o dinero pasa por `transaction()`, que abre con
BEGIN IMMEDIATE (toma el lock de escritura de entrada, en vez de esperar a
la primera escritura real) y hace ROLLBACK automático ante cualquier
excepción. Esto es lo que nos da el "casi 0% de error" pedido: o se aplica
completo, o no se aplica nada.
"""

import os
import re
import sqlite3
import threading
from contextlib import contextmanager

from pos_core.paths import db_path

_SCHEMA_CACHE = None
_local = threading.local()
_PALABRAS_RESERVADAS_SQL = {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"}


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


def _columnas_por_tabla_en_schema() -> dict:
    """Parsea sql/schema.sql y devuelve {tabla: {columna: 'definición SQL completa'}}
    a partir de cada CREATE TABLE IF NOT EXISTS. Sirve de única fuente de verdad
    para detectar columnas nuevas que una base ya instalada todavía no tiene."""
    sql_sin_comentarios = re.sub(r"--[^\n]*", "", _schema_sql())

    tablas = {}
    for match in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\)\s*;",
                              sql_sin_comentarios, re.DOTALL):
        tabla, cuerpo = match.group(1), match.group(2)

        partes, actual, nivel = [], [], 0
        for ch in cuerpo:
            if ch == "(":
                nivel += 1
            elif ch == ")":
                nivel -= 1
            if ch == "," and nivel == 0:
                partes.append("".join(actual))
                actual = []
            else:
                actual.append(ch)
        partes.append("".join(actual))

        columnas = {}
        for parte in partes:
            parte = parte.strip()
            if not parte:
                continue
            primer_token = parte.split(None, 1)[0]
            if primer_token.upper() in _PALABRAS_RESERVADAS_SQL:
                continue  # es una restricción de tabla (PRIMARY KEY, CHECK, etc.), no una columna
            columnas[primer_token] = parte
        tablas[tabla] = columnas
    return tablas


def _quitar_clausula_default(definicion: str) -> str:
    """Saca la cláusula DEFAULT ... de una definición de columna,
    respetando paréntesis anidados (p.ej. DEFAULT (strftime(...)))."""
    match = re.search(r"\bDEFAULT\b", definicion, re.IGNORECASE)
    if not match:
        return definicion
    inicio = match.start()
    resto = definicion[match.end():].lstrip()
    consumido = len(definicion[match.end():]) - len(resto)
    fin = match.end() + consumido

    if resto.startswith("("):
        nivel, i = 0, 0
        for i, ch in enumerate(resto):
            if ch == "(":
                nivel += 1
            elif ch == ")":
                nivel -= 1
                if nivel == 0:
                    break
        fin += i + 1
    else:
        # DEFAULT <literal-de-una-palabra> (número, 'texto', CURRENT_TIMESTAMP, etc.)
        token = re.match(r"\S+", resto)
        fin += len(token.group()) if token else 0

    return (definicion[:inicio] + definicion[fin:]).strip()


def aplicar_migraciones(path: str = None) -> list:
    """Pone al día una base ya instalada (potencialmente de una versión
    vieja del programa) con el esquema actual: crea tablas faltantes
    (CREATE TABLE IF NOT EXISTS, ya idempotente) y agrega columnas nuevas
    a tablas existentes vía ALTER TABLE ADD COLUMN, algo que CREATE TABLE
    IF NOT EXISTS no hace por sí solo. Pensado para el USB de
    Mantenimiento: así una instalación vieja no necesita reinstalarse
    entera para recibir cambios de estructura.

    Devuelve la lista de cambios aplicados (vacía si ya estaba al día).
    """
    conn = get_connection(path)
    conn.executescript(_schema_sql())

    cambios = []
    for tabla, columnas in _columnas_por_tabla_en_schema().items():
        existentes = {row[1] for row in conn.execute(f"PRAGMA table_info({tabla})")}
        for nombre_col, definicion in columnas.items():
            if nombre_col in existentes:
                continue
            try:
                conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {definicion}")
                cambios.append(f"{tabla}.{nombre_col} agregada")
            except sqlite3.OperationalError as e:
                if "non-constant default" not in str(e):
                    cambios.append(f"{tabla}.{nombre_col}: no se pudo agregar automáticamente ({e})")
                    continue
                # SQLite no permite ALTER TABLE ADD COLUMN con un DEFAULT
                # calculado (p.ej. strftime('now')); se agrega sin ese
                # DEFAULT en vez de dejar la columna afuera del todo — las
                # filas existentes quedan con NULL ahí, pero las filas
                # nuevas que inserte la app siguen completando el valor
                # ellas mismas.
                # sin DEFAULT no se puede sostener tampoco un NOT NULL (las
                # filas ya existentes no tendrían con qué llenarlo).
                sin_default = re.sub(r"\bNOT\s+NULL\b", "", _quitar_clausula_default(definicion),
                                      flags=re.IGNORECASE).strip()
                try:
                    conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {sin_default}")
                    cambios.append(f"{tabla}.{nombre_col} agregada (sin su valor por defecto "
                                    f"automático, que SQLite no admite agregar después de creada "
                                    f"la tabla; las filas existentes quedan con NULL ahí)")
                except sqlite3.OperationalError as e2:
                    cambios.append(f"{tabla}.{nombre_col}: no se pudo agregar automáticamente ({e2})")
    return cambios
