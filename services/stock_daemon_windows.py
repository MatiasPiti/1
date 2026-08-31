"""Servicio oculto de Windows: escucha eventos "ticket cerrado" y aplica
el descuento de stock. Corre sin ventana (pythonw.exe) y, opcionalmente,
registrado como Servicio de Windows real vía pywin32 para que arranque
solo con el sistema operativo, sin sesión de usuario logueada.

Modelo de comunicación elegido: cola de eventos persistida en la propia
tabla `Movimientos_Stock` de SQLite (ya es transaccional y compartida por
todos los procesos), en vez de sockets/colas en memoria: así, si el
servicio estuviera caído un instante, el evento no se pierde apenas
vuelve a levantar, porque la Caja ya escribió el ticket antes de que este
demonio lo procese, y no hay ventana de pérdida.

En la práctica, `pos_core.sales.cerrar_ticket()` YA descuenta el stock en
la misma llamada (ver sales.py), por lo que este demonio cumple el rol
de watchdog de refuerzo: reintenta cualquier venta cuyo detalle no tenga
todavía su movimiento SALIDA_VENTA correspondiente (por ejemplo si la
Caja se cerró de golpe justo después de grabar el ticket).
"""

import logging
import os
import sys
import time

from pos_core.paths import logs_dir, set_base_override_to_parent_dir

# Este demonio SIEMPRE es parte del Maestro (nunca corre en un USB), y
# tiene que apuntar a la MISMA database/ que Caja y Dueño Maestro (ver
# apps/master_caja/main.py y apps/master_dueno/main.py). Se aplica acá
# arriba de todo, antes de calcular logs_dir()/init_db(), porque este
# módulo también se importa como librería desde stock_windows_service.py.
set_base_override_to_parent_dir()

from pos_core.db import get_connection, init_db
from pos_core import stock_service

logging.basicConfig(
    filename=os.path.join(logs_dir(), "stock_daemon.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("stock_daemon")

INTERVALO_SEGUNDOS = 5


def _ventas_con_stock_pendiente():
    """Detecta ventas cuyo total de líneas en Detalle_Ventas no tiene un
    movimiento SALIDA_VENTA equivalente todavía (caída justo entre el
    INSERT de la venta y el descuento de stock)."""
    conn = get_connection()
    return conn.execute(
        """
        SELECT dv.venta_uuid, dv.producto_codigo, dv.cantidad, v.usuario
        FROM Detalle_Ventas dv
        JOIN Ventas v ON v.uuid_unico = dv.venta_uuid
        WHERE v.anulada = 0
          AND NOT EXISTS (
              SELECT 1 FROM Movimientos_Stock ms
              WHERE ms.ticket_uuid = dv.venta_uuid AND ms.producto_codigo = dv.producto_codigo
          )
        """
    ).fetchall()


def ciclo_watchdog():
    pendientes = _ventas_con_stock_pendiente()
    for p in pendientes:
        try:
            stock_service.descontar_por_venta(
                p["producto_codigo"], p["cantidad"], ticket_uuid=p["venta_uuid"],
                usuario=p["usuario"], origen="MAESTRO")
            log.info("Descuento diferido aplicado: venta=%s producto=%s",
                      p["venta_uuid"], p["producto_codigo"])
        except Exception:
            log.exception("Fallo aplicando descuento diferido: venta=%s producto=%s",
                           p["venta_uuid"], p["producto_codigo"])


def main():
    init_db()
    log.info("Servicio de stock iniciado (oculto, sin ventana)")

    # Bot de Telegram: corre acá para que las alertas de stoploss/sobre-stock
    # lleguen 24/7 aunque nadie tenga abierto el Panel del Dueño.
    try:
        from pos_core.telegram_bot import MonitorAlertas
        MonitorAlertas(intervalo_segundos=300).start()
        log.info("Monitor de alertas de Telegram iniciado")
    except Exception:
        log.exception("No se pudo iniciar el monitor de alertas de Telegram")

    # API remota para el Dueño Remoto (otra PC, vía VPN tipo Tailscale):
    # no hace nada si está deshabilitada en config.ini (default).
    from services.remote_api import iniciar_si_esta_habilitado
    if iniciar_si_esta_habilitado() is not None:
        log.info("API remota iniciada")

    while True:
        try:
            ciclo_watchdog()
        except Exception:
            log.exception("Error en ciclo del watchdog de stock")
        time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    # Se ejecuta con pythonw.exe (sin consola). Para registrarlo como
    # servicio real de Windows, envolver esta función con pywin32
    # (win32serviceutil.ServiceFramework) — ver README sección de
    # instalación del servicio.
    main()
