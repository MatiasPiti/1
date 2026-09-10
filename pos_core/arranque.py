"""Que una app que no puede abrir DIGA por qué, y si puede, se arregle.

El problema real que resuelve, verificado: los ejecutables se compilan con
`--windowed`, o sea sin consola. Si algo falla antes de que exista la
ventana —la base dañada es el caso típico— el proceso muere imprimiendo un
traceback que no va a ninguna parte. El cajero hace doble clic a las 8 de
la mañana, no pasa absolutamente nada, y no hay ni un cartel ni un archivo
que explique qué mirar. Con gente esperando, eso es el negocio parado.

Acá se hacen tres cosas, en orden:

1. Se atrapa cualquier error de arranque y se muestra en un cartel que se
   entiende, con el paso siguiente escrito.
2. Si el error es que la base no se puede leer y HAY una copia diaria
   (ver pos_core/respaldo.py), se ofrece restaurarla ahí mismo. Una
   persona decide, viendo la fecha de la copia y qué se perdería: eso es
   la diferencia entre "abrimos en 10 segundos" y "el negocio está parado
   hasta que venga Matías".
3. Pase lo que pase, queda escrito en logs/arranque.log, para que el USB
   de Mantenimiento y el propio Matías puedan ver después qué pasó.

Regla 6 del proyecto: ante la duda, la caja abre. Cuando no puede abrir,
que al menos explique por qué y ofrezca la salida.
"""

import os
import shutil
import time
import traceback
from datetime import datetime


def _registrar(nombre_app: str, error: BaseException) -> str:
    """Deja el error en logs/arranque.log. Devuelve la ruta, o "" si ni
    eso se pudo (disco lleno, sin permisos)."""
    try:
        from pos_core.paths import logs_dir
        ruta = os.path.join(logs_dir(), "arranque.log")
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} — {nombre_app} ===\n")
            f.write("".join(traceback.format_exception(type(error), error, error.__traceback__)))
        return ruta
    except Exception:
        return ""


def _parece_base_ilegible(error: BaseException) -> bool:
    """¿El error es 'no puedo leer la base' y no otra cosa?

    Se mira el texto y no solo el tipo porque sqlite3 usa DatabaseError
    para varias cosas distintas, y acá solo interesa el caso en que
    restaurar una copia tiene sentido.
    """
    texto = str(error).lower()
    señales = ("not a database", "file is encrypted", "database disk image is malformed",
               "database is locked", "unable to open database", "no such table")
    return any(s in texto for s in señales)


def _dialogo(titulo: str, mensaje: str, preguntar: bool = False) -> bool:
    """Cartel con Tk propio. Devuelve True si se apretó 'Sí' (o si solo
    era un aviso)."""
    import tkinter as tk
    from tkinter import messagebox
    raiz = tk.Tk()
    raiz.withdraw()
    raiz.attributes("-topmost", True)
    try:
        if preguntar:
            return bool(messagebox.askyesno(titulo, mensaje, parent=raiz))
        messagebox.showerror(titulo, mensaje, parent=raiz)
        return True
    finally:
        try:
            raiz.destroy()
        except Exception:
            pass


def _copiar_encima(origen: str, destino: str, intentos: int = 5) -> None:
    """Escribe `origen` ENCIMA de `destino`, reintentando unas veces.

    `copyfile` abre el destino en modo escritura en vez de renombrarlo, que
    es lo único que Windows deja hacer sobre un archivo que alguien todavía
    tiene abierto. Los reintentos son para el caso en que un antivirus o el
    indexador de Windows lo estén mirando justo en ese instante: son
    bloqueos de fracciones de segundo, pero acá caerse significa que el
    negocio no abre.
    """
    for intento in range(intentos):
        try:
            shutil.copyfile(origen, destino)
            return
        except OSError:
            if intento == intentos - 1:
                raise
            time.sleep(0.4)


def _ofrecer_restaurar_copia(nombre_app: str) -> bool:
    """Si hay copias diarias, ofrece volver a la más reciente.

    Devuelve True si se restauró y conviene reintentar el arranque.
    """
    try:
        from pos_core import respaldo
        from pos_core.paths import db_path
        copias = respaldo.listar_copias()
    except Exception:
        return False

    if not copias:
        return False

    ultima = copias[0]
    restaurar = _dialogo(
        f"{nombre_app}: la base de datos está dañada",
        "La base de datos no se puede leer.\n\n"
        f"Hay una copia automática del {ultima['fecha']} ({ultima['mb']} MB).\n\n"
        "¿Querés restaurarla y abrir el sistema ahora?\n\n"
        "IMPORTANTE: se pierde lo que se haya cargado DESPUÉS de esa copia\n"
        "(ventas, cambios de precio, movimientos de stock). La base dañada\n"
        "no se borra: queda guardada al lado, por si se puede recuperar algo.\n\n"
        "Si preferís no arriesgar nada, elegí No y llamá a Matías.",
        preguntar=True)

    if not restaurar:
        return False

    try:
        from pos_core.db import cerrar_conexion
        # Sin esto, en Windows el archivo sigue abierto por el intento de
        # arranque fallido y no se puede tocar.
        cerrar_conexion()

        destino = db_path()
        sello = datetime.now().strftime("%Y%m%d_%H%M%S")
        aviso_extra = ""

        # La base dañada se COPIA a un lado, no se mueve. En Windows, mover
        # o renombrar un archivo que todavía tenga un handle abierto falla
        # con "el archivo está en uso" — y falla de verdad: se comprobó
        # corriendo las pruebas en Windows real, donde la restauración
        # entera abortaba por esto y la caja se quedaba sin abrir. Copiar
        # funciona igual con el archivo abierto, y sobreescribir después el
        # original también (se abre en modo escritura, no se renombra).
        if os.path.exists(destino):
            try:
                shutil.copy2(destino, f"{destino}.danada_{sello}")
            except Exception as e:
                # Que no se pueda guardar la dañada NO puede impedir
                # restaurar: sin restaurar el negocio no abre, que es peor.
                # Se avisa para que quede claro qué se perdió.
                aviso_extra = ("\n\nOJO: no se pudo guardar una copia de la base dañada "
                               f"({e}), así que lo que hubiera adentro se pierde.")

        # Los sidecars pertenecen a la base ANTERIOR: si quedan, SQLite
        # intenta reproducirlos sobre la copia restaurada y la arruina.
        for sufijo in ("-wal", "-shm"):
            sidecar = destino + sufijo
            try:
                if os.path.exists(sidecar):
                    os.remove(sidecar)
            except OSError:
                pass

        _copiar_encima(ultima["ruta"], destino)

        _dialogo(nombre_app,
                 f"Listo: se restauró la copia del {ultima['fecha']}.\n\n"
                 "El sistema va a abrir ahora. Avisale a Matías igual, para\n"
                 "revisar qué pasó y recuperar lo que falte." + aviso_extra)
        return True
    except Exception as e:
        _dialogo(nombre_app,
                 "No se pudo restaurar la copia.\n\n"
                 f"Detalle: {e}\n\n"
                 "Llamá a Matías y no toques nada más.")
        return False


def iniciar(nombre_app: str, preparar, construir_ventana) -> None:
    """Arranca la app avisando si no puede.

    `preparar` es todo lo que hay que hacer antes de que exista la ventana
    (la base, el candado de instancia única); `construir_ventana` devuelve
    la ventana ya lista, para hacerle mainloop().
    """
    try:
        preparar()
    except BaseException as error:      # incluye SystemExit y KeyboardInterrupt
        if isinstance(error, SystemExit):
            raise
        ruta_log = _registrar(nombre_app, error)

        if _parece_base_ilegible(error) and _ofrecer_restaurar_copia(nombre_app):
            try:
                preparar()
            except BaseException as error2:
                if isinstance(error2, SystemExit):
                    raise
                _registrar(nombre_app, error2)
                _dialogo(nombre_app,
                         "Se restauró la copia pero el sistema sigue sin poder abrir.\n\n"
                         f"Detalle: {error2}\n\n"
                         "Llamá a Matías. Esto no se arregla desde acá.")
                raise SystemExit(1)
        else:
            _dialogo(
                f"{nombre_app} no pudo abrir",
                f"{error}\n\n"
                "Qué hacer:\n"
                "1. Anotá o sacale una foto a este cartel.\n"
                "2. Probá conectar el USB de Mantenimiento y usar\n"
                "   'REPARAR TODO AUTOMÁTICAMENTE'.\n"
                "3. Si sigue igual, llamá a Matías.\n\n"
                + (f"El detalle técnico quedó en:\n{ruta_log}" if ruta_log else ""))
            raise SystemExit(1)

    ventana = construir_ventana()
    if ventana is not None:
        ventana.mainloop()
