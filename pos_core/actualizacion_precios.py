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


def _leer_excel(ruta: str) -> list:
    """Devuelve [{fila, codigo, nombre, precio}] o levanta el error de
    formato. Reusa el lector y el mapeo de columnas de excel_import para
    que los dos acepten exactamente los mismos archivos."""
    filas = _leer_filas(ruta)
    encabezados, *datos = filas
    mapeo = _mapear_columnas([str(h) for h in encabezados])

    leidas, errores = [], []
    for n, fila in enumerate(datos, start=2):
        if not fila or all(str(c).strip() == "" for c in fila):
            continue
        try:
            codigo = str(fila[mapeo["codigo"]]).strip()
            nombre = str(fila[mapeo["nombre"]]).strip()
            precio = float(fila[mapeo["precio_venta"]] or 0)
            if not codigo or not nombre:
                raise ValueError("código o nombre vacío")
            if precio < 0:
                raise ValueError(f"precio de venta negativo ({precio})")
            leidas.append({"fila": n, "codigo": codigo, "nombre": nombre, "precio": precio})
        except Exception as e:
            errores.append({"fila": n, "error": str(e)})
    return leidas, errores


def planificar(ruta: str) -> dict:
    """Decide qué va a pasar con cada fila del Excel, sin escribir nada."""
    leidas, errores = _leer_excel(ruta)
    conn = get_connection()
    productos = [dict(r) for r in conn.execute(
        "SELECT codigo, nombre, precio_venta, activo FROM Productos").fetchall()]

    por_codigo = {p["codigo"]: p for p in productos}
    por_nombre = {}
    for p in productos:
        # Solo los activos son candidatos a emparejarse por nombre: un
        # producto dado de baja no es "el mismo producto con otro código".
        if p["activo"]:
            por_nombre.setdefault(_clave_nombre(p["nombre"]), []).append(p["codigo"])

    actualizan, sin_cambios, recodifican, nuevos, ambiguos = [], [], [], [], []
    reclamados = set()     # productos de Otter ya tomados por alguna fila
    codigos_del_excel = set()

    # Primera pasada: las filas cuyo código YA existe en Otter. Van
    # primero a propósito — reclaman su producto antes de que una fila de
    # la segunda pasada intente llevárselo por nombre. Sin esto, cuando el
    # código viejo de un producto pasa a ser el de otro, los dos se pelean
    # por el mismo registro.
    pendientes = []
    for item in leidas:
        if item["codigo"] in codigos_del_excel:
            ambiguos.append({**item, "motivo": "el código aparece más de una vez en el Excel"})
            continue
        codigos_del_excel.add(item["codigo"])

        actual = por_codigo.get(item["codigo"])
        if actual is None:
            pendientes.append(item)
            continue
        reclamados.add(item["codigo"])
        destino = {"codigo": item["codigo"], "nombre": actual["nombre"],
                    "precio_viejo": actual["precio_venta"], "precio_nuevo": item["precio"],
                    "fila": item["fila"]}
        if abs(actual["precio_venta"] - item["precio"]) < 0.005:
            sin_cambios.append(destino)
        else:
            actualizan.append(destino)

    # Segunda pasada: el código no está. ¿Es un producto que ya tenemos,
    # con el código de barras cambiado?
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
            if por_nombre.get(_clave_nombre(item["nombre"])):
                ambiguos.append({**item, "motivo":
                                  "el producto con ese nombre ya lo tomó otra fila del Excel"})
            else:
                nuevos.append(item)
            continue

        viejo = candidatos[0]
        reclamados.add(viejo)
        actual = por_codigo[viejo]
        recodifican.append({"codigo_viejo": viejo, "codigo_nuevo": item["codigo"],
                             "nombre": actual["nombre"], "precio_viejo": actual["precio_venta"],
                             "precio_nuevo": item["precio"], "fila": item["fila"]})

    no_vienen = [{"codigo": p["codigo"], "nombre": p["nombre"]}
                  for p in productos if p["activo"] and p["codigo"] not in reclamados]

    return {
        "actualizan": actualizan, "sin_cambios": sin_cambios, "recodifican": recodifican,
        "nuevos": nuevos, "ambiguos": ambiguos, "errores": errores,
        "no_vienen": no_vienen, "total_filas": len(leidas) + len(errores),
        "filtros": _impacto_en_filtros(recodifican, ambiguos),
    }


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

    hecho = {"precios": 0, "recodificados": 0, "creados": 0,
              "ofertas": 0, "umbrales": 0, "filtros": set(), "errores": list(plan["errores"])}

    # Cada producto va en su propia transacción: con 4587 filas, que una
    # falle no puede tirar abajo las 4586 que estaban bien (regla 6).
    for item in plan["actualizan"]:
        try:
            with transaction() as conn:
                conn.execute(
                    """UPDATE Productos SET precio_venta = ?, actualizado_en = ?,
                       version = version + 1 WHERE codigo = ?""",
                    (item["precio_nuevo"], now, item["codigo"]))
            hecho["precios"] += 1
        except Exception as e:
            hecho["errores"].append({"fila": item["fila"], "error": str(e)})

    for item in plan["recodifican"]:
        try:
            with transaction() as conn:
                conn.execute(
                    """UPDATE Productos SET codigo = ?, precio_venta = ?, actualizado_en = ?,
                       version = version + 1 WHERE codigo = ?""",
                    (item["codigo_nuevo"], item["precio_nuevo"], now, item["codigo_viejo"]))
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
                       (uuid_unico, codigo, nombre, precio_venta, stock, origen, sincronizado)
                       VALUES (?,?,?,?,0,?,?)""",
                    (str(uuid.uuid4()), item["codigo"], item["nombre"], item["precio"],
                     origen, sincronizado))
                conn.execute(
                    """INSERT INTO Movimientos_Stock
                       (uuid_unico, producto_codigo, tipo, cantidad, stock_resultante,
                        motivo, usuario, origen, fecha_hora, sincronizado)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), item["codigo"], "ENTRADA_EXCEL", 0, 0,
                     "Alta por lista de precios", usuario, origen, now, sincronizado))
            hecho["creados"] += 1
        except Exception as e:
            hecho["errores"].append({"fila": item["fila"], "error": str(e)})

    hecho["filtros"] = sorted(hecho["filtros"])
    hecho["plan"] = plan
    hecho["informe"] = informe_texto(plan, ruta=os.path.basename(ruta), aplicado=hecho)
    return hecho


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
    L += [f"Productos nuevos:             {len(plan['nuevos'])}"]
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

    L += ["--- Filtros guardados ---",
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
               f"Errores al aplicar:     {len(aplicado['errores'])}", ""]
        for e in aplicado["errores"][:_TOPE_DETALLE]:
            L += [f"    fila {e.get('fila', '?')}: {e['error']}"]

    L += ["=== Fin del informe ==="]
    return "\n".join(x for x in L if x is not None)
