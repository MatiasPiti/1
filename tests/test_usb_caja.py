"""Prueba real de USB_Caja: arma el carrito y LEE lo que quedó dibujado
en pantalla (celda.get()), no solo lo que hay en self.carrito."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="usbcaja_")
from pos_core import paths
paths.set_base_override(base)
from pos_core.db import init_db
init_db()

from pos_core import products
from tkinter import messagebox
messagebox.showinfo = lambda *a, **k: None
messagebox.showwarning = lambda *a, **k: None
messagebox.showerror = lambda *a, **k: print("ERROR DIALOG:", a)

products.crear_producto(codigo="7790001", nombre="Yerba Playadito 1kg",
                        precio_venta=3500.0, stock_inicial=10, usuario="test")
products.crear_producto(codigo="7790002", nombre="Fideos Matarazzo",
                        precio_venta=1200.0, stock_inicial=10, usuario="test")

from apps.usb_caja.main import AppUsbCaja
app = AppUsbCaja()
app.update()

fallos = []

# --- 1. escanear un producto y LEER la pantalla ---
app.buscador.insert(0, "7790001")
app._on_buscar()
app.update()

filas = app._filas_carrito
if len(filas) != 1:
    fallos.append(f"esperaba 1 fila dibujada, hay {len(filas)}")
else:
    celdas = list(filas.values())[0]["widgets"]
    leido = [c.get() for c in celdas]
    print("FILA DIBUJADA:", leido)
    esperado = ["7790001", "YERBA PLAYADITO 1KG", "1", "$3500.00", "$3500.00"]
    if leido != esperado:
        fallos.append(f"la fila dibujada no coincide.\n  leido={leido}\n  esperado={esperado}")

print("TOTAL EN PANTALLA:", app.lbl_total.cget("text"))
if app.lbl_total.cget("text") != "$3500.00":
    fallos.append(f"total mal dibujado: {app.lbl_total.cget('text')}")

# --- 2. escanear el mismo producto otra vez: suma cantidad, se relee ---
app.buscador.insert(0, "7790001")
app._on_buscar()
app.update()
celdas = list(app._filas_carrito.values())[0]["widgets"]
if celdas[2].get() != "2" or celdas[4].get() != "$7000.00":
    fallos.append(f"al sumar cantidad la pantalla quedó: cant={celdas[2].get()} sub={celdas[4].get()}")

# --- 3. artículos sin código: sacar uno no debe borrar los otros ---
from pos_core.sales import CODIGO_SIN_BARRA, NOMBRE_SIN_BARRA
for importe in (100.0, 200.0, 300.0):
    app.carrito.append(app._nueva_linea(CODIGO_SIN_BARRA, NOMBRE_SIN_BARRA, importe))
app._refrescar_grilla_carrito()
app.update()
total_antes = sum(i["cantidad"] * i["precio_unitario"] for i in app.carrito)
app.carrito_seleccionado = app.carrito[2]["_id"]   # el de $200
app._quitar_linea()
app.update()
codigos_sueltos = [i for i in app.carrito if i["codigo"] == CODIGO_SIN_BARRA]
if len(codigos_sueltos) != 2:
    fallos.append(f"quitar un suelto dejó {len(codigos_sueltos)} sueltos (esperaba 2)")
total_despues = sum(i["cantidad"] * i["precio_unitario"] for i in app.carrito)
if abs((total_antes - total_despues) - 200.0) > 0.01:
    fallos.append(f"al quitar el suelto de $200 el total bajó {total_antes - total_despues}")

# la pantalla tiene que tener exactamente las filas que quedaron
if len(app._filas_carrito) != len(app.carrito):
    fallos.append(f"filas dibujadas={len(app._filas_carrito)} vs carrito={len(app.carrito)}")
for item in app.carrito:
    celdas = app._filas_carrito[item["_id"]]["widgets"]
    if celdas[0].get() == "":
        fallos.append(f"la línea {item['_id']} quedó dibujada VACÍA")

# --- 4. editar cantidad desde la grilla ---
primero = app.carrito[0]["_id"]
app._editar_cantidad(primero)
app.update()
app._confirmar_cantidad(primero, "5")
app.update()
celdas = app._filas_carrito[primero]["widgets"]
if celdas[2].get() != "5":
    fallos.append(f"tras editar cantidad, la celda dice {celdas[2].get()!r} y no '5'")

# --- 5. cobrar de verdad y verificar que descuenta stock ---
from pos_core import sales
def _stock(codigo):
    return next(p for p in products.listar_stock(codigo) if p["codigo"] == codigo)["stock"]
stock_antes = _stock("7790001")
app._cobrar_sin_facturar()
app.update()
stock_despues = _stock("7790001")
if stock_antes - stock_despues != 5:
    fallos.append(f"stock: antes={stock_antes} despues={stock_despues} (esperaba -5)")
if app.carrito:
    fallos.append("después de cobrar el carrito no quedó vacío")
if app._filas_carrito:
    fallos.append(f"después de cobrar quedaron {len(app._filas_carrito)} filas dibujadas")

ventas = sales.listar_ventas_de_hoy()
print("VENTAS REGISTRADAS:", len(ventas), [f"${v['total']:.2f}" for v in ventas])

# --- 6. exportar sincronización ---
from pos_core import sync_export
ruta = sync_export.exportar_caja()
print("SYNC EXPORTADO:", os.path.basename(ruta), os.path.getsize(ruta), "bytes")

app.destroy()
print()
if fallos:
    print("=== FALLOS USB_CAJA ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== USB_CAJA OK ===")
