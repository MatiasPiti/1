"""La revisión final del Actualizador: que haga sola lo que antes había que
tipear a mano en la PC del local, y que NUNCA voltee una actualización que
ya terminó bien.

Lo que se prueba es lo que va a pasar en el local:
  - Corre sola al terminar de actualizar la PC del local, y NO en la
    laptop de Leo (ahí no hay servicio, ni puerto, ni respaldos).
  - Aunque falle TODO lo que consulta (sin Windows, sin permisos, sin
    Defender), la actualización sigue dada por buena: las filas dicen NO y
    se sigue. Regla 6.
  - Las filas dicen la verdad: una copia de hace 20 días no puede marcar SI.
  - Se lleva al Escritorio la evidencia del blindaje, que es un archivo
    único que el próximo blindaje pisa.
  - Deja el informe escrito para poder mandarlo sin tipearlo.
"""
import atexit
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apps.actualizador import main as act

fallos = []

# ---------------------------------------------------------------- #
# Aislar la carpeta del usuario ANTES de correr nada.
#
# Esta prueba escribe archivos en el Escritorio (la evidencia del blindaje
# y el informe). Sin esto los deja en el Escritorio REAL de quien corre las
# pruebas y, peor, le pisa el estado_antes_del_blindaje.txt de verdad si lo
# tenía ahí — que es justo el archivo que no se puede perder.
#
# En Windows expanduser("~") NO mira HOME: mira USERPROFILE, y si no está,
# HOMEDRIVE+HOMEPATH. Aislar solo con HOME funcionaba en Linux y en Windows
# escribía en el Escritorio de verdad mientras la prueba buscaba el archivo
# en la carpeta temporal: dos fallos que parecían del programa y eran de
# la prueba.
# ---------------------------------------------------------------- #
_CLAVES_CASA = ("HOME", "USERPROFILE", "HOMEPATH", "HOMEDRIVE", "OneDrive", "OneDriveConsumer")
_casa_original = {k: os.environ.get(k) for k in _CLAVES_CASA}
escritorio = tempfile.mkdtemp(prefix="escritorio_")
os.makedirs(os.path.join(escritorio, "Desktop"))
for _k in _CLAVES_CASA:
    os.environ.pop(_k, None)
os.environ["HOME"] = escritorio
os.environ["USERPROFILE"] = escritorio


@atexit.register
def _devolver_la_casa():
    for k, v in _casa_original.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


if os.path.expanduser("~") != escritorio:
    fallos.append(f"no se pudo aislar la carpeta del usuario: expanduser da "
                   f"{os.path.expanduser('~')!r} — la prueba estaría escribiendo en el "
                   f"Escritorio real de quien la corre")


class ActualizadorDePrueba:
    """El Actualizador sin ventana: solo los métodos de la revisión final.

    Se arma así en vez de abrir la app para poder mirar cada fila por
    separado; la revisión no toca Tk salvo por _log.
    """
    def __init__(self):
        self.lineas = []

    def _log(self, texto):
        self.lineas.append(texto)

    _revision_final = act.Actualizador._revision_final
    _puerto_remoto = act.Actualizador._puerto_remoto
    _excluir_del_antivirus = act.Actualizador._excluir_del_antivirus
    _estado_de_los_respaldos = act.Actualizador._estado_de_los_respaldos
    _rescatar_evidencia = act.Actualizador._rescatar_evidencia
    _guardar_informe = act.Actualizador._guardar_informe


def instalacion(con_backup_dias=None, con_evidencia=False, puerto=None):
    raiz = tempfile.mkdtemp(prefix="revision_")
    os.makedirs(os.path.join(raiz, "database"))
    os.makedirs(os.path.join(raiz, "backups"))
    if puerto is not None:
        with open(os.path.join(raiz, "config.ini"), "w", encoding="utf-8") as f:
            f.write(f"[remoto]\nhabilitado = true\npuerto = {puerto}\n")
    if con_backup_dias is not None:
        copia = os.path.join(raiz, "backups", "stock_2026-09-13.db")
        with open(copia, "w") as f:
            f.write("base")
        viejo = time.time() - con_backup_dias * 86400
        os.utime(copia, (viejo, viejo))
    if con_evidencia:
        os.makedirs(os.path.join(raiz, "watchdog"))
        with open(os.path.join(raiz, "watchdog", "estado_antes_del_blindaje.txt"),
                  "w", encoding="utf-8") as f:
            f.write("TIPO_INICIO: 3 DEMAND_START")
    return raiz


def filas_de(lineas):
    """(texto, ok) de cada fila de la tabla que se mostró en pantalla."""
    salida = []
    for l in lineas:
        limpio = l.strip()
        if limpio.startswith("[SI]") or limpio.startswith("[NO]"):
            salida.append((limpio[4:].strip(), limpio.startswith("[SI]")))
    return salida


# ---------------------------------------------------------------- #
# 1. Sin Windows falla todo, y aun así la actualización queda dada por buena
# ---------------------------------------------------------------- #
app = ActualizadorDePrueba()
destino = instalacion(con_backup_dias=0, con_evidencia=True, puerto=8765)
try:
    app._revision_final(destino)
except Exception as e:
    fallos.append(f"la revisión final lanzó una excepción y voltearía la actualización: {e!r}")

filas = filas_de(app.lineas)
print("FILAS DE LA TABLA:")
for texto, ok in filas:
    print(f"   [{'SI' if ok else 'NO'}] {texto}")
if len(filas) < 5:
    fallos.append(f"la tabla salió con {len(filas)} filas, esperaba al menos 5")

# La verificación tiene que VERIFICAR: acá no hay Windows ni Defender, así
# que el antivirus y el servicio no pueden decir SI.
for aguja in ("antivirus", "servicio"):
    mintieron = [t for t, ok in filas if aguja in t.lower() and ok]
    if mintieron:
        fallos.append(f"sin Windows, estas filas se ganaron un SI sin comprobar nada: {mintieron}")
if not fallos:
    print("OK: sin Windows la revisión no explota y ninguna fila se gana un SI de prestado")

# ---------------------------------------------------------------- #
# 2. El respaldo dice la verdad
# ---------------------------------------------------------------- #
app = ActualizadorDePrueba()
ok, detalle = app._estado_de_los_respaldos(instalacion(con_backup_dias=0))
if not ok:
    fallos.append(f"con una copia de hoy dijo que NO hay respaldo reciente: {detalle}")

import re
app = ActualizadorDePrueba()
ok, detalle = app._estado_de_los_respaldos(instalacion(con_backup_dias=60))
# Se extraen los días del texto en vez de buscar "60" adentro: el nombre del
# archivo es stock_2026-..., así que buscar "20" para una copia de 20 días
# daba por buena la fila por el "20" de "2026" — una comprobación con
# dientes falsos.
dias = re.search(r"hace (\d+) d", detalle)
if ok:
    fallos.append("una copia de hace 60 días se marcó como respaldo reciente")
elif not dias:
    fallos.append(f"no dice de cuántos días es la copia vieja: {detalle!r}")
elif not (59 <= int(dias.group(1)) <= 61):
    fallos.append(f"dice {dias.group(1)} días para una copia de hace 60: {detalle!r}")
else:
    print(f"OK: copia de hace 60 días -> NO, y dice {dias.group(1)} días")

app = ActualizadorDePrueba()
ok, detalle = app._estado_de_los_respaldos(instalacion())
if ok:
    fallos.append("sin ninguna copia dijo que hay respaldo reciente")
else:
    print("OK: sin ninguna copia -> NO")

# ---------------------------------------------------------------- #
# 3. La evidencia del blindaje se copia de verdad
# ---------------------------------------------------------------- #
app = ActualizadorDePrueba()
destino = instalacion(con_evidencia=True)
ok, ruta = app._rescatar_evidencia(destino)
copiado = os.path.join(escritorio, "Desktop", "estado_antes_del_blindaje.txt")
if not ok or not os.path.isfile(copiado):
    fallos.append(f"no se copió la evidencia al Escritorio (ok={ok}, ruta={ruta})")
elif "DEMAND_START" not in open(copiado, encoding="utf-8").read():
    fallos.append("la evidencia copiada no tiene el contenido original")
else:
    print("OK: la evidencia del blindaje llega sola al Escritorio")

# Sin blindaje corrido en esa PC no es un pendiente: no debe salir NO.
app2 = ActualizadorDePrueba()
ok2, detalle2 = app2._rescatar_evidencia(instalacion())
if not ok2 or detalle2:
    fallos.append(f"sin evidencia lo trató como pendiente: ok={ok2} detalle={detalle2!r}")
else:
    print("OK: si esa PC nunca se blindó, no aparece como pendiente")

# El informe queda escrito para poder mandarlo
app3 = ActualizadorDePrueba()
app3._guardar_informe([("Una cosa", True, "salió bien"), ("Otra cosa", False, "falta esto")])
informe = os.path.join(escritorio, "Desktop", "otter_revision_final.txt")
if not os.path.isfile(informe):
    fallos.append("no se escribió el informe de la revisión final")
else:
    texto = open(informe, encoding="utf-8").read()
    if "[SI] Una cosa" not in texto or "[NO] Otra cosa" not in texto or "falta esto" not in texto:
        fallos.append(f"el informe no refleja la tabla:\n{texto}")
    else:
        print("OK: el informe queda en el Escritorio con las filas y el detalle")

# ---------------------------------------------------------------- #
# 4. El puerto sale del config del cliente, no de una suposición
# ---------------------------------------------------------------- #
app = ActualizadorDePrueba()
if app._puerto_remoto(instalacion(puerto=9100)) != 9100:
    fallos.append("no lee el puerto del config.ini del cliente")
elif app._puerto_remoto(instalacion()) != 8765:
    fallos.append("sin config.ini no cae en el puerto por defecto 8765")
else:
    print("OK: el puerto sale del config.ini del cliente (8765 solo como último recurso)")

# ---------------------------------------------------------------- #
# 5. En la laptop de Leo NO tiene que correr
# ---------------------------------------------------------------- #
codigo = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "apps", "actualizador", "main.py"), encoding="utf-8").read()
if 'if modo == "local":\n            try:\n                self._revision_final(destino)' not in codigo:
    fallos.append("la revisión final no está condicionada a modo == 'local': en la laptop de "
                   "Leo no hay servicio, ni puerto, ni respaldos que revisar")
else:
    print("OK: la revisión final solo corre en la PC del local")

print()
if fallos:
    print("=== FALLOS REVISIÓN FINAL ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== REVISIÓN FINAL OK ===")
