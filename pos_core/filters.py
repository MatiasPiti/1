"""Filtros anidados guardables sobre la tabla Productos.

Un filtro es un árbol de condiciones AND/OR serializado a JSON, ej.:

{
  "op": "AND",
  "conditions": [
    {"op": "OR", "conditions": [
        {"campo": "marca", "operador": "=", "valor": "Colombia"},
        {"campo": "proveedor", "operador": "=", "valor": "XYZ"}
    ]},
    {"campo": "categoria", "operador": "=", "valor": "Café"}
  ]
}

Se traduce a SQL parametrizado (nunca concatenando el valor directo, para
evitar inyección SQL) y puede guardarse con nombre en Filtros_Guardados
para reutilizar desde el panel del dueño.
"""

import json

from pos_core.db import get_connection, transaction

_CAMPOS_VALIDOS = {"codigo", "nombre", "marca", "proveedor", "categoria",
                    "precio_venta", "stock", "activo"}
_OPERADORES_VALIDOS = {"=", "!=", ">", ">=", "<", "<=", "LIKE"}


def _construir_sql(nodo: dict) -> tuple:
    if not isinstance(nodo, dict):
        raise ValueError(f"Nodo de filtro inválido: {nodo!r}")

    if "op" in nodo and nodo["op"] in ("AND", "OR"):
        partes, params = [], []
        for hijo in nodo.get("conditions") or []:
            sql_hijo, params_hijo = _construir_sql(hijo)
            partes.append(f"({sql_hijo})")
            params.extend(params_hijo)
        if not partes:
            # Un grupo AND/OR sin condiciones adentro generaba un WHERE
            # vacío -> "AND ()" -> error de sintaxis SQL en pantalla. Se
            # rechaza con un mensaje entendible antes de llegar a la base.
            raise ValueError("El filtro tiene un grupo de condiciones vacío")
        return f" {nodo['op']} ".join(partes), params

    faltantes = {"campo", "operador", "valor"} - nodo.keys()
    if faltantes:
        raise ValueError(f"Condición de filtro incompleta, falta: {sorted(faltantes)}")
    campo, operador, valor = nodo["campo"], nodo["operador"], nodo["valor"]
    if campo not in _CAMPOS_VALIDOS:
        raise ValueError(f"Campo de filtro no permitido: {campo}")
    if operador not in _OPERADORES_VALIDOS:
        raise ValueError(f"Operador de filtro no permitido: {operador}")
    if operador == "LIKE":
        return f"{campo} LIKE ?", [f"%{valor}%"]
    return f"{campo} {operador} ?", [valor]


def aplicar_filtro(definicion: dict) -> list:
    """Aplica un filtro guardado. Soporta dos tipos de definición:
    - {"tipo": "manual", "codigos": [...]}: el dueño eligió a mano,
      producto por producto, qué entra en el filtro (sin depender de
      ningún campo en común entre ellos).
    - árbol AND/OR de condiciones (formato legado, ver _construir_sql):
      sigue funcionando para quien lo haya guardado antes.
    """
    if definicion.get("tipo") == "manual":
        codigos = definicion.get("codigos", [])
        if not codigos:
            return []
        conn = get_connection()
        # La consulta se parte en tandas: SQLite tiene un tope de variables
        # por sentencia (999 en varias compilaciones todavía en uso), y un
        # filtro manual sobre un catálogo grande lo pasa sin esfuerzo — con
        # un IN (...) de golpe reventaba con "too many SQL variables".
        por_codigo = {}
        TANDA = 500
        for i in range(0, len(codigos), TANDA):
            tanda = codigos[i:i + TANDA]
            placeholders = ",".join("?" for _ in tanda)
            rows = conn.execute(
                f"SELECT * FROM Productos WHERE activo = 1 AND codigo IN ({placeholders})", tanda
            ).fetchall()
            por_codigo.update({r["codigo"]: dict(r) for r in rows})
        # se preserva el orden en que el dueño los eligió, no el de la DB
        return [por_codigo[c] for c in codigos if c in por_codigo]

    where_sql, params = _construir_sql(definicion)
    conn = get_connection()
    rows = conn.execute(
        f"SELECT * FROM Productos WHERE activo = 1 AND ({where_sql})", params
    ).fetchall()
    return [dict(r) for r in rows]


def guardar_filtro_manual(nombre: str, codigos: list) -> None:
    """Crea/actualiza un filtro con una lista explícita de productos,
    elegidos uno por uno por el dueño desde la grilla de selección."""
    guardar_filtro(nombre, {"tipo": "manual", "codigos": list(codigos)})


def eliminar_filtro(nombre: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM Filtros_Guardados WHERE nombre = ?", (nombre,))


def guardar_filtro(nombre: str, definicion: dict) -> None:
    with transaction() as conn:
        conn.execute(
            """INSERT INTO Filtros_Guardados (nombre, definicion_json)
               VALUES (?, ?)
               ON CONFLICT(nombre) DO UPDATE SET definicion_json = excluded.definicion_json""",
            (nombre, json.dumps(definicion, ensure_ascii=False)),
        )


def listar_filtros_guardados() -> list:
    conn = get_connection()
    rows = conn.execute("SELECT nombre, definicion_json FROM Filtros_Guardados ORDER BY nombre").fetchall()
    filtros = []
    for r in rows:
        try:
            definicion = json.loads(r["definicion_json"])
        except (TypeError, ValueError):
            # Un filtro con el JSON dañado no puede tumbar la lista entera
            # de filtros: se muestra vacío y el dueño puede borrarlo.
            definicion = {"tipo": "manual", "codigos": []}
        filtros.append({"nombre": r["nombre"], "definicion": definicion})
    return filtros
