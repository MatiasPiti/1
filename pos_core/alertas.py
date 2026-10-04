"""Umbrales de alerta de stock (stoploss / sobre-stock).

Los umbrales efectivos de un producto salen de `Configuracion_Alertas`:
primero la fila del propio producto, si no la fila global
(`producto_codigo IS NULL`). Es el mismo criterio que usa el bot de
Telegram (pos_core/telegram_bot.py), así la app y Telegram muestran
exactamente las mismas alertas.
"""

from pos_core.db import get_connection, transaction

# La fila global se toma con un sub-SELECT (la más vieja) en vez de un JOIN:
# versiones anteriores del panel insertaban filas globales duplicadas, y un
# JOIN repetiría cada producto una vez por duplicado.
_SQL_GLOBAL = ("(SELECT {col} FROM Configuracion_Alertas "
               "WHERE producto_codigo IS NULL AND activo = 1 ORDER BY id LIMIT 1)")

SQL_UMBRALES_EFECTIVOS = f"""
    COALESCE(a.stock_minimo, {_SQL_GLOBAL.format(col='stock_minimo')}, 0) AS stock_minimo,
    COALESCE(a.stock_maximo, {_SQL_GLOBAL.format(col='stock_maximo')}, 0) AS stock_maximo
"""

SQL_JOIN_UMBRAL_PRODUCTO = (
    "LEFT JOIN Configuracion_Alertas a ON a.producto_codigo = p.codigo AND a.activo = 1"
)


def obtener_umbral_global() -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT stock_minimo, stock_maximo FROM Configuracion_Alertas "
        "WHERE producto_codigo IS NULL ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        return {"stock_minimo": 0, "stock_maximo": 0}
    return {"stock_minimo": row["stock_minimo"], "stock_maximo": row["stock_maximo"]}


def guardar_umbral_global(stock_minimo: int, stock_maximo: int) -> None:
    """Upsert de la fila global. `ON CONFLICT(producto_codigo)` no sirve
    acá porque en SQLite cada NULL cuenta como distinto para el UNIQUE, así
    que se resuelve a mano, y de paso se colapsan duplicados viejos."""
    if stock_minimo < 0 or stock_maximo < 0:
        raise ValueError("Los umbrales no pueden ser negativos")
    with transaction() as conn:
        filas = conn.execute(
            "SELECT id FROM Configuracion_Alertas WHERE producto_codigo IS NULL ORDER BY id"
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
