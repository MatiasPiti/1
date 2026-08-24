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
    if "op" in nodo and nodo["op"] in ("AND", "OR"):
        partes, params = [], []
        for hijo in nodo["conditions"]:
            sql_hijo, params_hijo = _construir_sql(hijo)
            partes.append(f"({sql_hijo})")
            params.extend(params_hijo)
        return f" {nodo['op']} ".join(partes), params

    campo, operador, valor = nodo["campo"], nodo["operador"], nodo["valor"]
    if campo not in _CAMPOS_VALIDOS:
        raise ValueError(f"Campo de filtro no permitido: {campo}")
    if operador not in _OPERADORES_VALIDOS:
        raise ValueError(f"Operador de filtro no permitido: {operador}")
    if operador == "LIKE":
        return f"{campo} LIKE ?", [f"%{valor}%"]
    return f"{campo} {operador} ?", [valor]


def aplicar_filtro(definicion: dict) -> list:
    where_sql, params = _construir_sql(definicion)
    conn = get_connection()
    rows = conn.execute(
        f"SELECT * FROM Productos WHERE activo = 1 AND ({where_sql})", params
    ).fetchall()
    return [dict(r) for r in rows]


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
    return [{"nombre": r["nombre"], "definicion": json.loads(r["definicion_json"])} for r in rows]
