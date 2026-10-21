"""Punto de entrada de ApiCelular.exe (servicio de Windows de la app del celular).

    ApiCelular.exe                                   lo arranca el Administrador de servicios
    ApiCelular.exe install|update|start|stop|remove  pywin32 (install/update: --startup auto)
    ApiCelular.exe definir-pin [--base DIR] [--pausa]
    ApiCelular.exe cerrar-sesiones [--base DIR]
    ApiCelular.exe habilitar|deshabilitar [--base DIR]
    ApiCelular.exe diagnostico [--base DIR]
    ApiCelular.exe consola [--base DIR] [--puerto N]  (desarrollo: NO en la PC del local)
    ApiCelular.exe autoprueba [--conservar]          (base temporal propia; nunca toca la real)

En desarrollo: python services/api_celular_servicio.py <verbo> --base <carpeta>.

pywin32 se importa ADENTRO de una función: así definir-pin, diagnostico y
autoprueba corren en Linux en las pruebas. Se compila SIN --noconsole (como
el StockService): sin consola "install" falla sin mostrar nada y definir-pin
no puede pedir el PIN.
"""

import glob
import os
import sys
import tempfile
import time

if __package__ in (None, ""):
    # Corriendo como script (python services/api_celular_servicio.py): la raíz
    # del repo tiene que estar en el path para importar pos_core.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NOMBRE_SERVICIO = "SistemaDualApiCelular"
VERBOS_PYWIN32 = ("install", "update", "start", "stop", "remove")
VERBOS_RECHAZADOS = ("debug", "restart")
VERBOS_CLI = ("definir-pin", "cerrar-sesiones", "habilitar", "deshabilitar", "diagnostico", "consola")
# Opciones que llevan un valor detrás (de pywin32 y nuestras).
_CON_VALOR = {"--base", "--puerto", "--startup", "--username", "--password", "--perfmonini",
              "--perfmondll", "--wait"}

TEXTO_RECHAZADO = "Ese verbo no existe en ApiCelular. Para desarrollo usá «consola»."
TEXTO_DOBLE_CLIC = ("Este programa es un servicio de Windows: no se abre con doble clic. Para cambiar el PIN "
                    "del celular usá «Otter - PIN del celular» en el menú Inicio.")


def _buscar_verbo(argv: list):
    saltear = False
    for i, a in enumerate(argv):
        if saltear:
            saltear = False
            continue
        if a.startswith("-"):
            saltear = a in _CON_VALOR
            continue
        return i, a
    return None, None


def _sacar_opcion(argv: list, nombre: str):
    """(valor, argv sin la opción). El valor es None si no vino."""
    salida, valor, i = [], None, 0
    while i < len(argv):
        if argv[i] == nombre and i + 1 < len(argv):
            valor = argv[i + 1]
            i += 2
            continue
        if argv[i].startswith(nombre + "="):
            valor = argv[i].split("=", 1)[1]
            i += 1
            continue
        salida.append(argv[i])
        i += 1
    return valor, salida


def fijar_base(base_arg) -> bool:
    """--base si viene; si no y está congelado, la carpeta padre del exe
    (C:\\SistemaDual, como el StockService). En desarrollo sin --base no se
    adivina: la base sería la raíz del repo."""
    from pos_core import paths
    if base_arg:
        paths.set_base_override(os.path.abspath(base_arg))
        return True
    if getattr(sys, "frozen", False):
        paths.set_base_override_to_parent_dir()
        return True
    return False


def _asegurar_salida_estandar() -> None:
    """Como stock_windows_service._asegurar_salida_estandar, pero hacia
    logs\\api_celular_consola.log. Si pasa los 5 MB se renombra a .viejo
    antes de abrirlo: nadie lo rota y el servicio corre 24/7."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from pos_core.paths import logs_dir
    ruta = os.path.join(logs_dir(), "api_celular_consola.log")
    try:
        if os.path.getsize(ruta) > 5 * 1024 * 1024:
            os.replace(ruta, ruta + ".viejo")
    except OSError:
        pass
    destino = open(ruta, "a", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = destino
    if sys.stderr is None:
        sys.stderr = destino


def _borrar_temporales_viejos() -> None:
    """PDFs de facturas que quedaron de un corte (se borran en finally, pero
    un proceso matado no llega al finally)."""
    limite = time.time() - 24 * 3600
    for ruta in glob.glob(os.path.join(tempfile.gettempdir(), "otter_celular_*.pdf")):
        try:
            if os.path.getmtime(ruta) < limite:
                os.remove(ruta)
        except OSError:
            pass


def _correr_servicio(servicio) -> None:
    """SvcDoRun: el bucle del servicio. Nada de acá puede tirar el proceso."""
    import win32event
    from services import api_celular
    api_celular.configurar_logs("servicio")
    log = api_celular.log
    try:
        import win32api
        import win32process
        # La caja corre en la misma PC: la API nunca le puede ganar la CPU.
        win32process.SetPriorityClass(win32api.GetCurrentProcess(), win32process.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        log.exception("No se pudo bajar la prioridad del proceso")
    try:
        _borrar_temporales_viejos()
    except Exception:
        pass
    try:
        from pos_core import acceso_celular, config, db, panel_celular
        ruta = panel_celular.ruta_base_datos()
        db.usar_solo_base_existente(ruta)
        cfg = config.leer_config_celular()
        log.info("ApiCelular %s (compilado %s) arrancando. Puerto %s, habilitado %s. Base %s: %s. PIN: %s",
                 api_celular.VERSION_API, api_celular.compilado(), cfg["puerto"], cfg["habilitado"], ruta,
                 panel_celular.estado_base()["detalle"], acceso_celular.estado_pin())
        db.cerrar_conexion()
    except Exception:
        log.exception("No se pudo anotar el estado de arranque")
    estado = api_celular.EstadoApi()
    servicio.supervisor = api_celular.Supervisor(autochequeo=True, estado=estado)
    while True:
        servicio.supervisor.paso()
        if win32event.WaitForSingleObject(servicio.hWaitStop, 5000) == win32event.WAIT_OBJECT_0:
            break
    # Por si el pedido de parar llegó antes de que existiera el supervisor
    # (SvcStop no tenía a quién avisarle): que no quede uvicorn escuchando.
    servicio.supervisor.detener(8)
    log.info("ApiCelular detenido")


def _servicio_windows():
    """La clase del servicio, definida acá adentro para no importar pywin32
    en Linux ni en los verbos de consola."""
    import win32event
    import win32service
    import win32serviceutil

    class ServicioApiCelular(win32serviceutil.ServiceFramework):
        _svc_name_ = NOMBRE_SERVICIO
        _svc_display_name_ = "Otter - API del celular"
        _svc_description_ = "App Panel Dueño (Android). Solo por Tailscale. No afecta a la caja."
        # Sin _svc_deps_ A PROPÓSITO: con dependencia del StockService,
        # "StockService.exe stop" y Restart-Service del StockService fallarían.

        def __init__(self, args):
            super().__init__(args)
            self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
            self.supervisor = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            try:
                if self.supervisor is not None:
                    self.supervisor.detener(8)
            finally:
                win32event.SetEvent(self.hWaitStop)

        def SvcDoRun(self):
            _correr_servicio(self)

    return ServicioApiCelular


def _como_servicio() -> int:
    import pywintypes
    import servicemanager
    clase = _servicio_windows()
    try:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(clase)
        servicemanager.StartServiceCtrlDispatcher()
        return 0
    except pywintypes.error as e:
        if getattr(e, "winerror", None) == 1063:
            # Doble clic sobre el exe: no lo está arrancando el Administrador
            # de servicios. Que la ventana no se cierre sin decir nada.
            print(TEXTO_DOBLE_CLIC)
            try:
                input("Apretá Enter para cerrar esta ventana.")
            except (EOFError, KeyboardInterrupt):
                pass
            return 1
        raise


def _pywin32(verbo_idx: int, argv: list) -> int:
    import win32serviceutil
    argumentos = list(argv)
    verbo = argumentos[verbo_idx]
    if verbo in ("install", "update") and not any(a.startswith("--startup") for a in argumentos):
        # pywin32 registra en Manual por defecto: no se nota el día de la
        # instalación y el servicio no vuelve al primer reinicio de la PC.
        argumentos = ["--startup", "auto"] + argumentos
    win32serviceutil.HandleCommandLine(_servicio_windows(), argv=[sys.argv[0]] + argumentos)
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        if not getattr(sys, "frozen", False):
            print(__doc__)
            return 2
        from pos_core import paths
        paths.set_base_override_to_parent_dir()
        _asegurar_salida_estandar()
        return _como_servicio()

    idx, verbo = _buscar_verbo(argv)
    if verbo in VERBOS_RECHAZADOS:
        print(TEXTO_RECHAZADO)
        return 2
    if verbo == "autoprueba":
        # Fija su carpeta temporal como base ANTES de cualquier logs_dir():
        # si no, dejaría un dist\logs en el build.
        from services import api_celular_autoprueba
        return api_celular_autoprueba.main(argv[:idx] + argv[idx + 1:])

    base, resto = _sacar_opcion(argv, "--base")
    if not fijar_base(base):
        print("Falta la carpeta de la instalación: pasá --base <carpeta>")
        return 2
    idx, verbo = _buscar_verbo(resto)
    if verbo in VERBOS_PYWIN32:
        _asegurar_salida_estandar()
        return _pywin32(idx, resto)
    if verbo in VERBOS_CLI:
        from services import api_celular_cli
        return api_celular_cli.main(verbo, resto[:idx] + resto[idx + 1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
