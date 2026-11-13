"""Reimportar la lista de precios del sistema viejo SIN romper lo que ya
está cargado en Otter.

El problema que resuelve, con un caso real: el código que Otter usa para un
producto sale del PRIMER código de barras que tiene en el sistema viejo
(ver scripts/generar_lista_precios.py). Ese código NO es estable entre dos
extracciones: si en el sistema viejo le agregaron, borraron o reordenaron
un código de barras, la lista nueva trae otro código para el MISMO
producto. La carga masiva de excel_import.py empareja solo por código, así
que en ese caso no actualiza: crea un producto nuevo al lado. El viejo
queda con el precio de antes, y como los filtros guardados del Panel son
listas de códigos, el filtro sigue apuntando al viejo. El dueño ve el
filtro armado, con el precio desactualizado, y el producto con el precio
bueno afuera de todo filtro.

Lo que hace este módulo, decidido con Matías:

  - Si el código del Excel ya existe en Otter: actualiza SOLO el precio.
    El nombre y el proveedor cargados en Otter no se tocan — el Excel trae
    el proveedor vacío, y pisarlo se lo borraba a los 4587 productos.
  - Si el código no existe pero hay UN producto con el mismo nombre:
    es el mismo producto con el código de barras cambiado. Se le actualiza
    el precio Y el código, y se arrastra ese código a los filtros
    guardados, las ofertas y los umbrales de alerta, para que todo siga
    apuntando al mismo producto. El historial (ventas, movimientos) no se
    toca: ya pasó, y guarda su propio snapshot de nombre y precio.
  - Si hay VARIOS productos con ese nombre, o el nombre no aparece:
    no se adivina. Va al informe para que lo mire una persona. Misma regla
    que el emparejado de facturas (pos_core/matching.py): equivocarse acá
    le cambia el precio al producto equivocado, y lo cobra la caja.
  - Los productos que están en Otter y no vienen en el Excel quedan
    intactos, y el informe dice cuántos son.

`analizar()` no escribe NADA: devuelve el plan para mirarlo antes.
`aplicar()` vuelve a planificar sobre la base actual y ejecuta. Los dos
usan la misma función de planificación a propósito: un ensayo que se
calcula distinto de lo que después se ejecuta es peor que no tener ensayo.
"""

import json
import os
import uuid
from datetime import datetime

from pos_core.db import get_connection, transaction
from pos_core.excel_import import _leer_filas, _mapear_columnas
from pos_core.matching import normalizar


def _clave_nombre(nombre: str) -> str:
    """Nombre llevado a una forma comparable (sin acentos, sin puntuación,
    minúsculas). Se exige coincidencia EXACTA de esta forma: no hay
    parecido ni porcentajes. El nombre sale del mismo campo del mismo .DBF
    en las dos extracciones, así que cuando el producto es el mismo el
    nombre coincide igual; y cuando no coincide, preferimos que caiga en
    el informe antes que emparejar de más."""
    return normalizar(nombre)


# Encabezados que se aceptan para la columna del grupo. Se busca acá y no
# en excel_import._ALIASES a propósito: la carga masiva de siempre no usa
# el rubro, y meterle una columna que ignora solo confunde.
_ALIAS_RUBRO = {"rubro", "categoria", "categoría", "familia"}
_ALIAS_GRUPO = {"grupo"}
_ALIAS_COD_VIEJO = {"codigo interno", "código interno", "cod interno", "codigo viejo"}


def _columna(encabezados: list, alias: set):
    """El índice de una columna opcional, o None si el Excel no la trae.
    TODAS son opcionales: un Excel sin ellas se importa igual que siempre, sin
    tocar ningún filtro. Así un archivo viejo nunca borra los grupos."""
    for idx, h in enumerate(encabezados):
        if str(h).strip().lower() in alias:
            return idx
    return None


def _leer_excel(ruta: str) -> list:
    """Devuelve [{fila, codigo, nombre, precio, rubro}] o levanta el error
    de formato. Reusa el lector y el mapeo de columnas de excel_import para
    que los dos acepten exactamente los mismos archivos."""
    filas = _leer_filas(ruta)
    encabezados, *datos = filas
    encabezados = [str(h) for h in encabezados]
    mapeo = _mapear_columnas(encabezados)
    col_rubro = _columna(encabezados, _ALIAS_RUBRO)
    col_grupo = _columna(encabezados, _ALIAS_GRUPO)
    col_cod_viejo = _columna(encabezados, _ALIAS_COD_VIEJO)

    leidas, errores = [], []
    for n, fila in enumerate(datos, start=2):
        if not fila or all(str(c).strip() == "" for c in fila):
            continue
        try:
            codigo = str(fila[mapeo["codigo"]]).strip()
            nombre = str(fila[mapeo["nombre"]]).strip()
            precio = float(fila[mapeo["precio_venta"]] or 0)
            def celda(idx):
                return str(fila[idx] or "").strip() if idx is not None and idx < len(fila) else ""
            rubro = celda(col_rubro)
            grupo = celda(col_grupo)
            cod_viejo = celda(col_cod_viejo)
            if not codigo or not nombre:
                raise ValueError("código o nombre vacío")
            if precio < 0:
                raise ValueError(f"precio de venta negativo ({precio})")
            leidas.append({"fila": n, "codigo": codigo, "nombre": nombre,
                            "precio": precio, "rubro": rubro,
                            "grupo": grupo, "cod_viejo": cod_viejo})
        except Exception as e:
            errores.append({"fila": n, "error": str(e)})
    return leidas, errores, col_rubro is not None, col_grupo is not None


def planificar(ruta: str) -> dict:
    """Decide qué va a pasar con cada fila del Excel, sin escribir nada."""
    leidas, errores, trae_rubros, trae_grupos = _leer_excel(ruta)
    conn = get_connection()
    productos = [dict(r) for r in conn.execute(
        "SELECT codigo, nombre, precio_venta, activo, categoria FROM Productos").fetchall()]

    por_codigo = {p["codigo"]: p for p in productos}
    por_nombre = {}
    for p in productos:
        # Solo los activos son candidatos a emparejarse por nombre: un
        # producto dado de baja no es "el mismo producto con otro código".
        if p["activo"]:
            por_nombre.setdefault(_clave_nombre(p["nombre"]), []).append(p["codigo"])

    actualizan, sin_cambios, recodifican, nuevos, ambiguos = [], [], [], [], []
    reclamados = set()     # productos de Otter ya tomados por alguna fila
    # De qué producto del sistema viejo (código interno) es cada fila que
    # reclamó un producto de Otter. Sirve para reconocer un código de barras
    # que el sistema viejo le AGREGÓ a un producto que Otter ya tiene.
    interno_del_reclamo = {}
    codigos_del_excel = set()

    # Un código que viene MÁS DE UNA VEZ con precios distintos: en el sistema
    # viejo ese código de barras lo usan dos productos (MILKA OREO 155G. y
    # MILKA 155G. "precio oferta" comparten uno). No hay forma de saber cuál
    # de los dos es el producto de Otter, así que NINGUNA de esas filas se
    # aplica. Antes se aplicaba la primera que aparecía —el Excel viene
    # ordenado por nombre, o sea la primera por abecedario— y solo las demás
    # iban al informe como "no se tocan": el 11/11/2026 doce productos
    # quedaron con el precio de OTRO producto, y el informe decía que no se
    # habían tocado. Si todas las filas traen el mismo precio no hay nada que
    # adivinar: se aplica una sola vez.
    precios_por_codigo = {}
    for item in leidas:
        precios_por_codigo.setdefault(item["codigo"], []).append(item["precio"])
    en_disputa = {codigo: precios for codigo, precios in precios_por_codigo.items()
                   if len(precios) > 1 and max(precios) - min(precios) >= 0.005}

    # Primera pasada: las filas cuyo código YA existe en Otter. Van
    # primero a propósito — reclaman su producto antes de que una fila de
    # la segunda pasada intente llevárselo por nombre. Sin esto, cuando el
    # código viejo de un producto pasa a ser el de otro, los dos se pelean
    # por el mismo registro.
    pendientes = []
    for item in leidas:
        if item["codigo"] in en_disputa:
            otros = ", ".join(f"${p:.2f}" for p in sorted(set(en_disputa[item["codigo"]])))
            ambiguos.append({**item, "motivo":
                              f"ese código lo usan {len(en_disputa[item['codigo']])} productos del "
                              f"sistema viejo con precios distintos ({otros}): no se sabe cuál es"})
            # El producto de Otter que tiene ese código queda apartado: no se
            # le toca el precio, y tampoco puede llevárselo por nombre una fila
            # de la segunda pasada (le cambiaría el código, que sigue vigente).
            if item["codigo"] in por_codigo:
                reclamados.add(item["codigo"])
            continue
        if item["codigo"] in codigos_del_excel:
            continue    # repetida con el mismo precio: ya se aplicó con la primera
        codigos_del_excel.add(item["codigo"])

        actual = por_codigo.get(item["codigo"])
        if actual is None:
            pendientes.append(item)
            continue
        reclamados.add(item["codigo"])
        interno_del_reclamo[item["codigo"]] = item.get("cod_viejo", "")
        destino = {"codigo": item["codigo"], "nombre": actual["nombre"],
                    "precio_viejo": actual["precio_venta"], "precio_nuevo": item["precio"],
                    "fila": item["fila"], "rubro": item.get("rubro", ""),
                    "grupo": item.get("grupo", ""), "cod_viejo": item.get("cod_viejo", ""),
                    # Solo se llena la columna Rubro si está vacía: si el dueño
                    # le puso una a mano en la pantalla de Precios, manda la suya
                    # (mismo criterio que con el nombre y el proveedor).
                    "pone_categoria": bool(item.get("rubro")) and not (actual.get("categoria") or "").strip()}
        if abs(actual["precio_venta"] - item["precio"]) < 0.005:
            sin_cambios.append(destino)
        else:
            actualizan.append(destino)

    # Segunda pasada: el código no está. ¿Es un producto que ya tenemos,
    # con el código de barras cambiado?
    #
    # Antes de emparejar por nombre se mira quién más quiere ese nombre. Si
    # dos filas se llaman igual pero son productos DISTINTOS del sistema viejo
    # (otro código interno: pasa con MATARAZZO FIDEOS, BC MERMELADA...) y en
    # Otter hay un solo producto libre con ese nombre, dárselo a la primera
    # es adivinar: se lleva el código y el PRECIO de un producto que puede no
    # ser el suyo. El 11/11/2026 PUGLISI CABALLA pasó así de $3500 a $7300.
    internos_por_nombre, filas_por_nombre = {}, {}
    for item in pendientes:
        clave = _clave_nombre(item["nombre"])
        internos_por_nombre.setdefault(clave, set()).add(item.get("cod_viejo") or "")
        filas_por_nombre[clave] = filas_por_nombre.get(clave, 0) + 1

    def _nombre_en_disputa(clave: str) -> bool:
        internos = internos_por_nombre.get(clave, set())
        # Sin código interno (un Excel de los viejos) no se puede saber si dos
        # filas del mismo nombre son el mismo producto: se toman por distintas.
        return len(internos) > 1 or ("" in internos and filas_por_nombre.get(clave, 0) > 1)

    for item in pendientes:
        candidatos = [c for c in por_nombre.get(_clave_nombre(item["nombre"]), [])
                       if c not in reclamados]
        if len(candidatos) > 1:
            ambiguos.append({**item, "motivo":
                              f"hay {len(candidatos)} productos en Otter con ese mismo nombre"})
            continue
        if not candidatos:
            # Ningún nombre libre coincide: o es un producto nuevo de
            # verdad, o su único candidato ya se lo llevó otra fila.
            tomados = por_nombre.get(_clave_nombre(item["nombre"]))
            if not tomados:
                nuevos.append(item)
            elif item.get("cod_viejo") and any(
                    interno_del_reclamo.get(c) == item["cod_viejo"] for c in tomados):
                # Es un código de barras que el sistema viejo le AGREGÓ a un
                # producto que Otter ya tiene (un sabor nuevo de AQUARIUS
                # 1,5L.): el mismo producto viejo, con su mismo precio, ya
                # está en Otter con otro de sus códigos. No hay nada que
                # adivinar, y sin crearlo la caja dice "no registrado" con un
                # código que el sistema viejo sí conocía. Pasó el 11/11/2026
                # con ~86 códigos.
                nuevos.append({**item, "agregado": True})
            else:
                ambiguos.append({**item, "motivo":
                                  "el producto con ese nombre ya lo tomó otra fila del Excel"})
            continue

        if _nombre_en_disputa(_clave_nombre(item["nombre"])):
            ambiguos.append({**item, "motivo":
                              f"hay {filas_por_nombre[_clave_nombre(item['nombre'])]} filas del Excel con ese "
                              f"nombre que en el sistema viejo son productos distintos: no se sabe cuál "
                              f"es el de Otter"})
            continue

        viejo = candidatos[0]
        reclamados.add(viejo)
        interno_del_reclamo[viejo] = item.get("cod_viejo", "")
        actual = por_codigo[viejo]
        recodifican.append({"codigo_viejo": viejo, "codigo_nuevo": item["codigo"],
                             "nombre": actual["nombre"], "precio_viejo": actual["precio_venta"],
                             "precio_nuevo": item["precio"], "fila": item["fila"],
                             "rubro": item.get("rubro", ""),
                             "grupo": item.get("grupo", ""),
                             "cod_viejo": item.get("cod_viejo", ""),
                             "pone_categoria": bool(item.get("rubro"))
                                               and not (actual.get("categoria") or "").strip()})

    no_vienen = [{"codigo": p["codigo"], "nombre": p["nombre"]}
                  for p in productos if p["activo"] and p["codigo"] not in reclamados]

    plan = {
        "actualizan": actualizan, "sin_cambios": sin_cambios, "recodifican": recodifican,
        "nuevos": nuevos, "ambiguos": ambiguos, "errores": errores,
        "no_vienen": no_vienen, "total_filas": len(leidas) + len(errores),
        "filtros": _impacto_en_filtros(recodifican, ambiguos),
        "trae_rubros": trae_rubros,
    }
    plan["trae_grupos"] = trae_grupos
    plan["grupos"] = _planificar_grupos(plan, trae_rubros, trae_grupos)
    return plan


# ---------------------------------------------------------------------- #
# Los GRUPOS: un filtro guardado por cada rubro del sistema viejo
# ---------------------------------------------------------------------- #
# Es lo que el dueño usa para cambiar precios en masa. En el sistema viejo
# son los rubros de ARC_RUB.DBF (16 en uso sobre 2500 productos); en Otter
# el equivalente son los filtros guardados del Panel.
#
# Un filtro creado desde acá lleva la marca "origen": así, en la
# importación siguiente se sabe cuál se puede volver a armar solo y cuál lo
# armó el dueño. Los del dueño NUNCA se pisan. Si él toca uno de estos
# desde el celular o el Panel, se guarda sin la marca y pasa a ser suyo —
# que es lo correcto: lo ajustó a mano.
ORIGEN_RUBRO = "rubro_sistema_viejo"
ORIGEN_GRUPO = "grupo_sistema_viejo"


def _codigos_finales(plan: dict, campo: str) -> dict:
    """Agrupa los códigos por el valor de `campo` (rubro o grupo), usando el
    código que el producto va a tener DESPUÉS de aplicar: si cambió de código
    de barras, el viejo ya no existe. Devuelve {valor: ([codigos], cod_viejo)}.

    Los ambiguos quedan afuera a propósito: no se tocan, así que meterlos en un
    grupo diría que sí se tocaron."""
    por_valor, cod_viejo_de = {}, {}
    def sumar(valor, codigo, item):
        if not valor:
            return
        por_valor.setdefault(valor, []).append(codigo)
        if item.get("cod_viejo"):
            cod_viejo_de.setdefault(valor, item["cod_viejo"])

    for item in plan["actualizan"] + plan["sin_cambios"] + plan["nuevos"]:
        sumar(item.get(campo), item["codigo"], item)
    for item in plan["recodifican"]:
        sumar(item.get(campo), item["codigo_nuevo"], item)
    return {v: (c, cod_viejo_de.get(v, "")) for v, c in por_valor.items()}


def _planificar_grupos(plan: dict, trae_rubros: bool, trae_grupos: bool) -> list:
    """Qué filtro queda y con qué productos, para los dos tipos de grupo.
    No escribe nada: solo dice qué pasaría.

    Hay dos tipos y sirven para cosas distintas:
      - por RUBRO (16): "subile 10% a toda la limpieza".
      - por PRODUCTO (683): "cambiale el precio a los 53 Saphirus Textil, que
        valen todos lo mismo". Es un producto del sistema viejo con sus N
        códigos de barras, que en Otter son N productos sueltos.
    """
    from pos_core import filters

    existentes = {f["nombre"]: f["definicion"] for f in filters.listar_filtros_guardados()}
    # Para los grupos por producto el nombre puede cambiar (lleva el precio
    # cuando dos productos viejos se llaman igual), así que se los reconoce por
    # el código interno del sistema viejo, que no cambia nunca.
    por_cod_viejo = {d.get("cod_viejo"): n for n, d in existentes.items()
                      if d.get("origen") == ORIGEN_GRUPO and d.get("cod_viejo")}

    salida = []
    pedidos = []
    if trae_rubros:
        pedidos.append(("rubro", ORIGEN_RUBRO, "rubro"))
    if trae_grupos:
        pedidos.append(("grupo", ORIGEN_GRUPO, "producto"))

    for campo, origen, etiqueta in pedidos:
        for valor, (codigos, cod_viejo) in sorted(_codigos_finales(plan, campo).items()):
            base = {"tipo": etiqueta, "rubro": valor, "productos": len(codigos),
                     "cod_viejo": cod_viejo}
            try:
                nombre = filters.validar_nombre(valor)
            except ValueError as e:
                salida.append({**base, "nombre": valor, "accion": "no_se_puede", "motivo": str(e)})
                continue
            if len(codigos) > filters.MAXIMO_CODIGOS:
                salida.append({**base, "nombre": nombre, "accion": "no_se_puede",
                                "motivo": f"tiene más de {filters.MAXIMO_CODIGOS} productos"})
                continue

            # ¿Ya existe este mismo grupo, aunque se llame distinto?
            anterior = por_cod_viejo.get(cod_viejo) if (origen == ORIGEN_GRUPO and cod_viejo) else None
            actual = existentes.get(nombre)

            if anterior and anterior != nombre:
                # Le cambió el nombre (p.ej. le entró el precio porque ahora hay
                # otro producto que se llama igual). Se renombra el que ya está
                # en vez de dejar dos.
                if actual is not None and actual.get("origen") != origen:
                    salida.append({**base, "nombre": nombre, "accion": "respeta",
                                    "motivo": f"el dueño ya tiene un filtro «{nombre}»"})
                    continue
                salida.append({**base, "nombre": nombre, "codigos": codigos,
                                "accion": "renombra", "renombra_desde": anterior, "motivo": ""})
                continue

            if actual is None:
                salida.append({**base, "nombre": nombre, "codigos": codigos,
                                "accion": "crea", "motivo": ""})
            elif actual.get("origen") == origen:
                salida.append({**base, "nombre": nombre, "codigos": codigos,
                                "accion": "actualiza", "motivo": ""})
            else:
                salida.append({**base, "nombre": nombre, "accion": "respeta",
                                "motivo": "ya hay un filtro con ese nombre que armó el dueño: "
                                           "no se toca"})
    return salida


def _impacto_en_filtros(recodifican: list, ambiguos: list) -> list:
    """Qué le pasa a cada filtro guardado. Es lo que más le importa al
    dueño: el filtro no se borra nunca, pero si un producto suyo cambia de
    código y no se arrastra, el filtro se queda con el precio viejo."""
    from pos_core import filters

    cambios = {r["codigo_viejo"]: r["codigo_nuevo"] for r in recodifican}
    salida = []
    for f in filters.listar_filtros_guardados():
        definicion = f["definicion"]
        if definicion.get("tipo") != "manual":
            salida.append({"nombre": f["nombre"], "total": None, "arrastrados": 0,
                            "sin_resolver": [], "nota": "filtro por condiciones, no por lista de códigos"})
            continue
        codigos = definicion.get("codigos", [])
        arrastrados = [c for c in codigos if c in cambios]
        # Un código del filtro que no está en el Excel ni cambia: el
        # producto queda como está. Eso no es una pérdida, pero si encima
        # su fila quedó en "ambiguos" el dueño tiene que saberlo.
        dudosos = {a["codigo"] for a in ambiguos}
        salida.append({
            "nombre": f["nombre"], "total": len(codigos),
            "arrastrados": len(arrastrados),
            "sin_resolver": [c for c in codigos if c in dudosos],
            "nota": "",
        })
    return salida


def analizar(ruta: str) -> dict:
    """Ensayo: el plan, sin tocar la base.

    El informe ya viene armado adentro del resultado a propósito: el Panel
    del Dueño Remoto recibe esto por HTTP y armarlo de su lado obligaría a
    una segunda llamada (y a que el local la tenga en la allowlist)."""
    plan = planificar(ruta)
    plan["informe"] = informe_texto(plan, ruta=os.path.basename(ruta))
    return plan


# ---------------------------------------------------------------------- #
# Arrastrar el código viejo a todo lo que lo referencia
# ---------------------------------------------------------------------- #
# El historial NO está acá a propósito: Detalle_Ventas, Movimientos_Stock
# y Lineas_Eliminadas guardan lo que pasó, con su snapshot de nombre y
# precio al momento. Reescribirles el código sería cambiar el pasado.
def _migrar_referencias(conn, viejo: str, nuevo: str, filtros_cache: dict) -> dict:
    tocado = {"filtros": [], "ofertas": 0, "umbrales": 0}

    cur = conn.execute("UPDATE Ofertas SET producto_codigo = ? WHERE producto_codigo = ?",
                        (nuevo, viejo))
    tocado["ofertas"] = cur.rowcount if cur.rowcount > 0 else 0

    # producto_codigo es UNIQUE en Configuracion_Alertas y PRIMARY KEY en
    # Alertas_Enviadas. Si quedó una fila huérfana con el código nuevo (de
    # un producto que ya no existe), el UPDATE chocaría: se borra primero.
    for tabla in ("Configuracion_Alertas", "Alertas_Enviadas"):
        conn.execute(f"DELETE FROM {tabla} WHERE producto_codigo = ?", (nuevo,))
        cur = conn.execute(f"UPDATE {tabla} SET producto_codigo = ? WHERE producto_codigo = ?",
                            (nuevo, viejo))
        if tabla == "Configuracion_Alertas":
            tocado["umbrales"] = cur.rowcount if cur.rowcount > 0 else 0

    # Los filtros guardados son el motivo de todo esto: son listas de
    # códigos, así que si el código cambia y acá no se arrastra, el filtro
    # queda señalando un producto que ya no existe y se encoge solo.
    for nombre, definicion in filtros_cache.items():
        codigos = definicion.get("codigos", [])
        if viejo not in codigos:
            continue
        # Si el filtro ya tenía el código nuevo, no se duplica la entrada.
        nuevos_codigos, visto = [], set()
        for c in codigos:
            c = nuevo if c == viejo else c
            if c not in visto:
                visto.add(c)
                nuevos_codigos.append(c)
        definicion["codigos"] = nuevos_codigos
        conn.execute("UPDATE Filtros_Guardados SET definicion_json = ? WHERE nombre = ?",
                      (json.dumps(definicion, ensure_ascii=False), nombre))
        tocado["filtros"].append(nombre)

    return tocado


def aplicar(ruta: str, *, usuario: str, origen: str = "MAESTRO") -> dict:
    """Ejecuta el plan. Vuelve a planificar sobre la base de ahora: el
    ensayo pudo haberse corrido hace rato y mientras tanto el dueño pudo
    tocar precios desde la pantalla de Precios."""
    plan = planificar(ruta)
    now = datetime.now().isoformat(timespec="milliseconds")
    sincronizado = 1 if origen == "MAESTRO" else 0

    # Los filtros se leen una sola vez y se escriben solo cuando cambian:
    # releerlos por cada producto recodificado sería recorrer la tabla
    # miles de veces.
    from pos_core import filters
    filtros_cache = {f["nombre"]: f["definicion"] for f in filters.listar_filtros_guardados()
                      if f["definicion"].get("tipo") == "manual"}

    hecho = {"precios": 0, "recodificados": 0, "creados": 0, "rubros": 0,
              "grupos_creados": [], "grupos_actualizados": [], "grupos_respetados": [],
              "grupos_renombrados": [],
              "ofertas": 0, "umbrales": 0, "filtros": set(), "errores": list(plan["errores"])}

    # Cada producto va en su propia transacción: con 4587 filas, que una
    # falle no puede tirar abajo las 4586 que estaban bien (regla 6).
    # Los "sin cambios" también pasan por acá cuando hay que ponerles el
    # rubro: el precio ya está bien, pero la columna Rubro puede estar vacía.
    for item in plan["actualizan"] + [i for i in plan["sin_cambios"] if i.get("pone_categoria")]:
        cambia_precio = abs(item["precio_viejo"] - item["precio_nuevo"]) >= 0.005
        try:
            with transaction() as conn:
                if cambia_precio:
                    conn.execute(
                        """UPDATE Productos SET precio_venta = ?, actualizado_en = ?,
                           version = version + 1 WHERE codigo = ?""",
                        (item["precio_nuevo"], now, item["codigo"]))
                    hecho["precios"] += 1
                if item.get("pone_categoria"):
                    conn.execute(
                        "UPDATE Productos SET categoria = ? WHERE codigo = ?",
                        (item["rubro"], item["codigo"]))
                    hecho["rubros"] += 1
        except Exception as e:
            hecho["errores"].append({"fila": item["fila"], "error": str(e)})

    for item in plan["recodifican"]:
        try:
            with transaction() as conn:
                conn.execute(
                    """UPDATE Productos SET codigo = ?, precio_venta = ?, actualizado_en = ?,
                       version = version + 1 WHERE codigo = ?""",
                    (item["codigo_nuevo"], item["precio_nuevo"], now, item["codigo_viejo"]))
                if item.get("pone_categoria"):
                    conn.execute("UPDATE Productos SET categoria = ? WHERE codigo = ?",
                                  (item["rubro"], item["codigo_nuevo"]))
                    hecho["rubros"] += 1
                tocado = _migrar_referencias(conn, item["codigo_viejo"], item["codigo_nuevo"],
                                              filtros_cache)
            hecho["recodificados"] += 1
            hecho["ofertas"] += tocado["ofertas"]
            hecho["umbrales"] += tocado["umbrales"]
            hecho["filtros"].update(tocado["filtros"])
        except Exception as e:
            hecho["errores"].append({"fila": item["fila"], "error": str(e)})

    for item in plan["nuevos"]:
        try:
            with transaction() as conn:
                conn.execute(
                    """INSERT INTO Productos
                       (uuid_unico, codigo, nombre, precio_venta, stock, categoria,
                        origen, sincronizado)
                       VALUES (?,?,?,?,0,?,?,?)""",
                    (str(uuid.uuid4()), item["codigo"], item["nombre"], item["precio"],
                     item.get("rubro") or None, origen, sincronizado))
                conn.execute(
                    """INSERT INTO Movimientos_Stock
                       (uuid_unico, producto_codigo, tipo, cantidad, stock_resultante,
                        motivo, usuario, origen, fecha_hora, sincronizado)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), item["codigo"], "ENTRADA_EXCEL", 0, 0,
                     "Alta por lista de precios", usuario, origen, now, sincronizado))
            hecho["creados"] += 1
            # El rubro del alta se cuenta igual que el de un UPDATE: si no,
            # el informe dice "Rubros cargados: 0" después de haber cargado
            # 2500 en el INSERT, y un informe que miente no sirve.
            if item.get("rubro"):
                hecho["rubros"] += 1
        except Exception as e:
            hecho["errores"].append({"fila": item["fila"], "error": str(e)})

    # Los grupos van ÚLTIMOS, cuando los códigos ya son los definitivos: un
    # producto que se recodificó entra al filtro con el código nuevo, no con
    # el que acaba de dejar de existir.
    _escribir_grupos(plan, hecho)

    hecho["filtros"] = sorted(hecho["filtros"])
    hecho["plan"] = plan
    hecho["informe"] = informe_texto(plan, ruta=os.path.basename(ruta), aplicado=hecho)
    return hecho


def _escribir_grupos(plan: dict, hecho: dict) -> None:
    """Crea, actualiza o renombra un filtro guardado por cada grupo. Cada uno va
    en su propia transacción: que un grupo falle no puede llevarse los otros ni,
    mucho menos, los precios que ya se escribieron bien."""
    for g in plan.get("grupos", []):
        if g["accion"] == "respeta":
            hecho["grupos_respetados"].append(g["nombre"])
            continue
        if g["accion"] == "no_se_puede":
            hecho["errores"].append({"fila": "-", "error": f"grupo «{g['rubro']}»: {g['motivo']}"})
            continue
        try:
            # Se escribe directo y no con filters.crear_filtro_manual porque ese
            # guarda la definición SIN campos extra, y acá hacen falta la marca
            # de origen (para saber mañana cuál se puede rearmar solo) y el
            # código interno del sistema viejo (para reconocer el grupo aunque
            # le haya cambiado el nombre).
            definicion = {"tipo": "manual", "codigos": g["codigos"],
                           "origen": ORIGEN_GRUPO if g["tipo"] == "producto" else ORIGEN_RUBRO}
            if g.get("cod_viejo"):
                definicion["cod_viejo"] = g["cod_viejo"]
            texto = json.dumps(definicion, ensure_ascii=False)
            with transaction() as conn:
                if g["accion"] == "renombra":
                    # El mismo grupo con otro nombre: se renombra el que ya está
                    # en vez de dejar el viejo huérfano al lado del nuevo.
                    conn.execute("DELETE FROM Filtros_Guardados WHERE nombre = ?", (g["nombre"],))
                    conn.execute(
                        "UPDATE Filtros_Guardados SET nombre = ?, definicion_json = ? WHERE nombre = ?",
                        (g["nombre"], texto, g["renombra_desde"]))
                else:
                    conn.execute(
                        """INSERT INTO Filtros_Guardados (nombre, definicion_json) VALUES (?, ?)
                           ON CONFLICT(nombre) DO UPDATE SET definicion_json = excluded.definicion_json""",
                        (g["nombre"], texto))
            if g["accion"] == "crea":
                hecho["grupos_creados"].append(g["nombre"])
            elif g["accion"] == "renombra":
                hecho["grupos_renombrados"].append(f"{g['renombra_desde']} -> {g['nombre']}")
            else:
                hecho["grupos_actualizados"].append(g["nombre"])
        except Exception as e:
            hecho["errores"].append({"fila": "-", "error": f"grupo «{g['nombre']}»: {e}"})


# ---------------------------------------------------------------------- #
# El informe
# ---------------------------------------------------------------------- #
_TOPE_DETALLE = 40   # cuántos casos se listan uno por uno antes de resumir


def _lista(titulo: str, items: list, formato) -> list:
    if not items:
        return []
    lineas = [f"{titulo} ({len(items)}):"]
    for it in items[:_TOPE_DETALLE]:
        lineas.append(f"    {formato(it)}")
    if len(items) > _TOPE_DETALLE:
        lineas.append(f"    ... y {len(items) - _TOPE_DETALLE} más")
    return lineas + [""]


def informe_texto(plan: dict, *, ruta: str = "", aplicado: dict = None) -> str:
    """El informe en castellano, para mostrarlo en pantalla y guardarlo en
    un archivo que se pueda mandar sin tipearlo."""
    L = [f"=== Lista de precios — {'RESULTADO' if aplicado else 'ENSAYO (no se tocó nada)'} "
          f"— {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===",
          f"Archivo: {ruta}" if ruta else "", ""]

    L += [f"Filas leídas del Excel: {plan['total_filas']}", ""]
    L += [f"Cambian de precio:            {len(plan['actualizan'])}"]
    L += [f"Ya tenían ese precio:         {len(plan['sin_cambios'])}"]
    L += [f"Cambian de código de barras:  {len(plan['recodifican'])}"]
    agregados = sum(1 for n in plan["nuevos"] if n.get("agregado"))
    L += [f"Productos nuevos:             {len(plan['nuevos'])}"
           + (f"  ({agregados} son códigos que le faltaban a un producto que ya estaba)"
              if agregados else "")]
    L += [f"SIN RESOLVER (mirar abajo):   {len(plan['ambiguos'])}"]
    L += [f"Filas con error en el Excel:  {len(plan['errores'])}", ""]

    if plan["recodifican"]:
        L += ["--- Cambian de código de barras ---",
               "Son productos que YA están en Otter y en el sistema viejo les cambió el código.",
               "Se les actualiza el precio y el código, y ese código se arrastra solo a los",
               "filtros guardados, las ofertas y los umbrales de alerta.", ""]
        L += _lista("Detalle", plan["recodifican"],
                     lambda r: f"{r['nombre'][:40]:<40} {r['codigo_viejo']} -> {r['codigo_nuevo']}"
                                f"   ${r['precio_viejo']:.2f} -> ${r['precio_nuevo']:.2f}")

    if plan["ambiguos"]:
        L += ["--- SIN RESOLVER: estos NO se tocan ---",
               "No se puede saber con certeza a qué producto de Otter corresponden, y",
               "equivocarse le cambia el precio al producto equivocado. Resolvelos a mano",
               "desde la pantalla de Precios del Panel.", ""]
        L += _lista("Detalle", plan["ambiguos"],
                     lambda a: f"fila {a['fila']:<6} {a['codigo']:<18} {a['nombre'][:34]:<34} {a['motivo']}")

    if plan["errores"]:
        L += _lista("--- Filas con error en el Excel ---", plan["errores"],
                     lambda e: f"fila {e['fila']}: {e['error']}")

    if plan.get("trae_rubros") or plan.get("trae_grupos"):
        L += ["--- GRUPOS para la edición masiva ---", ""]
        for tipo, titulo, explicacion in [
                ("producto", "Por producto",
                 "Un producto del sistema viejo con sus varios códigos de barras: los 53 Saphirus\n"
                 "Textil valen todos lo mismo, y acá se les cambia el precio de una."),
                ("rubro", "Por rubro",
                 "Los rubros del sistema viejo: para subirle el precio a toda la limpieza de una.")]:
            delta = [g for g in plan["grupos"] if g["tipo"] == tipo]
            if not delta:
                continue
            cuenta = {a: sum(1 for g in delta if g["accion"] == a)
                       for a in ("crea", "actualiza", "renombra", "respeta", "no_se_puede")}
            L += [f"{titulo} ({len(delta)}):", "    " + explicacion.replace("\n", "\n    "), "",
                   f"    se crean {cuenta['crea']}, se actualizan {cuenta['actualiza']}, "
                   f"se renombran {cuenta['renombra']}, se respetan {cuenta['respeta']}"
                   + (f", NO SE PUEDE {cuenta['no_se_puede']}" if cuenta["no_se_puede"] else ""), ""]
            # Se listan uno por uno los que hay que mirar: los que se crean (para
            # saber qué apareció) y los que piden atención. Los que se
            # actualizan no, que son la rutina: con 683 grupos taparían el resto.
            atencion = [g for g in delta
                         if g["accion"] in ("crea", "respeta", "no_se_puede", "renombra")]
            for g in atencion[:_TOPE_DETALLE]:
                L += [f"    {g['accion'].upper():<12} {g['nombre'][:34]:<34} "
                       f"{g['productos']:>4} productos" + (f"   -- {g['motivo']}" if g["motivo"] else "")]
            if len(atencion) > _TOPE_DETALLE:
                L += [f"    ... y {len(atencion) - _TOPE_DETALLE} más"]
            if atencion:
                L += [""]
        if not plan.get("trae_grupos"):
            L += ["Este Excel no trae la columna Grupo, así que no se arma ningún grupo por",
                   "producto. Generá la lista de nuevo con scripts/generar_lista_precios.py.", ""]
    else:
        L += ["--- GRUPOS ---",
               "Este Excel no trae las columnas Rubro ni Grupo, así que no se toca ningún grupo.",
               "Para que las traiga, generá la lista de nuevo con scripts/generar_lista_precios.py.",
               ""]

    L += ["--- Filtros guardados que ya existían ---",
           "Ningún filtro se borra nunca. Esto dice cuántos de sus productos cambian de código", ""]
    if not plan["filtros"]:
        L += ["    (no hay filtros guardados)", ""]
    for f in plan["filtros"]:
        if f["total"] is None:
            L += [f"    {f['nombre']}: {f['nota']}"]
            continue
        linea = f"    {f['nombre']}: {f['total']} productos, {f['arrastrados']} cambian de código"
        if f["sin_resolver"]:
            linea += f"  -- OJO: {len(f['sin_resolver'])} quedan sin resolver y conservan el precio viejo"
        L += [linea]
    L += [""]

    L += [f"--- Productos de Otter que NO vienen en el Excel: {len(plan['no_vienen'])} ---",
           "Quedan como están, con su precio actual. Son los que cargaste a mano o los que",
           "el dueño creó desde el Panel.", ""]
    L += _lista("Detalle", plan["no_vienen"],
                 lambda p: f"{p['codigo']:<18} {p['nombre'][:50]}")

    if aplicado:
        L += ["=== Lo que se aplicó ===",
               f"Precios actualizados:   {aplicado['precios']}",
               f"Códigos cambiados:      {aplicado['recodificados']}",
               f"Productos creados:      {aplicado['creados']}",
               f"Ofertas arrastradas:    {aplicado['ofertas']}",
               f"Umbrales arrastrados:   {aplicado['umbrales']}",
               f"Filtros actualizados:   {', '.join(aplicado['filtros']) or 'ninguno'}",
               f"Rubros cargados:        {aplicado['rubros']}",
               f"Grupos creados:         {len(aplicado['grupos_creados'])}",
               f"Grupos actualizados:    {len(aplicado['grupos_actualizados'])}",
               f"Grupos renombrados:     {len(aplicado['grupos_renombrados'])}",
               f"Grupos respetados:      {len(aplicado['grupos_respetados'])}"
               + (f"  ({', '.join(aplicado['grupos_respetados'])})" if aplicado['grupos_respetados'] else ""),
               f"Errores al aplicar:     {len(aplicado['errores'])}", ""]
        for e in aplicado["errores"][:_TOPE_DETALLE]:
            L += [f"    fila {e.get('fila', '?')}: {e['error']}"]

    L += ["=== Fin del informe ==="]
    return "\n".join(x for x in L if x is not None)
