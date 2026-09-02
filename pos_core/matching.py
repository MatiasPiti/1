"""Emparejar por NOMBRE un producto de una factura con uno del catálogo.

Se usa cuando la factura del proveedor no trae el código de barras (pasa
seguido: muchos remitos listan solo descripción, cantidad y precio). El
camino normal sigue siendo por código — esto es el plan B.

La regla que manda acá: **equivocarse es peor que no encontrar**. Si se
empareja mal, el stock se le suma a otro producto y nadie se entera hasta
que la góndola no cierra con el sistema. Por eso:

  - La medida (1L, 500g, 2.25L...) tiene que coincidir SIEMPRE. "Coca 1L"
    y "Coca 2L" comparten casi todas las letras pero son productos
    distintos; si las dos partes declaran medida y no es la misma, se
    descarta de entrada por más parecido que sea el resto.
  - Se exige además un margen contra el segundo candidato: si dos
    productos empatan, no hay certeza, y sin certeza no se sugiere como
    seguro.
  - Nada se aplica solo: esta capa devuelve candidatos con su nivel de
    confianza y quien llama decide (en el Panel del Dueño, confirma una
    persona).

Cómo compara, en orden:
  1. Normaliza: minúsculas, sin acentos, sin puntuación, unidades
     unificadas (1lt/1 l/1litro -> 1l; 500gr/500 g -> 500g; 1kg -> 1000g).
  2. Compara palabra por palabra aceptando abreviaturas: "mayo" contra
     "mayonesa" o "coca" contra "cocacola" puntúan alto porque una es
     prefijo de la otra, que es justo como abrevian los kioscos.
  3. Exige que TODAS las palabras del nombre más corto tengan con qué
     emparejarse; que sobren palabras del lado largo es normal (la
     factura suele ser más descriptiva), que falten no.
"""

import difflib
import re
import unicodedata

# Palabras de relleno que no aportan a la identidad del producto.
_RELLENO = {"de", "del", "la", "el", "los", "las", "x", "por", "con", "y", "un", "una"}

# Todo a una unidad base: los líquidos a mililitros y los sólidos a gramos.
_EQUIV_UNIDAD = {
    "l": ("ml", 1000), "lt": ("ml", 1000), "lts": ("ml", 1000), "litro": ("ml", 1000),
    "litros": ("ml", 1000), "ml": ("ml", 1), "cc": ("ml", 1),
    "kg": ("g", 1000), "kgs": ("g", 1000), "kilo": ("g", 1000), "kilos": ("g", 1000),
    "g": ("g", 1), "gr": ("g", 1), "grs": ("g", 1), "gramo": ("g", 1), "gramos": ("g", 1),
}

_RE_MEDIDA = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(" + "|".join(sorted(_EQUIV_UNIDAD, key=len, reverse=True)) + r")\b")


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto)
                    if not unicodedata.combining(c))


def normalizar(texto: str) -> str:
    """Deja el nombre en una forma comparable: sin acentos, sin puntuación
    y en minúsculas."""
    texto = _sin_acentos((texto or "").lower())
    texto = texto.replace("&", " ")
    texto = re.sub(r"[^\w\s.,]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


_RE_NUMERO_SUELTO = re.compile(r"\b(\d+(?:[.,]\d+)?)\b")


def extraer_medidas(texto: str) -> tuple:
    """Saca los números del nombre, en dos formas:

      - `con_unidad`: medidas llevadas a unidad base — "Coca 1L" da
        {("ml", 1000.0)} y "Yerba 500gr" da {("g", 500.0)}.
      - `crudos`: los números tal como están escritos, incluidos los que
        vienen SIN unidad.

    Lo segundo importa mucho con el catálogo real de un kiosco, donde la
    presentación se anota pelada: "MAYO NATURA 500", "LA VIRGINIA CAFE
    250". Ese "500" es una medida aunque no diga "gr", y hay que poder
    aparearlo con el "500gr" que sí trae la factura.
    """
    normalizado = normalizar(texto)
    con_unidad, crudos = set(), set()

    for cantidad, unidad in _RE_MEDIDA.findall(normalizado):
        base, factor = _EQUIV_UNIDAD[unidad]
        try:
            valor = float(cantidad.replace(",", "."))
        except ValueError:
            continue
        con_unidad.add((base, round(valor * factor, 3)))
        crudos.add(round(valor, 3))

    # Números que quedaron sin unidad al lado.
    for cantidad in _RE_NUMERO_SUELTO.findall(_RE_MEDIDA.sub(" ", normalizado)):
        try:
            crudos.add(round(float(cantidad.replace(",", ".")), 3))
        except ValueError:
            continue

    return con_unidad, crudos


def _tokens(texto: str) -> list:
    """Palabras significativas: sin medidas ni números sueltos (se comparan
    aparte) ni palabras de relleno."""
    limpio = _RE_MEDIDA.sub(" ", normalizar(texto))
    limpio = _RE_NUMERO_SUELTO.sub(" ", limpio)
    limpio = re.sub(r"[.,]", " ", limpio)
    return [t for t in limpio.split() if t and t not in _RELLENO]


def _parecido_token(a: str, b: str) -> float:
    """Cuánto se parecen dos palabras sueltas (0 a 1).

    Una abreviatura cuenta casi como igual: los kioscos escriben "mayo"
    por "mayonesa" y "coca" por "cocacola". Se exige un mínimo de 3 letras
    para que un prefijo valga, si no "co" emparejaría con cualquier cosa.
    """
    if a == b:
        return 1.0
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        return 0.92
    return difflib.SequenceMatcher(None, a, b).ratio()


def puntuar(nombre_factura: str, nombre_catalogo: str) -> float:
    """Puntaje de 0 a 1 de que los dos nombres sean el mismo producto.

    Devuelve 0 (descarte total) si ambos declaran medida y no coinciden.
    """
    unidades_f, crudos_f = extraer_medidas(nombre_factura)
    unidades_c, crudos_c = extraer_medidas(nombre_catalogo)

    if unidades_f and unidades_c:
        # Los dos declaran unidad: se comparan ya normalizadas, y tienen
        # que coincidir. "Coca 1L" vs "Coca 2L" muere acá.
        if not (unidades_f & unidades_c):
            return 0.0
    elif crudos_f and crudos_c:
        # Al menos uno anota la medida pelada ("MAYO NATURA 500"): se
        # comparan los números tal como están escritos.
        if not (crudos_f & crudos_c):
            return 0.0

    tokens_f, tokens_c = _tokens(nombre_factura), _tokens(nombre_catalogo)
    if not tokens_f or not tokens_c:
        return 0.0

    # Se recorre el nombre MÁS CORTO: que a la factura le sobren palabras
    # descriptivas es normal ("mayonesa natura clasica" vs "mayo natura").
    corto, largo = (tokens_f, tokens_c) if len(tokens_f) <= len(tokens_c) else (tokens_c, tokens_f)
    disponibles = list(largo)
    puntajes = []
    for token in corto:
        if not disponibles:
            break
        mejor = max(disponibles, key=lambda otro: _parecido_token(token, otro))
        valor = _parecido_token(token, mejor)
        puntajes.append(valor)
        if valor >= 0.8:
            disponibles.remove(mejor)   # cada palabra se usa una sola vez

    if not puntajes:
        return 0.0
    score = sum(puntajes) / len(puntajes)

    # Que TODAS las palabras del nombre corto tengan con qué emparejarse:
    # con una sola que quede floja, ya no hay certeza.
    if min(puntajes) < 0.6:
        score *= 0.5

    # Palabras de más del lado largo diluyen un poco la certeza (pueden ser
    # otra variedad: "coca zero", "coca light").
    sobrantes = len(largo) - len(corto)
    if sobrantes:
        score *= max(0.75, 1 - 0.08 * sobrantes)

    # Solo uno de los dos anota alguna medida: puede ser la misma
    # presentación o no, no hay forma de saberlo. Baja la certeza, no
    # descarta.
    if bool(crudos_f) != bool(crudos_c):
        score *= 0.88

    return round(min(score, 1.0), 4)


# Umbrales. UMBRAL_SEGURO es deliberadamente exigente: por encima de eso
# se muestra como "coincidencia segura", y aun así la confirma una persona.
UMBRAL_SEGURO = 0.86
UMBRAL_POSIBLE = 0.62
MARGEN_MINIMO = 0.08     # cuánto tiene que despegarse del segundo candidato


def buscar_candidatos(nombre_factura: str, productos: list, *, maximo: int = 5) -> list:
    """productos: lista de dicts con al menos {codigo, nombre}.

    Devuelve los mejores candidatos ordenados por puntaje, cada uno como
    {codigo, nombre, score}. Los que puntúan 0 no se devuelven.
    """
    puntuados = []
    for p in productos:
        score = puntuar(nombre_factura, p.get("nombre", ""))
        if score > 0:
            puntuados.append({"codigo": p.get("codigo"), "nombre": p.get("nombre"),
                               "score": score})
    puntuados.sort(key=lambda x: (-x["score"], x["nombre"] or ""))
    return puntuados[:maximo]


def sugerir(nombre_factura: str, productos: list) -> dict:
    """Resuelve un nombre de factura contra el catálogo.

    Devuelve {confianza, candidatos, elegido}:
      - confianza "SEGURA": un único candidato claramente por encima del
        resto. Es el único caso que conviene ofrecer pre-marcado.
      - "POSIBLE": hay algo parecido, pero no alcanza para asegurarlo (o
        hay dos candidatos peleando). Lo decide una persona.
      - "NINGUNA": no apareció nada razonable; se carga a mano.
    """
    candidatos = buscar_candidatos(nombre_factura, productos)
    if not candidatos:
        return {"confianza": "NINGUNA", "candidatos": [], "elegido": None}

    mejor = candidatos[0]
    segundo = candidatos[1]["score"] if len(candidatos) > 1 else 0.0
    margen = mejor["score"] - segundo

    if mejor["score"] >= UMBRAL_SEGURO and margen >= MARGEN_MINIMO:
        return {"confianza": "SEGURA", "candidatos": candidatos, "elegido": mejor}
    if mejor["score"] >= UMBRAL_POSIBLE:
        # Incluye el caso "dos candidatos empatados": hay parecido, pero
        # justamente por el empate no hay certeza de cuál es.
        return {"confianza": "POSIBLE", "candidatos": candidatos, "elegido": None}
    return {"confianza": "NINGUNA", "candidatos": candidatos, "elegido": None}
