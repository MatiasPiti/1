"""Registro como Servicio de Windows real (arranca con el sistema, sin
sesión de usuario, sin ventana) usando pywin32. Uso:

    python stock_windows_service.py install
    python stock_windows_service.py start
    python stock_windows_service.py stop
    python stock_windows_service.py remove

Requiere `pip install pywin32` y compilar con PyInstaller apuntando a
este archivo para el ejecutable del servicio (ver build/build_all.bat).
"""

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
        init_db()
        log.info("Servicio de Windows iniciado")
        while not self._detener:
            try:
                ciclo_watchdog()
            except Exception:
                log.exception("Error en ciclo del servicio de Windows")
            # espera interrumpible: reacciona rápido a SvcStop en vez de un time.sleep ciego
            win32event.WaitForSingleObject(self.hWaitStop, 5000)


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(StockService)
