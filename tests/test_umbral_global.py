"""El umbral global tiene que poder apagarse. A Leo no se le podía.

El caso real, tal cual pasó (septiembre 2026): Leo puso el umbral global en
20/20, el bot mandó las alertas, y después puso 0/0 para apagarlas y le
siguieron llegando. No era culpa de él: al mandar cada alerta, el sistema le
creaba al producto un umbral PROPIO con el 20/20 congelado adentro, y desde
ese momento el global dejaba de aplicarle. Poner 0/0 cambiaba un número que
ya nadie miraba.

Con el catálogo real es peor todavía: entró entero con stock 0, así que con
mínimo 20 califican los 2529 productos.

Esta prueba corre el bot de verdad (con el envío simulado, no la red) y mira
lo que le importa al negocio: ¿siguen llegando alertas o no?
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

base = tempfile.mkdtemp(prefix="umbral_")
from pos_core import paths
paths.set_base_override(base)
from pos_core import db
db.preparar_base()

from pos_core import products, alerts, config
import pos_core.telegram_bot as tb

fallos = []

for n in range(6):
    products.crear_producto(codigo=f"P{n}", nombre=f"Producto {n}", precio_venta=100.0,
                            stock_inicial=0, usuario="test")   # stock 0, como el catálogo real

config.actualizar_config_dict({"telegram": {"bot_token": "falso", "chat_id_default": "123",
                                            "habilitado": "true"}})

enviados = []


class _RespuestaOk:
    ok = True


def _post_simulado(*a, **k):
    enviados.append(k.get("json", {}).get("text", ""))
    return _RespuestaOk()


tb.requests = type("R", (), {"post": staticmethod(_post_simulado),
                              "RequestException": Exception})()


def vuelta_de_alertas():
    """Una revisión completa, sin que el cooldown tape el resultado."""
    enviados.clear()
    with db.transaction() as conn:
        conn.execute("DELETE FROM Alertas_Enviadas")
    tb.revisar_umbrales_y_alertar()
    return len(enviados)


def umbrales_propios():
    return len(alerts.listar_umbrales_por_producto())


# ---------------------------------------------------------------- #
# 1. Con umbral puesto, las alertas salen (si no, no se prueba nada)
# ---------------------------------------------------------------- #
alerts.set_umbral_global(20, 20)
salieron = vuelta_de_alertas()
print(f"global 20/20 -> {salieron} alertas")
if salieron != 6:
    fallos.append(f"con el global en 20/20 salieron {salieron} alertas, esperaba 6")

# ---------------------------------------------------------------- #
# 2. Mandar alertas NO puede configurarle un umbral a nadie
# ---------------------------------------------------------------- #
# Esto es el bug: antes acá quedaban 6 umbrales propios con 20/20 adentro.
propios = umbrales_propios()
print(f"umbrales propios creados por mandar alertas: {propios}")
if propios:
    fallos.append(f"mandar alertas le creó umbral propio a {propios} producto(s): "
                   f"esos productos dejan de seguir el umbral global para siempre")

# ---------------------------------------------------------------- #
# 3. Poner 0/0 APAGA las alertas — que es lo que Leo no podía hacer
# ---------------------------------------------------------------- #
alerts.set_umbral_global(0, 0)
salieron = vuelta_de_alertas()
print(f"global 0/0 -> {salieron} alertas")
if salieron:
    fallos.append(f"con el global en 0/0 siguieron llegando {salieron} alertas: "
                   f"el dueño no tiene forma de apagarlas")
else:
    print("OK: con 0 y 0 no llega ninguna alerta")

# ---------------------------------------------------------------- #
# 4. Apagar una sola mitad también tiene que andar
# ---------------------------------------------------------------- #
alerts.set_umbral_global(20, 0)
salieron = vuelta_de_alertas()
if salieron != 6:
    fallos.append(f"con 20/0 (solo stock bajo) salieron {salieron} alertas, esperaba 6")
elif any("SOBRE-STOCK" in e for e in enviados):
    fallos.append("con el máximo en 0 igual avisó por sobre-stock")
else:
    print("OK: con máximo en 0 avisa por stock bajo y nunca por sobre-stock")

# ---------------------------------------------------------------- #
# 5. El cooldown sigue funcionando (no puede spamear cada 5 minutos)
# ---------------------------------------------------------------- #
enviados.clear()
tb.revisar_umbrales_y_alertar()      # segunda vuelta seguida, sin limpiar
if enviados:
    fallos.append(f"mandó {len(enviados)} alertas repetidas en la vuelta siguiente: "
                   f"se rompió el cooldown de 4 horas")
else:
    print("OK: el cooldown sigue frenando la alerta repetida")

# ---------------------------------------------------------------- #
# 6. Un umbral propio puesto A MANO tiene que seguir pisando al global
# ---------------------------------------------------------------- #
alerts.set_umbral_global(0, 0)
alerts.set_umbral_producto("P1", 20, 0)
salieron = vuelta_de_alertas()
if salieron != 1:
    fallos.append(f"con el global apagado y UN umbral propio salieron {salieron} alertas, esperaba 1")
elif "P1" not in enviados[0]:
    fallos.append(f"la alerta no era del producto con umbral propio: {enviados[0]}")
else:
    print("OK: el umbral propio que se pone a mano sigue mandando sobre el global")

# ---------------------------------------------------------------- #
# 7. El botón "Quitar TODOS": deja a todos siguiendo el global
# ---------------------------------------------------------------- #
alerts.set_umbral_producto("P2", 20, 0)
alerts.set_umbral_producto("P3", 20, 0)
antes = umbrales_propios()
quitados = alerts.quitar_todos_los_umbrales_propios()
if quitados != antes or umbrales_propios():
    fallos.append(f"quitar todos los umbrales propios sacó {quitados} de {antes} y quedaron "
                   f"{umbrales_propios()}")
else:
    salieron = vuelta_de_alertas()
    if salieron:
        fallos.append(f"tras quitar todos los umbrales propios y con el global en 0/0 "
                       f"siguieron saliendo {salieron} alertas")
    else:
        print(f"OK: quitar los {quitados} umbrales propios + global 0/0 -> silencio total")

# y no puede haberse llevado puesto el umbral global
conn = db.get_connection()
globales = conn.execute(
    "SELECT COUNT(*) FROM Configuracion_Alertas WHERE producto_codigo IS NULL").fetchone()[0]
if globales != 1:
    fallos.append(f"quitar los umbrales propios dejó {globales} filas globales (esperaba 1)")

# ---------------------------------------------------------------- #
# 8. Una base vieja se migra sin perder el cooldown
# ---------------------------------------------------------------- #
otra = tempfile.mkdtemp(prefix="umbral_vieja_")
paths.set_base_override(otra)
db.cerrar_conexion()
db.init_db()
with db.transaction() as conn:
    conn.execute("DROP TABLE IF EXISTS Alertas_Enviadas")
    conn.execute("""INSERT INTO Configuracion_Alertas
                    (producto_codigo, stock_minimo, stock_maximo, ultima_alerta_enviada, activo)
                    VALUES ('P9', 20, 20, '2026-09-16T10:00:00.000', 1)""")
cambios = db.aplicar_migraciones()
conn = db.get_connection()
fila = conn.execute("SELECT ultima_alerta_enviada FROM Alertas_Enviadas "
                    "WHERE producto_codigo = 'P9'").fetchone()
if not fila or fila[0] != "2026-09-16T10:00:00.000":
    fallos.append(f"la migración perdió el cooldown de una base vieja: {fila}")
else:
    print(f"OK: la migración mudó el cooldown sin perderlo ({[c for c in cambios if 'Alertas_Enviadas' in c]})")

print()
if fallos:
    print("=== FALLOS UMBRAL GLOBAL ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== UMBRAL GLOBAL OK ===")
