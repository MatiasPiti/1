"""Umbrales de alerta (stoploss / sobre-stock) personalizados POR PRODUCTO.

Reutiliza la tabla Configuracion_Alertas: una fila con producto_codigo
NULL es el umbral global por defecto (ver scripts/setup_inicial.py); una
fila con producto_codigo puntual pisa ese default SOLO para ese producto.
pos_core.telegram_bot ya hace el join con COALESCE(específico, global) —
este módulo es la capa de escritura/lectura que usa la UI del Dueño.
"""

from pos_core.db import get_connection, transaction


def set_umbral_global(stock_minimo: int, stock_maximo: int) -> None:
    """Umbral por defecto para todos los productos que no tengan uno propio.

    OJO con el UNIQUE de producto_codigo: en SQLite cada NULL cuenta como
    distinto de cualquier otro NULL, así que un ON CONFLICT(producto_codigo)
    NUNCA se dispara para la fila global (producto_codigo IS NULL) —
    insertaría una fila global nueva cada vez, con dos efectos feos: el
    umbral global "no se guardaría" (queda ganando la fila vieja) y el
    LEFT JOIN de telegram_bot._productos_fuera_de_umbral empezaría a
    multiplicar filas, mandando una alerta repetida por cada global de más.
    Por eso se hace UPDATE explícito y solo se inserta si no existía
    ninguna (mismo criterio que scripts/setup_inicial.py).
    """
    if stock_minimo < 0 or stock_maximo < 0:
        raise ValueError("Los umbrales no pueden ser negativos")

    with transaction() as conn:
        actualizadas = conn.execute(
            "UPDATE Configuracion_Alertas SET stock_minimo = ?, stock_maximo = ?, activo = 1 "
            "WHERE producto_codigo IS NULL",
            (stock_minimo, stock_maximo),
        ).rowcount
        if not actualizadas:
            conn.execute(
                """INSERT INTO Configuracion_Alertas
                   (producto_codigo, stock_minimo, stock_maximo, activo)
                   VALUES (NULL, ?, ?, 1)""",
                (stock_minimo, stock_maximo),
            )


def obtener_umbral_global() -> dict:
    """Lo que hay configurado hoy como umbral global.

    Existe porque la pantalla del Panel abría los dos campos VACÍOS: el
    dueño no tenía forma de ver qué estaba puesto, y apretar "Guardar" sin
    escribir nada lo dejaba en 0/0 —o sea, apagaba todas las alertas— sin
    decir una palabra. Un campo vacío no puede significar "no sé" y "cero"
    a la vez.
    """
    conn = get_connection()
    fila = conn.execute(
        "SELECT stock_minimo, stock_maximo FROM Configuracion_Alertas "
        "WHERE producto_codigo IS NULL AND activo = 1 ORDER BY id LIMIT 1").fetchone()
    if not fila:
        return {"stock_minimo": 0, "stock_maximo": 0}
    return {"stock_minimo": fila["stock_minimo"], "stock_maximo": fila["stock_maximo"]}


def set_umbral_producto(codigo: str, stock_minimo: int, stock_maximo: int) -> None:
    codigo = (codigo or "").strip()
    if not codigo:
        raise ValueError("Hace falta el código del producto")
    if stock_minimo < 0 or stock_maximo < 0:
        raise ValueError("Los umbrales no pueden ser negativos")
    with transaction() as conn:
        conn.execute(
            """INSERT INTO Configuracion_Alertas (producto_codigo, stock_minimo, stock_maximo, activo)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(producto_codigo) DO UPDATE SET
                   stock_minimo = excluded.stock_minimo,
                   stock_maximo = excluded.stock_maximo,
                   activo = 1""",
            (codigo, stock_minimo, stock_maximo),
        )


def quitar_umbral_producto(codigo: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM Configuracion_Alertas WHERE producto_codigo = ?", (codigo,))


def resumen_umbrales_propios() -> list:
    """Los umbrales por producto AGRUPADOS por su valor, del grupo más
    grande al más chico.

    Sirve para separar los que se crearon solos de los que puso una persona.
    Los que se crearon solos (por el bug del cooldown) son **todos iguales
    entre sí**: llevan el valor que tenía el umbral global el día que
    salieron las alertas, y son muchos. Los que el dueño puso a mano son
    pocos y con valores variados.

    El código NO puede saberlo con certeza —nada distingue una fila de la
    otra— así que no elige: muestra los grupos con su cantidad y decide una
    persona mirando los números. Borrar configuración del cliente
    adivinando sería peor que no borrar nada.
    """
    conn = get_connection()
    filas = conn.execute(
        """SELECT stock_minimo, stock_maximo, COUNT(*) AS cantidad
           FROM Configuracion_Alertas
           WHERE producto_codigo IS NOT NULL
           GROUP BY stock_minimo, stock_maximo
           ORDER BY cantidad DESC, stock_minimo, stock_maximo"""
    ).fetchall()
    return [dict(f) for f in filas]


def quitar_umbrales_propios_con(stock_minimo: int, stock_maximo: int) -> int:
    """Saca los umbrales por producto que tengan EXACTAMENTE ese par.

    Es el bisturí: deja intacto todo lo demás, incluido el umbral global y
    los umbrales propios con cualquier otro valor.
    """
    with transaction() as conn:
        return conn.execute(
            "DELETE FROM Configuracion_Alertas "
            "WHERE producto_codigo IS NOT NULL AND stock_minimo = ? AND stock_maximo = ?",
            (int(stock_minimo), int(stock_maximo)),
        ).rowcount


def quitar_todos_los_umbrales_propios() -> int:
    """Borra TODOS los umbrales por producto y deja mandando al global.

    Existe por un problema real: mandar una alerta le creaba al producto un
    umbral propio con el valor que el global tenía ese día, así que después
    de una sola vuelta de alertas el catálogo entero quedaba "pegado" a ese
    número y cambiar el global no hacía nada. Eso ya está arreglado, pero
    las bases que pasaron por ahí se quedaron con miles de filas que nadie
    puso a mano, y sacarlas de a una desde la pantalla no es viable.

    No toca el umbral global (producto_codigo IS NULL) ni el cooldown: solo
    saca los umbrales propios. Devuelve cuántos sacó, para poder decirlo.
    """
    with transaction() as conn:
        return conn.execute(
            "DELETE FROM Configuracion_Alertas WHERE producto_codigo IS NOT NULL").rowcount


def listar_umbrales_por_producto() -> list:
    conn = get_connection()
    rows = conn.execute(
        """SELECT ca.producto_codigo AS codigo, p.nombre, ca.stock_minimo, ca.stock_maximo
           FROM Configuracion_Alertas ca
           JOIN Productos p ON p.codigo = ca.producto_codigo
           WHERE ca.producto_codigo IS NOT NULL
           ORDER BY p.nombre"""
    ).fetchall()
    return [dict(r) for r in rows]
