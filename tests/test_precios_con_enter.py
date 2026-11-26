"""En Precios, Enter pasa al campo siguiente y en GUARDAR CAMBIOS guarda.

Pedido de Leo (26/11/2026), que actualiza precios desde el Dueño Remoto (es
la misma ventana que el Panel): cargar Costo, Enter, el próximo, Enter...
hasta el botón, y un Enter más guarda el producto, sin tocar el mouse. Se
prueba mandando las teclas de verdad (también el Enter del teclado numérico)
y leyendo lo que quedó GUARDADO en la base, no una bandera de la pantalla.
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="precios_enter_")
from pos_core import paths  # noqa: E402
paths.set_base_override(base)

from tkinter import messagebox  # noqa: E402
errores = []
messagebox.showinfo = lambda *a, **k: None
messagebox.showwarning = lambda *a, **k: None
messagebox.showerror = lambda *a, **k: errores.append(a)

from pos_core import db, products  # noqa: E402

db.preparar_base()
products.crear_producto(codigo="7790001", nombre="Yerba Mate 1kg", precio_venta=3500, usuario="prueba")
db.cerrar_conexion()

from apps.master_dueno.main import AppDueno  # noqa: E402

fallos = []
app = AppDueno()
app.nb.select(app.tab_stock)   # Precios vive en la pestaña Stock: sin abrirla no hay foco
app.update()


def precio_guardado():
    con = sqlite3.connect(os.path.join(base, "database", "stock.db"))
    try:
        return con.execute("SELECT precio_compra, precio_venta FROM Productos WHERE codigo='7790001'").fetchone()
    finally:
        con.close()


def tecla(widget, nombre):
    widget.event_generate(f"<{nombre}>")
    app.update()


def foco():
    return app.focus_get()


def enfocar(widget):
    # Bajo xvfb no hay gestor de ventanas: focus_force, no focus_set.
    widget.focus_force()
    app.update()


app.precio_buscar.insert(0, "7790001")
app._buscar_para_precios()
app.update()

e = app.entradas_precio
orden = [app.precio_nombre, app.precio_rubro, app.precio_subrubro,
         e["costo_sin_iva"], e["precio_costo"], e["margen"], e["precio_final"], app.btn_guardar_precio]
nombres = ["Nombre", "Rubro", "Subrubro", "Costo S/IVA", "Precio Costo", "% Ganancia", "Precio Final",
           "GUARDAR CAMBIOS"]

# Recorrido completo con Enter, del Nombre al botón, sin guardar nada en el camino.
enfocar(app.precio_nombre)
if foco() is not app.precio_nombre:
    print("SALTEADA: no hay gestor de ventanas que dé el foco (no se puede probar el teclado acá)")
    sys.exit(0)
for i, (actual, siguiente) in enumerate(zip(orden, orden[1:])):
    tecla(actual, "Return")
    if foco() is not siguiente:
        fallos.append(f"Enter en {nombres[i]} no pasó a {nombres[i + 1]} (el foco quedó en {foco()})")
        break
else:
    print("OK: Enter recorre Nombre -> Rubro -> Subrubro -> los cuatro números -> GUARDAR CAMBIOS")
if precio_guardado() != (0, 3500):
    fallos.append(f"recorrer los campos con Enter guardó algo antes del botón: {precio_guardado()}")

# Lo que hace Leo: Costo, Enter, Ganancia, Enter, Enter, y Enter en el botón guarda.
enfocar(e["precio_costo"])
e["precio_costo"].delete(0, "end")
e["precio_costo"].insert(0, "2.000")
# El Enter del teclado numérico. En Linux se simula y tiene que pasar de campo.
# En Windows Tk no deja simularlo (event_generate de KP_Enter no llega a
# ningún lado: se vio en la laptop de Matías el 26/11/2026), y la tecla de
# verdad la entrega como Return o como KP_Enter según la versión de Tk: el
# Panel atiende las dos, así que ahí se comprueba que la de KP_Enter esté.
tecla(e["precio_costo"], "KP_Enter")
if foco() is not e["margen"]:
    if sys.platform == "win32" and e["precio_costo"].bind("<KP_Enter>"):
        print("OK: (Windows) el Enter del teclado numérico está atendido; no se puede simular acá")
        tecla(e["precio_costo"], "Return")
    else:
        fallos.append("el Enter del teclado numérico no pasó al campo siguiente")
        enfocar(e["margen"])     # que una falla no arrastre a las que siguen
e["margen"].insert(0, "50")
tecla(e["margen"], "Return")
if e["precio_final"].get() != "3000.00":
    fallos.append(f"al pasar con Enter no se recalculó el precio final: {e['precio_final'].get()!r}")
tecla(e["precio_final"], "Return")
if foco() is not app.btn_guardar_precio:
    fallos.append("Enter en el Precio Final no llevó a GUARDAR CAMBIOS")
    enfocar(app.btn_guardar_precio)
tecla(app.btn_guardar_precio, "Return")
if precio_guardado() != (2000, 3000):
    fallos.append(f"Enter en GUARDAR CAMBIOS no guardó: en la base quedó {precio_guardado()}")
else:
    print("OK: Costo 2.000 + 50 % con Enter, y Enter en el botón guarda $3000 en la base")
if foco() is not app.precio_buscar:
    fallos.append("después de guardar el foco no volvió al buscador para escanear el siguiente")
if errores:
    fallos.append(f"apareció un cartel de error: {errores}")

app.destroy()
if fallos:
    print("\nFALLÓ:\n - " + "\n - ".join(fallos))
    sys.exit(1)
print("\nTodo OK")
