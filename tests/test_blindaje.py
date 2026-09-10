"""OtterBlindaje: el .exe de un botón que deja la PC del local blindada.

Lo que se cuida acá es lo que no se ve mirando la ventana:

  - Que encuentre los .ps1 que va a ejecutar. Si no los encuentra, el
    botón no hace NADA y la ventana igual se ve bien: es la peor forma de
    fallar, porque Matías se va del local creyendo que quedó blindado.
  - Que no haya lógica duplicada. La app corre los .ps1, no los reescribe:
    si alguien copia los comandos adentro del .py, en tres meses una mitad
    va a estar arreglada y la otra no.
  - Que los .ps1 sigan cubriendo las causas conocidas (energía, botón,
    tapa, placa de red, puerto).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apps.blindaje.main import buscar_script, _decodificar, USUARIO_SOPORTE

fallos = []

# ---------------------------------------------------------------- #
# 1. Los scripts que el botón va a ejecutar tienen que existir
# ---------------------------------------------------------------- #
for nombre in ("blindar_local.ps1", "soporte_remoto.ps1"):
    ruta = buscar_script(nombre)
    print(f"ENCONTRADO: {nombre} -> {'sí' if ruta else 'NO'}")
    if not ruta:
        fallos.append(f"la app no encuentra {nombre}: el botón no haría nada")

blindar = open(buscar_script("blindar_local.ps1"), encoding="utf-8").read()
soporte = open(buscar_script("soporte_remoto.ps1"), encoding="utf-8").read()

# ---------------------------------------------------------------- #
# 2. Las causas conocidas siguen cubiertas
# ---------------------------------------------------------------- #
COBERTURA = {
    "el servicio arranca solo con Windows": "StartupType Automatic",
    "todos los planes de energía (no solo el activo)": "setacvalueindex",
    "el botón de encendido no suspende": "PBUTTONACTION",
    "cerrar la tapa no suspende": "LIDACTION",
    "la hibernación queda apagada": "hibernate off",
    "la placa de red no se apaga sola": "Disable-NetAdapterPowerManagement",
    "el watchdog mira EL PUERTO, no el estado del servicio": "PuertoVivo",
    "Tailscale queda en unattended": "unattended",
}
for que_cuida, marca in COBERTURA.items():
    if marca not in blindar:
        fallos.append(f"el blindaje ya no cubre: {que_cuida} (falta '{marca}')")
print(f"COBERTURA DEL BLINDAJE: {len(COBERTURA) - len([f for f in fallos if 'ya no cubre' in f])}"
      f"/{len(COBERTURA)} causas")

# El SSH tiene que quedar SOLO por la VPN: si esa línea desaparece, el
# puerto 22 queda abierto a lo que sea que alcance la máquina.
if "100.64.0.0/10" not in soporte:
    fallos.append("¡el soporte remoto ya no limita el SSH al rango de Tailscale!")
if "-Password" not in soporte:
    fallos.append("soporte_remoto.ps1 no acepta -Password: la app no puede pasársela")
if USUARIO_SOPORTE not in soporte:
    fallos.append(f"la app y el script no coinciden en el usuario ({USUARIO_SOPORTE})")

# ---------------------------------------------------------------- #
# 3. Nada de lógica duplicada entre el .py y los .ps1
# ---------------------------------------------------------------- #
app = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "apps", "blindaje", "main.py"), encoding="utf-8").read()
# Se mira el CÓDIGO, no los comentarios: el archivo explica qué hacen los
# scripts, y esas menciones son legítimas.
codigo = "\n".join(l for l in app.splitlines()
                   if not l.strip().startswith("#"))
for comando in ("powercfg", "Set-Service", "Register-ScheduledTask", "Disable-NetAdapter"):
    if comando in codigo:
        fallos.append(f"'{comando}' está reimplementado en el .py: tiene que vivir "
                       f"solo en el .ps1, o se van a desincronizar")

# ---------------------------------------------------------------- #
# 4. La salida de PowerShell viene en la codepage de la consola
# ---------------------------------------------------------------- #
# Si esto rompe, los acentos de los mensajes de Windows llenan la pantalla
# de basura justo cuando hay que leer un error.
for crudo, espera in ((b"Servicio en Automatic", "Servicio en Automatic"),
                      ("conexión".encode("cp1252"), "conexión"),
                      ("configuración".encode("utf-8"), "configuración")):
    obtenido = _decodificar(crudo)
    if obtenido != espera:
        fallos.append(f"decodificar {crudo!r} dio {obtenido!r}, esperaba {espera!r}")
print("DECODIFICACIÓN: ok (utf-8 y cp1252)")

# ---------------------------------------------------------------- #
# 4b. Nada que dependa del IDIOMA de Windows
# ---------------------------------------------------------------- #
# powercfg, sc.exe y compania hablan el idioma del sistema. Buscar "Index"
# en la salida de powercfg funcionaba en inglés y daba SIEMPRE cero
# coincidencias en la PC del cliente (Windows en español dice "Índice"):
# la tabla marcaba NO aunque los cambios hubieran entrado bien. Un chequeo
# que depende del idioma no es un chequeo.
PALABRAS_EN_INGLES = ("'Index'", '"Index"', "'Running'", "'Enabled'", "'Success'")
for archivo, contenido in (("blindar_local.ps1", blindar), ("soporte_remoto.ps1", soporte)):
    for palabra in PALABRAS_EN_INGLES:
        if f"Select-String {palabra}" in contenido or f"-Pattern {palabra}" in contenido:
            fallos.append(f"{archivo} busca {palabra} en la salida de un comando: "
                           f"eso depende del idioma de Windows y falla en español")
print("SIN CHEQUEOS QUE DEPENDAN DEL IDIOMA: ok")

# ---------------------------------------------------------------- #
# 4c. Cada fila de la tabla se gana el SI comprobando algo
# ---------------------------------------------------------------- #
# En la PC del local la fila "Usuario de soporte" dijo SI con el usuario
# INEXISTENTE: se marcaba OK apenas terminaba el bloque sin error, no
# porque el usuario estuviera. Un OK que no comprueba nada es peor que no
# tener la fila, porque hace dar por resuelto algo que no lo está.
if "Get-LocalUser" not in soporte or "net user" not in soporte:
    fallos.append("soporte_remoto.ps1 no comprueba que el usuario exista de verdad "
                   "después de crearlo")
for marca, que_cuida in (("$sinSuspension", "que la PC no se suspenda"),
                          ("$existe", "que el usuario de soporte exista"),
                          ("$puerto", "que el puerto 8765 escuche")):
    fuente = blindar if marca in blindar else soporte
    if marca not in fuente:
        fallos.append(f"no hay verificación real de: {que_cuida}")
print("CADA 'SI' DE LA TABLA COMPRUEBA ALGO: ok")

# ---------------------------------------------------------------- #
# 5. La ventana abre de verdad, y el log es seguro entre hilos
# ---------------------------------------------------------------- #
# Que el .exe compile no dice nada: si _armar_ui explota, el usuario hace
# doble clic y no pasa nada (los .exe van sin consola).
import threading
from apps.blindaje.main import AppBlindaje

ventana = AppBlindaje()
ventana.update()
print("VENTANA: abre y dibuja")

if "self.texto.get(" in app:
    fallos.append("se lee el widget de Tk para armar el informe: eso corre en el hilo "
                   "de trabajo y Tk no es seguro entre hilos (ver CLAUDE.md)")

# El hilo de trabajo escribe en el log; el widget lo pinta el hilo de Tk.
def _desde_otro_hilo():
    for i in range(20):
        ventana._log(f"linea {i}")

h = threading.Thread(target=_desde_otro_hilo)
h.start(); h.join()
# _bombear es lo que el hilo de Tk corre cada 100 ms para vaciar la cola a
# la pantalla. Se lo llama directo en vez de dormir 100 ms: es la misma
# función, sin esperar.
ventana._bombear()
ventana.update()

if len(ventana._historial) < 20:
    fallos.append(f"el historial perdió líneas escritas desde otro hilo "
                   f"({len(ventana._historial)} de 20+)")
dibujado = ventana.texto.get("1.0", "end")
if "linea 19" not in dibujado:
    fallos.append("lo que escribió el otro hilo no llegó a la pantalla")
print(f"LOG ENTRE HILOS: {len(ventana._historial)} líneas guardadas y dibujadas")

# El informe se arma del historial, no del widget: tiene que salir igual
# aunque la ventana ya no exista.
ventana.destroy()
try:
    contenido = "\n".join(ventana._historial)
    if "linea 19" not in contenido:
        fallos.append("el informe no contiene lo que se mostró")
    print(f"INFORME: {len(contenido)} caracteres, armado sin tocar la ventana")
except Exception as e:
    fallos.append(f"no se puede armar el informe con la ventana cerrada: {e}")

print()
if fallos:
    print("=== FALLOS BLINDAJE ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== BLINDAJE OK ===")
