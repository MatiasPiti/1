"""Una venta se cobra UNA vez y se factura UNA vez.

Dos formas de registrar dos veces la misma plata, las dos sin que nadie se
dé cuenta en el momento:

1. La Caja. El carrito de una venta seguía cargado hasta que se cerraba el
   cartel de "Venta cobrada". Si en ese rato se atendía un segundo F5/F12
   (la tecla apretada dos veces, o apretada de nuevo porque la pantalla
   quedó quieta mientras contestaba ARCA), la misma venta se registraba
   otra vez: dos tickets, dos descuentos de stock y, con F12, dos facturas.
   Acá el cartel se reemplaza por uno que atiende lo que quedó en la cola
   de teclas, que es lo que hace cualquier ventana que espera una respuesta.

2. El Panel. "Reintentar facturación de la venta seleccionada" muestra
   todas las ventas del día, también las ya facturadas: elegir una de esas
   le pedía a ARCA un comprobante NUEVO y pisaba el CAE guardado. Una
   factura de más solo se anula con una nota de crédito.

La prueba cuenta lo único que importa: cuántas ventas quedaron en la base,
cuánto stock se descontó y cuántas veces se le pidió una factura a ARCA.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="cobro_unico_")
from pos_core import paths
paths.set_base_override(base)
from pos_core import db
db.preparar_base()

from tkinter import messagebox
from pos_core import arca, products, sales, ticket_printer

fallos = []

products.crear_producto(codigo="111", nombre="COCA COLA 1,5L.", precio_venta=2500.0,
                        stock_inicial=20, usuario="test")
ticket_printer.imprimir_ticket = lambda *a, **k: (False, "sin impresora")


def cuantas_ventas(origen=None):
    if origen:
        return db.get_connection().execute(
            "SELECT COUNT(*) c FROM Ventas WHERE origen = ?", (origen,)).fetchone()["c"]
    return db.get_connection().execute("SELECT COUNT(*) c FROM Ventas").fetchone()["c"]


def stock():
    return db.get_connection().execute(
        "SELECT stock FROM Productos WHERE codigo = '111'").fetchone()["stock"]


# ARCA de mentira: cuenta cuántas facturas se le pidieron.
facturas_pedidas = []


def _arca_falsa(venta):
    facturas_pedidas.append(venta["total"])
    return {"tipo_comprobante": "C", "numero_comprobante": len(facturas_pedidas),
            "cae": f"CAE-{len(facturas_pedidas)}", "cae_vencimiento": "2026-12-01"}


arca.facturar_venta = _arca_falsa


def preparar(app):
    """Deja la app con un cartel que atiende la cola de teclas mientras está
    abierto, y un contador de cuántas veces llegó la tecla de cobrar."""
    carteles = []

    def cartel(titulo, mensaje="", **_k):
        carteles.append(titulo)
        app.update()          # lo que queda en la cola se atiende con el cartel abierto
    for nombre in ("showinfo", "showwarning", "showerror"):
        setattr(messagebox, nombre, cartel)
    return carteles


def escanear(app, codigo="111"):
    app.buscador.delete(0, "end")
    app.buscador.insert(0, codigo)
    app._on_buscar()
    app.update()


def apretar(app, tecla, veces):
    """Deja `veces` teclas en la cola ANTES de atender ninguna."""
    app.focus_force()
    app.update()
    for _ in range(veces):
        app.event_generate(tecla, when="tail")
    app.update()


# --------------------------------------------------------------------- #
# 1. Caja del local: F5 dos veces seguidas
# --------------------------------------------------------------------- #
import apps.master_caja.main as caja_maestro

app = caja_maestro.AppCaja()
app.update()
carteles = preparar(app)

llegadas = {"f5": 0, "f12": 0}
_f5, _f12 = app._cobrar_sin_facturar, app._cobrar_y_facturar
app._cobrar_sin_facturar = lambda: (llegadas.__setitem__("f5", llegadas["f5"] + 1), _f5())[1]
app._cobrar_y_facturar = lambda: (llegadas.__setitem__("f12", llegadas["f12"] + 1), _f12())[1]

escanear(app)
apretar(app, "<F5>", 2)
if llegadas["f5"] != 2:
    fallos.append(f"la prueba no llegó a mandar los dos F5 (llegaron {llegadas['f5']}): no prueba nada")
if cuantas_ventas() != 1:
    fallos.append(f"dos F5 seguidos registraron {cuantas_ventas()} ventas por un solo carrito")
elif stock() != 19:
    fallos.append(f"dos F5 seguidos descontaron mal el stock: quedó en {stock()} (tenía 20, se vendió 1)")
else:
    print("OK: dos F5 seguidos registran UNA sola venta y descuentan UNA vez el stock")
if app.carrito:
    fallos.append("después de cobrar el carrito no quedó vacío")

# La marca de "cobro en curso" tiene que haberse bajado: si quedara puesta,
# la caja no cobraría nunca más.
escanear(app)
apretar(app, "<F5>", 1)
if cuantas_ventas() != 2:
    fallos.append(f"después de un doble F5 la caja dejó de cobrar: hay {cuantas_ventas()} ventas y tenían que ser 2")
else:
    print("OK: la venta siguiente se cobra normalmente (la caja no queda trabada)")

# --------------------------------------------------------------------- #
# 2. F12 dos veces: una venta y UNA factura
# --------------------------------------------------------------------- #
antes = cuantas_ventas()
facturas_pedidas.clear()
escanear(app)
apretar(app, "<F12>", 2)
if llegadas["f12"] != 2:
    fallos.append(f"la prueba no llegó a mandar los dos F12 (llegaron {llegadas['f12']})")
if cuantas_ventas() - antes != 1:
    fallos.append(f"dos F12 seguidos registraron {cuantas_ventas() - antes} ventas")
elif len(facturas_pedidas) != 1:
    fallos.append(f"dos F12 seguidos le pidieron {len(facturas_pedidas)} facturas a ARCA por una sola venta")
else:
    print("OK: dos F12 seguidos registran UNA venta y le piden UNA factura a ARCA")

# --------------------------------------------------------------------- #
# 3. Si el cartel falla, el carrito igual queda vacío
# --------------------------------------------------------------------- #
# La venta ya está registrada cuando se muestra el cartel. Si el carrito
# quedara cargado por un error ahí, el próximo F5 la registraría de nuevo.
antes = cuantas_ventas()
escanear(app)


def _cartel_roto(*_a, **_k):
    raise RuntimeError("el cartel no se pudo mostrar")


messagebox.showinfo = _cartel_roto
try:
    _f5()
except RuntimeError:
    pass
carteles = preparar(app)
if app.carrito:
    fallos.append("falló el cartel de 'Venta cobrada' y el carrito quedó cargado: otro F5 la cobra de nuevo")
apretar(app, "<F5>", 1)
if cuantas_ventas() - antes != 1:
    fallos.append(f"con el cartel roto, la venta quedó registrada {cuantas_ventas() - antes} veces")
elif "Carrito vacío" not in carteles:
    fallos.append("después de cobrar, un F5 de más tenía que avisar 'Carrito vacío'")
else:
    print("OK: aunque falle el cartel, el carrito se vacía y un F5 de más no cobra nada")
app.destroy()

# --------------------------------------------------------------------- #
# 4. Caja del USB de emergencia: mismo carrito, misma regla
# --------------------------------------------------------------------- #
import apps.usb_caja.main as caja_usb

usb = caja_usb.AppUsbCaja()
usb.update()
preparar(usb)
escanear(usb)
apretar(usb, "<F5>", 2)
if cuantas_ventas("USB_CAJA") != 1:
    fallos.append(f"en la Caja del USB, dos F5 seguidos registraron {cuantas_ventas('USB_CAJA')} ventas")
else:
    print("OK: en la Caja del USB, dos F5 seguidos registran UNA sola venta")
escanear(usb)
apretar(usb, "<F12>", 1)
if cuantas_ventas("USB_CAJA") != 2:
    fallos.append("la Caja del USB dejó de cobrar después de un doble F5")
usb.destroy()

# --------------------------------------------------------------------- #
# 5. Una venta ya facturada no se vuelve a facturar
# --------------------------------------------------------------------- #
facturas_pedidas.clear()
venta = sales.cerrar_ticket(
    [{"codigo": "111", "nombre": "COCA COLA 1,5L.", "cantidad": 1, "precio_unitario": 2500.0}],
    metodo_pago="EFECTIVO", usuario="test")
sales.facturar_venta_arca(venta["venta_uuid"])
cae = db.get_connection().execute(
    "SELECT cae, numero_comprobante FROM Ventas WHERE uuid_unico = ?", (venta["venta_uuid"],)).fetchone()
try:
    sales.facturar_venta_arca(venta["venta_uuid"])
    fallos.append("facturar dos veces la misma venta no dio error")
except arca.ArcaError as e:
    if "ya está facturada" not in str(e):
        fallos.append(f"el error de la segunda factura no explica qué pasa: {e}")
despues = db.get_connection().execute(
    "SELECT cae, numero_comprobante FROM Ventas WHERE uuid_unico = ?", (venta["venta_uuid"],)).fetchone()
if len(facturas_pedidas) != 1:
    fallos.append(f"por una sola venta se le pidieron {len(facturas_pedidas)} facturas a ARCA")
elif dict(cae) != dict(despues):
    fallos.append(f"la segunda vez pisó el comprobante guardado: {dict(cae)} -> {dict(despues)}")
else:
    print("OK: una venta ya facturada no se vuelve a facturar (ni se pisa su CAE)")

# Y por el camino real: el botón del Panel sobre una venta que ya tiene factura.
from apps.master_dueno.main import AppDueno

avisos = []
for nombre in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, nombre, lambda titulo, mensaje="", _n=nombre, **k: avisos.append((_n, titulo, mensaje)))
panel = AppDueno()
panel.update()
panel._refrescar_resumen_arca()
panel.tree_arca.selection_set(venta["venta_uuid"])
panel._reintentar_facturacion()
panel.update()
if len(facturas_pedidas) != 1:
    fallos.append("el botón 'Reintentar facturación' del Panel volvió a facturar una venta ya facturada")
elif not any(n == "showwarning" and "ya está facturada" in m for n, _t, m in avisos):
    fallos.append(f"el Panel no avisó que la venta ya estaba facturada: {avisos}")
else:
    print("OK: el botón 'Reintentar facturación' del Panel avisa y no factura de nuevo")
panel.destroy()

print()
if fallos:
    print("=== FALLOS COBRO UNA SOLA VEZ ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== COBRO UNA SOLA VEZ OK ===")
