"""Registro como Servicio de Windows real (arranca con el sistema, sin
sesión de usuario, sin ventana) usando pywin32. Uso:

    python stock_windows_service.py install
    python stock_windows_service.py start
    python stock_windows_service.py stop
    python stock_windows_service.py remove

Requiere `pip install pywin32` y compilar con PyInstaller apuntando a
este archivo para el ejecutable del servicio (ver build/build_all.bat).
"""

import os
import sys

import servicemanager
import win32event
import win32service
import win32serviceutil

from services.stock_daemon_windows import ciclo_watchdog, log
from pos_core.db import init_db


class StockService(win32serviceutil.ServiceFramework):
    _svc_name_ = "SistemaDualStockService"
    _svc_display_name_ = "Sistema Dual - Servicio de Stock"
    _svc_description_ = "Aplica descuentos de stock de forma oculta para el Sistema Maestro de Caja."

    def __init__(self, args):
        super().__init__(args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self._detener = False

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self._detener = True
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE, servicemanager.PYS_SERVICE_STARTED,
                               (self._svc_name_, ""))
        try:
            init_db()
            log.info("Servicio de Windows iniciado")
        except Exception:
            # Si esto falla, el servicio muere sin dejar rastro y en el
            # Administrador de servicios solo se ve "Detenido". Se deja
            # constancia en el log Y en el Visor de eventos de Windows,
            # que es donde se mira cuando el servicio ni siquiera arranca.
            log.exception("No se pudo iniciar el servicio")
            servicemanager.LogErrorMsg(f"Otter StockService no pudo iniciar: {sys.exc_info()[1]}")
            raise

        # La API remota del Dueño Remoto vive dentro de este servicio.
        try:
            from services.remote_api import iniciar_si_esta_habilitado
            if iniciar_si_esta_habilitado() is not None:
                log.info("API remota iniciada")
        except Exception:
            log.exception("No se pudo iniciar la API remota")

        # Bot de Telegram: alertas de stock 24/7, aunque nadie tenga el
        # Panel del Dueño abierto.
        try:
            from pos_core.telegram_bot import MonitorAlertas
            MonitorAlertas(intervalo_segundos=300).start()
            log.info("Monitor de alertas de Telegram iniciado")
        except Exception:
            log.exception("No se pudo iniciar el monitor de alertas de Telegram")

        while not self._detener:
            try:
                ciclo_watchdog()
            except Exception:
                log.exception("Error en ciclo del servicio de Windows")
            # espera interrumpible: reacciona rápido a SvcStop en vez de un time.sleep ciego
            win32event.WaitForSingleObject(self.hWaitStop, 5000)


def _asegurar_salida_estandar():
    """Garantiza que sys.stdout / sys.stderr existan.

    Un .exe compilado con --noconsole se queda sin salida estándar, y
    win32serviceutil.HandleCommandLine imprime ("Installing service...")
    antes de hacer nada: sin stdout, eso falla y la instalación del
    servicio se aborta sin mostrar ni un error, dejando la sensación de
    que el comando "no hizo nada". Se redirige al log para no perder el
    mensaje y para que install/start/stop funcionen igual.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    from pos_core.paths import logs_dir
    destino = open(os.path.join(logs_dir(), "servicio_consola.log"), "a", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = destino
    if sys.stderr is None:
        sys.stderr = destino


if __name__ == "__main__":
    _asegurar_salida_estandar()
    if len(sys.argv) == 1:
        # Sin argumentos = lo está arrancando el Administrador de
        # servicios de Windows, que ejecuta el .exe pelado. Hay que
        # entregarle el control al despachador de servicios.
        #
        # Sin esto, HandleCommandLine imprime su ayuda y termina: el
        # servicio queda instalado pero SIEMPRE en "Detenido", sin ningún
        # error visible (y menos aún compilado con --noconsole, donde esa
        # ayuda no se ve en ningún lado).
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(StockService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        # Con argumentos = lo está llamando una persona desde la consola:
        # install / start / stop / remove.
        win32serviceutil.HandleCommandLine(StockService)
