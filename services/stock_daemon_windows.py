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
from logging.handlers import RotatingFileHandler

from pos_core.paths import logs_dir, set_base_override_to_parent_dir

# Este demonio SIEMPRE es parte del Maestro (nunca corre en un USB), y
# tiene que apuntar a la MISMA database/ que Caja y Dueño Maestro (ver
# apps/master_caja/main.py y apps/master_dueno/main.py). Se aplica acá
# arriba de todo, antes de calcular logs_dir()/init_db(), porque este
# módulo también se importa como librería desde stock_windows_service.py.
set_base_override_to_parent_dir()

from pos_core.db import get_connection, init_db
from pos_core import stock_service
from pos_core.sales import CODIGO_SIN_BARRA

# Con rotación y NO con basicConfig(filename=...): este servicio corre
# 24/7 desde que arranca Windows, y una sola línea de error repitiéndose
# (una venta que nunca se puede aplicar, la API remota rechazando) escribe
# sin parar en un archivo que nunca se cierra. Un disco lleno no es solo
# "se acabó el espacio": es la forma más común de corromper una base
# SQLite en pleno uso, o sea exactamente el desastre que este servicio
# tiene que evitar. 5 archivos de 2 MB = 10 MB como techo, para siempre.
_handler = RotatingFileHandler(
    os.path.join(logs_dir(), "stock_daemon.log"),
    maxBytes=2 * 1024 * 1024, backupCount=4, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log = logging.getLogger("stock_daemon")
log.setLevel(logging.INFO)
if not log.handlers:
    log.addHandler(_handler)

INTERVALO_SEGUNDOS = 5


DIAS_VENTANA_WATCHDOG = 2   # solo se reintentan ventas recientes, ver abajo


def _ventas_con_stock_pendiente():
    """Detecta líneas de venta que todavía no tienen su movimiento
    SALIDA_VENTA (caída justo entre el INSERT de la venta y el descuento
    de stock).

    Dos filtros importantes, los dos para no quedar reintentando para
    siempre algo que nunca va a poder aplicarse:

    - Se excluye el código reservado de "artículo sin código de barra":
      esas líneas NO tienen producto en Productos y cerrar_ticket() las
      saltea a propósito, así que su movimiento no va a existir nunca.
    - Se mira solo la ventana de días reciente: una línea vieja que falla
      siempre (producto borrado, por ejemplo) se reintentaría en cada
      ciclo, cada 5 segundos, llenando el log para siempre. Un descuento
      que quedó pendiente de verdad se resuelve en segundos, no en días.
    """
    conn = get_connection()
    return conn.execute(
        """
        SELECT dv.venta_uuid, dv.producto_codigo, dv.cantidad, v.usuario
        FROM Detalle_Ventas dv
        JOIN Ventas v ON v.uuid_unico = dv.venta_uuid
        WHERE v.anulada = 0
          AND dv.producto_codigo <> ?
          AND v.fecha_hora >= datetime('now', 'localtime', ?)
          AND NOT EXISTS (
              SELECT 1 FROM Movimientos_Stock ms
              WHERE ms.ticket_uuid = dv.venta_uuid AND ms.producto_codigo = dv.producto_codigo
          )
        """,
        (CODIGO_SIN_BARRA, f"-{DIAS_VENTANA_WATCHDOG} days"),
    ).fetchall()


# Líneas que ya fallaron y cuyo error se registró: se siguen reintentando
# (el problema puede resolverse solo, p.ej. reactivando el producto), pero
# el error se escribe UNA vez y no en cada ciclo, para no inflar el log.
_fallas_ya_registradas = set()


# El respaldo se intenta desde acá y no desde una tarea aparte porque
# este servicio es lo único que ya corre 24/7 y arranca con Windows: es el
# único lugar donde una copia diaria no depende de que alguien se acuerde.
# Se chequea una vez por hora (no en cada ciclo de 5 segundos) y la
# función se encarga sola de no repetir la copia del día.
INTERVALO_RESPALDO_SEGUNDOS = 3600
_ultimo_intento_respaldo = 0.0


def _respaldar_si_corresponde():
    global _ultimo_intento_respaldo
    ahora = time.time()
    if ahora - _ultimo_intento_respaldo < INTERVALO_RESPALDO_SEGUNDOS:
        return
    _ultimo_intento_respaldo = ahora
    try:
        from pos_core import respaldo
        respaldo.hacer_copia()
    except Exception:
        # Un fallo del respaldo NUNCA puede frenar el descuento de stock.
        log.exception("No se pudo intentar la copia diaria")


def ciclo_watchdog():
    _respaldar_si_corresponde()
    pendientes = _ventas_con_stock_pendiente()
    for p in pendientes:
        clave = (p["venta_uuid"], p["producto_codigo"])
        try:
            stock_service.descontar_por_venta(
                p["producto_codigo"], p["cantidad"], ticket_uuid=p["venta_uuid"],
                usuario=p["usuario"], origen="MAESTRO")
            _fallas_ya_registradas.discard(clave)
            log.info("Descuento diferido aplicado: venta=%s producto=%s",
                      p["venta_uuid"], p["producto_codigo"])
        except Exception:
            if clave not in _fallas_ya_registradas:
                _fallas_ya_registradas.add(clave)
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
