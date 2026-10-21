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

import hashlib
import json

from pos_core.db import get_connection, transaction

_CAMPOS_VALIDOS = {"codigo", "nombre", "marca", "proveedor", "categoria",
                    "precio_venta", "stock", "activo"}
_OPERADORES_VALIDOS = {"=", "!=", ">", ">=", "<", "<=", "LIKE"}

LARGO_MAXIMO_NOMBRE = 80
MAXIMO_CODIGOS = 5000           # lo mismo que acepta un ajuste masivo de la app del celular
_TANDA = 500                    # SQLite tiene tope de variables por sentencia (ver aplicar_filtro)


class FiltroExistenteError(ValueError):
    """Ya hay un filtro guardado con ese nombre (crear no pisa)."""


class FiltroNoExisteError(ValueError):
    """El filtro no está (lo borraron desde otro lado)."""


class FiltroCambiadoError(ValueError):
    """El filtro guardado ya no es el que se cargó: lo guardaron desde otro lado."""


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


def eliminar_filtro(nombre: str, huella_esperada: str = None) -> None:
    """Borra el filtro. Con `huella_esperada` (la manda la app del celular),
    solo si sigue siendo el que se cargó: si lo cambiaron o lo borraron desde
    otro lado, FiltroCambiadoError / FiltroNoExisteError y no se toca nada.
    Sin ella (el Panel), lo de siempre."""
    with transaction() as conn:
        if huella_esperada is not None:
            _exigir_huella(conn, nombre, huella_esperada)
        conn.execute("DELETE FROM Filtros_Guardados WHERE nombre = ?", (nombre,))


def guardar_filtro(nombre: str, definicion: dict) -> None:
    with transaction() as conn:
        conn.execute(
            """INSERT INTO Filtros_Guardados (nombre, definicion_json)
               VALUES (?, ?)
               ON CONFLICT(nombre) DO UPDATE SET definicion_json = excluded.definicion_json""",
            (nombre, json.dumps(definicion, ensure_ascii=False)),
        )


def _leer_definicion(texto) -> dict:
    try:
        definicion = json.loads(texto)
    except (TypeError, ValueError):
        definicion = None
    if not isinstance(definicion, dict):
        # Un filtro con el JSON dañado no puede tumbar la lista entera
        # de filtros: se muestra vacío y el dueño puede borrarlo.
        definicion = {"tipo": "manual", "codigos": []}
    return definicion


def listar_filtros_guardados() -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT nombre, definicion_json, creado_en FROM Filtros_Guardados ORDER BY nombre").fetchall()
    return [{"nombre": r["nombre"], "definicion": _leer_definicion(r["definicion_json"]),
             "huella": huella(r["definicion_json"]), "creado_en": r["creado_en"]} for r in rows]


def obtener_filtro(nombre: str):
    """{"nombre", "definicion", "huella", "creado_en"} o None si no existe."""
    r = get_connection().execute(
        "SELECT nombre, definicion_json, creado_en FROM Filtros_Guardados WHERE nombre = ?", (nombre,)).fetchone()
    if r is None:
        return None
    return {"nombre": r["nombre"], "definicion": _leer_definicion(r["definicion_json"]),
            "huella": huella(r["definicion_json"]), "creado_en": r["creado_en"]}


# --------------------------------------------------------------------- #
# Crear y reemplazar SIN pisar lo que guardó otro (app del celular)
# --------------------------------------------------------------------- #

def huella(definicion_json) -> str:
    """Identifica una versión de un filtro: el sha256 del JSON TAL CUAL está
    guardado. Si el Panel (o la app) lo vuelve a guardar, cambia."""
    return hashlib.sha256((definicion_json or "").encode("utf-8")).hexdigest()


def validar_nombre(nombre: str) -> str:
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("Ponele un nombre al filtro.")
    if len(nombre) > LARGO_MAXIMO_NOMBRE:
        raise ValueError(f"El nombre del filtro puede tener hasta {LARGO_MAXIMO_NOMBRE} letras.")
    if any(ord(c) < 32 or ord(c) == 127 for c in nombre):
        raise ValueError("El nombre del filtro tiene caracteres que no se pueden guardar.")
    return nombre


def _validar_codigos(conn, codigos: list) -> list:
    """Sin repetidos y en el orden en que se eligieron. Todos tienen que ser
    productos activos: un código que no existe no se guarda en silencio."""
    limpios, vistos = [], set()
    for c in codigos or []:
        c = str(c or "").strip()
        if c and c not in vistos:
            vistos.add(c)
            limpios.append(c)
    if not limpios:
        raise ValueError("Agregá al menos un producto al filtro.")
    if len(limpios) > MAXIMO_CODIGOS:
        raise ValueError(f"Un filtro puede tener hasta {MAXIMO_CODIGOS} productos.")
    activos = set()
    for i in range(0, len(limpios), _TANDA):
        tanda = limpios[i:i + _TANDA]
        marcas = ",".join("?" for _ in tanda)
        activos.update(r["codigo"] for r in conn.execute(
            f"SELECT codigo FROM Productos WHERE activo = 1 AND codigo IN ({marcas})", tanda))
    faltan = [c for c in limpios if c not in activos]
    if faltan:
        resto = f" y {len(faltan) - 5} más" if len(faltan) > 5 else ""
        raise ValueError(f"Estos productos no existen o están dados de baja: {', '.join(faltan[:5])}{resto}. "
                         f"Sacalos del filtro.")
    return limpios


def _exigir_huella(conn, nombre: str, huella_esperada: str) -> None:
    r = conn.execute("SELECT definicion_json FROM Filtros_Guardados WHERE nombre = ?", (nombre,)).fetchone()
    if r is None:
        raise FiltroNoExisteError(f"El filtro «{nombre}» ya no existe: lo borraron desde otro lado.")
    if huella(r["definicion_json"]) != huella_esperada:
        raise FiltroCambiadoError(f"El filtro «{nombre}» cambió mientras tanto (lo guardaron desde otro lado). "
                                  f"Cargalo de nuevo antes de cambiarlo.")


def crear_filtro_manual(nombre: str, codigos: list) -> dict:
    """Crea un filtro nuevo con una lista de productos elegidos a mano. A
    diferencia de guardar_filtro_manual (el Panel), NO pisa uno que ya se
    llame así: FiltroExistenteError. Devuelve {"nombre", "huella"}."""
    nombre = validar_nombre(nombre)
    with transaction() as conn:
        limpios = _validar_codigos(conn, codigos)
        if conn.execute("SELECT 1 FROM Filtros_Guardados WHERE nombre = ?", (nombre,)).fetchone():
            raise FiltroExistenteError(f"Ya hay un filtro que se llama «{nombre}». Elegí otro nombre o "
                                       f"cargalo para cambiarlo.")
        texto = json.dumps({"tipo": "manual", "codigos": limpios}, ensure_ascii=False)
        conn.execute("INSERT INTO Filtros_Guardados (nombre, definicion_json) VALUES (?, ?)", (nombre, texto))
    return {"nombre": nombre, "huella": huella(texto)}


def reemplazar_filtro_manual(nombre: str, codigos: list, huella_esperada: str) -> dict:
    """Cambia los productos de un filtro que ya existe, solo si sigue siendo
    el que se cargó (`huella_esperada`). Un filtro armado con condiciones en
    el Panel queda como lista fija. Devuelve {"nombre", "huella"}."""
    with transaction() as conn:
        _exigir_huella(conn, nombre, huella_esperada)
        limpios = _validar_codigos(conn, codigos)
        texto = json.dumps({"tipo": "manual", "codigos": limpios}, ensure_ascii=False)
        conn.execute("UPDATE Filtros_Guardados SET definicion_json = ? WHERE nombre = ?", (texto, nombre))
    return {"nombre": nombre, "huella": huella(texto)}
