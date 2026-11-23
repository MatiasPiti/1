"""La Caja tiene que leerse bien: letra grande, y nada cortado en la pantalla del local.

Matías pidió agrandar la letra de la Caja para que se lea mejor, también la
gente que ve poco. Al mirar la pantalla con el tamaño de ventana que toma en
el local (1366x768) aparecieron tres cosas que la letra grande empeoraba:

1. El TOTAL A PAGAR salía cortado por la mitad. La barra de abajo se armaba
   después del carrito y, cuando no había lugar, era la que se achicaba.
2. Los nombres salían cortados ("FIDEOS MATARAZZO TIRAI"): cada columna de
   números pedía 20 letras y se comía el lugar del nombre.
3. En la Caja del pendrive no entraba la fila de arriba: "Cargar productos
   (Excel)" desaparecía y "Configurar impresora" salía cortado.

La prueba arma las dos Cajas con esa ventana, maximizada y sin maximizar,
carga un ticket con nombres largos y un total de siete cifras, y mira lo que
se ve: que el total y los botones entren enteros, que los nombres y la plata
no salgan cortados, que se vean al menos 3 líneas del carrito, y que la
letra no baje del tamaño que se eligió.
"""
import os
import sys
import tempfile
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="caja_legible_")
from pos_core import paths
paths.set_base_override(base)
from pos_core import db, products
db.preparar_base()

for nombre in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, nombre, lambda *a, **k: None)

fallos = []

LINEAS = (("7790895000997", "COCA COLA 1,5L.", 2500.0),
          ("7790387013801", "YERBA PLAYADITO 1KG", 4100.0),
          ("7790580660000", "FIDEOS MATARAZZO TIRABUZON 500G", 1890.0),
          ("7790070411514", "SERENISIMA LECHE ENTERA 1L", 1650.0),
          ("7791293008900", "SAPHIRUS TEXTIL 250ML.", 4601.0),
          ("7798434460028", "WHISKY CHIVAS REGAL 18 AÑOS 750ML", 987654.32))   # total de 7 cifras
for codigo, nombre, precio in LINEAS:
    products.crear_producto(codigo=codigo, nombre=nombre, precio_venta=precio, usuario="test")

# El tamaño de ventana que toma en el local: maximizada, y sin maximizar
# (ajustar_ventana la deja en 1180 de ancho y lo que entre de alto).
VENTANAS = ((1366, 697, "maximizada"), (1180, 704, "sin maximizar"))

# Lo que se eligió para que se lea de lejos. Si alguien lo achica, que se note.
MINIMOS = {"nombre": 15, "codigo": 12, "precio": 15, "subtotal": 18, "total": 34, "buscador": 15, "boton": 12}


def tamano(fuente) -> int:
    return abs(int(tkfont.Font(font=fuente).actual("size")))


def todos_los_botones(widget):
    for hijo in widget.winfo_children():
        if isinstance(hijo, ttk.Button):
            yield hijo
        yield from todos_los_botones(hijo)


def entero_y_adentro(app, widget):
    """Por qué no se ve entero, o "" si se ve entero."""
    if not widget.winfo_ismapped():
        return "no está a la vista"
    if widget.winfo_width() < widget.winfo_reqwidth() or widget.winfo_height() < widget.winfo_reqheight():
        return (f"sale achicado: {widget.winfo_width()}x{widget.winfo_height()} de "
                f"{widget.winfo_reqwidth()}x{widget.winfo_reqheight()}")
    x, y = widget.winfo_rootx() - app.winfo_rootx(), widget.winfo_rooty() - app.winfo_rooty()
    if x < 0 or y < 0 or x + widget.winfo_width() > app.winfo_width() or \
            y + widget.winfo_height() > app.winfo_height():
        return "queda afuera de la ventana"
    return ""


def revisar(app, caja, ventana):
    donde = f"{caja} {ventana}"
    problemas = []
    # 1. El total y los botones de cobrar, enteros.
    a_la_vista = {"el TOTAL A PAGAR": app.lbl_total, "el medio de pago": app.metodo_pago,
                  "el buscador": app.buscador}
    for boton in todos_los_botones(app):
        a_la_vista[f"el botón «{boton.cget('text')}»"] = boton
    for que, widget in a_la_vista.items():
        motivo = entero_y_adentro(app, widget)
        if motivo:
            problemas.append(f"{donde}: {que} {motivo}")

    # 2. Nombres y plata sin cortar.
    for item in app.carrito:
        celdas = app._filas_carrito[item["_id"]]["widgets"]
        for col, que in ((1, "el nombre"), (3, "el precio"), (4, "el subtotal")):
            celda = celdas[col]
            ancho_texto = tkfont.Font(font=celda.cget("font")).measure(celda.get())
            if ancho_texto > celda.winfo_width():
                problemas.append(f"{donde}: {que} «{celda.get()}» sale cortado "
                                 f"({ancho_texto}px de texto en {celda.winfo_width()}px)")

    # 3. Al menos tres líneas del carrito a la vista.
    alto_linea = app.carrito_grid.grid_bbox(column=1, row=1)[3]
    lineas_a_la_vista = app.carrito_canvas.winfo_height() // max(alto_linea, 1)
    if lineas_a_la_vista < 3:
        problemas.append(f"{donde}: el carrito muestra {lineas_a_la_vista} líneas a la vez "
                         f"(alto {app.carrito_canvas.winfo_height()}px, {alto_linea}px por línea)")
    return problemas, lineas_a_la_vista


def letra_minima(app, caja):
    problemas = []
    celdas = app._filas_carrito[app.carrito[0]["_id"]]["widgets"]
    medidas = {"codigo": tamano(celdas[0].cget("font")), "nombre": tamano(celdas[1].cget("font")),
               "precio": tamano(celdas[3].cget("font")), "subtotal": tamano(celdas[4].cget("font")),
               "total": tamano(app.lbl_total.cget("font")), "buscador": tamano(app.buscador.cget("font"))}
    estilo = ttk.Style(app)
    botones = [b for b in todos_los_botones(app) if "COBRAR" in b.cget("text") or "Quitar" in b.cget("text")]
    medidas["boton"] = min(tamano(estilo.lookup(b.cget("style") or "TButton", "font") or "TkDefaultFont")
                           for b in botones)
    for que, minimo in MINIMOS.items():
        if medidas[que] < minimo:
            problemas.append(f"{caja}: la letra de «{que}» es de {medidas[que]} puntos "
                             f"(se eligió {minimo} para que se lea de lejos)")
    # El precio por unidad grande no puede confundirse con el subtotal, que es
    # lo que se cobra: el subtotal sigue más grande y de otro color.
    if medidas["subtotal"] <= medidas["precio"]:
        problemas.append(f"{caja}: el subtotal ({medidas['subtotal']} puntos) no es más grande que el "
                         f"precio por unidad ({medidas['precio']}): se pueden confundir")
    if celdas[3].cget("fg").lower() == celdas[4].cget("fg").lower():
        problemas.append(f"{caja}: el precio por unidad y el subtotal van del mismo color")
    return problemas


import apps.master_caja.main as caja_local
import apps.usb_caja.main as caja_pendrive

for caja, clase in (("la Caja del local", caja_local.AppCaja), ("la Caja del pendrive", caja_pendrive.AppUsbCaja)):
    for ancho, alto, ventana in VENTANAS:
        app = clase()
        try:
            app.state("normal")             # en Windows abre maximizada: se fija la ventana a mano
        except tk.TclError:
            pass
        app.geometry(f"{ancho}x{alto}+0+0")
        app.update()
        for codigo, _nombre, _precio in LINEAS:
            app.buscador.delete(0, "end")
            app.buscador.insert(0, codigo)
            app._on_buscar()
            app.update()
        for _ in range(3):
            app.update()
        problemas, lineas = revisar(app, caja, ventana)
        if ventana == "maximizada":
            problemas += letra_minima(app, caja)
        if problemas:
            fallos.extend(problemas)
        else:
            print(f"OK: {caja} {ventana} ({ancho}x{alto}): total y botones enteros, nombres y plata "
                  f"sin cortar, {lineas} líneas del carrito a la vista")
        app.destroy()

db.cerrar_conexion()
print()
if fallos:
    print("=== FALLOS CAJA LEGIBLE ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== CAJA LEGIBLE OK ===")
