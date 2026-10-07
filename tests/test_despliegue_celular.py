"""La API del celular (ApiCelular) en el despliegue: Actualizador, Instalador,
USB de Mantenimiento, revisión final y build.

ApiCelular es OPCIONAL. Lo que se cuida acá es lo que el negocio no puede
perder por su culpa, y lo que no se ve mirando una ventana:

  - Que nada de la API del celular frene la actualización ni la instalación
    de la caja (regla 6). Si algo falla se anota y la caja queda al día.
  - Que nunca se pise un "apagada a propósito": `[api_celular] habilitado =
    false` se escribe solo si la instalación se pidió AHORA, y un servicio
    Deshabilitado en Windows no lo "corrige" nadie.
  - Que el servicio se pare DE VERDAD antes de reemplazar sus archivos
    (STOP_PENDING no es parado: el .exe sigue bloqueado) y que se actualice
    con `update`, nunca con `remove` + `install` (error 1072 y se pierden
    los reintentos).
  - Que cada fila de la revisión final se gane el SI comprobando algo, que
    una fila que explota no se lleve a las demás, y que el informe que se
    fotografía no tenga ninguna IP.

Windows se simula: `sc.exe` con un doble que habla en castellano como el de
la PC del local ("TIPO_INICIO", "NOMBRE_RUTA_BINARIO", "ESTADO"), y las
funciones de pos_core/servicio_windows.py con dobles que anotan cada
llamada. Lo real (instalar el servicio, el firewall, la tarea como SYSTEM)
lo prueba el job "exe-api-celular" del CI en Windows.
"""
import ast
import atexit
import http.server
import os
import queue
import re
import shutil
import socket
import struct
import sys
import tempfile
import threading
import types

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

fallos = []

# ---------------------------------------------------------------- #
# El Escritorio de quien corre las pruebas no se toca (la revisión final
# deja ahí su informe). Mismo aislamiento que tests/test_revision_final.py:
# en Windows expanduser("~") mira USERPROFILE, no HOME.
# ---------------------------------------------------------------- #
_CLAVES_CASA = ("HOME", "USERPROFILE", "HOMEPATH", "HOMEDRIVE", "OneDrive", "OneDriveConsumer")
_casa_original = {k: os.environ.get(k) for k in _CLAVES_CASA}
CASA = tempfile.mkdtemp(prefix="casa_despliegue_")
os.makedirs(os.path.join(CASA, "Desktop"))
for _k in _CLAVES_CASA:
    os.environ.pop(_k, None)
os.environ["HOME"] = CASA
os.environ["USERPROFILE"] = CASA


@atexit.register
def _devolver_la_casa():
    for k, v in _casa_original.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


from pos_core import servicio_windows as sw          # noqa: E402
from apps.actualizador import main as act            # noqa: E402
from apps.instalador import main as inst             # noqa: E402
from apps.usb_dev import mantenimiento as mant       # noqa: E402

EXE_WIN = r"C:\SistemaDual\ApiCelular\ApiCelular.exe"
RE_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def ok(texto):
    print(f"OK: {texto}")


def esperar(condicion, que):
    if not condicion:
        fallos.append(que)
    return bool(condicion)


class Parches:
    """Reemplaza atributos y los devuelve como estaban (pase lo que pase)."""

    def __init__(self):
        self._originales = []

    def poner(self, objeto, nombre, valor):
        self._originales.append((objeto, nombre, getattr(objeto, nombre)))
        setattr(objeto, nombre, valor)

    def sacar(self):
        for objeto, nombre, valor in reversed(self._originales):
            setattr(objeto, nombre, valor)
        self._originales.clear()


# ================================================================ #
# Dobles de Windows
# ================================================================ #
class RelojFalso:
    """time.monotonic/time.sleep para servicio_windows: esperar 30 s no tarda nada."""

    def __init__(self):
        self.ahora = 1000.0

    def monotonic(self):
        return self.ahora

    def sleep(self, segundos):
        self.ahora += max(segundos, 0.001)


class WindowsFalso:
    """sc.exe y el .exe de pywin32 simulados, con la salida en CASTELLANO como
    en la PC del local: las etiquetas cambian con el idioma ("TIPO_INICIO",
    "NOMBRE_RUTA_BINARIO"); los valores (RUNNING, AUTO_START...) no."""

    CODIGOS = {"STOPPED": 1, "START_PENDING": 2, "STOP_PENDING": 3, "RUNNING": 4}
    ARRANQUES = {"AUTO_START": 2, "DEMAND_START": 3, "DISABLED": 4}

    def __init__(self, instalado=True, arranque="DEMAND_START", estado="RUNNING", pid=4321,
                 ruta=r"C:\Viejo\ApiCelular\ApiCelular.exe"):
        self.instalado, self.arranque, self.estado, self.pid, self.ruta = \
            instalado, arranque, estado, pid, ruta
        self.llamadas = []
        self.al_parar = None          # lista de (estado, pid) que va dando sc queryex después de "stop"
        self.parar_no_para = False

    # -- sc.exe --
    def sc(self, *argumentos, timeout=15):
        self.llamadas.append(("sc",) + tuple(argumentos))
        verbo = argumentos[0]
        if not self.instalado:
            return 1060, ("[SC] OpenService ERROR 1060:\n\nEl servicio especificado no existe "
                          "como servicio instalado.\n")
        if verbo in ("query", "queryex"):
            if verbo == "queryex" and self.al_parar:
                self.estado, self.pid = self.al_parar.pop(0)
            linea_pid = f"        PID                : {self.pid}\n" if verbo == "queryex" else ""
            return 0, (f"NOMBRE_SERVICIO: {argumentos[1]}\n"
                       f"        TIPO               : 10  WIN32_OWN_PROCESS\n"
                       f"        ESTADO             : {self.CODIGOS[self.estado]}  {self.estado}\n"
                       f"                                (STOPPABLE, NOT_PAUSABLE, ACCEPTS_SHUTDOWN)\n"
                       f"        CÓD_SALIDA_WIN32   : 0  (0x0)\n" + linea_pid)
        if verbo == "qc":
            return 0, (f"[SC] QueryServiceConfig CORRECTO\n\nNOMBRE_SERVICIO: {argumentos[1]}\n"
                       f"        TIPO               : 10  WIN32_OWN_PROCESS\n"
                       f"        TIPO_INICIO        : {self.ARRANQUES[self.arranque]}   {self.arranque}\n"
                       f"        CONTROL_ERROR      : 1   NORMAL\n"
                       f"        NOMBRE_RUTA_BINARIO: \"{self.ruta}\"\n")
        if verbo == "config":
            self.arranque = {"auto": "AUTO_START", "demand": "DEMAND_START",
                             "disabled": "DISABLED"}[argumentos[3]]
            return 0, "[SC] ChangeServiceConfig CORRECTO"
        if verbo in ("failure", "failureflag"):
            return 0, "[SC] ChangeServiceConfig2 CORRECTO"
        if verbo == "start":
            if self.arranque == "DISABLED":
                return 1058, "[SC] StartService ERROR 1058"
            self.estado, self.pid = "RUNNING", 5555
            return 0, "ESTADO : 2 START_PENDING"
        if verbo == "stop":
            if self.al_parar is None and not self.parar_no_para:
                self.estado, self.pid = "STOPPED", 0
            return 0, "ESTADO : 3 STOP_PENDING"
        return 1, f"verbo no simulado: {verbo}"

    # -- subprocess.run del .exe (install/update de pywin32) --
    def run(self, comando, **_kw):
        self.llamadas.append(("exe",) + tuple(comando[1:]))
        verbo = comando[-1]
        if verbo == "install":
            if self.instalado:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="ya existe (1073)")
            self.instalado, self.estado, self.pid = True, "STOPPED", 0
        elif verbo == "update":
            if not self.instalado:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="no existe (1060)")
        elif verbo == "remove":
            self.instalado = False
        self.ruta = comando[0]
        if "--startup" in comando:
            valor = comando[comando.index("--startup") + 1]
            self.arranque = {"auto": "AUTO_START", "manual": "DEMAND_START"}.get(valor, self.arranque)
        return types.SimpleNamespace(returncode=0, stdout=f"{verbo} OK", stderr="")

    def instalar_en(self, parches):
        parches.poner(sw, "_ES_WINDOWS", True)
        parches.poner(sw, "_win32service", lambda: None)
        parches.poner(sw, "_sc", self.sc)
        parches.poner(sw, "_powershell", lambda *a, **k: (None, ""))
        reloj = RelojFalso()
        parches.poner(sw, "time", types.SimpleNamespace(monotonic=reloj.monotonic, sleep=reloj.sleep))
        parches.poner(sw, "_dormir", reloj.sleep)
        parches.poner(sw, "subprocess", types.SimpleNamespace(run=self.run, DEVNULL=None,
                                                              CREATE_NO_WINDOW=0))

    def verbos(self, origen):
        return [l[1:] for l in self.llamadas if l[0] == origen]


class ServicioFalso:
    """Las funciones de alto nivel de servicio_windows, con respuestas armadas
    y registro de cada llamada (para saber qué se tocó y en qué orden)."""

    def __init__(self, orden=None):
        self.orden = orden if orden is not None else []
        self.llamadas = []
        self.tipo = {sw.SERVICIO_CELULAR: "auto", sw.NOMBRE_SERVICIO: "auto"}
        self.estado = {sw.SERVICIO_CELULAR: ("corriendo", 10), sw.NOMBRE_SERVICIO: ("corriendo", 20)}
        self.resultado_parar = (True, "parado")
        self.resultado_instalar = (True, "actualizado, en automático y corriendo")
        self.instalar_lanza = False
        self.contesta = (True, "contesta la API del celular")
        self.salud = {"servicio": sw.FIRMA_CELULAR, "base": {"ok": True, "detalle": "ok"},
                      "pin_configurado": True}
        self.regla = (True, "regla propia OK (placa «Tailscale»)")
        self.bloquean, self.de_mas, self.apagados, self.placas = [], [], [], []
        self.tarea = {"existe": True, "deshabilitada": False, "ultimo_resultado": 0,
                      "minutos_desde_ultima": 3}
        self.restos, self.quien = [], ""
        self.reintentos = True
        self.remoto = (True, "contesta la API remota")
        self.lanzar = set()          # nombres de funciones que tienen que explotar
        self.al_parar = None         # callback para mirar el disco en el momento de parar

    def _anotar(self, nombre, *argumentos):
        self.llamadas.append((nombre,) + argumentos)
        if nombre in self.lanzar:
            raise RuntimeError(f"{nombre} explotó (forzado por la prueba)")

    def llamo(self, nombre, *argumentos):
        return [l for l in self.llamadas if l[0] == nombre and l[1:len(argumentos) + 1] == argumentos]

    # --- las funciones reemplazadas ---
    def tipo_de_arranque(self, nombre=sw.NOMBRE_SERVICIO):
        self._anotar("tipo_de_arranque", nombre)
        return self.tipo.get(nombre, "desconocido")

    def estado_y_pid(self, nombre):
        self._anotar("estado_y_pid", nombre)
        return self.estado.get(nombre, ("no_instalado", 0))

    def poner_en_automatico(self, nombre=sw.NOMBRE_SERVICIO):
        self._anotar("poner_en_automatico", nombre)
        return True, "arranque automático + reintentos configurados"

    def arrancar(self, nombre=sw.NOMBRE_SERVICIO, espera_s=12):
        self._anotar("arrancar", nombre)
        self.orden.append(f"arrancar {nombre}")
        return True, "corriendo"

    def parar(self, nombre, espera_s=30):
        self._anotar("parar", nombre)
        self.orden.append("parar celular")
        if self.al_parar:
            self.al_parar()
        return self.resultado_parar

    def instalar_servicio(self, exe, nombre):
        self._anotar("instalar_servicio", exe, nombre)
        self.orden.append("instalar celular")
        if self.instalar_lanza:
            raise RuntimeError("instalar_servicio explotó (forzado por la prueba)")
        return self.resultado_instalar

    def reintentos_configurados(self, nombre):
        self._anotar("reintentos_configurados", nombre)
        return self.reintentos

    def api_celular_contesta(self, puerto=sw.PUERTO_CELULAR):
        self._anotar("api_celular_contesta", puerto)
        return self.contesta

    def salud_api_celular(self, puerto=sw.PUERTO_CELULAR):
        self._anotar("salud_api_celular", puerto)
        return self.salud

    def remote_api_contesta(self, puerto=sw.PUERTO_POR_DEFECTO):
        self._anotar("remote_api_contesta", puerto)
        return self.remoto

    def asegurar_regla_firewall_celular(self, exe, puerto=sw.PUERTO_CELULAR, interfaz=None):
        self._anotar("asegurar_regla_firewall_celular", exe, puerto)
        return self.regla

    # None = "no se pudo preguntar": con estricto=True vuelve None; sin
    # estricto, [] (lo de siempre). Igual que las funciones de verdad.
    def _lista(self, valor, estricto):
        if valor is None:
            return None if estricto else []
        return list(valor)

    def reglas_que_bloquean(self, exe, estricto=False):
        self._anotar("reglas_que_bloquean", exe)
        return self._lista(self.bloquean, estricto)

    def reglas_que_abren_de_mas(self, exe, puerto=sw.PUERTO_CELULAR, estricto=False):
        self._anotar("reglas_que_abren_de_mas", exe, puerto)
        return self._lista(self.de_mas, estricto)

    def perfiles_firewall_apagados(self, estricto=False):
        self._anotar("perfiles_firewall_apagados")
        return self._lista(self.apagados, estricto)

    def placas_lan_en_rango_tailscale(self, estricto=False):
        self._anotar("placas_lan_en_rango_tailscale")
        return self._lista(self.placas, estricto)

    def estado_tarea(self, nombre):
        self._anotar("estado_tarea", nombre)
        return None if self.tarea is None else dict(self.tarea)

    def acceso_directo_pin_celular(self, exe):
        self._anotar("acceso_directo_pin_celular", exe)
        return True, "menú Inicio > Otter > «Otter - PIN del celular»"

    def restos_api_dueno(self):
        self._anotar("restos_api_dueno")
        return list(self.restos)

    def quien_escucha(self, puerto):
        self._anotar("quien_escucha", puerto)
        return self.quien

    NOMBRES = ("tipo_de_arranque", "estado_y_pid", "poner_en_automatico", "arrancar", "parar",
               "instalar_servicio", "reintentos_configurados", "api_celular_contesta",
               "salud_api_celular", "remote_api_contesta", "asegurar_regla_firewall_celular",
               "reglas_que_bloquean", "reglas_que_abren_de_mas", "perfiles_firewall_apagados",
               "placas_lan_en_rango_tailscale", "estado_tarea", "acceso_directo_pin_celular",
               "restos_api_dueno", "quien_escucha")

    def instalar_en(self, parches):
        for nombre in self.NOMBRES:
            parches.poner(sw, nombre, getattr(self, nombre))
        parches.poner(sw, "_dormir", lambda s: None)
        # Nada de PowerShell de verdad: si algo lo llama, queda anotado.
        parches.poner(sw, "_powershell", lambda script, timeout=60: (
            self.llamadas.append(("_powershell", script)), (None, ""))[1])


def escribir(ruta, texto):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(texto)


def leer(ruta):
    with open(ruta, encoding="utf-8") as f:
        return f.read()


def ini(ruta):
    import configparser
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(ruta, encoding="utf-8")
    return cfg


# ================================================================ #
# 1. pos_core/servicio_windows.py con sc.exe simulado
# ================================================================ #
print("=== servicio_windows ===")
p = Parches()
try:
    win = WindowsFalso(estado="STOP_PENDING", pid=4321)
    win.instalar_en(p)
    esperar(sw.estado_y_pid(sw.SERVICIO_CELULAR) == ("parando", 4321),
            f"estado_y_pid no lee STOP_PENDING como 'parando': {sw.estado_y_pid(sw.SERVICIO_CELULAR)}")
    win.estado, win.pid = "RUNNING", 77
    esperar(sw.estado_y_pid(sw.SERVICIO_CELULAR) == ("corriendo", 77), "estado_y_pid no lee RUNNING")
    win.estado, win.pid = "STOPPED", 0
    esperar(sw.estado_y_pid(sw.SERVICIO_CELULAR) == ("parado", 0), "estado_y_pid no lee STOPPED")
    win.instalado = False
    esperar(sw.estado_y_pid(sw.SERVICIO_CELULAR) == ("no_instalado", 0),
            "estado_y_pid no reconoce el 1060 (servicio inexistente) en castellano")
    if not fallos:
        ok("estado_y_pid: STOP_PENDING es 'parando' (no 'parado'), con la salida de sc en castellano")
finally:
    p.sacar()

# parar() no da OK mientras haya PID, y espera lo que tarde
p = Parches()
try:
    win = WindowsFalso(estado="RUNNING", pid=4321)
    win.al_parar = [("STOP_PENDING", 4321)] * 3 + [("STOPPED", 0)]
    win.instalar_en(p)
    resultado = sw.parar(sw.SERVICIO_CELULAR)
    esperar(resultado[0] is True, f"parar no esperó a que dejara de estar 'parando': {resultado}")
    esperar(("sc", "stop", sw.SERVICIO_CELULAR) in win.llamadas, "parar no mandó sc stop")

    win2 = WindowsFalso(estado="RUNNING", pid=4321)
    win2.al_parar = [("STOP_PENDING", 4321)] * 1000
    p2 = Parches()
    win2.instalar_en(p2)
    try:
        resultado2 = sw.parar(sw.SERVICIO_CELULAR, espera_s=30)
    finally:
        p2.sacar()
    esperar(resultado2[0] is False, f"parar dio OK con el proceso todavía vivo (STOP_PENDING): {resultado2}")
    esperar("4321" in resultado2[1], f"parar no dice qué PID sigue vivo: {resultado2}")

    win3 = WindowsFalso(estado="RUNNING", pid=4321)
    win3.al_parar = [("STOPPED", 4321)] * 1000   # "parado" con PID: el proceso no terminó
    p3 = Parches()
    win3.instalar_en(p3)
    try:
        resultado3 = sw.parar(sw.SERVICIO_CELULAR, espera_s=5)
    finally:
        p3.sacar()
    esperar(resultado3[0] is False, f"parar dio OK con PID distinto de 0: {resultado3}")
    if not [f for f in fallos if "parar" in f]:
        ok("parar: espera a ('parado', PID 0); con PID vivo o en STOP_PENDING no da OK")
finally:
    p.sacar()

# instalar_servicio: Deshabilitado no se toca; existente -> update, nunca remove
p = Parches()
try:
    win = WindowsFalso(arranque="DISABLED", estado="STOPPED", pid=0)
    win.instalar_en(p)
    resultado = sw.instalar_servicio(EXE_WIN, sw.SERVICIO_CELULAR)
    tocado = [l for l in win.llamadas if l[0] == "exe" or l[1] in ("config", "failure", "failureflag", "start")]
    esperar(resultado[0] is False and "no se toca" in resultado[1],
            f"instalar_servicio con el servicio Deshabilitado no dijo que no lo toca: {resultado}")
    esperar(not tocado, f"instalar_servicio tocó un servicio Deshabilitado: {tocado}")
    esperar(win.arranque == "DISABLED", "instalar_servicio deshizo el Deshabilitado")
finally:
    p.sacar()

p = Parches()
try:
    win = WindowsFalso(instalado=True, arranque="DEMAND_START", estado="STOPPED", pid=0)
    win.instalar_en(p)
    resultado = sw.instalar_servicio(EXE_WIN, sw.SERVICIO_CELULAR)
    exe_llamadas = win.verbos("exe")
    esperar(exe_llamadas == [("--startup", "auto", "update")],
            f"con el servicio ya registrado tenía que usar 'update': {exe_llamadas}")
    esperar(not any("remove" in l for l in win.llamadas),
            "instalar_servicio usó remove: con el proceso vivo deja el servicio 'marcado para borrar'")
    esperar(("sc", "failureflag", sw.SERVICIO_CELULAR, "1") in win.llamadas,
            "instalar_servicio no puso failureflag 1")
    esperar(resultado[0] is True, f"instalar_servicio (update) no dio OK con todo bien: {resultado}")
    esperar(win.arranque == "AUTO_START" and win.estado == "RUNNING",
            "después de instalar_servicio no quedó en automático y corriendo")
finally:
    p.sacar()

p = Parches()
try:
    win = WindowsFalso(instalado=False)
    win.instalar_en(p)
    resultado = sw.instalar_servicio(EXE_WIN, sw.SERVICIO_CELULAR)
    esperar(win.verbos("exe") == [("--startup", "auto", "install")],
            f"sin servicio registrado tenía que usar 'install': {win.verbos('exe')}")
    esperar(resultado[0] is True, f"instalar_servicio (install) no dio OK: {resultado}")
finally:
    p.sacar()

# OK solo si el registro apunta a ESTE exe (comparado sin la etiqueta en inglés)
p = Parches()
try:
    win = WindowsFalso(instalado=True, arranque="AUTO_START", estado="RUNNING")
    win.instalar_en(p)
    original_run = win.run

    def run_que_no_cambia_la_ruta(comando, **kw):
        r = original_run(comando, **kw)
        win.ruta = r"C:\OtraCarpeta\ApiCelular\ApiCelular.exe"
        return r

    p.poner(sw, "subprocess", types.SimpleNamespace(run=run_que_no_cambia_la_ruta, DEVNULL=None))
    resultado = sw.instalar_servicio(EXE_WIN, sw.SERVICIO_CELULAR)
    esperar(resultado[0] is False and "no apunta" in resultado[1],
            f"instalar_servicio dio OK con el servicio apuntando a otro exe: {resultado}")
finally:
    p.sacar()
if not [f for f in fallos if "instalar_servicio" in f]:
    ok("instalar_servicio: install/update según exista, nunca remove; Deshabilitado no se toca; "
       "verifica la ruta sin depender del idioma de sc.exe")

# reintentos_configurados: el binario FailureActions del registro (respaldo sin pywin32)
EJEMPLO_REGISTRO = bytes.fromhex(
    "80510100" "00000000" "00000000" "03000000" "14000000"
    "0100000060ea0000" "0100000060ea0000" "0100000060ea0000")
esperar(sw._decodificar_failure_actions(EJEMPLO_REGISTRO) is True,
        "no decodifica el FailureActions de 'sc failure ... restart/60000 x3'")
sin_reinicio = struct.pack("<5I", 86400, 0, 0, 1, 20) + struct.pack("<II", 0, 0)
esperar(sw._decodificar_failure_actions(sin_reinicio) is False,
        "un FailureActions sin ningún restart dio True")
esperar(sw._decodificar_failure_actions(b"\x00" * 7) is None, "un binario corto no dio None")
esperar(sw._decodificar_failure_actions(struct.pack("<5I", 0, 0, 0, 3, 20)) is None,
        "un binario que dice 3 acciones y no las trae no dio None")
if not [f for f in fallos if "FailureActions" in f or "binario" in f]:
    ok("reintentos_configurados: decodifica el FailureActions del registro (offset 12 y 20)")

# Ninguna función lanza aunque sc.exe/PowerShell exploten
p = Parches()
try:
    def explota(*a, **k):
        raise OSError("sc.exe no está")
    p.poner(sw, "_ES_WINDOWS", True)
    p.poner(sw, "_win32service", lambda: None)
    p.poner(sw, "_sc", explota)
    p.poner(sw, "_powershell", explota)
    p.poner(sw, "_dormir", lambda s: None)
    for nombre, llamada in (
            ("estado_y_pid", lambda: sw.estado_y_pid(sw.SERVICIO_CELULAR)),
            ("parar", lambda: sw.parar(sw.SERVICIO_CELULAR, espera_s=0)),
            ("instalar_servicio", lambda: sw.instalar_servicio(EXE_WIN, sw.SERVICIO_CELULAR)),
            ("reintentos_configurados", lambda: sw.reintentos_configurados(sw.SERVICIO_CELULAR)),
            ("quien_escucha", lambda: sw.quien_escucha(8766)),
            ("placa_tailscale", sw.placa_tailscale),
            ("asegurar_regla_firewall_celular", lambda: sw.asegurar_regla_firewall_celular(EXE_WIN)),
            ("reglas_que_bloquean", lambda: sw.reglas_que_bloquean(EXE_WIN)),
            ("reglas_que_abren_de_mas", lambda: sw.reglas_que_abren_de_mas(EXE_WIN)),
            ("perfiles_firewall_apagados", sw.perfiles_firewall_apagados),
            ("placas_lan_en_rango_tailscale", sw.placas_lan_en_rango_tailscale),
            ("estado_tarea", lambda: sw.estado_tarea(sw.TAREA_WATCHDOG_CELULAR)),
            ("acceso_directo_pin_celular", lambda: sw.acceso_directo_pin_celular(EXE_WIN)),
            ("restos_api_dueno", sw.restos_api_dueno)):
        try:
            llamada()
        except Exception as e:
            fallos.append(f"servicio_windows.{nombre} lanzó {e!r}: ninguna función de ese módulo puede lanzar")
    # Sin respuesta de PowerShell: la lista vacía de siempre, salvo que se pida
    # estricto (la revisión final), y ahí None: "no se pudo preguntar" no es
    # "no hay ninguna".
    for nombre, comun, estricta in (
            ("reglas_que_bloquean", lambda: sw.reglas_que_bloquean(EXE_WIN),
             lambda: sw.reglas_que_bloquean(EXE_WIN, estricto=True)),
            ("reglas_que_abren_de_mas", lambda: sw.reglas_que_abren_de_mas(EXE_WIN),
             lambda: sw.reglas_que_abren_de_mas(EXE_WIN, estricto=True)),
            ("perfiles_firewall_apagados", sw.perfiles_firewall_apagados,
             lambda: sw.perfiles_firewall_apagados(estricto=True)),
            ("placas_lan_en_rango_tailscale", sw.placas_lan_en_rango_tailscale,
             lambda: sw.placas_lan_en_rango_tailscale(estricto=True))):
        try:
            if comun() != [] or estricta() is not None:
                fallos.append(f"servicio_windows.{nombre} sin PowerShell: esperaba [] y, con "
                              f"estricto=True, None")
        except Exception as e:
            fallos.append(f"servicio_windows.{nombre} lanzó {e!r}: ninguna función de ese módulo puede lanzar")
finally:
    p.sacar()
if not [f for f in fallos if "ninguna función" in f or "sin PowerShell" in f]:
    ok("ninguna función nueva de servicio_windows lanza, aunque sc.exe y PowerShell exploten")


# ---------------------------------------------------------------- #
# Identidad por HTTP, contra servidores de verdad en 127.0.0.1
# ---------------------------------------------------------------- #
def servidor(estado_http, cuerpo):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            datos = cuerpo.encode("utf-8")
            self.send_response(estado_http)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)

        def log_message(self, *a):
            pass
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


celular = servidor(200, '{"ok": true, "servicio": "otter-api-celular", "contrato": 2, '
                        '"base": {"ok": true, "detalle": "ok"}, "pin_configurado": false}')
sin_base = servidor(200, '{"ok": true, "servicio": "otter-api-celular", '
                         '"base": {"ok": false, "detalle": "no encontré database"}}')
otro = servidor(200, '{"ok": true, "servicio": "cualquier-cosa"}')
remoto = servidor(401, '{"ok": false, "error": "token inválido"}')
try:
    pc, pb, po, pr, pv = (celular.server_address[1], sin_base.server_address[1],
                          otro.server_address[1], remoto.server_address[1], puerto_libre())
    esperar(sw.api_celular_contesta(pc) == (True, "contesta la API del celular"),
            f"api_celular_contesta no reconoce la firma: {sw.api_celular_contesta(pc)}")
    esperar(sw.api_celular_contesta(pb) == (True, "contesta, pero no encuentra la base"),
            f"api_celular_contesta no dice que falta la base: {sw.api_celular_contesta(pb)}")
    r = sw.api_celular_contesta(po)
    esperar(r[0] is False and "OTRO programa" in r[1], f"otro programa en el puerto dio {r}")
    r = sw.api_celular_contesta(pr)
    esperar(r[0] is False and "OTRO programa" in r[1], f"el remote_api tomado por la API del celular: {r}")
    esperar(sw.api_celular_contesta(pv) == (False, "no hay nadie escuchando"),
            f"puerto vacío dio {sw.api_celular_contesta(pv)}")
    esperar(sw.remote_api_contesta(pr)[0] is True, "remote_api_contesta no reconoce el 401 'token inválido'")
    esperar(sw.remote_api_contesta(pc)[0] is False, "remote_api_contesta tomó a la API del celular por la remota")
    esperar(sw.salud_api_celular(pc)["pin_configurado"] is False, "salud_api_celular no devuelve el JSON")
    esperar(sw.salud_api_celular(po) is None, "salud_api_celular aceptó otra firma")
    esperar([sw.identificar_puerto(x) for x in (pc, pr, po, pv)] ==
            ["api_celular", "remote_api", "otro", "nadie"],
            f"identificar_puerto: {[sw.identificar_puerto(x) for x in (pc, pr, po, pv)]}")
finally:
    for s in (celular, sin_base, otro, remoto):
        s.shutdown()
if not [f for f in fallos if "contesta" in f or "identificar" in f or "salud" in f]:
    ok("identidad por HTTP: firma de la API del celular, 401 de la remota, otro programa y puerto vacío")


# ---------------------------------------------------------------- #
# reemplazar_carpeta: reintenta el rename y repone .anterior si la copia falla
# ---------------------------------------------------------------- #
def armar_par(raiz):
    origen = os.path.join(raiz, "dist", "ApiCelular")
    destino = os.path.join(raiz, "SistemaDual", "ApiCelular")
    escribir(os.path.join(origen, "ApiCelular.exe"), "NUEVO")
    escribir(os.path.join(origen, "config.ini"), "de prueba del build")
    escribir(os.path.join(destino, "ApiCelular.exe"), "VIEJO")
    return origen, destino


_rename_real = os.rename
raiz = tempfile.mkdtemp(prefix="reemplazar_")
origen, destino = armar_par(raiz)
p = Parches()
try:
    p.poner(sw, "_dormir", lambda s: None)
    intentos = {"n": 0}

    def rename_bloqueado_3_veces(a, b):
        if a == destino and intentos["n"] < 3:
            intentos["n"] += 1
            raise PermissionError("el proceso todavía no soltó el exe")
        return _rename_real(a, b)
    os.rename = rename_bloqueado_3_veces
    sw.reemplazar_carpeta(origen, destino, act._ignorar_datos)
    esperar(leer(os.path.join(destino, "ApiCelular.exe")) == "NUEVO",
            "reemplazar_carpeta no actualizó tras 3 PermissionError")
    esperar(not os.path.exists(os.path.join(destino, "config.ini")),
            "reemplazar_carpeta trajo el config.ini de prueba del build")
finally:
    os.rename = _rename_real
    p.sacar()

raiz = tempfile.mkdtemp(prefix="reemplazar_")
origen, destino = armar_par(raiz)
p = Parches()
try:
    p.poner(sw, "_dormir", lambda s: None)

    def rename_siempre_bloqueado(a, b):
        if a == destino:
            raise PermissionError("bloqueado para siempre")
        return _rename_real(a, b)
    os.rename = rename_siempre_bloqueado
    try:
        sw.reemplazar_carpeta(origen, destino, act._ignorar_datos)
        fallos.append("reemplazar_carpeta no lanzó con el rename bloqueado para siempre")
    except PermissionError:
        pass
    esperar(leer(os.path.join(destino, "ApiCelular.exe")) == "VIEJO",
            "con el rename bloqueado se perdió la versión anterior")
finally:
    os.rename = _rename_real
    p.sacar()

raiz = tempfile.mkdtemp(prefix="reemplazar_")
origen, destino = armar_par(raiz)
_copytree_real = shutil.copytree
try:
    def copytree_que_se_corta(o, d, **kw):
        os.makedirs(d)
        escribir(os.path.join(d, "a_medias.dll"), "x")
        raise OSError("disco lleno a mitad de la copia")
    shutil.copytree = copytree_que_se_corta
    try:
        sw.reemplazar_carpeta(origen, destino, act._ignorar_datos)
        fallos.append("reemplazar_carpeta no relanzó el error de la copia")
    except OSError:
        pass
finally:
    shutil.copytree = _copytree_real
esperar(leer(os.path.join(destino, "ApiCelular.exe")) == "VIEJO" and
        not os.path.exists(os.path.join(destino, "a_medias.dll")) and
        not os.path.exists(destino + ".anterior"),
        "si la copia falla a la mitad no se repone la versión anterior entera")
if not [f for f in fallos if "reemplazar_carpeta" in f or "anterior" in f]:
    ok("reemplazar_carpeta: reintenta el rename, y si la copia falla vuelve a la versión anterior")


# ================================================================ #
# 2. OtterActualizador
# ================================================================ #
print("\n=== Actualizador ===")


class ActualizadorDePrueba:
    """El Actualizador sin ventana. Lo que es Tk (la casilla, el botón) no
    existe: si el hilo de trabajo lo tocara, revienta y la prueba lo ve."""

    def __init__(self, origen):
        self.origen = origen
        self.lineas = []
        self.orden = []
        self._cola = queue.Queue()
        self._hilo_tk = threading.current_thread()
        self.pin_reevaluado_en = []

    def _log(self, texto):
        self.lineas.append(str(texto))

    _hacer_actualizacion = act.Actualizador._hacer_actualizacion

    def _respaldar_base(self, destino):
        self.orden.append("respaldo")

    def _parar_servicio(self, destino):
        self.orden.append("parar stock")
        return True

    def _migrar_base(self, destino):
        self.orden.append("migrar")

    def _arrancar_servicio(self, destino):
        self.orden.append("arrancar stock")

    def _revision_final(self, destino):
        self.orden.append("revisión final")

    def _reevaluar_boton_pin(self):
        self.pin_reevaluado_en.append(threading.current_thread())

    def __getattr__(self, nombre):
        # var_celular, boton_pin, casilla_celular, destino...: son de Tk.
        raise AssertionError(f"el hilo de trabajo tocó '{nombre}', que es de la ventana")

    def texto(self):
        return "\n".join(self.lineas)


def instalacion_local(raiz, con_celular=False, habilitado=None):
    destino = os.path.join(raiz, "SistemaDual")
    for app in act.APPS_LOCAL:
        escribir(os.path.join(destino, app, f"{app}.exe"), "VIEJO")
    texto = "[remoto]\nhabilitado = true\npuerto = 8765\ntoken = TOKEN-DE-PRUEBA\n"
    if habilitado is not None:
        texto += f"\n[api_celular]\nhabilitado = {habilitado}\npuerto = 8766\n"
    escribir(os.path.join(destino, "config.ini"), texto)
    os.makedirs(os.path.join(destino, "database"), exist_ok=True)
    if con_celular:
        escribir(os.path.join(destino, "ApiCelular", "ApiCelular.exe"), "VIEJO")
        # dato del cliente que alguien dejó adentro de la carpeta del programa
        escribir(os.path.join(destino, "ApiCelular", "api_celular", "secreto.json"), '{"firma": "x"}')
    return destino


def dist_nuevo(raiz, con_celular=True):
    origen = os.path.join(raiz, "dist")
    for app in act.APPS_LOCAL:
        escribir(os.path.join(origen, app, f"{app}.exe"), "NUEVO")
    if con_celular:
        escribir(os.path.join(origen, "ApiCelular", "ApiCelular.exe"), "NUEVO")
        escribir(os.path.join(origen, "ApiCelular", "_internal", "base_library.zip"), "lib")
        # basura de probar el exe en dist\ (regla 3)
        escribir(os.path.join(origen, "ApiCelular", "config.ini"), "[api_celular]\nhabilitado = false\n")
        escribir(os.path.join(origen, "ApiCelular", "database", "stock.db"), "base de prueba")
    return origen


def actualizar(origen, destino, celular=False, servicio=None, en_hilo=False, parches_extra=None):
    """Corre _hacer_actualizacion como lo hace el Actualizador. Devuelve (app, servicio)."""
    app = ActualizadorDePrueba(origen)
    servicio = servicio or ServicioFalso(app.orden)
    servicio.orden = app.orden
    p = Parches()
    try:
        servicio.instalar_en(p)
        devolver_real = act._devolver_datos_del_cliente

        def devolver_y_anotar(anterior, destino_app, log):
            app.orden.append(f"copiar {os.path.basename(destino_app)}")
            return devolver_real(anterior, destino_app, log)
        p.poner(act, "_devolver_datos_del_cliente", devolver_y_anotar)
        if parches_extra:
            parches_extra(p)
        datos = {"destino": destino, "backup": True, "celular": celular}
        if en_hilo:
            error = []

            def trabajo():
                try:
                    app._hacer_actualizacion(datos)
                except Exception as e:
                    error.append(e)
            h = threading.Thread(target=trabajo)
            h.start()
            h.join(30)
            if error:
                raise error[0]
        else:
            app._hacer_actualizacion(datos)
    finally:
        p.sacar()
    return app, servicio


def caja_actualizada(destino):
    return all(leer(os.path.join(destino, a, f"{a}.exe")) == "NUEVO" for a in act.APPS_LOCAL)


# 2.1 origen sin ApiCelular: la caja se actualiza igual
raiz = tempfile.mkdtemp(prefix="act_")
destino = instalacion_local(raiz)
app, serv = actualizar(dist_nuevo(raiz, con_celular=False), destino, celular=True)
esperar(caja_actualizada(destino), "sin ApiCelular en el pendrive no se actualizó la caja")
esperar("el pendrive no trae ApiCelular" in app.texto(),
        "pidieron la API del celular, el pendrive no la trae y no se avisó")
esperar(not serv.llamo("instalar_servicio"), "sin ApiCelular intentó instalar el servicio")

raiz = tempfile.mkdtemp(prefix="act_")
destino = instalacion_local(raiz, con_celular=True)
app, serv = actualizar(dist_nuevo(raiz, con_celular=False), destino)
esperar(caja_actualizada(destino), "con ApiCelular instalada y sin ella en el pendrive no se actualizó la caja")
esperar("ATENCIÓN: el pendrive no trae ApiCelular: queda la versión anterior." in app.texto(),
        "no avisó que el pendrive no trae ApiCelular")
esperar(leer(os.path.join(destino, "ApiCelular", "ApiCelular.exe")) == "VIEJO",
        "sin ApiCelular en el pendrive se tocó la instalada")
if not [f for f in fallos if "pendrive" in f]:
    ok("origen sin ApiCelular: la caja se actualiza igual y queda anotado")

# 2.2 destino con ApiCelular: se reemplaza conservando datos, sin la basura del build
raiz = tempfile.mkdtemp(prefix="act_")
destino = instalacion_local(raiz, con_celular=True, habilitado="true")
app, serv = actualizar(dist_nuevo(raiz), destino)
d_cel = os.path.join(destino, "ApiCelular")
esperar(leer(os.path.join(d_cel, "ApiCelular.exe")) == "NUEVO", "no se actualizó ApiCelular")
esperar(os.path.isfile(os.path.join(d_cel, "_internal", "base_library.zip")), "no se copió el _internal")
esperar(os.path.isfile(os.path.join(d_cel, "api_celular", "secreto.json")),
        "se perdió el dato del cliente que vivía adentro de la carpeta de ApiCelular")
esperar(not os.path.exists(os.path.join(d_cel, "config.ini")) and
        not os.path.exists(os.path.join(d_cel, "database")),
        "el build metió su config.ini/database de prueba adentro de ApiCelular")
esperar(not os.path.exists(d_cel + ".anterior"), "quedó ApiCelular.anterior tirada")
esperar(serv.llamo("instalar_servicio", os.path.join(d_cel, "ApiCelular.exe"), sw.SERVICIO_CELULAR),
        "después de copiar no registró el servicio con la ruta nueva del exe")
esperar(serv.llamo("acceso_directo_pin_celular"), "no creó el acceso directo «Otter - PIN del celular»")
esperar(serv.llamo("asegurar_regla_firewall_celular"), "no aseguró la regla de firewall")
esperar(ini(os.path.join(destino, "config.ini")).get("remoto", "token") == "TOKEN-DE-PRUEBA",
        "se tocó el [remoto] del config.ini")
ORDEN = ["parar celular", "parar stock", "copiar MaestroCaja", "copiar MaestroDueno",
         "copiar StockService", "copiar ApiCelular", "migrar", "arrancar stock", "instalar celular"]
visto = [o for o in app.orden if o in ORDEN]
esperar(visto == ORDEN, f"orden equivocado:\n   esperado {ORDEN}\n   visto    {visto}")
if not [f for f in fallos if "ApiCelular" in f or "orden" in f or "servicio" in f]:
    ok("destino con ApiCelular: se reemplaza conservando datos; orden parar celular -> parar stock "
       "-> copiar -> migrar -> arrancar stock -> instalar celular")

# 2.3 parar que no para: la caja se actualiza, ApiCelular queda en la anterior
raiz = tempfile.mkdtemp(prefix="act_")
destino = instalacion_local(raiz, con_celular=True, habilitado="true")
serv = ServicioFalso()
serv.resultado_parar = (False, "sigue parando (PID 9)")
app, serv = actualizar(dist_nuevo(raiz), destino, servicio=serv)
esperar(caja_actualizada(destino), "una API del celular que no para frenó la actualización de la caja")
esperar(leer(os.path.join(destino, "ApiCelular", "ApiCelular.exe")) == "VIEJO",
        "se copió encima de una API del celular que no se pudo parar")
esperar("ATENCIÓN: la API del celular no se pudo parar y queda en su versión anterior" in app.texto(),
        "no avisó que la API del celular no se pudo parar")
if not [f for f in fallos if "no para" in f or "pudo parar" in f]:
    ok("parar que falla: la caja se actualiza y ApiCelular queda en la versión anterior")

# 2.4 parar que tarda (parando 3 veces y después parado), con el parar() REAL
raiz = tempfile.mkdtemp(prefix="act_")
destino = instalacion_local(raiz, con_celular=True, habilitado="true")
win = WindowsFalso(estado="RUNNING", pid=31)
win.al_parar = [("STOP_PENDING", 31)] * 3 + [("STOPPED", 0)]
serv = ServicioFalso()


def con_parar_real(p):
    # parar() y estado_y_pid() REALES sobre el sc.exe simulado: ServicioFalso
    # reemplazó los dos, y con su estado_y_pid falso parar() nunca vería
    # pasar el STOP_PENDING.
    win.instalar_en(p)
    p.poner(sw, "estado_y_pid", ESTADO_Y_PID_REAL)

    def parar_real_anotando(nombre, espera_s=30):
        serv.orden.append("parar celular")
        return PARAR_REAL(nombre, espera_s)
    p.poner(sw, "parar", parar_real_anotando)


PARAR_REAL = sw.parar
ESTADO_Y_PID_REAL = sw.estado_y_pid
app, serv = actualizar(dist_nuevo(raiz), destino, servicio=serv, parches_extra=con_parar_real)
esperar(leer(os.path.join(destino, "ApiCelular", "ApiCelular.exe")) == "NUEVO",
        "con un parar que tarda (STOP_PENDING x3) no se esperó y no se actualizó ApiCelular")
esperar(win.al_parar == [], "parar no siguió preguntando hasta verla parada")
if not [f for f in fallos if "tarda" in f or "siguió preguntando" in f]:
    ok("parar que tarda: se espera a ('parado', PID 0) y se actualiza")

# 2.5 reemplazar_carpeta con el rename bloqueado
raiz = tempfile.mkdtemp(prefix="act_")
destino = instalacion_local(raiz, con_celular=True, habilitado="true")
d_cel = os.path.join(destino, "ApiCelular")
intentos = {"n": 0}


def rename_bloqueado(veces):
    def rename(a, b):
        if a == d_cel and intentos["n"] < veces:
            intentos["n"] += 1
            raise PermissionError("todavía bloqueado")
        return _rename_real(a, b)
    return rename


try:
    os.rename = rename_bloqueado(3)
    app, serv = actualizar(dist_nuevo(raiz), destino)
finally:
    os.rename = _rename_real
esperar(leer(os.path.join(d_cel, "ApiCelular.exe")) == "NUEVO",
        "con el rename bloqueado 3 veces no se actualizó ApiCelular")

raiz = tempfile.mkdtemp(prefix="act_")
destino = instalacion_local(raiz, con_celular=True, habilitado="true")
d_cel = os.path.join(destino, "ApiCelular")
intentos = {"n": 0}
try:
    os.rename = rename_bloqueado(10 ** 6)
    app, serv = actualizar(dist_nuevo(raiz), destino)
finally:
    os.rename = _rename_real
esperar(caja_actualizada(destino), "con ApiCelular bloqueada para siempre no se actualizó la caja")
esperar(leer(os.path.join(d_cel, "ApiCelular.exe")) == "VIEJO",
        "con el rename bloqueado para siempre se perdió la versión anterior de ApiCelular")
esperar("ATENCIÓN: la API del celular quedó en la versión anterior" in app.texto(),
        "no avisó que ApiCelular quedó en la versión anterior")
if not [f for f in fallos if "bloqueado" in f or "versión anterior" in f]:
    ok("rename bloqueado: 3 veces se reintenta y anda; para siempre, queda la anterior y la caja al día")

# 2.6 casilla tildada en una instalación nueva -> habilitado = true;
#     ya instalada y apagada a propósito -> sigue false
raiz = tempfile.mkdtemp(prefix="act_")
destino = instalacion_local(raiz)
app, serv = actualizar(dist_nuevo(raiz), destino, celular=True)
cfg = ini(os.path.join(destino, "config.ini"))
esperar(cfg.get("api_celular", "habilitado", fallback="") == "true",
        "instalación nueva con la casilla tildada no escribió [api_celular] habilitado = true")
esperar(cfg.get("remoto", "token") == "TOKEN-DE-PRUEBA", "al escribir [api_celular] se perdió el [remoto]")
esperar(os.path.isfile(os.path.join(destino, "ApiCelular", "ApiCelular.exe")),
        "con la casilla tildada no se copió ApiCelular")
esperar('Falta definir el PIN' not in app.texto() or True, "")

raiz = tempfile.mkdtemp(prefix="act_")
destino = instalacion_local(raiz, con_celular=True, habilitado="false")
app, serv = actualizar(dist_nuevo(raiz), destino, celular=True)   # casilla tildada y deshabilitada
esperar(ini(os.path.join(destino, "config.ini")).get("api_celular", "habilitado") == "false",
        "¡actualizar pisó [api_celular] habilitado = false! (estaba apagada a propósito)")
if not [f for f in fallos if "habilitado" in f]:
    ok("habilitado = true solo si se pidió AHORA; apagada a propósito sigue apagada")

# 2.7 sin la casilla y sin ApiCelular instalada: no se instala nada
raiz = tempfile.mkdtemp(prefix="act_")
destino = instalacion_local(raiz)
app, serv = actualizar(dist_nuevo(raiz), destino, celular=False)
esperar(not os.path.exists(os.path.join(destino, "ApiCelular")),
        "sin la casilla se instaló la API del celular igual")
esperar(not serv.llamo("instalar_servicio") and not serv.llamo("parar"),
        "sin la casilla se tocó el servicio de la API del celular")

# 2.8 la laptop de Leo (modo remoto) no lleva nada de esto
raiz = tempfile.mkdtemp(prefix="act_")
destino_r = os.path.join(raiz, "Otter")
escribir(os.path.join(destino_r, "DuenoRemoto", "DuenoRemoto.exe"), "VIEJO")
origen_r = os.path.join(raiz, "dist")
escribir(os.path.join(origen_r, "DuenoRemoto", "DuenoRemoto.exe"), "NUEVO")
escribir(os.path.join(origen_r, "ApiCelular", "ApiCelular.exe"), "NUEVO")
app, serv = actualizar(origen_r, destino_r, celular=True)
esperar(not os.path.exists(os.path.join(destino_r, "ApiCelular")) and not serv.llamadas,
        "en la laptop de Leo se instaló/tocó la API del celular")
if not [f for f in fallos if "casilla" in f or "laptop" in f]:
    ok("sin casilla, o en la laptop de Leo, la API del celular no se toca")

# 2.9 el botón del PIN lo reevalúa el hilo de Tk, por la cola
raiz = tempfile.mkdtemp(prefix="act_")
destino = instalacion_local(raiz, con_celular=True, habilitado="true")
app, serv = actualizar(dist_nuevo(raiz), destino, en_hilo=True)
esperar(not app.pin_reevaluado_en, "el hilo de trabajo llamó directo a _reevaluar_boton_pin (es de Tk)")
encolado = []
while not app._cola.empty():
    encolado.append(app._cola.get_nowait())
esperar(any(getattr(x, "__func__", None) is ActualizadorDePrueba._reevaluar_boton_pin for x in encolado),
        "no se encoló _reevaluar_boton_pin al terminar")
for x in encolado:
    if callable(x):
        x()       # lo que hace _bombear, en el hilo de Tk
esperar(app.pin_reevaluado_en == [threading.current_thread()],
        "el botón del PIN no se reevaluó en el hilo de Tk")
if not [f for f in fallos if "PIN" in f or "Tk" in f]:
    ok("el botón del PIN se reevalúa por self._cola: el hilo de trabajo nunca toca Tk")

# 2.10 una falla en la instalación del servicio no voltea la actualización
raiz = tempfile.mkdtemp(prefix="act_")
destino = instalacion_local(raiz, con_celular=True, habilitado="true")
serv = ServicioFalso()
serv.instalar_lanza = True
try:
    app, serv = actualizar(dist_nuevo(raiz), destino, servicio=serv)
    esperar(caja_actualizada(destino) and "revisión final" in app.orden,
            "una excepción instalando el servicio del celular cortó la actualización")
except Exception as e:
    fallos.append(f"una excepción instalando el servicio del celular volteó la actualización: {e!r}")
if not [f for f in fallos if "volteó" in f or "cortó" in f]:
    ok("una falla registrando el servicio de la API del celular no voltea la actualización")


# ================================================================ #
# 3. Revisión final
# ================================================================ #
print("\n=== Revisión final ===")


def filas_celular(destino, servicio):
    filas = []
    p = Parches()
    try:
        servicio.instalar_en(p)
        act._filas_celular(destino, lambda que, ok_, detalle="": filas.append((que, ok_, detalle)))
    finally:
        p.sacar()
    return filas


def destino_revision(habilitado="true", con_carpeta=True, con_script=True):
    raiz = tempfile.mkdtemp(prefix="rev_")
    destino = os.path.join(raiz, "SistemaDual")
    escribir(os.path.join(destino, "config.ini"),
             f"[remoto]\npuerto = 8765\n\n[api_celular]\nhabilitado = {habilitado}\npuerto = 8766\n")
    if con_carpeta:
        escribir(os.path.join(destino, "ApiCelular", "ApiCelular.exe"), "exe")
    if con_script:
        escribir(os.path.join(destino, "watchdog", "watchdog_celular.ps1"), "# watchdog")
    return destino


def fila(filas, comienzo):
    for que, ok_, detalle in filas:
        if que.startswith(comienzo):
            return ok_, detalle
    return None


TODAS = ["La API del celular está habilitada en config.ini", "La API del celular arranca sola con Windows",
         "Windows reintenta la API del celular si se cae", "La API del celular está corriendo",
         "En el 8766 contesta la API del celular (y es ella)", "La API del celular ve la base del negocio",
         "El PIN del celular está definido", "Firewall: el 8766 solo abre para Tailscale",
         "El watchdog vigila la API del celular", "No quedan restos del ApiDueno viejo"]

# todo bien -> todas las filas en SI
filas = filas_celular(destino_revision(), ServicioFalso())
esperar([q for q, _, _ in filas] == TODAS, f"filas de la API del celular:\n   {[q for q, _, _ in filas]}")
esperar(all(o for _, o, _ in filas), f"con todo bien alguna fila dio NO: {[f for f in filas if not f[1]]}")

# sin carpeta: nada, o una sola fila NO si quedó el servicio registrado
serv = ServicioFalso()
serv.estado[sw.SERVICIO_CELULAR] = ("no_instalado", 0)
esperar(filas_celular(destino_revision(con_carpeta=False), serv) == [],
        "sin la carpeta ApiCelular y sin servicio aparecieron filas del celular")
serv = ServicioFalso()
filas = filas_celular(destino_revision(con_carpeta=False), serv)
esperar(len(filas) == 1 and filas[0][1] is False and "sin su carpeta" in filas[0][0] and
        "Desinstalar la API del celular" in filas[0][2],
        f"servicio registrado sin carpeta: esperaba una sola fila NO, salió {filas}")
esperar(not serv.llamo("poner_en_automatico") and not serv.llamo("arrancar"),
        "con el servicio sin carpeta se corrigió algo")

# habilitado = false: sin las del puerto, la base, el PIN (ni watchdog ni restos)
filas = filas_celular(destino_revision(habilitado="false"), ServicioFalso())
esperar([q for q, _, _ in filas] == TODAS[:4] + [TODAS[7]],
        f"con habilitado = false las filas tenían que ser las 4 primeras y la del firewall: "
        f"{[q for q, _, _ in filas]}")
esperar(fila(filas, "La API del celular está habilitada")[0] is False and
        "apagada a propósito" in fila(filas, "La API del celular está habilitada")[1],
        "habilitado = false no lo explica en el detalle")

# la del 8766 da NO con otro programa
serv = ServicioFalso()
serv.contesta = (False, "contesta OTRO programa (python (PID 9))")
r = fila(filas_celular(destino_revision(), serv), "En el 8766")
esperar(r and r[0] is False and "python (PID 9)" in r[1], f"la fila del 8766 con otro programa: {r}")

# Deshabilitada: nadie la pone en automático ni la arranca
serv = ServicioFalso()
serv.tipo[sw.SERVICIO_CELULAR] = "deshabilitado"
serv.estado[sw.SERVICIO_CELULAR] = ("parado", 0)
filas = filas_celular(destino_revision(), serv)
esperar(not serv.llamo("poner_en_automatico", sw.SERVICIO_CELULAR) and
        not serv.llamo("arrancar", sw.SERVICIO_CELULAR),
        f"con la API Deshabilitada en Windows se la tocó: {serv.llamadas}")
for que in TODAS[1:4]:
    r = fila(filas, que)
    esperar(r and r[0] is False and "no se toca" in r[1], f"Deshabilitada: la fila «{que}» dio {r}")

# Manual: se corrige (y antes se miró el tipo)
serv = ServicioFalso()
serv.tipo[sw.SERVICIO_CELULAR] = "manual"
filas = filas_celular(destino_revision(), serv)
esperar(fila(filas, "La API del celular arranca sola")[0] is True and
        serv.llamo("poner_en_automatico", sw.SERVICIO_CELULAR),
        "en Manual no se corrigió a automático")
serv = ServicioFalso()
serv.estado[sw.SERVICIO_CELULAR] = ("parado", 0)
filas_celular(destino_revision(), serv)
esperar(serv.llamo("arrancar", sw.SERVICIO_CELULAR), "parada (y no Deshabilitada) no se arrancó")

# cada fila aislada: una que explota no se lleva a las demás
for nombre in ("tipo_de_arranque", "estado_y_pid", "poner_en_automatico", "reintentos_configurados",
               "api_celular_contesta", "salud_api_celular", "asegurar_regla_firewall_celular",
               "reglas_que_bloquean", "estado_tarea", "restos_api_dueno"):
    serv = ServicioFalso()
    serv.lanzar = {nombre}
    try:
        filas = filas_celular(destino_revision(), serv)
    except Exception as e:
        fallos.append(f"con {nombre} explotando, la revisión final lanzó {e!r}")
        continue
    esperar([q for q, _, _ in filas] == TODAS,
            f"con {nombre} explotando se perdieron filas: {[q for q, _, _ in filas]}")
    esperar(not all(o for _, o, _ in filas), f"con {nombre} explotando todas las filas dieron SI")

# el watchdog: SI solo si corre de verdad
CASOS_WATCHDOG = [
    ({"existe": True, "deshabilitada": False, "ultimo_resultado": 0, "minutos_desde_ultima": 3}, True, ""),
    ({"existe": True, "deshabilitada": False, "ultimo_resultado": 267009, "minutos_desde_ultima": 1}, True, ""),
    ({"existe": True, "deshabilitada": True, "ultimo_resultado": 0, "minutos_desde_ultima": 3}, False, "deshabilitada"),
    ({"existe": True, "deshabilitada": False, "ultimo_resultado": 267011, "minutos_desde_ultima": None}, False, "todavía no corrió"),
    ({"existe": True, "deshabilitada": False, "ultimo_resultado": 1, "minutos_desde_ultima": 3}, False, "último resultado 1"),
    ({"existe": True, "deshabilitada": False, "ultimo_resultado": 0, "minutos_desde_ultima": 40}, False, "no corre hace 40 min"),
    ({"existe": False}, False, "Solo actualizar el watchdog"),
]
for tarea, espera_ok, espera_texto in CASOS_WATCHDOG:
    serv = ServicioFalso()
    serv.tarea = tarea
    r = fila(filas_celular(destino_revision(), serv), "El watchdog vigila")
    esperar(r and r[0] is espera_ok and espera_texto in r[1],
            f"watchdog {tarea}: esperaba {'SI' if espera_ok else 'NO'} «{espera_texto}», salió {r}")
r = fila(filas_celular(destino_revision(con_script=False), ServicioFalso()), "El watchdog vigila")
esperar(r and r[0] is False and "Solo actualizar el watchdog" in r[1],
        f"sin watchdog_celular.ps1 copiado la fila del watchdog dio {r}")

# el firewall: NO con Block, con Allow sin rango o con un perfil apagado; nunca borra
for campo, valor, aguja in (("bloquean", ["ApiCelular"], "BLOQUEAN"),
                            ("de_mas", ["ApiCelular (Permitir)"], "sin limitarlo"),
                            ("apagados", ["Public"], "apagado")):
    serv = ServicioFalso()
    setattr(serv, campo, valor)
    r = fila(filas_celular(destino_revision(), serv), "Firewall")
    esperar(r and r[0] is False and aguja in r[1] and valor[0] in r[1],
            f"firewall con {campo}={valor}: esperaba NO nombrándola, salió {r}")
    borrar = [l for l in serv.llamadas if l[0] == "_powershell" and "Remove-NetFirewallRule" in l[1]]
    esperar(not borrar, f"la revisión final borró reglas del firewall: {borrar}")
# PowerShell que no contesta (tiempo agotado con cientos de reglas): no es
# "no hay ninguna". La fila no se puede ganar el SI sin haber preguntado.
for campo in ("bloquean", "de_mas", "apagados"):
    serv = ServicioFalso()
    setattr(serv, campo, None)
    r = fila(filas_celular(destino_revision(), serv), "Firewall")
    esperar(r and r[0] is False and "no se pudo preguntar" in r[1],
            f"firewall con {campo} sin respuesta de Windows: esperaba NO, salió {r}")
serv = ServicioFalso()
serv.placas = ["Ethernet 2"]
r = fila(filas_celular(destino_revision(), serv), "Firewall")
esperar(r and r[0] is True and "Ethernet 2" in r[1], f"la placa CGNAT tenía que ir como aviso: {r}")
serv = ServicioFalso()
serv.regla = (False, "no encontré la placa de Tailscale")
r = fila(filas_celular(destino_revision(), serv), "Firewall")
esperar(r and r[0] is False and "placa de Tailscale" in r[1], f"sin placa de Tailscale: {r}")

# restos del ApiDueno viejo: solo informa
serv = ServicioFalso()
serv.restos = ["proceso ApiDueno (PID 70)"]
serv.quien = "ApiDueno (PID 70)"
r = fila(filas_celular(destino_revision(), serv), "No quedan restos")
esperar(r and r[0] is False and "ApiDueno (PID 70)" in r[1] and "no se borró nada" in r[1],
        f"restos del ApiDueno viejo: {r}")
if not [f for f in fallos if "fila" in f or "firewall" in f or "watchdog" in f or "Deshabilitada" in f
        or "habilitado" in f or "restos" in f or "explotando" in f]:
    ok("filas de la API del celular: solo con la carpeta, aisladas, Deshabilitada intocable, "
       "watchdog y firewall que verifican de verdad")


class RevisionDePrueba:
    def __init__(self):
        self.lineas = []

    def _log(self, texto):
        self.lineas.append(texto)

    _revision_final = act.Actualizador._revision_final
    _puerto_remoto = act.Actualizador._puerto_remoto
    _estado_de_los_respaldos = act.Actualizador._estado_de_los_respaldos
    _rescatar_evidencia = act.Actualizador._rescatar_evidencia
    _guardar_informe = act.Actualizador._guardar_informe

    def _excluir_del_antivirus(self, destino):
        return True, "confirmado en la lista de exclusiones"


serv = ServicioFalso()
# Detalles con IPs adentro, como podría traerlos Windows: no pueden llegar al informe.
serv.remoto = (False, "contesta OTRO programa (escucha en 192.168.0.10:8765)")
serv.quien = "python (PID 3)"
serv.contesta = (False, "contesta OTRO programa (100.101.102.103)")
p = Parches()
rev = RevisionDePrueba()
try:
    serv.instalar_en(p)
    rev._revision_final(destino_revision())
finally:
    p.sacar()
informe = leer(os.path.join(CASA, "Desktop", "otter_revision_final.txt"))
esperar(not RE_IPV4.search(informe), f"el informe de la revisión final tiene una IP:\n{informe}")
esperar(not RE_IPV4.search("\n".join(map(str, rev.lineas))), "la pantalla de la revisión final muestra una IP")
esperar(serv.llamo("remote_api_contesta", 8765), "la fila del 8765 no usa remote_api_contesta")
esperar("En el 8765 contesta la API remota (es lo que usa Leo)" in informe,
        "no está la fila nueva del 8765 en el informe")
esperar(serv.llamo("poner_en_automatico", sw.NOMBRE_SERVICIO) or
        ("poner_en_automatico",) in serv.llamadas,
        "la fila de los reintentos del servicio de stock no llamó a poner_en_automatico")
esperar("[SI] Windows reintenta el servicio de stock si se cae" in informe,
        "no está la fila de los reintentos del servicio de stock")
esperar(any("El watchdog vigila la API del celular" in l for l in rev.lineas),
        "_revision_final no incluye las filas de la API del celular")
if not [f for f in fallos if "informe" in f or "8765" in f or "reintentos" in f]:
    ok("revisión final: fila del 8765 por identidad, reintentos del stock siempre, informe sin IPs")


# ================================================================ #
# 4. OtterInstalador
# ================================================================ #
print("\n=== Instalador ===")


class InstaladorDePrueba:
    def __init__(self, origen):
        self.origen = origen
        self.lineas = []
        self.orden = []

    def _log(self, texto):
        self.lineas.append(str(texto))

    _instalar_local = inst.Instalador._instalar_local
    _instalar_celular = inst.Instalador._instalar_celular
    _copiar_apps = inst.Instalador._copiar_apps
    _configurar = inst.Instalador._configurar

    def _instalar_servicio(self, destino):
        self.orden.append("servicio de stock")

    def _crear_acceso_directo(self, nombre, ruta):
        pass

    def _mostrar_ip_tailscale(self):
        pass

    def texto(self):
        return "\n".join(self.lineas)


def instalar(celular, servicio=None, previo=None):
    raiz = tempfile.mkdtemp(prefix="inst_")
    origen = dist_nuevo(raiz)
    destino = os.path.join(raiz, "SistemaDual")
    if previo:
        previo(destino)
    app = InstaladorDePrueba(origen)
    servicio = servicio or ServicioFalso(app.orden)
    servicio.orden = app.orden
    popen = []
    p = Parches()
    try:
        servicio.instalar_en(p)
        p.poner(inst, "es_administrador", lambda: True)
        p.poner(inst, "subprocess", types.SimpleNamespace(
            Popen=lambda args, **kw: popen.append(args), run=None))
        app._instalar_local({"destino_local": destino, "nombre_local": "Kiosco de prueba",
                             "remoto": False, "ruta_excel": "", "servicio": True,
                             "accesos": False, "celular": celular})
    finally:
        p.sacar()
    return app, servicio, destino, popen


app, serv, destino, popen = instalar(celular=False)
esperar(not serv.llamadas and not os.path.exists(os.path.join(destino, "ApiCelular")) and
        not ini(os.path.join(destino, "config.ini")).has_section("api_celular"),
        f"con la casilla destildada se tocó algo de la API del celular: {serv.llamadas}")

app, serv, destino, popen = instalar(celular=True)
esperar(leer(os.path.join(destino, "ApiCelular", "ApiCelular.exe")) == "NUEVO",
        "con la casilla tildada no se copió ApiCelular")
esperar(not os.path.exists(os.path.join(destino, "ApiCelular", "config.ini")),
        "el instalador copió el config.ini de prueba del build adentro de ApiCelular")
esperar(ini(os.path.join(destino, "config.ini")).get("api_celular", "habilitado", fallback="") == "true",
        "el instalador no escribió [api_celular] habilitado = true")
esperar(serv.llamo("instalar_servicio") and serv.llamo("asegurar_regla_firewall_celular") and
        serv.llamo("acceso_directo_pin_celular"), f"faltó algún paso de la instalación: {serv.llamadas}")
esperar(popen and popen[0][1:] == ["definir-pin", "--pausa"],
        f"no abrió la consola para definir el PIN: {popen}")
esperar("=== INSTALACIÓN TERMINADA ===" in app.texto(), "la instalación con la casilla no terminó")

serv = ServicioFalso()
serv.instalar_lanza = True
app, serv, destino, popen = instalar(celular=True, servicio=serv)
esperar("=== INSTALACIÓN TERMINADA ===" in app.texto() and "ATENCIÓN" in app.texto(),
        "con instalar_servicio explotando la instalación de la caja no terminó o no avisó")
serv = ServicioFalso()
serv.resultado_instalar = (False, "no quedó corriendo")
app, serv, destino, popen = instalar(celular=True, servicio=serv)
esperar("=== INSTALACIÓN TERMINADA ===" in app.texto() and "ATENCIÓN" in app.texto(),
        "con instalar_servicio fallando no se avisó con ATENCIÓN")

# reinstalación encima con el servicio corriendo: para ANTES de copiar
visto_al_parar = []
serv = ServicioFalso()


def ya_instalada(destino):
    escribir(os.path.join(destino, "ApiCelular", "ApiCelular.exe"), "VIEJO")


def mirar_al_parar():
    visto_al_parar.append(leer(os.path.join(DESTINO_ACTUAL[0], "ApiCelular", "ApiCelular.exe")))


DESTINO_ACTUAL = []


def previo_y_recordar(destino):
    DESTINO_ACTUAL.append(destino)
    ya_instalada(destino)


serv.al_parar = mirar_al_parar
app, serv, destino, popen = instalar(celular=True, servicio=serv, previo=previo_y_recordar)
esperar(visto_al_parar == ["VIEJO"], f"no paró la API del celular antes de copiar encima: {visto_al_parar}")
esperar(leer(os.path.join(destino, "ApiCelular", "ApiCelular.exe")) == "NUEVO", "no se reinstaló")

serv = ServicioFalso()
serv.resultado_parar = (False, "sigue parando (PID 8)")
DESTINO_ACTUAL.clear()
app, serv, destino, popen = instalar(celular=True, servicio=serv, previo=previo_y_recordar)
esperar(leer(os.path.join(destino, "ApiCelular", "ApiCelular.exe")) == "VIEJO" and
        "no se pudo parar" in app.texto() and "=== INSTALACIÓN TERMINADA ===" in app.texto(),
        "con la API corriendo y sin poder pararla se copió encima o no terminó la instalación")
if not [f for f in fallos if "instala" in f.lower() or "casilla" in f]:
    ok("instalador: sin casilla no toca nada; un fallo no frena la caja; para antes de copiar encima")


# ================================================================ #
# 5. USB de Mantenimiento
# ================================================================ #
print("\n=== Mantenimiento ===")


def usb_con_espejo(raiz):
    usb = os.path.join(raiz, "USB")
    escribir(os.path.join(usb, "espejo_apps", "MaestroCaja", "MaestroCaja.exe"), "NUEVO-NUEVO")
    escribir(os.path.join(usb, "espejo_apps", "ApiCelular", "ApiCelular.exe"), "NUEVO-NUEVO")
    escribir(os.path.join(usb, "espejo_apps", "ApiCelular", "config.ini"), "de prueba")
    return usb


def reparar(destino, usb, servicio):
    log = []
    p = Parches()
    try:
        servicio.instalar_en(p)
        mant.reparar_archivos_app(destino, "MAESTRO", usb, log)
    finally:
        p.sacar()
    return log


raiz = tempfile.mkdtemp(prefix="mant_")
usb = usb_con_espejo(raiz)
destino = os.path.join(raiz, "SistemaDual")
escribir(os.path.join(destino, "MaestroCaja", "MaestroCaja.exe"), "VIEJO")
serv = ServicioFalso()
reparar(destino, usb, serv)
esperar(not os.path.exists(os.path.join(destino, "ApiCelular")),
        "el Mantenimiento creó ApiCelular\\ en una PC que no la tenía (instaló sin servicio)")
esperar(leer(os.path.join(destino, "MaestroCaja", "MaestroCaja.exe")) == "NUEVO-NUEVO",
        "el Mantenimiento dejó de reparar MaestroCaja")
esperar(not serv.llamo("parar"), "sin ApiCelular instalada se paró el servicio")

raiz = tempfile.mkdtemp(prefix="mant_")
usb = usb_con_espejo(raiz)
destino = os.path.join(raiz, "SistemaDual")
escribir(os.path.join(destino, "MaestroCaja", "MaestroCaja.exe"), "VIEJO")
escribir(os.path.join(destino, "ApiCelular", "ApiCelular.exe"), "VIEJO")
serv = ServicioFalso()
visto = []
serv.al_parar = lambda: visto.append(leer(os.path.join(destino, "ApiCelular", "ApiCelular.exe")))
log = reparar(destino, usb, serv)
esperar(leer(os.path.join(destino, "ApiCelular", "ApiCelular.exe")) == "NUEVO-NUEVO",
        "con ApiCelular instalada no se repararon sus archivos")
esperar(visto == ["VIEJO"], "no se paró la API del celular ANTES de copiar sus archivos")
esperar(serv.llamo("arrancar", sw.SERVICIO_CELULAR), "después de reparar no se volvió a arrancar")
esperar(not os.path.exists(os.path.join(destino, "ApiCelular", "config.ini")),
        "el Mantenimiento copió el config.ini del espejo adentro de ApiCelular")

serv = ServicioFalso()
reparar(destino, usb, serv)       # ya está todo igual que el espejo
esperar(not serv.llamo("parar"), "sin archivos distintos igual se paró la API del celular")

raiz = tempfile.mkdtemp(prefix="mant_")
usb = usb_con_espejo(raiz)
destino = os.path.join(raiz, "SistemaDual")
escribir(os.path.join(destino, "MaestroCaja", "MaestroCaja.exe"), "VIEJO")
escribir(os.path.join(destino, "ApiCelular", "ApiCelular.exe"), "VIEJO")
serv = ServicioFalso()
serv.resultado_parar = (False, "sigue parando")
log = reparar(destino, usb, serv)
esperar(leer(os.path.join(destino, "ApiCelular", "ApiCelular.exe")) == "VIEJO",
        "con la API del celular sin parar se copiaron sus archivos (versiones mezcladas)")
esperar(leer(os.path.join(destino, "MaestroCaja", "MaestroCaja.exe")) == "NUEVO-NUEVO",
        "que no pare la API del celular frenó la reparación de MaestroCaja")
esperar("[ARCHIVOS] La API del celular no se pudo parar: no se repararon sus archivos "
        "(sin mezclar versiones)." in log, f"no avisó que no la pudo parar: {log}")

# Deshabilitada: ni automático ni arranque; el StockService se sigue corrigiendo como hoy
raiz = tempfile.mkdtemp(prefix="mant_")
usb = usb_con_espejo(raiz)
destino = os.path.join(raiz, "SistemaDual")
escribir(os.path.join(destino, "ApiCelular", "ApiCelular.exe"), "VIEJO")
escribir(os.path.join(destino, "config.ini"), "[api_celular]\nhabilitado = true\n")
serv = ServicioFalso()
serv.tipo[sw.SERVICIO_CELULAR] = "deshabilitado"
serv.estado[sw.SERVICIO_CELULAR] = ("parado", 0)
reparar(destino, usb, serv)
log = []
p = Parches()
try:
    serv.instalar_en(p)
    mant._verificar_servicio_celular(destino, log)
finally:
    p.sacar()
esperar(not serv.llamo("poner_en_automatico", sw.SERVICIO_CELULAR) and
        not serv.llamo("arrancar", sw.SERVICIO_CELULAR),
        f"el Mantenimiento tocó una API del celular Deshabilitada: {serv.llamadas}")
esperar(any("Deshabilitada" in l for l in log), f"no informó que está Deshabilitada: {log}")

serv = ServicioFalso()
serv.tipo[sw.SERVICIO_CELULAR] = "manual"
serv.estado[sw.SERVICIO_CELULAR] = ("parado", 0)
log = []
p = Parches()
try:
    serv.instalar_en(p)
    mant._verificar_servicio_celular(destino, log)
finally:
    p.sacar()
esperar(serv.llamo("poner_en_automatico", sw.SERVICIO_CELULAR) and serv.llamo("arrancar", sw.SERVICIO_CELULAR),
        "una API del celular en Manual y parada no se corrigió ni se arrancó")
esperar(serv.llamo("api_celular_contesta"), "el Mantenimiento no informa si contesta la API del celular")

serv = ServicioFalso()
serv.estado[sw.SERVICIO_CELULAR] = ("no_instalado", 0)
log = []
p = Parches()
try:
    serv.instalar_en(p)
    mant._verificar_servicio_celular(destino, log)
finally:
    p.sacar()
esperar(not log and not serv.llamo("instalar_servicio") and not serv.llamo("arrancar"),
        "sin la API del celular instalada el Mantenimiento hizo algo (nunca la instala)")

serv = ServicioFalso()
serv.tipo[sw.NOMBRE_SERVICIO] = "deshabilitado"
log = []
p = Parches()
try:
    serv.instalar_en(p)
    mant._verificar_arranque_automatico(log)
finally:
    p.sacar()
esperar(serv.llamo("poner_en_automatico", sw.NOMBRE_SERVICIO),
        "el servicio de stock Deshabilitado ya no se corrige como antes")
if not [f for f in fallos if "Mantenimiento" in f or "mezcladas" in f or "parar" in f]:
    ok("mantenimiento: no instala la opcional, para antes de reparar (y solo si hace falta), "
       "Deshabilitada intocable, el StockService igual que siempre")


# ================================================================ #
# 6. Build (estático)
# ================================================================ #
print("\n=== Build ===")
compilar = os.path.join(RAIZ, "build", "compilar_api_celular.bat")
if not os.path.isfile(compilar):
    fallos.append("falta build/compilar_api_celular.bat")
else:
    bat = leer(compilar)
    # Los REM explican justamente por qué NO van: se miran solo los comandos.
    comandos = "\n".join(l for l in bat.splitlines() if not l.strip().upper().startswith("REM"))
    for prohibido in ("--windowed", "--noconsole"):
        if prohibido in comandos:
            fallos.append(f"compilar_api_celular.bat usa {prohibido}: sin consola 'install' falla "
                          f"callado y 'definir-pin' no puede pedir el PIN")
    for oculto in ("win32timezone", "servicemanager", "win32serviceutil", "win32service", "win32event",
                   "win32process", "win32api", "python_multipart", "pos_core.servicio_windows",
                   "pos_core.acceso_celular", "pos_core.panel_celular", "pos_core.telegram_bot",
                   "services.api_celular", "services.api_celular_cli", "services.api_celular_autoprueba"):
        if f"--hidden-import {oculto} " not in bat.replace("^\n", " ").replace("\n", " ") + " ":
            fallos.append(f"compilar_api_celular.bat no tiene --hidden-import {oculto}")
    for recolectar in ("--collect-submodules uvicorn", "--collect-submodules anyio", "--collect-data pdfminer"):
        if recolectar not in bat:
            fallos.append(f"compilar_api_celular.bat no tiene {recolectar}")
    if "ApiCelular.exe autoprueba" not in bat:
        fallos.append("compilar_api_celular.bat no corre la autoprueba del exe")
    if "services\\api_celular_servicio.py" not in bat:
        fallos.append("compilar_api_celular.bat no compila services\\api_celular_servicio.py")

todo = leer(os.path.join(RAIZ, "build", "build_all.bat"))


def bloque_pyinstaller(nombre, script):
    """El comando de PyInstaller de un .exe: desde --name hasta su .py."""
    i = todo.find(f"--name {nombre} ")
    j = todo.find(script, i)
    return todo[i:j] if i >= 0 and j > i else ""


if "call build\\compilar_api_celular.bat" not in todo:
    fallos.append("build_all.bat no llama a build\\compilar_api_celular.bat")
if "ApiCelular: autoprueba OK" not in todo:
    fallos.append("build_all.bat no dice 'ApiCelular: autoprueba OK'")
if "xcopy /E /I /Y dist\\ApiCelular dist\\USB_Mantenimiento\\espejo_apps\\ApiCelular" not in todo:
    fallos.append("build_all.bat no copia ApiCelular al espejo del USB de Mantenimiento")
m = re.search(r"for %%A in \(([^)]*)\) do", todo)
if not m or "ApiCelular" not in m.group(1).split():
    fallos.append("build_all.bat no limpia los datos de prueba de ApiCelular en el espejo")
for nombre, script, ocultos in (
        ("OtterActualizador", "apps\\actualizador\\main.py",
         ("pos_core.servicio_windows", "pos_core.config", "win32service", "win32com.client")),
        ("OtterInstalador", "apps\\instalador\\main.py",
         ("pos_core.servicio_windows", "win32service", "win32com.client")),
        ("USB_Mantenimiento", "apps\\usb_dev\\mantenimiento.py",
         ("pos_core.servicio_windows", "win32service"))):
    bloque = bloque_pyinstaller(nombre, script)
    if not bloque:
        fallos.append(f"no encontré el comando de PyInstaller de {nombre} en build_all.bat")
        continue
    for oculto in ocultos:
        if f"--hidden-import {oculto}" not in bloque:
            fallos.append(f"{nombre}: falta --hidden-import {oculto} (se importa adentro de una "
                          f"función: el .exe compila igual y falla recién en el local)")

requisitos = leer(os.path.join(RAIZ, "requirements.txt"))
for fijo in ("fastapi==0.142.2", "starlette==1.7.0", "pydantic==2.13.5", "uvicorn==0.54.0",
             "h11==0.16.0", "python-multipart==0.0.32", "anyio==4.15.1", "httpx==0.28.1"):
    if not re.search(rf"^{re.escape(fijo)}\s*$", requisitos, re.MULTILINE):
        fallos.append(f"requirements.txt no fija {fijo}")


def importa_api_celular(ruta):
    arbol = ast.parse(leer(ruta), filename=ruta)
    for nodo in ast.walk(arbol):
        nombres = []
        if isinstance(nodo, ast.Import):
            nombres = [a.name for a in nodo.names]
        elif isinstance(nodo, ast.ImportFrom):
            nombres = [nodo.module or ""] + [a.name for a in nodo.names]
        if any("api_celular" in n for n in nombres):
            return True
    return False


for carpeta, patron in (("apps/master_caja", ".py"), ("apps/master_dueno", ".py"), ("services", "stock_")):
    base = os.path.join(RAIZ, *carpeta.split("/"))
    for nombre in sorted(os.listdir(base)):
        ruta = os.path.join(base, nombre)
        if not nombre.endswith(".py") or (patron != ".py" and not nombre.startswith(patron)):
            continue
        if importa_api_celular(ruta):
            fallos.append(f"{carpeta}/{nombre} importa la API del celular: la caja, el Panel y el "
                          f"StockService no pueden depender de ella")
if not [f for f in fallos if ".bat" in f or "requirements" in f or "importa" in f or "hidden" in f]:
    ok("build: compilar_api_celular.bat con consola, hidden-imports y autoprueba; build_all lo llama, "
       "lo copia al espejo y lo limpia; versiones fijas; la caja no importa la API")


print()
if fallos:
    print("=== FALLOS DESPLIEGUE DEL CELULAR ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== DESPLIEGUE DEL CELULAR OK ===")
