"""Copia diaria de la base, automática y verificada.

Por qué existe: hasta septiembre de 2026 el sistema NO tenía ninguna copia
de seguridad automática. Solo se respaldaba la base en dos momentos, los
dos manuales y ocasionales: cuando corría `OtterActualizador` y cuando el
USB de Mantenimiento encontraba la base corrupta. Es decir que el día que
el disco fallara, o que la base se dañara sin que nadie lo notara, el
negocio perdía todo: las ventas, el stock, y los 4587 productos que costó
migrar del sistema viejo. Peor todavía: la reparación de último recurso
del USB de Mantenimiento (`_restaurar_backup_mas_reciente`) busca copias
que, sin esto, casi nunca existían.

Cómo:

- Corre adentro del servicio de stock, que ya está 24/7 y arranca con
  Windows. No hace falta que nadie se acuerde de nada.
- Usa la API de backup de SQLite y no una copia de archivo: con WAL,
  copiar el .db suelto puede llevarse una base a medio escribir.
- **Verifica la copia** antes de darla por buena. Un backup que no se
  probó es una sensación de seguridad, no un backup.
- Nunca interrumpe la venta: cualquier error se registra y se sigue.

Lo que esto NO cubre, y hay que resolver aparte: las copias quedan en el
MISMO disco. Protegen contra la base dañada, un borrado accidental o un
bug, pero no contra que el disco muera o que se roben la máquina. Para
eso hace falta que salgan de la PC (un pendrive que quede puesto, o una
copia a la PC de Matías por Tailscale).
"""

import glob
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from pos_core.paths import backups_dir, db_path

log = logging.getLogger("stock_daemon")

DIAS_A_CONSERVAR = 14

# Con menos de esto en disco no se intenta la copia: llenar el disco es
# justamente una de las formas de corromper la base viva, y el remedio no
# puede ser peor que la enfermedad.
MINIMO_LIBRE_MB = 300


def _espacio_libre_mb(carpeta: str) -> float:
    import shutil
    return shutil.disk_usage(carpeta).free / (1024 * 1024)


def _copia_sana(ruta: str) -> bool:
    """¿Esta copia abre y pasa integrity_check?"""
    try:
        conn = sqlite3.connect(ruta)
        try:
            return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
    except Exception:
        return False


def ruta_de_hoy(fecha: datetime = None) -> str:
    dia = (fecha or datetime.now()).strftime("%Y-%m-%d")
    return os.path.join(backups_dir(), f"stock_{dia}.db")


def hay_copia_de_hoy(fecha: datetime = None) -> bool:
    return os.path.isfile(ruta_de_hoy(fecha))


def limpiar_viejas(dias: int = DIAS_A_CONSERVAR) -> list:
    """Borra las copias de más de `dias`. Devuelve lo que borró."""
    limite = datetime.now() - timedelta(days=dias)
    borradas = []
    for ruta in glob.glob(os.path.join(backups_dir(), "stock_*.db")):
        try:
            if datetime.fromtimestamp(os.path.getmtime(ruta)) < limite:
                os.remove(ruta)
                borradas.append(os.path.basename(ruta))
        except OSError:
            pass   # que no se pueda borrar una vieja no es motivo de nada
    return borradas


def hacer_copia(forzar: bool = False) -> dict:
    """Hace la copia del día si todavía no está.

    Devuelve {"hecha": bool, "ruta": str|None, "motivo": str}. Nunca
    lanza: esto corre adentro del servicio y un fallo del respaldo no
    puede tumbar el descuento de stock ni la API remota.
    """
    try:
        origen = db_path()
        if not os.path.isfile(origen):
            return {"hecha": False, "ruta": None, "motivo": "todavía no hay base"}

        destino = ruta_de_hoy()
        if os.path.isfile(destino) and not forzar:
            return {"hecha": False, "ruta": destino, "motivo": "ya hay copia de hoy"}

        libres = _espacio_libre_mb(backups_dir())
        if libres < MINIMO_LIBRE_MB:
            motivo = (f"quedan {libres:.0f} MB libres (mínimo {MINIMO_LIBRE_MB}): "
                      f"no se respalda para no llenar el disco")
            log.warning("[RESPALDO] %s", motivo)
            return {"hecha": False, "ruta": None, "motivo": motivo}

        # Se escribe primero a un temporal: si el proceso muere a la mitad,
        # no queda un archivo con el nombre del día que parece una copia
        # buena y está incompleto.
        temporal = destino + ".enproceso"
        if os.path.exists(temporal):
            os.remove(temporal)

        conexion_origen = sqlite3.connect(origen)
        conexion_destino = sqlite3.connect(temporal)
        try:
            with conexion_destino:
                conexion_origen.backup(conexion_destino)
        finally:
            conexion_destino.close()
            conexion_origen.close()

        if not _copia_sana(temporal):
            os.remove(temporal)
            motivo = "la copia recién hecha no pasó integrity_check; se descartó"
            log.error("[RESPALDO] %s", motivo)
            return {"hecha": False, "ruta": None, "motivo": motivo}

        os.replace(temporal, destino)
        borradas = limpiar_viejas()
        tamano = os.path.getsize(destino) / (1024 * 1024)
        log.info("[RESPALDO] Copia diaria OK: %s (%.1f MB). Borradas %d viejas.",
                 destino, tamano, len(borradas))
        return {"hecha": True, "ruta": destino, "motivo": "ok"}

    except Exception as e:
        log.exception("[RESPALDO] No se pudo hacer la copia diaria")
        return {"hecha": False, "ruta": None, "motivo": f"error: {e}"}


def listar_copias() -> list:
    """Las copias que hay, de la más nueva a la más vieja, con su tamaño.

    La usa el USB de Mantenimiento para poder decir en el informe si el
    negocio está respaldado o no.
    """
    copias = []
    for ruta in sorted(glob.glob(os.path.join(backups_dir(), "stock_*.db")), reverse=True):
        try:
            copias.append({
                "archivo": os.path.basename(ruta),
                "ruta": ruta,
                "mb": round(os.path.getsize(ruta) / (1024 * 1024), 1),
                "fecha": datetime.fromtimestamp(os.path.getmtime(ruta)).strftime("%Y-%m-%d %H:%M"),
            })
        except OSError:
            pass
    return copias
