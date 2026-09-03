"""Evita que la misma app quede abierta dos veces sobre la misma base.

El caso real: el cajero hace doble clic en el acceso directo, se abren
dos ventanas de la Caja iguales, escanea en una y mira la otra — y cree
que el sistema "perdió" los productos. Los datos nunca corren riesgo (la
base aguanta varios procesos a la vez, está probado), pero la confusión
sí es un problema.

Se resuelve con un archivo bloqueado por el sistema operativo. Lo
importante de que el candado lo lleve el SO y no un archivo con el PID
adentro: si la app se cierra mal (se corta la luz, la matan desde el
Administrador de tareas), el candado se libera solo. Un archivo "PID"
quedaría ahí para siempre y a la mañana siguiente la Caja no abriría.

Regla de oro: ante cualquier problema, DEJA ABRIR. Que se abran dos
ventanas es molesto; que la Caja no abra a las 8 de la mañana con gente
esperando es inaceptable.
"""

import os

# El archivo se deja abierto todo lo que dure el proceso: el candado vive
# mientras viva el descriptor. Se guarda acá para que el recolector de
# basura no lo cierre por su cuenta.
_abiertos = []


def _bloquear(descriptor) -> bool:
    """Intenta tomar el candado exclusivo sin esperar. True si lo tomó."""
    try:
        import msvcrt          # Windows
    except ImportError:
        import fcntl           # Linux/Mac (los tests corren acá)
        try:
            fcntl.flock(descriptor.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    try:
        msvcrt.locking(descriptor.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def tomar(nombre_app: str) -> bool:
    """True si esta es la única instancia; False si ya hay otra abierta.

    El candado es por app Y por carpeta de datos: la Caja del pendrive de
    emergencia y la Caja instalada en la PC son dos cosas distintas y
    tienen que poder convivir (de hecho es justo lo que pasa el día que
    se usa el USB).
    """
    try:
        from pos_core import paths
        ruta = os.path.join(paths.data_dir(), f".candado_{nombre_app}")
        descriptor = open(ruta, "a+b")
        # Windows bloquea a partir de la posición actual del archivo, así
        # que se deja explícita en 0: el archivo siempre está vacío, pero
        # "a+b" puede dejar el cursor al final según la plataforma.
        descriptor.seek(0)
        if not _bloquear(descriptor):
            descriptor.close()
            return False
        _abiertos.append(descriptor)
        return True
    except Exception:
        # Carpeta de solo lectura, sistema de archivos raro, lo que sea:
        # no es motivo para no dejar trabajar. Ver "regla de oro" arriba.
        return True
