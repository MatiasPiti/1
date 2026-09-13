"""El carrito tiene que BAJAR SOLO cuando se agrega una línea que no entra
en pantalla.

Esta prueba no mira `self.carrito` ni banderas internas: le pregunta al
canvas del carrito por dónde está mirando y a la celda dónde quedó
dibujada, y verifica que la línea recién agregada caiga dentro de lo que
se ve. Es la única forma de agarrar el caso real: el cajero escanea el
producto 30, el total sube, pero la pantalla sigue mostrando el 1 al 12 y
él no tiene manera de saber si el lector leyó bien.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="carrito_visible_")
from pos_core import paths
paths.set_base_override(base)
from pos_core.db import preparar_base
preparar_base()

from pos_core import products
from tkinter import messagebox
messagebox.showinfo = lambda *a, **k: None
messagebox.showwarning = lambda *a, **k: None
messagebox.showerror = lambda *a, **k: print("ERROR DIALOG:", a)

CANTIDAD = 40   # de sobra para desbordar cualquier pantalla
for n in range(CANTIDAD):
    products.crear_producto(codigo=f"77900{n:03d}", nombre=f"Producto de prueba {n:02d}",
                            precio_venta=100.0 + n, stock_inicial=50, usuario="test")

from apps.usb_caja.main import AppUsbCaja
app = AppUsbCaja()
app.update()

fallos = []


def vista():
    """(arriba, abajo) de lo que se ve del carrito, en coordenadas de la grilla."""
    canvas = app.carrito_canvas
    canvas.update_idletasks()
    arriba = canvas.canvasy(0)
    return arriba, arriba + canvas.winfo_height()


def lugar_de_la_linea(linea_id):
    celda = app._filas_carrito[linea_id]["widgets"][0]
    return celda.winfo_y(), celda.winfo_y() + celda.winfo_height()


def esta_a_la_vista(linea_id):
    v_arriba, v_abajo = vista()
    f_arriba, f_abajo = lugar_de_la_linea(linea_id)
    return f_arriba >= v_arriba - 1 and f_abajo <= v_abajo + 1


def escanear(codigo):
    app.buscador.delete(0, "end")
    app.buscador.insert(0, codigo)
    app._on_buscar()
    app.update()


# --- 1. escanear de a uno: la línea nueva SIEMPRE tiene que quedar a la vista --
desbordo = False
for n in range(CANTIDAD):
    escanear(f"77900{n:03d}")
    if len(app.carrito) != n + 1:
        fallos.append(f"al escanear el producto {n} el carrito quedó con {len(app.carrito)} líneas")
        break
    ultima = app.carrito[-1]["_id"]
    # ¿ya hay más contenido que pantalla? Desde acá la prueba tiene sentido.
    alto_grilla = app.carrito_grid.winfo_height()
    if alto_grilla > app.carrito_canvas.winfo_height():
        desbordo = True
    if not esta_a_la_vista(ultima):
        v = vista(); f = lugar_de_la_linea(ultima)
        fallos.append(f"línea {n + 1} (id {ultima}) NO se ve: fila en {f}, vista en "
                       f"({v[0]:.0f}, {v[1]:.0f})")
        break

if not desbordo:
    fallos.append(f"la pantalla nunca se llenó con {CANTIDAD} líneas: la prueba no probó nada")
else:
    print(f"OK: {CANTIDAD} líneas escaneadas, cada una quedó visible al agregarse")
    print(f"   grilla={app.carrito_grid.winfo_height()}px  ventana del carrito="
          f"{app.carrito_canvas.winfo_height()}px")

# --- 2. la última línea tiene que verse, y las primeras ya no (bajó de verdad) --
if not fallos:
    if esta_a_la_vista(app.carrito[0]["_id"]):
        fallos.append("con 40 líneas la PRIMERA sigue visible: el carrito no se desplazó")

# --- 3. re-escanear un producto de arriba: tiene que SUBIR a mostrarlo ----------
if not fallos:
    primera = app.carrito[0]
    escanear(primera["codigo"])
    if primera["cantidad"] != 2:
        fallos.append(f"re-escanear no sumó cantidad: quedó en {primera['cantidad']}")
    if not esta_a_la_vista(primera["_id"]):
        v = vista(); f = lugar_de_la_linea(primera["_id"])
        fallos.append(f"al re-escanear un producto de arriba la pantalla no subió a mostrarlo: "
                       f"fila en {f}, vista en ({v[0]:.0f}, {v[1]:.0f})")
    else:
        print("OK: re-escanear una línea que quedó arriba sube la vista a mostrarla")
    # y la celda tiene que decir 2 DE VERDAD, no solo el diccionario
    celdas = app._filas_carrito[primera["_id"]]["widgets"]
    if celdas[2].get() != "2":
        fallos.append(f"la celda de cantidad dice {celdas[2].get()!r} y no '2'")

# --- 4. escanear NO debe cambiar qué línea está seleccionada --------------------
# La seleccionada es la que borra Supr: si escanear la moviera, el cajero
# borraría una línea distinta de la que está mirando.
if not fallos:
    app.carrito_seleccionado = app.carrito[5]["_id"]
    app._refrescar_grilla_carrito()
    app.update()
    antes = app.carrito_seleccionado
    escanear(app.carrito[-1]["codigo"])
    if app.carrito_seleccionado != antes:
        fallos.append(f"escanear cambió la línea seleccionada: {antes} -> {app.carrito_seleccionado}")
    else:
        print("OK: escanear mueve la vista pero no la selección")

# --- 5. artículo sin código: también tiene que quedar a la vista ----------------
if not fallos:
    from pos_core.sales import CODIGO_SIN_BARRA, NOMBRE_SIN_BARRA
    # se llama al camino real con el importe ya resuelto, sin el diálogo
    import tkinter.simpledialog as sd
    original = sd.askfloat
    sd.askfloat = lambda *a, **k: 777.0
    try:
        app._agregar_articulo_sin_codigo()
    finally:
        sd.askfloat = original
    app.update()
    suelto = app.carrito[-1]
    if suelto["codigo"] != CODIGO_SIN_BARRA or abs(suelto["precio_unitario"] - 777.0) > 0.01:
        fallos.append(f"el artículo sin código no se cargó bien: {suelto}")
    elif not esta_a_la_vista(suelto["_id"]):
        v = vista(); f = lugar_de_la_linea(suelto["_id"])
        fallos.append(f"el artículo sin código quedó fuera de la vista: fila en {f}, "
                       f"vista en ({v[0]:.0f}, {v[1]:.0f})")
    else:
        print("OK: el artículo sin código también queda a la vista")

# --- 6. el total dibujado tiene que coincidir con la plata del carrito ----------
if not fallos:
    esperado = sum(i["cantidad"] * i["precio_unitario"] for i in app.carrito)
    en_pantalla = app.lbl_total.cget("text")
    if en_pantalla != f"${esperado:.2f}":
        fallos.append(f"total en pantalla {en_pantalla} vs carrito ${esperado:.2f}")
    else:
        print(f"OK: total en pantalla {en_pantalla} con {len(app.carrito)} líneas")

app.destroy()
print()
if fallos:
    print("=== FALLOS CARRITO VISIBLE ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== CARRITO VISIBLE OK ===")
