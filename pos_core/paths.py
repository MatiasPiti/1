"""Resolución de rutas portables.

Regla de oro: NUNCA usar rutas absolutas hardcodeadas (C:\\..., D:\\...).
Toda la app se ubica respecto de la carpeta donde vive el ejecutable (o el
script .py en desarrollo), sin importar si el USB quedó montado en E:, F:
o G:. Esto es lo que permite que el mismo USB funcione en cualquier PC.
"""

import os
import sys

_base_override = None


def set_base_override(path: str) -> None:
    """Fuerza la 'carpeta base' de la app a un valor explícito.

    Lo usan los 3 ejecutables del Sistema Maestro (Caja, Dueño y el
    Servicio oculto de stock) para apuntar los tres a la MISMA
    database/config/logs, aunque cada uno viva en su propia subcarpeta
    de instalación (así lo genera PyInstaller --onedir: un exe por
    carpeta). Los USBs de emergencia NUNCA llaman a esto: cada uno debe
    seguir siendo autocontenido en su propia carpeta portable.
    """
    global _base_override
    _base_override = path


def _carpeta_del_programa() -> str:
    """La carpeta donde REALMENTE está el programa, sin mirar ningún override.

    - Si corre "congelado" por PyInstaller (--onefile), sys._MEIPASS apunta
      a la carpeta temporal de extracción de recursos empaquetados, pero
      para datos persistentes (DB, JSON, logs) usamos la carpeta donde
      REALMENTE está el .exe (sys.executable), no la temporal.
    - Si corre "congelado" con --onedir, sys.executable ya vive junto a
      los recursos y sirve igual.
    - Si corre como script .py normal (desarrollo), usamos la carpeta del
      archivo que se está ejecutando.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(sys.argv[0]))


def get_base_path() -> str:
    """Devuelve la carpeta base de la aplicación en ejecución: la fijada con
    set_base_override() si hay una, y si no la del programa."""
    if _base_override:
        return _base_override
    return _carpeta_del_programa()


def set_base_override_to_parent_dir() -> None:
    """Atajo: usa la carpeta PADRE de donde vive este ejecutable como
    base compartida. Es la convención de instalación del Maestro:
    C:\\SistemaDual\\MaestroCaja\\MaestroCaja.exe
    C:\\SistemaDual\\MaestroDueno\\MaestroDueno.exe
    C:\\SistemaDual\\StockService\\StockService.exe
    Los tres, aplicando esto, terminan compartiendo C:\\SistemaDual\\database\\stock.db.

    Se calcula SIEMPRE desde la carpeta real del ejecutable, no desde la
    base que esté puesta en ese momento: así llamarla dos veces da lo mismo
    que llamarla una. Antes subía un nivel por cada llamada, y hay un caso
    en que se llama dos veces: cuando la base está dañada y el cajero acepta
    restaurar la copia, el arranque se reintenta entero (pos_core/arranque).
    La segunda vuelta dejaba la base en C:\\ y la Caja abría, sin ningún
    error, contra una base NUEVA Y VACÍA en C:\\database — justo después de
    haber restaurado bien la de verdad. Es la misma trampa de la regla 2.
    """
    set_base_override(os.path.dirname(_carpeta_del_programa()))


def get_resource_path(relative_path: str) -> str:
    """Ruta a un recurso empaquetado de solo lectura (íconos, plantillas).

    Usa sys._MEIPASS cuando existe (--onefile), porque ahí es donde
    PyInstaller descomprime los recursos embebidos en tiempo de ejecución.
    """
    base = getattr(sys, "_MEIPASS", get_base_path())
    return os.path.join(base, relative_path)


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def data_dir() -> str:
    """Carpeta 'database/' junto al ejecutable, creada si no existe."""
    return ensure_dir(os.path.join(get_base_path(), "database"))


def db_path(filename: str = "stock.db") -> str:
    return os.path.join(data_dir(), filename)


def sync_dir() -> str:
    """Carpeta 'SYNC_DATA/' donde los USBs dejan sus JSON de exportación."""
    return ensure_dir(os.path.join(get_base_path(), "SYNC_DATA"))


def logs_dir() -> str:
    return ensure_dir(os.path.join(get_base_path(), "logs"))


def backups_dir() -> str:
    """Carpeta 'backups/' con las copias diarias de la base.

    Va FUERA de database/ a propósito: el USB de Mantenimiento excluye
    database/ al reponer archivos, y las copias tienen que quedar
    igualmente lejos de cualquier cosa que reemplace la base viva.
    """
    return ensure_dir(os.path.join(get_base_path(), "backups"))


def tickets_dir() -> str:
    """Respaldo en texto de los tickets, usado cuando no hay impresora
    disponible (ver pos_core/ticket_printer.py)."""
    return ensure_dir(os.path.join(get_base_path(), "tickets"))


def config_path() -> str:
    return os.path.join(get_base_path(), "config.ini")


def carpeta_escritorio() -> str:
    """Dónde dejar los informes que se fotografían o se mandan.

    No alcanza con "~/Desktop": si la PC tiene OneDrive, Windows redirige
    el Escritorio adentro de OneDrive y ahí la carpeta puede llamarse
    Escritorio en castellano. Un informe que queda en un lugar que nadie
    mira es lo mismo que no haberlo escrito, así que se prueban las
    variantes conocidas y, si ninguna existe, se cae a la carpeta del
    usuario — que siempre existe.

    Ojo: en Windows `expanduser("~")` NO mira HOME, mira USERPROFILE.

    Vive acá y no en cada app a propósito: el Actualizador y el Panel la
    necesitan igual, y tenerla copiada en los dos garantiza que en tres
    meses una mitad esté arreglada y la otra no.
    """
    casa = os.path.expanduser("~")
    candidatos = [
        os.path.join(casa, "Desktop"),
        os.path.join(casa, "Escritorio"),
        os.path.join(casa, "OneDrive", "Desktop"),
        os.path.join(casa, "OneDrive", "Escritorio"),
    ]
    entorno = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    if entorno:
        candidatos += [os.path.join(entorno, "Desktop"), os.path.join(entorno, "Escritorio")]
    for c in candidatos:
        if os.path.isdir(c):
            return c
    return casa
