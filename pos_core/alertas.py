"""Umbrales de alerta de stock (stoploss / sobre-stock).

Los umbrales efectivos de un producto salen de `Configuracion_Alertas`:
primero la fila del propio producto, si no la fila global
(`producto_codigo IS NULL`). Es el mismo criterio que usa el bot de
Telegram (pos_core/telegram_bot.py), así la app y Telegram muestran
exactamente las mismas alertas.

Las filas por producto con `activo = 0` no son umbrales: el bot de Telegram
las usa solo para recordar `ultima_alerta_enviada` (cooldown), así que no
pisan el umbral global.
"""

from pos_core.db import get_connection, transaction

# La fila global se toma con un sub-SELECT en vez de un JOIN: versiones
# anteriores del panel insertaban filas globales duplicadas, y un JOIN
# repetiría cada producto una vez por duplicado. De las duplicadas vale la
# MÁS NUEVA: la más vieja es el default del setup, la nueva la que guardó el
# dueño.
_SQL_GLOBAL = ("(SELECT {col} FROM Configuracion_Alertas "
               "WHERE producto_codigo IS NULL AND activo = 1 ORDER BY id DESC LIMIT 1)")

SQL_UMBRALES_EFECTIVOS = f"""
    COALESCE(a.stock_minimo, {_SQL_GLOBAL.format(col='stock_minimo')}, 0) AS stock_minimo,
    COALESCE(a.stock_maximo, {_SQL_GLOBAL.format(col='stock_maximo')}, 0) AS stock_maximo
"""

SQL_JOIN_UMBRAL_PRODUCTO = (
    "LEFT JOIN Configuracion_Alertas a ON a.producto_codigo = p.codigo AND a.activo = 1"
)

# Chat de Telegram efectivo (mismo criterio: el del producto o el global).
SQL_CHAT_ID_EFECTIVO = (
    f"COALESCE(a.telegram_chat_id, {_SQL_GLOBAL.format(col='telegram_chat_id')}) AS chat_id"
)

# Firma de las filas que el bot de Telegram viejo insertaba solo para el
# cooldown: quedaban con los DEFAULT del esquema (5 / 0) y activo = 1, y
# pasaban a ser el umbral efectivo del producto.
_SQL_FIRMA_COOLDOWN_VIEJO = """
    producto_codigo IS NOT NULL AND ultima_alerta_enviada IS NOT NULL
    AND stock_minimo = 5 AND stock_maximo = 0 AND telegram_chat_id IS NULL
    AND activo = 1
"""


def migrar_cooldown_viejo(path: str = None) -> int:
    """Pasa a `activo = 0` las filas de cooldown que dejó el bot viejo, así
    deja de pisar el umbral global. Idempotente (la llama `init_db()` en
    cada arranque); hoy ninguna pantalla crea umbrales por producto, así que
    toda fila con esa firma es de cooldown. Devuelve cuántas filas tocó."""
    conn = get_connection(path)
    # Chequeo de solo lectura primero: en el arranque normal no hay nada
    # que migrar y no hace falta tomar el lock de escritura.
    if conn.execute(
        f"SELECT 1 FROM Configuracion_Alertas WHERE {_SQL_FIRMA_COOLDOWN_VIEJO} LIMIT 1"
    ).fetchone() is None:
        return 0
    with transaction(path) as conn:
        cur = conn.execute(
            f"UPDATE Configuracion_Alertas SET activo = 0 WHERE {_SQL_FIRMA_COOLDOWN_VIEJO}"
        )
        return cur.rowcount


def obtener_umbral_global() -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT stock_minimo, stock_maximo FROM Configuracion_Alertas "
        "WHERE producto_codigo IS NULL AND activo = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return {"stock_minimo": 0, "stock_maximo": 0}
    return {"stock_minimo": row["stock_minimo"], "stock_maximo": row["stock_maximo"]}


def guardar_umbral_global(stock_minimo: int, stock_maximo: int) -> None:
    """Upsert de la fila global. `ON CONFLICT(producto_codigo)` no sirve
    acá porque en SQLite cada NULL cuenta como distinto para el UNIQUE, así
    que se resuelve a mano, y de paso se colapsan duplicados viejos
    (se conserva la más nueva, la misma que leen los umbrales efectivos)."""
    if stock_minimo < 0 or stock_maximo < 0:
        raise ValueError("Los umbrales no pueden ser negativos")
    with transaction() as conn:
        filas = conn.execute(
            "SELECT id FROM Configuracion_Alertas WHERE producto_codigo IS NULL ORDER BY id DESC"
        ).fetchall()
        if not filas:
            conn.execute(
                """INSERT INTO Configuracion_Alertas (producto_codigo, stock_minimo, stock_maximo, activo)
                   VALUES (NULL, ?, ?, 1)""",
                (stock_minimo, stock_maximo),
            )
            return
        conservar = filas[0]["id"]
        conn.execute(
            "DELETE FROM Configuracion_Alertas WHERE producto_codigo IS NULL AND id != ?",
            (conservar,),
        )
        conn.execute(
            "UPDATE Configuracion_Alertas SET stock_minimo = ?, stock_maximo = ?, activo = 1 WHERE id = ?",
            (stock_minimo, stock_maximo, conservar),
        )


def listar_alertas() -> list:
    """Productos activos fuera de umbral. tipo = 'BAJO' (stock <= mínimo)
    o 'SOBRE' (stock >= máximo); un umbral en 0 significa "sin límite"."""
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT p.codigo, p.nombre, p.stock, {SQL_UMBRALES_EFECTIVOS}
        FROM Productos p
        {SQL_JOIN_UMBRAL_PRODUCTO}
        WHERE p.activo = 1
        """
    ).fetchall()
    alertas = []
    for r in rows:
        if r["stock_minimo"] and r["stock"] <= r["stock_minimo"]:
            tipo = "BAJO"
        elif r["stock_maximo"] and r["stock"] >= r["stock_maximo"]:
            tipo = "SOBRE"
        else:
            continue
        alertas.append({**dict(r), "tipo": tipo})
    # Primero lo más urgente: stock bajo, ordenado por cuánto falta.
    alertas.sort(key=lambda a: (a["tipo"] != "BAJO", a["stock"] - a["stock_minimo"], a["nombre"]))
    return alertas
