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

# ---------------------------------------------------------------- #
# 9. Por la PANTALLA del Panel, que es como lo hace el dueño
# ---------------------------------------------------------------- #
# Hasta acá se llamó a la función directo. El dueño no hace eso: escribe en
# dos campos y aprieta un botón, y ahí puede haber un paso roto en el medio.
import tkinter as tk
from tkinter import messagebox
messagebox.showinfo = lambda *a, **k: None
messagebox.showwarning = lambda *a, **k: None
messagebox.showerror = lambda *a, **k: print("   ERROR DIALOG:", a)
messagebox.askyesno = lambda *a, **k: True

paths.set_base_override(base)
db.cerrar_conexion()
from apps.master_dueno.main import AppDueno

alerts.quitar_todos_los_umbrales_propios()
alerts.set_umbral_global(20, 20)
app = AppDueno()
app.update()

# Los campos tienen que MOSTRAR lo que está puesto. Si abren vacíos, el dueño
# no puede ver qué hay, y apretar Guardar sin escribir nada lo deja en 0/0 —
# apaga todas las alertas — sin decir una palabra.
if (app.um_min.get(), app.um_max.get()) != ("20", "20"):
    fallos.append(f"la pantalla abrió los campos del umbral global en "
                   f"{app.um_min.get()!r}/{app.um_max.get()!r} en vez de mostrar el 20/20 configurado")
else:
    print("OK: la pantalla muestra el umbral que está puesto, no campos vacíos")

app.um_min.delete(0, "end"); app.um_min.insert(0, "0")
app.um_max.delete(0, "end"); app.um_max.insert(0, "0")
app._guardar_umbrales()
app.update()
salieron = vuelta_de_alertas()
if salieron:
    fallos.append(f"escribiendo 0 y 0 en la pantalla y apretando Guardar siguieron "
                   f"saliendo {salieron} alertas")
elif (app.um_min.get(), app.um_max.get()) != ("0", "0"):
    fallos.append(f"tras guardar, la pantalla quedó mostrando "
                   f"{app.um_min.get()!r}/{app.um_max.get()!r} y no el 0/0 guardado")
else:
    print("OK: 0 y 0 escritos en la pantalla real apagan las alertas")
app.destroy()

# ---------------------------------------------------------------- #
# 10. Y por la API REMOTA, que es como lo hace Leo desde su laptop
# ---------------------------------------------------------------- #
# El Dueño Remoto no toca la base: manda un POST. Si los argumentos
# posicionales no viajaran bien, el 0/0 no llegaría nunca y Leo seguiría
# sin poder apagarlas — exactamente el problema original, por otra causa.
import socket
from services import remote_api
from pos_core.dueno_backend import RemoteBackend

alerts.set_umbral_global(20, 20)
s_libre = socket.socket()
s_libre.bind(("127.0.0.1", 0))
puerto = s_libre.getsockname()[1]
s_libre.close()

servidor = remote_api.iniciar_servidor(puerto=puerto, token="token-de-prueba")
try:
    backend = RemoteBackend(f"http://127.0.0.1:{puerto}", "token-de-prueba")
    leido = backend.alerts.obtener_umbral_global()
    if leido != {"stock_minimo": 20, "stock_maximo": 20}:
        fallos.append(f"por la API remota el umbral global se leyó como {leido}")
    backend.alerts.set_umbral_global(0, 0)
    ahora = alerts.obtener_umbral_global()
    if ahora != {"stock_minimo": 0, "stock_maximo": 0}:
        fallos.append(f"el 0/0 mandado por la API remota no llegó a la base: quedó {ahora}")
    else:
        salieron = vuelta_de_alertas()
        if salieron:
            fallos.append(f"tras poner 0/0 por la API remota siguieron saliendo {salieron} alertas")
        else:
            print("OK: 0 y 0 desde el Dueño Remoto (API real, por red) apagan las alertas")
    quitados = backend.alerts.quitar_todos_los_umbrales_propios()
    print(f"OK: 'Quitar TODOS los umbrales propios' también anda por la API remota ({quitados})")
finally:
    try:
        servidor.shutdown()
    except Exception:
        pass

# ---------------------------------------------------------------- #
# 11. El Bot Token no puede quedar a la vista en la pantalla
# ---------------------------------------------------------------- #
# Regla 4: el bot_token del cliente YA se filtró una vez por una captura de
# pantalla de esta misma pestaña. El valor tiene que seguir adentro del
# campo (si no, guardar lo borraría), pero no dibujarse.
paths.set_base_override(base)
db.cerrar_conexion()
config.actualizar_config_dict({"telegram": {"bot_token": "123456:SECRETO-DE-PRUEBA",
                                            "chat_id_default": "999", "habilitado": "true"}})
app = AppDueno()
app.update()
if app.tg_token.cget("show") in ("", None):
    fallos.append("el Bot Token se dibuja en claro en la pantalla de Alertas: una captura de esa "
                   "pantalla lo filtra, que es exactamente como ya se filtró una vez")
elif app.tg_token.get() != "123456:SECRETO-DE-PRUEBA":
    fallos.append(f"tapar el token rompió su valor: el campo tiene {app.tg_token.get()!r}, "
                   f"así que guardar lo perdería")
else:
    print("OK: el Bot Token está tapado en pantalla pero entero adentro del campo")

# y el guardado no puede perderlo
app._guardar_config_telegram()
app.update()
if config.cargar_config().get("telegram", "bot_token") != "123456:SECRETO-DE-PRUEBA":
    fallos.append("guardar la config de Telegram con el token tapado lo borró")
else:
    print("OK: guardar con el token tapado no lo pierde")

# ---------------------------------------------------------------- #
# 12. El botón de probar dice QUÉ pasó, y nunca filtra el token
# ---------------------------------------------------------------- #
casos = [
    ({"habilitado": "false"}, "destildado", "destild"),
    ({"habilitado": "true", "bot_token": ""}, "sin token", "token"),
    ({"habilitado": "true", "bot_token": "123456:SECRETO-DE-PRUEBA", "chat_id_default": ""},
     "sin chat id", "chat id"),
]
for cambios, etiqueta, esperado in casos:
    base_cfg = {"bot_token": "123456:SECRETO-DE-PRUEBA", "chat_id_default": "999",
                "habilitado": "true"}
    base_cfg.update(cambios)
    config.actualizar_config_dict({"telegram": base_cfg})
    r = tb.probar_envio()
    if r["ok"] or esperado not in r["detalle"].lower():
        fallos.append(f"probar con el bot {etiqueta} dijo {r!r}; esperaba un aviso sobre {esperado!r}")
    if "SECRETO-DE-PRUEBA" in r["detalle"]:
        fallos.append(f"¡el mensaje de la prueba con el bot {etiqueta} incluye el TOKEN!: {r['detalle']}")
else:
    print("OK: probar el bot explica cada caso (destildado / sin token / sin chat) sin filtrar el token")

# el caso en que Telegram rechaza: tampoco puede filtrar el token
config.actualizar_config_dict({"telegram": {"bot_token": "123456:SECRETO-DE-PRUEBA",
                                            "chat_id_default": "999", "habilitado": "true"}})


class _Rechazo:
    ok = False
    status_code = 401

    @staticmethod
    def json():
        return {"description": "Unauthorized"}


tb.requests = type("R", (), {"post": staticmethod(lambda *a, **k: _Rechazo()),
                              "RequestException": Exception})()
r = tb.probar_envio()
if r["ok"]:
    fallos.append("un 401 de Telegram se reportó como envío exitoso")
elif "SECRETO-DE-PRUEBA" in r["detalle"]:
    fallos.append(f"¡el mensaje de error incluye el TOKEN!: {r['detalle']}")
elif "token" not in r["detalle"].lower():
    fallos.append(f"un 401 no explica que el problema es el token: {r['detalle']}")
else:
    print("OK: si Telegram rechaza el token, lo dice — y no lo escribe en el cartel")

app.destroy()

# ---------------------------------------------------------------- #
# 13. Quitar el umbral GLOBAL conserva todos los personalizados
# ---------------------------------------------------------------- #
# Lo que pidió Matías: que se borre el global y que NINGÚN umbral por
# producto se pierda. Los que tienen el suyo siguen avisando (su umbral
# nunca dependió del global); los que no, dejan de avisar.
tb.requests = type("R", (), {"post": staticmethod(_post_simulado),
                              "RequestException": Exception})()
alerts.quitar_todos_los_umbrales_propios()
alerts.set_umbral_global(20, 20)
alerts.set_umbral_producto("P1", 3, 0)
alerts.set_umbral_producto("P2", 7, 0)

antes = vuelta_de_alertas()
if antes != 6:
    fallos.append(f"con global 20/20 y 2 propios salieron {antes} alertas, esperaba 6")

quitados = alerts.quitar_umbral_global()
propios = {u["codigo"]: (u["stock_minimo"], u["stock_maximo"])
           for u in alerts.listar_umbrales_por_producto()}
if quitados < 1:
    fallos.append("quitar el umbral global no borró ninguna fila")
elif propios != {"P1": (3, 0), "P2": (7, 0)}:
    fallos.append(f"quitar el umbral global se llevó puestos los personalizados: {propios}")
else:
    print(f"OK: se borró el umbral global ({quitados} fila) y los 2 personalizados quedaron intactos")

if alerts.obtener_umbral_global() != {"stock_minimo": 0, "stock_maximo": 0}:
    fallos.append(f"tras borrarlo, el global se lee como {alerts.obtener_umbral_global()}")

despues = vuelta_de_alertas()
if despues != 2:
    fallos.append(f"tras quitar el global salieron {despues} alertas, esperaba 2 "
                   f"(solo los que tienen umbral propio)")
else:
    print("OK: sin global avisan SOLO los 2 con umbral propio, los otros 4 se callaron")

# y poner uno nuevo tiene que volver a funcionar (no quedó la base en un estado raro)
alerts.set_umbral_global(20, 20)
if vuelta_de_alertas() != 6:
    fallos.append("después de borrar el global, volver a ponerlo no reactivó las alertas")
else:
    print("OK: volver a poner un global después de borrarlo funciona")
alerts.quitar_umbral_global()
alerts.quitar_todos_los_umbrales_propios()

print()
if fallos:
    print("=== FALLOS UMBRAL GLOBAL ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== UMBRAL GLOBAL OK ===")
