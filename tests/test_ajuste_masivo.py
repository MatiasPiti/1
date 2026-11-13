"""El ajuste masivo de precios no puede cobrar $ 100 de más por un error de cuenta.

La regla del negocio: un aumento se redondea hacia ARRIBA a la centena
(2500 + 3 % = 2575 -> 2600), y un resultado que ya cae justo en la centena
no se toca. Lo que pasaba: 3000 + 10 % tiene que dar 3300, pero la cuenta
con decimales binarios da 3300.0000000000005 y el techo a la centena lo
llevaba a 3400. Cien pesos de más en cada producto de precio "redondo" cada
vez que el dueño sube un grupo (pasaba con 3000, 6000, 7000, 11000... al
10 %, y con otros al 8 % y al 12 %).

Se prueba la cuenta contra aritmética exacta (fracciones, sin decimales
binarios) y, lo que importa de verdad, el precio que queda GUARDADO al
apretar "Aplicar %" en el Panel sobre un filtro.
"""
import math
import os
import sys
import tempfile
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="ajuste_masivo_")
from pos_core import paths
paths.set_base_override(base)
from pos_core import db
db.preparar_base()

from pos_core import bulk_edit, products, filters

fallos = []


def precio_de(codigo):
    return db.get_connection().execute(
        "SELECT precio_venta FROM Productos WHERE codigo = ?", (codigo,)).fetchone()["precio_venta"]


# --------------------------------------------------------------------- #
# 1. Los casos que se ven en el mostrador
# --------------------------------------------------------------------- #
casos = [
    # (precio, porcentaje, esperado, por qué)
    (2500, 3, 2600, "el ejemplo de la regla: 2575 sube a 2600"),
    (3000, 10, 3300, "cae justo en la centena: no puede ir a 3400"),
    (6000, 10, 6600, "idem"),
    (7500, 8, 8100, "idem, al 8 %"),
    (2600, 0, 2600, "un múltiplo exacto de 100 no se mueve"),
    (2500, -5, 2400, "una baja también redondea hacia arriba: 2375 -> 2400"),
    (100, -100, 0, "nunca queda negativo"),
]
for precio, pct, esperado, motivo in casos:
    dado = bulk_edit.calcular_nuevo_precio(precio, porcentaje=pct)
    if dado != esperado:
        fallos.append(f"{precio} {pct:+}% dio {dado} y tenía que dar {esperado} ({motivo})")
if not fallos:
    print(f"OK: los {len(casos)} casos del mostrador dan lo que dice la regla (3000 + 10 % = 3300)")

if bulk_edit.calcular_nuevo_precio(2500.10, monto_fijo=99.90) != 2600:
    fallos.append("2500,10 + 99,90 no dio 2600")
if bulk_edit.calcular_nuevo_precio(1234.56, porcentaje=3.5, redondear=False) != 1277.77:
    fallos.append("sin redondear a la centena tiene que quedar en centavos (1277,77)")

# --------------------------------------------------------------------- #
# 2. Contra aritmética exacta, en una grilla grande
# --------------------------------------------------------------------- #
distintos = []
total = 0
for pct in (-50, -10, -5, 3, 3.5, 5, 7.5, 8, 10, 12, 15, 20, 25, 30, 50, 100):
    for centavos in range(10000, 3000001, 2500):       # de $ 100 a $ 30.000, de a $ 25
        precio = centavos / 100
        exacto = Fraction(centavos, 100) * (1 + Fraction(str(pct)) / 100)
        en_centavos = Fraction(round(exacto * 100), 100)
        esperado = max(0, math.ceil(en_centavos / 100) * 100)
        dado = bulk_edit.calcular_nuevo_precio(precio, porcentaje=pct)
        total += 1
        if dado != esperado:
            distintos.append((precio, pct, dado, esperado))
if distintos:
    fallos.append(f"{len(distintos)} de {total} ajustes no dan lo que da la cuenta exacta; "
                   f"los primeros: {distintos[:5]}")
else:
    print(f"OK: {total} ajustes comparados contra la cuenta exacta, cero diferencias")

# --------------------------------------------------------------------- #
# 3. Lo que queda guardado al apretar "Aplicar %" en el Panel
# --------------------------------------------------------------------- #
products.crear_producto(codigo="F1", nombre="FIDEOS", precio_venta=3000.0, usuario="test")
products.crear_producto(codigo="F2", nombre="FIDEOS TIRABUZON", precio_venta=2500.0, usuario="test")
products.crear_producto(codigo="F3", nombre="FIDEOS MOÑO", precio_venta=7000.0, usuario="test")
filters.guardar_filtro_manual("FIDEOS", ["F1", "F2", "F3"])

from tkinter import messagebox
carteles = []
for nombre in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, nombre, lambda *a, _n=nombre, **k: carteles.append((_n, a)))
messagebox.askyesno = lambda *a, **k: True

from apps.master_dueno.main import AppDueno
app = AppDueno()
app.update()
app.buscar_filtro.insert(0, "fideos")
app._refrescar_lista_filtros()
app.lista_filtros.selection_set(0)
app._cargar_filtro_guardado()
app.update()
app.bulk_pct.insert(0, "10")
app._aplicar_bulk_pct()
app.update()

guardados = {c: precio_de(c) for c in ("F1", "F2", "F3")}
esperados = {"F1": 3300.0, "F2": 2800.0, "F3": 7700.0}
if guardados != esperados:
    fallos.append(f"'Aplicar %' con 10 dejó guardado {guardados} y tenía que dejar {esperados}")
else:
    print("OK: 'Aplicar %' del Panel deja 3000 -> 3300, 2500 -> 2800 y 7000 -> 7700")

# Lo que muestra la grilla del filtro es lo que quedó guardado.
en_pantalla = {app.bulk_tree.item(i, "values")[0]: float(app.bulk_tree.item(i, "values")[2])
               for i in app.bulk_tree.get_children()}
if en_pantalla != esperados:
    fallos.append(f"la grilla del filtro muestra {en_pantalla} y lo guardado es {esperados}")
else:
    print("OK: la grilla del filtro muestra los mismos precios que quedaron guardados")
# --------------------------------------------------------------------- #
# 4. Repetir un aumento que "no contestó" no lo suma dos veces
# --------------------------------------------------------------------- #
# Desde el Dueño Remoto, un aumento sobre un grupo grande puede tardar más
# que la espera de la conexión: el local lo aplica, pero al Panel le llega
# un error (o, antes, nada). El dueño aprieta de nuevo — y el aumento se
# sumaba otra vez a lo que ya había subido. Se simula eso: la primera vez el
# cambio se aplica y la respuesta se pierde.
from pos_core.dueno_backend import RemoteError

carteles.clear()
real = app.backend.bulk_edit.aplicar_ajuste_masivo
llamadas = []


def _se_aplica_pero_no_contesta(*a, **k):
    llamadas.append(k)
    real(*a, **k)
    raise RemoteError("No se pudo conectar con el local: Read timed out.")


app.backend.bulk_edit.aplicar_ajuste_masivo = _se_aplica_pero_no_contesta
app._aplicar_bulk_pct()                         # +10 %: se aplica, pero el Panel no se entera
app.update()
tras_la_primera = {c: precio_de(c) for c in ("F1", "F2", "F3")}
if tras_la_primera != {"F1": 3700.0, "F2": 3100.0, "F3": 8500.0}:
    fallos.append(f"la prueba no llegó a aplicar el segundo aumento: {tras_la_primera}")
if not carteles or carteles[-1][0] != "showerror":
    fallos.append(f"el aumento no contestó y el Panel no avisó nada (el botón parecía muerto): {carteles[-1:]}")
elif not llamadas or llamadas[0].get("esperados") != {"F1": 3300.0, "F2": 2800.0, "F3": 7700.0}:
    fallos.append(f"el Panel no mandó los precios que estaba mostrando: {llamadas[:1]}")
else:
    print("OK: si el aumento no contesta, el Panel avisa y no da nada por hecho")

app.backend.bulk_edit.aplicar_ajuste_masivo = real
carteles.clear()
app._aplicar_bulk_pct()                         # el dueño aprieta de nuevo
app.update()
tras_repetir = {c: precio_de(c) for c in ("F1", "F2", "F3")}
if tras_repetir != tras_la_primera:
    fallos.append(f"repetir el aumento lo sumó DOS veces: de {tras_la_primera} pasó a {tras_repetir}")
elif not carteles or carteles[-1][0] != "showwarning" or "NO se tocaron" not in str(carteles[-1][1]):
    fallos.append(f"repetir no dijo que esos productos no se tocaron: {carteles[-1:]}")
else:
    print("OK: repetir el aumento no lo suma dos veces, y el cartel dice qué no se tocó")
en_pantalla = {app.bulk_tree.item(i, "values")[0]: float(app.bulk_tree.item(i, "values")[2])
               for i in app.bulk_tree.get_children()}
if en_pantalla != tras_la_primera:
    fallos.append(f"después del rechazo la grilla muestra {en_pantalla} y lo guardado es {tras_la_primera}")
else:
    print("OK: después del rechazo la grilla muestra los precios que están guardados")

# Y ahora sí, viendo los precios de verdad, un aumento nuevo se aplica.
carteles.clear()
app._aplicar_bulk_pct()
app.update()
if precio_de("F1") != 4100.0:          # 3700 + 10 % = 4070 -> 4100
    fallos.append(f"con la grilla al día, un aumento nuevo no se aplicó: F1 quedó en {precio_de('F1')}")
else:
    print("OK: con la grilla al día, un aumento nuevo se aplica normalmente")

# --------------------------------------------------------------------- #
# 5. Un botón que falla por la conexión no falla en silencio
# --------------------------------------------------------------------- #
carteles.clear()
app.after(0, lambda: (_ for _ in ()).throw(RemoteError("No se pudo conectar con el local: se cortó")))
app.update()
if not carteles or "No se pudo completar" not in str(carteles[-1][1][0]):
    fallos.append(f"un error de conexión en un botón cualquiera no mostró ningún cartel: {carteles}")
else:
    print("OK: un error de conexión que nadie atajó se muestra en un cartel (antes no pasaba nada)")

app.destroy()

# --------------------------------------------------------------------- #
# 6. Lo mismo desde el Dueño Remoto, contra la API levantada de verdad
# --------------------------------------------------------------------- #
# Los precios que el Panel está mostrando viajan por la red como parte del
# pedido: si no llegaran bien, el aumento se rechazaría entero o, peor, se
# aplicaría sin el freno.
import socket
from services import remote_api
from pos_core.dueno_backend import RemoteBackend

_libre = socket.socket()
_libre.bind(("127.0.0.1", 0))
puerto = _libre.getsockname()[1]
_libre.close()
servidor = remote_api.iniciar_servidor(puerto=puerto, token="token-de-prueba", escuchar_en="127.0.0.1")
try:
    remoto = AppDueno(backend=RemoteBackend(f"http://127.0.0.1:{puerto}", "token-de-prueba"))
    remoto.update()
    remoto.buscar_filtro.insert(0, "fideos")
    remoto._refrescar_lista_filtros()
    remoto.lista_filtros.selection_set(0)
    remoto._cargar_filtro_guardado()
    remoto.update()
    antes = {c: precio_de(c) for c in ("F1", "F2", "F3")}
    carteles.clear()
    remoto.bulk_pct.insert(0, "10")
    remoto._aplicar_bulk_pct()
    remoto.update()
    despues = {c: precio_de(c) for c in ("F1", "F2", "F3")}
    esperado_remoto = {c: float(bulk_edit.calcular_nuevo_precio(p, porcentaje=10)) for c, p in antes.items()}
    if despues != esperado_remoto or not carteles or carteles[-1][0] != "showinfo":
        fallos.append(f"desde el Dueño Remoto, 'Aplicar %' dejó {despues} (esperado {esperado_remoto}); "
                       f"cartel: {carteles[-1:]}")
    else:
        print("OK: desde el Dueño Remoto (API real) el aumento se aplica una vez, con el freno puesto")

    # Otro programa cambia un precio mientras el Panel remoto tiene la lista abierta.
    from pos_core import precios as _precios
    _precios.actualizar_precios(codigo="F1", precio_final=9999, usuario="otro")
    carteles.clear()
    remoto._aplicar_bulk_pct()
    remoto.update()
    if precio_de("F1") != 9999.0:
        fallos.append(f"un precio que cambió por otro lado se pisó igual: F1 quedó en {precio_de('F1')}")
    elif not carteles or carteles[-1][0] != "showwarning":
        fallos.append("un producto que no se tocó no apareció en ningún cartel")
    else:
        print("OK: un precio que cambió por otro lado mientras tanto no se pisa, y se avisa")
    remoto.destroy()
finally:
    servidor.shutdown()
    servidor.server_close()

print()
if fallos:
    print("=== FALLOS AJUSTE MASIVO ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== AJUSTE MASIVO OK ===")
