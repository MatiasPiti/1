"""Actualización de precios de un producto, uno por uno.

Reemplaza el ida y vuelta con planillas de Excel: el dueño escanea (o
escribe) un producto, ve en pantalla lo que tiene cargado hoy, cambia lo
que haga falta y guarda. Los campos de costo son OPCIONALES — si solo
sabe a cuánto lo quiere vender, alcanza con poner el precio final.

Sobre los cálculos: hay cuatro números encadenados,

    Costo S/IVA  --(+IVA)-->  Precio Costo  --(+% Ganancia)-->  Precio Venta Final

y de cualquier par se puede deducir el resto. Lo que decide qué se
recalcula es **qué campo tocó el dueño** (ver `recalcular`), porque es lo
único que no obliga a adivinar su intención:

  - cambia el % de ganancia  -> se recalcula el precio final
  - cambia el precio final   -> se recalcula el % de ganancia
  - cambia un costo          -> se mantiene el % y se recalcula el final
    (el caso de todos los días: subió el proveedor, quiero mantener el
    margen y saber a cuánto lo tengo que vender)
"""

from datetime import datetime

from pos_core.db import get_connection, transaction

# IVA general de Argentina. Vive acá y no en el código de pantalla para
# que el día que haya que tocarlo sea un solo lugar.
IVA_POR_DEFECTO = 21.0


def _num(valor):
    """Convierte a float lo que venga de un campo de texto.

    Devuelve None si está vacío o no es un número — 'vacío' y 'cero' son
    cosas distintas acá: un costo vacío significa "no lo sé, deducilo",
    y un costo en 0 significa "me lo regalaron".
    """
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace("$", "").replace(" ", "")
    if not texto:
        return None

    # Números escritos a mano, a la argentina: "1.500,50" y "1.500" son
    # mil quinientos, no uno con medio. Confundirlos acá haría que la
    # Caja cobre $1,50 un producto de $1.500, así que el criterio es
    # explícito y está cubierto por tests:
    #   - punto Y coma  -> manda el que esté más a la derecha
    #   - solo coma     -> es el decimal
    #   - solo punto con EXACTAMENTE 3 dígitos detrás -> separador de miles
    #   - solo punto en cualquier otro caso -> es el decimal ("0.50")
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(",", ".")
    elif "." in texto:
        entero, _, decimales = texto.rpartition(".")
        if len(decimales) == 3 and decimales.isdigit() and entero:
            texto = texto.replace(".", "")

    try:
        return float(texto)
    except ValueError:
        return None


def _redondear(valor):
    return None if valor is None else round(valor + 0.0, 2)


def recalcular(*, costo_sin_iva=None, precio_costo=None, margen=None, precio_final=None,
               cambio: str = None, iva: float = IVA_POR_DEFECTO) -> dict:
    """Completa los campos que faltan a partir de los que hay.

    `cambio` es el nombre del campo que acaba de tocar el dueño
    ("costo_sin_iva", "precio_costo", "margen" o "precio_final"); si no
    se pasa, solo se rellenan los huecos sin pisar nada.

    Nunca lanza: ante cualquier combinación imposible (dividir por cero,
    márgenes de -100%) devuelve lo que sí pudo calcular. Esta pantalla no
    puede trabarse por un número raro.
    """
    costo_sin_iva = _num(costo_sin_iva)
    precio_costo = _num(precio_costo)
    margen = _num(margen)
    precio_final = _num(precio_final)
    factor_iva = 1 + (iva / 100.0)

    # --- 1) Los dos costos son el mismo número con y sin IVA ---
    if cambio == "costo_sin_iva" and costo_sin_iva is not None:
        precio_costo = costo_sin_iva * factor_iva
    elif cambio == "precio_costo" and precio_costo is not None:
        costo_sin_iva = precio_costo / factor_iva
    elif costo_sin_iva is not None and precio_costo is None:
        precio_costo = costo_sin_iva * factor_iva
    elif precio_costo is not None and costo_sin_iva is None:
        costo_sin_iva = precio_costo / factor_iva

    # --- 2) Ganancia y precio final ---
    tiene_costo = precio_costo is not None and precio_costo > 0

    if cambio == "precio_final" and precio_final is not None:
        # El dueño fijó a cuánto lo vende: la ganancia pasa a ser
        # consecuencia de esa decisión.
        if tiene_costo:
            margen = (precio_final / precio_costo - 1) * 100
        elif margen is not None and margen > -100:
            precio_costo = precio_final / (1 + margen / 100.0)
            costo_sin_iva = precio_costo / factor_iva

    elif cambio in ("margen", "costo_sin_iva", "precio_costo"):
        # Cambió el margen o el costo: el precio final es la consecuencia.
        if tiene_costo and margen is not None and margen > -100:
            precio_final = precio_costo * (1 + margen / 100.0)

    else:
        # Sin campo disparador: solo se rellenan huecos.
        if precio_final is None and tiene_costo and margen is not None and margen > -100:
            precio_final = precio_costo * (1 + margen / 100.0)
        elif margen is None and precio_final is not None and tiene_costo:
            margen = (precio_final / precio_costo - 1) * 100
        elif precio_costo is None and precio_final is not None and margen is not None and margen > -100:
            precio_costo = precio_final / (1 + margen / 100.0)
            costo_sin_iva = precio_costo / factor_iva

    return {
        "costo_sin_iva": _redondear(costo_sin_iva),
        "precio_costo": _redondear(precio_costo),
        "margen": _redondear(margen),
        "precio_final": _redondear(precio_final),
    }


def buscar_para_precios(termino: str) -> dict:
    """Busca UN producto para editarle los precios.

    Devuelve {"producto": {...}|None, "candidatos": [...]}:
      - Si el término es el código exacto de un producto (lo que pasa al
        escanear), viene resuelto en "producto" y listo.
      - Si no, "candidatos" trae las coincidencias por nombre o código
        parcial para que el dueño elija de una lista.
    """
    termino = (termino or "").strip()
    if not termino:
        return {"producto": None, "candidatos": []}

    conn = get_connection()
    columnas = ("codigo, nombre, categoria, subrubro, costo_sin_iva, precio_compra, "
                "margen_ganancia, precio_venta, stock, actualizado_en")

    exacto = conn.execute(
        f"SELECT {columnas} FROM Productos WHERE activo = 1 AND codigo = ?", (termino,)
    ).fetchone()
    if exacto:
        return {"producto": dict(exacto), "candidatos": []}

    like = f"%{termino}%"
    filas = conn.execute(
        f"SELECT {columnas} FROM Productos WHERE activo = 1 "
        f"AND (codigo LIKE ? OR nombre LIKE ?) ORDER BY nombre LIMIT 50", (like, like)
    ).fetchall()
    candidatos = [dict(f) for f in filas]

    # Una sola coincidencia es tan buena como el código exacto: se
    # resuelve sola en vez de obligar a elegir de una lista de uno.
    if len(candidatos) == 1:
        return {"producto": candidatos[0], "candidatos": []}
    return {"producto": None, "candidatos": candidatos}


def obtener_para_precios(codigo: str) -> dict:
    """El producto tal cual está guardado hoy, para llenar la pantalla."""
    conn = get_connection()
    fila = conn.execute(
        "SELECT codigo, nombre, categoria, subrubro, costo_sin_iva, precio_compra, "
        "margen_ganancia, precio_venta, stock, actualizado_en "
        "FROM Productos WHERE activo = 1 AND codigo = ?", ((codigo or "").strip(),)
    ).fetchone()
    if not fila:
        raise ValueError(f"No existe un producto activo con código '{codigo}'.")
    return dict(fila)


def actualizar_precios(*, codigo: str, nombre: str = None, categoria: str = None,
                        subrubro: str = None, costo_sin_iva=None, precio_costo=None,
                        margen=None, precio_final=None, usuario: str = "dueño",
                        origen: str = "MAESTRO") -> dict:
    """Guarda los cambios de UN producto y devuelve cómo quedó.

    El precio final es lo único obligatorio: es el número con el que
    cobra la Caja. El resto (costos, margen, rubro, subrubro) se guarda
    si vino, y si no queda como estaba.
    """
    codigo = (codigo or "").strip()
    if not codigo:
        raise ValueError("Hace falta el código del producto.")

    precio_final = _num(precio_final)
    if precio_final is None:
        raise ValueError("El precio de venta final es obligatorio.")
    if precio_final < 0:
        raise ValueError("El precio de venta no puede ser negativo.")

    nombre = (nombre or "").strip()
    ahora = datetime.now().isoformat(timespec="milliseconds")

    with transaction() as conn:
        actual = conn.execute(
            "SELECT nombre, categoria, subrubro, costo_sin_iva, precio_compra, margen_ganancia "
            "FROM Productos WHERE codigo = ? AND activo = 1", (codigo,)
        ).fetchone()
        if not actual:
            raise ValueError(f"No existe un producto activo con código '{codigo}'.")

        # Lo que no vino se deja como estaba: esta pantalla nunca tiene
        # que borrar un dato por omisión.
        nuevo_nombre = nombre or actual["nombre"]
        nueva_categoria = categoria.strip() if isinstance(categoria, str) and categoria.strip() else actual["categoria"]
        nuevo_subrubro = subrubro.strip() if isinstance(subrubro, str) and subrubro.strip() else actual["subrubro"]
        nuevo_costo_sin_iva = _num(costo_sin_iva)
        nuevo_precio_costo = _num(precio_costo)
        nuevo_margen = _num(margen)

        conn.execute(
            """UPDATE Productos
               SET nombre = ?, categoria = ?, subrubro = ?, costo_sin_iva = ?,
                   precio_compra = ?, margen_ganancia = ?, precio_venta = ?,
                   actualizado_en = ?, version = version + 1
               WHERE codigo = ?""",
            (nuevo_nombre, nueva_categoria, nuevo_subrubro,
             nuevo_costo_sin_iva if nuevo_costo_sin_iva is not None else actual["costo_sin_iva"],
             nuevo_precio_costo if nuevo_precio_costo is not None else actual["precio_compra"],
             nuevo_margen if nuevo_margen is not None else actual["margen_ganancia"],
             precio_final, ahora, codigo),
        )

    return obtener_para_precios(codigo)


def listar_rubros() -> list:
    """Rubros ya usados, para ofrecerlos en un desplegable y que el dueño
    no tenga que escribir 'ALMACEN' distinto cada vez."""
    conn = get_connection()
    filas = conn.execute(
        "SELECT DISTINCT categoria FROM Productos "
        "WHERE activo = 1 AND categoria IS NOT NULL AND TRIM(categoria) <> '' "
        "ORDER BY categoria"
    ).fetchall()
    return [f["categoria"] for f in filas]


def listar_subrubros(categoria: str = None) -> list:
    """Subrubros ya usados; si se pasa un rubro, solo los de ese rubro."""
    conn = get_connection()
    if categoria:
        filas = conn.execute(
            "SELECT DISTINCT subrubro FROM Productos "
            "WHERE activo = 1 AND subrubro IS NOT NULL AND TRIM(subrubro) <> '' "
            "AND categoria = ? ORDER BY subrubro", (categoria,)
        ).fetchall()
    else:
        filas = conn.execute(
            "SELECT DISTINCT subrubro FROM Productos "
            "WHERE activo = 1 AND subrubro IS NOT NULL AND TRIM(subrubro) <> '' "
            "ORDER BY subrubro"
        ).fetchall()
    return [f["subrubro"] for f in filas]
