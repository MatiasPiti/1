"""Poder copiar SOLO el código de un producto, en cualquier grilla del Panel.

Un ttk.Treeview no deja pintar texto con el mouse, así que lo único que
había era "copiar la fila entera": pegar el código en otro lado obligaba a
borrarle a mano el nombre y el precio.

La prueba va en dos partes, y ninguna mira banderas internas — las dos
LEEN EL PORTAPAPELES con clipboard_get(), que es lo que va a recibir
Matías cuando pegue:

  A) Sobre una grilla suelta, con el Ctrl+C de verdad (tecla generada).
  B) Sobre cada grilla real del Panel, invocando la opción del menú.

Por qué la tecla se prueba aparte: bajo xvfb no hay gestor de ventanas y
en el Panel el foco de teclado se lo queda el canvas del gráfico del
Dashboard, así que la tecla no llega a la grilla. Eso es del entorno de
prueba, no del programa (en Windows el foco lo da el clic). Para no dar
por buena una cobertura que no existe, la tecla se prueba donde SÍ se
puede y en el Panel se verifica que el atajo esté enganchado en cada
grilla más el resultado del copiado.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="copiar_codigo_")
from pos_core import paths
paths.set_base_override(base)
from pos_core import db
db.preparar_base()

from tkinter import messagebox
messagebox.showinfo = lambda *a, **k: None
messagebox.showwarning = lambda *a, **k: None
messagebox.showerror = lambda *a, **k: print("ERROR DIALOG:", a)

import tkinter as tk
from tkinter import ttk
from apps.theme import aplicar_tema, columna_de_codigo, habilitar_copiar_treeview

fallos = []


def menu_de(tree):
    hijos = [w for w in tree.winfo_children() if isinstance(w, tk.Menu)]
    return hijos[0] if hijos else None


def opcion(menu, texto):
    """Índice de la opción del menú cuyo nombre contiene `texto`."""
    for i in range(menu.index("end") + 1):
        if menu.type(i) != "separator" and texto.lower() in menu.entrycget(i, "label").lower():
            return i
    return None


# ================================================================== #
# A) El Ctrl+C de verdad, sobre una grilla suelta
# ================================================================== #
root = tk.Tk()
aplicar_tema(root)
root.geometry("700x300+10+10")
suelta = ttk.Treeview(root, columns=("codigo", "nombre", "stock"), show="headings")
for col, txt, ancho in (("codigo", "Código", 180), ("nombre", "Nombre", 360),
                         ("stock", "Stock actual", 120)):
    suelta.heading(col, text=txt)
    suelta.column(col, width=ancho)
suelta.pack(fill="both", expand=True)
suelta.insert("", "end", values=("7790580142766", "ARCOR JUGOS", "7"))
suelta.insert("", "end", values=("7622300990213", "MILKA 155G.", "3"))
habilitar_copiar_treeview(suelta)
root.update()


def con_tecla(tree, filas):
    root.clipboard_clear()
    root.clipboard_append("__nada__")
    tree.selection_set(filas)
    root.focus_force()
    tree.focus_set()
    root.update()
    if root.focus_get() is not tree:
        return "EL FOCO NO LLEGÓ A LA GRILLA"
    tree.event_generate("<Control-c>", when="now")
    root.update()
    try:
        return root.clipboard_get()
    except tk.TclError:
        return None


filas = suelta.get_children()
copiado = con_tecla(suelta, (filas[0],))
print(f"CTRL+C (1 fila) copió: {copiado!r}")
if copiado != "7790580142766":
    fallos.append(f"Ctrl+C copió {copiado!r} y no el código solo")
elif "\t" in copiado or "ARCOR" in copiado:
    fallos.append(f"lo copiado arrastra el resto de la línea: {copiado!r}")
else:
    print("OK: Ctrl+C copia el código solo, sin el nombre ni el stock")

copiado = con_tecla(suelta, filas)
if copiado != "7790580142766\n7622300990213":
    fallos.append(f"con 2 filas elegidas Ctrl+C copió {copiado!r}, esperaba un código por línea")
else:
    print("OK: con 2 filas elegidas copia 2 códigos, uno por línea (pegable en Excel)")

# "Copiar solo «Nombre»": la columna sale del clic derecho, así que hay que
# mandar el clic sobre esa columna antes de invocar la opción.
menu = menu_de(suelta)
suelta.selection_set(filas[0])
# El punto exacto de la celda del nombre, preguntándoselo a Tk: calcularlo a
# ojo desde el ancho de las columnas cae en el hueco de abajo de la última
# fila, y ahí el menú (bien) no ofrece copiar ninguna celda.
caja = suelta.bbox(filas[0], "nombre")
if not caja:
    fallos.append("no pude ubicar la celda del nombre en pantalla (bbox vacío)")
    caja = (0, 0, 0, 0)
x_celda, y_celda = caja[0] + caja[2] // 2, caja[1] + caja[3] // 2
suelta.event_generate("<Button-3>", x=x_celda, y=y_celda, when="now")
root.update()
try:
    menu.unpost()
except tk.TclError:
    pass
indice_celda = opcion(menu, "copiar solo")
if indice_celda is None:
    fallos.append(f"el clic derecho sobre la columna Nombre no ofreció copiar esa celda: "
                   f"{[menu.entrycget(i, 'label') for i in range(menu.index('end') + 1) if menu.type(i) != 'separator']}")
else:
    etiqueta = menu.entrycget(indice_celda, "label")
    root.clipboard_clear(); root.clipboard_append("__nada__")
    menu.invoke(indice_celda)
    root.update()
    copiado = root.clipboard_get()
    print(f"OPCIÓN {etiqueta!r} copió: {copiado!r}")
    if copiado != "ARCOR JUGOS":
        fallos.append(f"copiar la celda del nombre dio {copiado!r}, esperaba 'ARCOR JUGOS'")
    elif "Nombre" not in etiqueta:
        fallos.append(f"la opción no dice de qué columna es: {etiqueta!r}")
    else:
        print("OK: el clic derecho sobre una celda copia SOLO esa celda, y dice cuál")

root.destroy()

# ================================================================== #
# B) Cada grilla real del Panel del Dueño (= el Dueño Remoto)
# ================================================================== #
from pos_core import products
PRODUCTOS = [("7790580142766", "Arcor Jugos", 400.0),
             ("7791234567890", "Yerba Playadito 1kg", 3500.0),
             ("1002", "Tang Jugos", 500.0)]
for codigo, nombre, precio in PRODUCTOS:
    products.crear_producto(codigo=codigo, nombre=nombre, precio_venta=precio,
                            stock_inicial=7, usuario="test")

from apps.master_dueno.main import AppDueno
app = AppDueno()
app.update()


def portapapeles():
    try:
        return app.clipboard_get()
    except tk.TclError:
        return None


def copiar_codigo_de(tree):
    menu = menu_de(tree)
    indice = opcion(menu, "copiar código")
    app.clipboard_clear()
    app.clipboard_append("__nada__")
    menu.invoke(indice)
    app.update()
    return portapapeles()


# Toda grilla que muestre un código tiene que estar reconocida. Auditoría es
# el caso que agarra una implementación ingenua: ahí el código NO es la
# primera columna (la primera es la fecha).
ESPERADAS = {
    "tree_stock_actual": 0,    # Stock
    "tree_candidatos": 0,      # coincidencias de Precios
    "picker_todos": 0,         # armado de filtros
    "bulk_tree": 0,            # edición masiva
    "tree_umbrales": 0,        # Alertas
    "tree_auditoria": 1,       # Auditoría
}
for atributo, indice_esperado in ESPERADAS.items():
    tree = getattr(app, atributo, None)
    if tree is None:
        fallos.append(f"no existe {atributo}: cambió el panel y esta prueba quedó vieja")
        continue
    indice, _ = columna_de_codigo(tree)
    if indice != indice_esperado:
        fallos.append(f"{atributo}: código detectado en la columna {indice} "
                       f"(esperaba {indice_esperado}); columnas={list(tree['columns'])}")
    if menu_de(tree) is None:
        fallos.append(f"{atributo}: no tiene el menú de clic derecho")
    elif opcion(menu_de(tree), "copiar código") is None:
        fallos.append(f"{atributo}: el menú no ofrece copiar el código")
    if not tree.bind("<Control-c>"):
        fallos.append(f"{atributo}: no tiene enganchado el Ctrl+C")
if not fallos:
    print(f"OK: las {len(ESPERADAS)} grillas con código reconocidas, con menú y con Ctrl+C "
          f"(Auditoría incluida, donde el código es la 2da columna)")

# Stock: copiar el código y que sea SOLO el código
if not fallos:
    app._refrescar_stock_actual()
    app.update()
    tree = app.tree_stock_actual
    filas = tree.get_children()
    if not filas:
        fallos.append("la pestaña Stock no listó ningún producto")
    else:
        valores = [str(v) for v in tree.item(filas[0], "values")]
        tree.selection_set(filas[0])
        copiado = copiar_codigo_de(tree)
        print(f"STOCK, fila en pantalla: {valores}  ->  copiado: {copiado!r}")
        if copiado != valores[0]:
            fallos.append(f"en Stock copió {copiado!r} y no el código {valores[0]!r}")
        else:
            print("OK: en Stock copia el código solo")

# Auditoría: tiene que salir el código, no la fecha
if not fallos:
    tree = app.tree_auditoria
    tree.insert("", "end", values=("2026-09-13 10:00", "7790580142766", "ARCOR JUGOS",
                                    "2", "$400.00", "$800.00", "cajero"))
    app.update()
    fila = tree.get_children()[0]
    tree.selection_set(fila)
    copiado = copiar_codigo_de(tree)
    if copiado != "7790580142766":
        fallos.append(f"en Auditoría copió {copiado!r} (esperaba el código, no la fecha)")
    else:
        print("OK: en Auditoría copia el código y no la fecha de la 1ra columna")

# Copiar la fila entera sigue existiendo (no se rompió lo que ya andaba)
if not fallos:
    tree = app.tree_stock_actual
    fila = tree.get_children()[0]
    tree.selection_set(fila)
    valores = [str(v) for v in tree.item(fila, "values")]
    menu = menu_de(tree)
    indice = opcion(menu, "fila")
    if indice is None:
        fallos.append("desapareció la opción de copiar la fila entera")
    else:
        app.clipboard_clear(); app.clipboard_append("__nada__")
        menu.invoke(indice)
        app.update()
        copiado = portapapeles()
        if copiado != "\t".join(valores):
            fallos.append(f"copiar fila dio {copiado!r}, esperaba {chr(9).join(valores)!r}")
        else:
            print("OK: copiar la fila entera sigue funcionando, separada por tabulaciones")

# Una grilla SIN código no puede quedarse muda: copia la fila
if not fallos:
    tree = app.tree_metodos_pago
    indice, _ = columna_de_codigo(tree)
    if indice is not None:
        fallos.append(f"detectó código donde no hay: {list(tree['columns'])}")
    else:
        tree.insert("", "end", values=("EFECTIVO", "3", "$1500.00"))
        app.update()
        tree.selection_set(tree.get_children()[0])
        copiado = copiar_codigo_de(tree)
        if copiado != "EFECTIVO\t3\t$1500.00":
            fallos.append(f"en una grilla sin código copió {copiado!r} en vez de la fila entera")
        else:
            print("OK: en una grilla sin código copia la fila, no se queda muda")

app.destroy()
print()
if fallos:
    print("=== FALLOS COPIAR CÓDIGO ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== COPIAR CÓDIGO OK ===")
