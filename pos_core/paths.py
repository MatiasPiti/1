"""Resolución de rutas portables.

Regla de oro: NUNCA usar rutas absolutas hardcodeadas (C:\\..., D:\\...).
Toda la app se ubica respecto de la carpeta donde vive el ejecutable (o el
script .py en desarrollo), sin importar si el USB quedó montado en E:, F:
o G:. Esto es lo que permite que el mismo USB funcione en cualquier PC.
"""

import os
import sys


def get_base_path() -> str:
    """Devuelve la carpeta base de la aplicación en ejecución.

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


def config_path() -> str:
    return os.path.join(get_base_path(), "config.ini")
