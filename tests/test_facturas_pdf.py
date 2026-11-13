"""Cargar una factura PDF no puede sumar el stock dos veces ni guardar un costo de $ 3,50.

Dos fallas de la lectura de facturas, las dos silenciosas:

1. Una factura que trae una TABLA de verdad (con sus líneas, como salen de
   casi cualquier sistema de facturación) se lee de dos maneras: como tabla
   y como texto de la página. El mismo renglón aparecía en las dos lecturas
   y entraba dos veces: el Panel decía "¿Aplicar 6 ítems al stock?" para una
   factura de 3, y las 12 Oreo quedaban como 24.

2. Un precio escrito "3.500" (tres mil quinientos, como se escribe acá) se
   leía 3,5, y ese quedaba guardado como costo del producto.

No había ninguna prueba de la carga de facturas. Esta arma los PDF de verdad
(una con tabla y otra de texto suelto), los lee, y además hace el camino
entero por el Panel: elegir el archivo, confirmar, y mirar el stock y el
costo que quedaron GUARDADOS.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="facturas_pdf_")
from pos_core import paths
paths.set_base_override(base)
from pos_core import db
db.preparar_base()

from pos_core import pdf_import, products, stock_service

fallos = []

OREO, YERBA, FIDEOS = "7791234567890", "7790001112223", "7790009998887"
for codigo, nombre in ((OREO, "GALLETITAS OREO 118G"), (YERBA, "YERBA PLAYADITO 1KG"),
                       (FIDEOS, "FIDEOS GUISERO 500G")):
    products.crear_producto(codigo=codigo, nombre=nombre, precio_venta=1000.0, usuario="test")


def producto(codigo):
    return db.get_connection().execute(
        "SELECT stock, precio_compra FROM Productos WHERE codigo = ?", (codigo,)).fetchone()


# --------------------------------------------------------------------- #
# Los PDF de prueba. Se arman con matplotlib (ya es parte del sistema: es el
# gráfico del Dashboard), sin pyplot para no tocar el backend de pantalla.
# --------------------------------------------------------------------- #
from matplotlib.backends.backend_pdf import FigureCanvasPdf
from matplotlib.figure import Figure

RENGLONES = [[OREO, "GALLETITAS OREO 118G", "12", "450,50"],
             [YERBA, "YERBA PLAYADITO 1KG", "24", "3.500,00"],
             [FIDEOS, "FIDEOS GUISERO 500G", "10", "3.500"]]


def pdf_con_tabla(nombre, renglones):
    figura = Figure(figsize=(8.27, 11.69))
    FigureCanvasPdf(figura)
    ejes = figura.add_subplot(111)
    ejes.axis("off")
    tabla = ejes.table(cellText=renglones, colLabels=["Codigo", "Descripcion", "Cant", "P. Unit"],
                       loc="upper center")
    tabla.scale(1, 2)
    ruta = os.path.join(base, nombre)
    figura.savefig(ruta, format="pdf")
    return ruta


def pdf_de_texto(nombre, lineas):
    figura = Figure(figsize=(8.27, 11.69))
    FigureCanvasPdf(figura)
    ejes = figura.add_subplot(111)
    ejes.axis("off")
    for n, linea in enumerate(lineas):
        ejes.text(0.02, 0.9 - 0.04 * n, linea, family="monospace", fontsize=9)
    ruta = os.path.join(base, nombre)
    figura.savefig(ruta, format="pdf")
    return ruta


def resumen(items):
    return sorted((i.codigo, i.cantidad, i.precio_compra) for i in items)


ESPERADO = sorted([(OREO, 12, 450.5), (YERBA, 24, 3500.0), (FIDEOS, 10, 3500.0)])

# --------------------------------------------------------------------- #
# 1. Factura con tabla: cada renglón UNA vez
# --------------------------------------------------------------------- #
con_tabla = pdf_con_tabla("con_tabla.pdf", RENGLONES)
import pdfplumber
with pdfplumber.open(con_tabla) as _pdf:
    tablas = _pdf.pages[0].extract_tables()
if not tablas:
    fallos.append("la prueba no llegó a armar un PDF con tabla: no prueba el renglón leído dos veces")

leida = pdf_import.parsear_factura_pdf(con_tabla)
if len(leida.items) != 3:
    fallos.append(f"una factura de 3 renglones con tabla dio {len(leida.items)} ítems "
                   f"(cada renglón leído por la tabla Y por el texto): el stock se sumaría doble")
elif resumen(leida.items) != ESPERADO:
    fallos.append(f"la factura con tabla se leyó {resumen(leida.items)} y era {ESPERADO}")
else:
    print("OK: una factura con tabla da cada renglón UNA sola vez (3 ítems, no 6)")

# --------------------------------------------------------------------- #
# 2. Factura de texto suelto: igual que siempre
# --------------------------------------------------------------------- #
de_texto = pdf_de_texto("sin_tabla.pdf", [
    f"{OREO}  GALLETITAS OREO 118G     x12    $450,50",
    f"{YERBA}  YERBA PLAYADITO 1KG      x24    $3.500,00",
    f"{FIDEOS}  FIDEOS GUISERO 500G      x10    $3.500"])
leida_texto = pdf_import.parsear_factura_pdf(de_texto)
if resumen(leida_texto.items) != ESPERADO:
    fallos.append(f"la factura de texto se leyó {resumen(leida_texto.items)} y era {ESPERADO}")
else:
    print("OK: una factura de texto suelto se lee igual (3 ítems)")

# --------------------------------------------------------------------- #
# 3. "3.500" son tres mil quinientos
# --------------------------------------------------------------------- #
casos = {"3.500": 3500.0, "3.500,00": 3500.0, "3500.00": 3500.0, "3,500.00": 3500.0,
         "450,50": 450.5, "12.50": 12.5, "1.234.567": 1234567.0, "980": 980.0}
mal = {t: pdf_import._normalizar_numero(t) for t in casos if pdf_import._normalizar_numero(t) != casos[t]}
if mal:
    fallos.append(f"precios de factura mal leídos: {mal} (esperado {({t: casos[t] for t in mal})})")
else:
    print("OK: '3.500' se lee tres mil quinientos, con el mismo criterio que la hoja de precios")
try:
    pdf_import._normalizar_numero("abc")
    fallos.append("un precio que no es un número no dio error")
except ValueError:
    pass

# --------------------------------------------------------------------- #
# 4. El mismo producto facturado dos veces DE VERDAD sigue contando dos
# --------------------------------------------------------------------- #
repetida = pdf_con_tabla("repetida.pdf", [RENGLONES[0], RENGLONES[0], RENGLONES[1]])
leida_rep = pdf_import.parsear_factura_pdf(repetida)
oreos = [i for i in leida_rep.items if i.codigo == OREO]
if len(oreos) != 2 or len(leida_rep.items) != 3:
    fallos.append(f"dos renglones idénticos en la factura tenían que dar 2 ítems de ese producto: "
                   f"dio {len(oreos)} (y {len(leida_rep.items)} en total)")
else:
    print("OK: el mismo producto facturado dos veces en la factura sigue contando dos renglones")

# --------------------------------------------------------------------- #
# 5. El camino entero por el Panel: lo que queda GUARDADO
# --------------------------------------------------------------------- #
from tkinter import filedialog, messagebox

preguntas = []
messagebox.showinfo = lambda *a, **k: None
messagebox.showwarning = lambda *a, **k: None
messagebox.showerror = lambda t, m="", **k: fallos.append(f"el Panel mostró un error: {t} {m}")
messagebox.askyesno = lambda t, m="", **k: (preguntas.append(m), True)[1]
filedialog.askopenfilename = lambda **k: con_tabla

from apps.master_dueno.main import AppDueno
app = AppDueno()
app.update()
app._subir_pdf()
app.update()

if not preguntas or "3 ítems" not in preguntas[-1]:
    fallos.append(f"el Panel no preguntó por 3 ítems: {preguntas[-1:]}")
guardado = {c: (producto(c)["stock"], producto(c)["precio_compra"]) for c in (OREO, YERBA, FIDEOS)}
esperado = {OREO: (12, 450.5), YERBA: (24, 3500.0), FIDEOS: (10, 3500.0)}
if guardado != esperado:
    fallos.append(f"después de cargar la factura por el Panel quedó (stock, costo) {guardado} "
                   f"y tenía que quedar {esperado}")
else:
    print("OK: por el Panel, la factura suma 12 / 24 / 10 (no el doble) y el costo queda en $ 3.500")
app.destroy()

# --------------------------------------------------------------------- #
# 6. Una cantidad que no es un entero positivo no toca el stock
# --------------------------------------------------------------------- #
antes = producto(OREO)["stock"]
resultado = stock_service.sumar_stock_por_factura_pdf(
    [{"codigo": OREO, "cantidad": -5, "precio_compra": 1.0},
     {"codigo": OREO, "cantidad": 2.5, "precio_compra": 1.0},
     {"codigo": OREO, "cantidad": 0, "precio_compra": 1.0},
     {"codigo": YERBA, "cantidad": 1, "precio_compra": 3600.0}],
    usuario="test", factura_nombre="rara.pdf")
if [r["ok"] for r in resultado] != [False, False, False, True]:
    fallos.append(f"cantidades inválidas en una factura: {resultado}")
elif producto(OREO)["stock"] != antes or producto(OREO)["precio_compra"] != 450.5:
    fallos.append("una cantidad negativa, fraccionada o en cero modificó el stock o el costo")
elif producto(YERBA)["stock"] != 25:
    fallos.append("el renglón bueno de la factura no se aplicó por culpa de los malos")
else:
    print("OK: una cantidad negativa, con coma o en cero no toca el stock (y el resto se carga)")

print()
if fallos:
    print("=== FALLOS FACTURAS PDF ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== FACTURAS PDF OK ===")
