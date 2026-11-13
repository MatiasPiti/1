"""Regresión: el cambio de init_db() -> preparar_base() no puede romper
nada de lo que ya andaba (Caja y Panel del Maestro), ni impedir que una
app abra si la migración falla (regla 6: ante la duda, la caja abre)."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="maestro_")
from pos_core import paths
paths.set_base_override(base)

from tkinter import messagebox
messagebox.showinfo = lambda *a, **k: None
messagebox.showwarning = lambda *a, **k: None
messagebox.showerror = lambda *a, **k: print("ERROR DIALOG:", a)

fallos = []

# --- base nueva: preparar_base no debe hacer nada raro ---
from pos_core import db
cambios = db.preparar_base()
print("BASE NUEVA, cambios:", cambios)
if cambios:
    fallos.append(f"sobre una base recién creada la migración quiso cambiar algo: {cambios}")
# idempotente: correrla dos veces no cambia nada
if db.preparar_base():
    fallos.append("preparar_base() no es idempotente")

from pos_core import products
products.crear_producto(codigo="7790001", nombre="Yerba Playadito 1kg",
                        precio_venta=3500.0, stock_inicial=10, usuario="test")

# --- las dos apps del Maestro abren y venden ---
from apps.master_caja.main import AppCaja
app = AppCaja()
app.update()
app.buscador.insert(0, "7790001")
app._on_buscar()
app.update()
if len(app._filas_carrito) != 1:
    fallos.append("la Caja Maestra no cargó el producto")
else:
    print("CAJA MAESTRA, carrito:", [c.get() for c in list(app._filas_carrito.values())[0]["widgets"]])
app.destroy()

from apps.master_dueno.main import AppDueno
app2 = AppDueno()
app2.update()
print("PANEL DEL DUEÑO:", app2.title(), "-", len(app2.nb.tabs()), "pestañas -", app2.geometry())
app2.destroy()

# --- config.ini tocado a mano: la Caja vende igual y nada queda trabado ---
# Para rotar el token de [remoto] hay que editar config.ini, y el Bloc de
# notas lo puede guardar con BOM. Antes eso lo dejaba ilegible para todas
# las apps: el ticket salía sin el nombre del negocio, la impresora dejaba
# de usarse y "Configurar impresora" abría una ventanita vacía que se quedaba
# con el teclado. Con BOM ahora se lee igual; mal formado de verdad, la Caja
# vende igual y el diálogo avisa en vez de no hacer nada.
import tkinter as tk
from pos_core import config, ticket_printer
from apps.theme import abrir_dialogo_impresora

ticket_printer.imprimir_ticket = lambda *a, **k: (False, "sin impresora")
carteles = []
messagebox.showerror = lambda t, m="", **k: carteles.append((t, m))


def _ventas():
    return db.get_connection().execute("SELECT COUNT(*) c FROM Ventas").fetchone()["c"]


def _boton(ventana, texto):
    for w in ventana.winfo_children():
        if isinstance(w, tk.Widget) and w.winfo_class() in ("TButton", "Button") \
                and w.cget("text") == texto:
            return w
        encontrado = _boton(w, texto)
        if encontrado is not None:
            return encontrado
    return None


for etiqueta, contenido in (
        ("con BOM", "\ufeff[general]\nnombre_local = Kiosco Con Bom\n\n[remoto]\ntoken = TOKEN-REAL\n"),
        ("mal formado", "esto no es un ini\nclave = valor\n")):
    with open(config.config_path(), "w", encoding="utf-8") as f:
        f.write(contenido)
    with open(config.config_path(), "rb") as f:
        bytes_antes = f.read()
    antes = _ventas()
    caja = AppCaja()
    caja.update()
    caja.buscador.insert(0, "7790001")
    caja._on_buscar()
    caja.update()
    caja._cobrar_sin_facturar()
    caja.update()
    if _ventas() != antes + 1:
        fallos.append(f"con un config.ini {etiqueta} la Caja no pudo cobrar")

    carteles.clear()
    try:
        dialogo = abrir_dialogo_impresora(caja)
        caja.update()
    except Exception as e:
        dialogo = None
        fallos.append(f"con un config.ini {etiqueta}, 'Configurar impresora' explotó ({type(e).__name__}) "
                       f"y deja una ventana vacía con el teclado capturado")
    if dialogo is not None:
        _boton(dialogo, "Guardar").invoke()
        caja.update()
        if etiqueta == "con BOM":
            if carteles:
                fallos.append(f"con BOM, guardar la impresora dio error: {carteles}")
            elif config.cargar_config().get("remoto", "token", fallback="") != "TOKEN-REAL":
                fallos.append("guardar la impresora sobre un config.ini con BOM perdió el [remoto] token")
            elif ticket_printer._nombre_local() != "Kiosco Con Bom":
                fallos.append("con BOM, el ticket no sale con el nombre del negocio")
        else:
            with open(config.config_path(), "rb") as f:
                if f.read() != bytes_antes:
                    fallos.append("guardar la impresora pisó un config.ini mal formado con valores por defecto")
            if not carteles or "No se pudo guardar" not in carteles[0][0]:
                fallos.append("con el config.ini mal formado, 'Guardar' no avisó nada (el botón parecía muerto)")
            if dialogo.winfo_exists():
                dialogo.destroy()
    caja.destroy()
    print(f"CONFIG.INI {etiqueta.upper()}: la Caja cobró y el diálogo de impresora respondió")
os.remove(config.config_path())
messagebox.showerror = lambda *a, **k: print("ERROR DIALOG:", a)

# --- si la migración explota, la app tiene que abrir igual ---
original = db.aplicar_migraciones
db.aplicar_migraciones = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("base rara"))
try:
    r = db.preparar_base()
    print("MIGRACIÓN ROTA -> preparar_base devolvió:", r, "(y no explotó)")
except Exception as e:
    fallos.append(f"preparar_base propagó el error de migración: {e}")
finally:
    db.aplicar_migraciones = original

# --- un producto se encuentra por su código aunque muchos otros lo contengan ---
# Los productos sin código de barras llevan el código interno del sistema
# viejo ("100", "1352"). Tipear "100" trae todos los "...100G." del catálogo,
# ordenados por nombre y con tope de 30: el que TIENE el código 100 quedaba
# afuera de la lista y no se podía vender por su código.
from pos_core import sales as _sales
products.crear_producto(codigo="100", nombre="ZZ BURMIT", precio_venta=900.0, usuario="test")
for n in range(40):
    products.crear_producto(codigo=f"77900{n:03d}99", nombre=f"ALFAJOR {n:02d} 100G.", precio_venta=500.0,
                            usuario="test")
encontrados = _sales.buscar_productos("100")
if not encontrados or encontrados[0]["codigo"] != "100":
    fallos.append(f"buscando '100' el producto de código 100 no sale primero "
                   f"(sale {[p['codigo'] for p in encontrados[:3]]}...): con 40 productos que lo "
                   f"contienen en el nombre, no se lo puede vender por su código")
else:
    print("CÓDIGO EXACTO -> sale primero aunque otros 40 productos lo contengan")
caja = AppCaja()
caja.update()
caja.buscador.insert(0, "100")
caja._on_buscar()
caja.update()
primera = caja.resultados.item(caja.resultados.get_children()[0], "values")
if primera[0] != "100":
    fallos.append(f"en la Caja, buscando '100' la primera fila es {primera}")
caja.destroy()

# --- un guardado que falla en el último paso no puede trabar la caja ---
# Si el COMMIT falla (disco lleno, un error de E/S) la transacción puede
# quedar abierta, y entonces toda venta siguiente reventaba con
# "transaction() anidada" hasta cerrar y abrir la Caja. Se provoca un COMMIT
# que falla de verdad (una referencia que SQLite recién revisa al confirmar).
import sqlite3
conn = db.get_connection()
conn.execute("CREATE TABLE IF NOT EXISTS _padre (id INTEGER PRIMARY KEY)")
conn.execute("CREATE TABLE IF NOT EXISTS _hijo "
             "(padre INTEGER REFERENCES _padre(id) DEFERRABLE INITIALLY DEFERRED)")
fallo_el_commit = False
try:
    with db.transaction() as c:
        c.execute("INSERT INTO _hijo VALUES (999)")
except sqlite3.Error:
    fallo_el_commit = True
if not fallo_el_commit:
    fallos.append("la prueba no logró que fallara el COMMIT: no prueba nada")
elif conn.in_transaction:
    fallos.append("después de un COMMIT que falló, la transacción quedó abierta")
else:
    from pos_core import sales
    try:
        venta = sales.cerrar_ticket(
            [{"codigo": "7790001", "nombre": "Yerba", "cantidad": 1, "precio_unitario": 3500.0}],
            metodo_pago="EFECTIVO", usuario="test")
        print("COMMIT QUE FALLA -> la venta siguiente se cobra igual:", venta["total"])
    except Exception as e:
        fallos.append(f"después de un guardado que falló, la Caja ya no puede cobrar: {e}")
if conn.execute("SELECT COUNT(*) FROM _hijo").fetchone()[0] != 0:
    fallos.append("lo que no se pudo confirmar quedó guardado igual")

print()
if fallos:
    print("=== FALLOS REGRESIÓN ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== REGRESIÓN MAESTRO OK ===")
