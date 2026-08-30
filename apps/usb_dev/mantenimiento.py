"""Herramienta de Mantenimiento - USB del Desarrollador.

Un único ejecutable para reparaciones urgentes en la PC del cliente. El
flujo principal es "REPARAR TODO": detecta solo, sin que nadie tenga que
elegir carpetas, todas las instalaciones de Otter presentes en la PC y en
las unidades conectadas (Maestro, USB Caja, USB Dueño), y en cada una:

1. Verifica espacio libre en disco.
2. Verifica integridad de la base y la repara con .dump/.restore si está
   corrupta (con backup previo).
3. Pone al día la ESTRUCTURA de la base (columnas/tablas nuevas que una
   versión vieja del programa no tenía) sin perder los datos existentes.
4. Corrige inconsistencias de datos que se puedan arreglar solas (por
   ejemplo, stock que quedó en negativo), dejando registro de auditoría.
5. Verifica que el servicio oculto de stock esté corriendo (Maestro) y lo
   reinstala si hace falta.
6. Restaura config.ini desde una copia espejo guardada en este USB.
7. Comprime y archiva logs viejos.
8. Repone archivos del programa dañados/faltantes (ejecutables, DLLs)
   comparándolos contra la copia de referencia que este mismo USB lleva
   (generada por build_all.bat a partir del último build compilado) —
   esto es lo que permite que un bug de código ya corregido en una
   versión más nueva también se resuelva con solo conectar este USB,
   siempre que se lo haya reconstruido después de aplicar el arreglo.

Ninguno de estos pasos toca nunca database/, config.ini, logs/ ni
SYNC_DATA/ salvo para repararlos explícitamente: son datos del cliente,
no del programa. Todo lo que se hace (o lo que no se pudo resolver solo)
queda registrado en el informe en pantalla y en reporte_mantenimiento.txt.
"""

import glob
import gzip
import os
import re
import shutil
import sqlite3
import string
import subprocess
import sys
import tkinter as tk
import uuid
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, scrolledtext, ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pos_core.paths import get_base_path
from apps.theme import aplicar_tema

NOMBRE_SERVICIO_WINDOWS = "SistemaDualStockService"
LIMITE_REPORTE_BYTES = 2 * 1024 * 1024  # 2 MB
ESPACIO_MINIMO_MB = 500

# subcarpeta relativa a la instalación -> nombre de la copia de referencia
# dentro de espejo_apps/ que le corresponde. "" = el espejo va directo en
# la raíz de la instalación (caso de los USB de emergencia).
_MAPA_ESPEJO = {
    "MAESTRO": [("MaestroCaja", "MaestroCaja"), ("MaestroDueno", "MaestroDueno"),
                ("StockService", "StockService")],
    "USB_CAJA": [("", "USB_Caja")],
    "USB_DUENO": [("", "USB_Dueno")],
}
_EXCLUIR_DE_REPARACION_ARCHIVOS = {"database", "sync_data", "logs", "config.ini",
                                    "reporte_mantenimiento.txt", "sincronizacion_exitosa.txt"}


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------- #
# Detección automática de instalaciones
# ---------------------------------------------------------------------- #
def detectar_instalaciones() -> list:
    """Encuentra las instalaciones de Otter en esta PC y en las unidades
    conectadas, para no depender de que alguien elija la carpeta correcta
    a mano. Devuelve una lista de {"tipo", "etiqueta", "carpeta"}."""
    encontradas = []

    if os.name == "nt":
        if os.path.isdir(r"C:\SistemaDual"):
            encontradas.append({"tipo": "MAESTRO", "etiqueta": r"Maestro (C:\SistemaDual)",
                                 "carpeta": r"C:\SistemaDual"})
        for letra in string.ascii_uppercase:
            raiz = f"{letra}:\\"
            if not os.path.isdir(raiz):
                continue
            if os.path.isfile(os.path.join(raiz, "USB_Caja.exe")):
                encontradas.append({"tipo": "USB_CAJA", "etiqueta": f"USB Caja ({letra}:)", "carpeta": raiz})
            if os.path.isfile(os.path.join(raiz, "USB_Dueno.exe")):
                encontradas.append({"tipo": "USB_DUENO", "etiqueta": f"USB Dueño ({letra}:)", "carpeta": raiz})
    else:
        # Linux/Mac (desarrollo/testing): buscar bajo /media, /mnt y el home.
        for base in ("/media", "/mnt", os.path.expanduser("~")):
            for patron, tipo, nombre in (("USB_Caja.exe", "USB_CAJA", "USB Caja"),
                                          ("USB_Dueno.exe", "USB_DUENO", "USB Dueño")):
                for ruta in glob.glob(os.path.join(base, "**", patron), recursive=True):
                    carpeta = os.path.dirname(ruta) + os.sep
                    encontradas.append({"tipo": tipo, "etiqueta": f"{nombre} ({carpeta})", "carpeta": carpeta})

    return encontradas


# ---------------------------------------------------------------------- #
# Base de datos: integridad, estructura, datos
# ---------------------------------------------------------------------- #
def verificar_y_reparar_db(carpeta_instalacion: str, log: list) -> None:
    db_path = os.path.join(carpeta_instalacion, "database", "stock.db")
    if not os.path.isfile(db_path):
        log.append(f"[DB] No se encontró {db_path}, se omite este paso.")
        return

    conn = sqlite3.connect(db_path)
    try:
        resultado = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()

    if resultado == "ok":
        log.append(f"[DB] Integridad OK ({db_path}).")
        return

    log.append(f"[DB] ¡CORRUPCIÓN DETECTADA! integrity_check devolvió: {resultado}")
    backup_path = db_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(db_path, backup_path)
    log.append(f"[DB] Backup de seguridad guardado en: {backup_path}")

    try:
        conn = sqlite3.connect(db_path)
        dump_lines = list(conn.iterdump())
        conn.close()

        db_reparada = db_path + ".reparada"
        if os.path.exists(db_reparada):
            os.remove(db_reparada)
        conn_nueva = sqlite3.connect(db_reparada)
        conn_nueva.executescript("\n".join(dump_lines))
        conn_nueva.close()

        os.replace(db_reparada, db_path)
        log.append("[DB] Reparación por .dump/.restore aplicada con éxito.")
    except Exception as e:
        log.append(f"[DB] ERROR reparando la base con .dump/.restore: {e}")
        _restaurar_backup_mas_reciente(db_path, carpeta_instalacion, log)


def _restaurar_backup_mas_reciente(db_path: str, carpeta_instalacion: str, log: list) -> None:
    """Último recurso si ni siquiera .dump/.restore pudo reparar la base:
    buscar el backup .backup_* más reciente (de esta corrida o de una
    anterior) y restaurarlo, para no dejar la caja sin base usable."""
    carpeta_db = os.path.dirname(db_path)
    candidatos = sorted(glob.glob(db_path + ".backup_*"), reverse=True)
    if not candidatos:
        log.append("[DB] No hay ningún backup previo para restaurar automáticamente. "
                    "Se conserva la base dañada para diagnóstico manual — no se pierde nada, "
                    "pero hace falta intervención manual.")
        return
    mas_reciente = candidatos[0]
    shutil.copy2(mas_reciente, db_path)
    log.append(f"[DB] Se restauró el backup más reciente disponible: {mas_reciente}. "
               f"Puede faltar lo que se vendió entre ese backup y ahora — revisar con el cliente.")


def aplicar_migraciones_esquema(db_path: str, log: list) -> None:
    if not os.path.isfile(db_path):
        return
    try:
        from pos_core.db import aplicar_migraciones
        cambios = aplicar_migraciones(db_path)
    except Exception as e:
        log.append(f"[ESQUEMA] Error aplicando migraciones de estructura: {e}")
        return
    if cambios:
        log.append(f"[ESQUEMA] Se actualizó la estructura de la base ({len(cambios)} cambio(s)):")
        for c in cambios:
            log.append(f"    - {c}")
    else:
        log.append("[ESQUEMA] La estructura de la base ya está al día.")


def corregir_datos_invalidos(db_path: str, log: list) -> None:
    """Corrige inconsistencias de datos que son seguras de arreglar solas
    (hoy: stock negativo, que no debería poder pasar pero puede quedar así
    tras una conciliación o corte de luz en mal momento). Deja rastro en
    Movimientos_Stock para que quede auditado, no se corrige en silencio."""
    if not os.path.isfile(db_path):
        return
    conn = sqlite3.connect(db_path)
    try:
        negativos = conn.execute("SELECT codigo, stock FROM Productos WHERE stock < 0").fetchall()
        if not negativos:
            log.append("[DATOS] Sin inconsistencias de stock negativo.")
            return
        ahora = datetime.now().isoformat(timespec="milliseconds")
        for codigo, stock_anterior in negativos:
            conn.execute("UPDATE Productos SET stock = 0, version = version + 1 WHERE codigo = ?", (codigo,))
            conn.execute(
                """INSERT INTO Movimientos_Stock
                   (uuid_unico, producto_codigo, tipo, cantidad, stock_resultante,
                    motivo, usuario, origen, fecha_hora, sincronizado)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), codigo, "AJUSTE_BULK", abs(stock_anterior), 0,
                 "Corrección automática (USB Mantenimiento): stock negativo detectado",
                 "mantenimiento_usb", "MAESTRO", ahora, 1))
        conn.commit()
        log.append(f"[DATOS] Se corrigieron {len(negativos)} producto(s) con stock negativo (llevados a 0, "
                    f"con su movimiento de ajuste registrado para auditoría).")
    except sqlite3.OperationalError as e:
        log.append(f"[DATOS] No se pudo revisar consistencia de datos: {e}")
    finally:
        conn.close()


# ---------------------------------------------------------------------- #
# Espacio en disco, servicio de Windows, config, logs
# ---------------------------------------------------------------------- #
def verificar_espacio_disco(carpeta_instalacion: str, log: list, minimo_mb: int = ESPACIO_MINIMO_MB) -> None:
    try:
        uso = shutil.disk_usage(carpeta_instalacion)
    except Exception as e:
        log.append(f"[DISCO] No se pudo verificar espacio libre: {e}")
        return
    libres_mb = uso.free / (1024 * 1024)
    if libres_mb < minimo_mb:
        log.append(f"[DISCO] ¡ATENCIÓN! Solo quedan {libres_mb:.0f} MB libres (mínimo recomendado: "
                    f"{minimo_mb} MB). Liberá espacio: con el disco lleno la base de datos puede "
                    f"corromperse al escribir.")
    else:
        log.append(f"[DISCO] Espacio libre OK: {libres_mb:.0f} MB.")


def verificar_servicio_windows(carpeta_instalacion: str, log: list) -> None:
    if os.name != "nt":
        log.append("[SERVICIO] Este paso solo aplica en Windows; se omite en este entorno.")
        return
    try:
        estado = subprocess.run(["sc", "query", NOMBRE_SERVICIO_WINDOWS],
                                 capture_output=True, text=True, timeout=10)
        if "RUNNING" in estado.stdout:
            log.append("[SERVICIO] Estado actual: corriendo. OK.")
            return
        if "STOPPED" in estado.stdout:
            log.append("[SERVICIO] Estado actual: DETENIDO. Reiniciando...")
            subprocess.run(["sc", "start", NOMBRE_SERVICIO_WINDOWS], capture_output=True, text=True, timeout=15)
            log.append("[SERVICIO] Se envió comando de reinicio ('sc start').")
            return
        exe_servicio = os.path.join(carpeta_instalacion, "StockService", "StockService.exe")
        if os.path.isfile(exe_servicio):
            log.append("[SERVICIO] No está registrado en Windows. Instalándolo desde cero...")
            subprocess.run([exe_servicio, "install"], capture_output=True, text=True, timeout=30)
            subprocess.run([exe_servicio, "start"], capture_output=True, text=True, timeout=15)
            log.append("[SERVICIO] Instalado y arrancado.")
        else:
            log.append("[SERVICIO] No está registrado y no se encontró StockService.exe en esta "
                        "instalación; se omite (esperable si esto no es el Maestro).")
    except Exception as e:
        log.append(f"[SERVICIO] ERROR verificando/reiniciando el servicio: {e}")


def restaurar_config(carpeta_instalacion: str, carpeta_usb_dev: str, log: list) -> None:
    espejo = os.path.join(carpeta_usb_dev, "config_espejo", "config.ini")
    destino = os.path.join(carpeta_instalacion, "config.ini")
    if not os.path.isfile(espejo):
        log.append(f"[CONFIG] No hay copia espejo en {espejo}; se omite este paso.")
        return
    if os.path.isfile(destino):
        log.append("[CONFIG] config.ini ya existe; se deja como está (no se pisa configuración real "
                    "por una genérica).")
        return
    shutil.copy2(espejo, destino)
    log.append("[CONFIG] config.ini faltante, restaurado desde la copia espejo del USB.")


def limpiar_logs_viejos(carpeta_instalacion: str, log: list, dias: int = 30) -> None:
    carpeta_logs = os.path.join(carpeta_instalacion, "logs")
    if not os.path.isdir(carpeta_logs):
        log.append("[LOGS] No hay carpeta de logs; se omite.")
        return
    limite = datetime.now() - timedelta(days=dias)
    comprimidos = 0
    for nombre in os.listdir(carpeta_logs):
        ruta = os.path.join(carpeta_logs, nombre)
        if not os.path.isfile(ruta) or nombre.endswith(".gz"):
            continue
        mtime = datetime.fromtimestamp(os.path.getmtime(ruta))
        if mtime < limite:
            with open(ruta, "rb") as f_in, gzip.open(ruta + ".gz", "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.remove(ruta)
            comprimidos += 1
    log.append(f"[LOGS] Comprimidos y archivados {comprimidos} logs de más de {dias} días.")


# ---------------------------------------------------------------------- #
# Reparación de archivos del programa (ejecutables/DLLs dañados o
# faltantes) contra la copia de referencia que lleva este mismo USB.
# ---------------------------------------------------------------------- #
def reparar_archivos_app(carpeta_instalacion: str, tipo_instalacion: str,
                          carpeta_usb_dev: str, log: list) -> None:
    espejo_base = os.path.join(carpeta_usb_dev, "espejo_apps")
    if not os.path.isdir(espejo_base):
        log.append("[ARCHIVOS] Este USB no tiene copias de referencia (espejo_apps); se omite este paso. "
                    "Reconstruilo con build\\build_all.bat para poder usar esta función.")
        return

    mapa = _MAPA_ESPEJO.get(tipo_instalacion, [])
    total_repuestos = 0
    for subcarpeta_relativa, nombre_espejo in mapa:
        origen = os.path.join(espejo_base, nombre_espejo)
        if not os.path.isdir(origen):
            continue
        destino = os.path.join(carpeta_instalacion, subcarpeta_relativa) if subcarpeta_relativa \
            else carpeta_instalacion

        for raiz, carpetas, archivos in os.walk(origen):
            carpetas[:] = [c for c in carpetas if c.lower() not in _EXCLUIR_DE_REPARACION_ARCHIVOS]
            rel = os.path.relpath(raiz, origen)
            for nombre_archivo in archivos:
                origen_archivo = os.path.join(raiz, nombre_archivo)
                destino_dir = os.path.join(destino, rel) if rel != "." else destino
                destino_archivo = os.path.join(destino_dir, nombre_archivo)

                if not os.path.isfile(destino_archivo) or \
                        os.path.getsize(destino_archivo) != os.path.getsize(origen_archivo):
                    os.makedirs(destino_dir, exist_ok=True)
                    shutil.copy2(origen_archivo, destino_archivo)
                    total_repuestos += 1

    if total_repuestos:
        log.append(f"[ARCHIVOS] Se repusieron {total_repuestos} archivo(s) del programa faltante(s) o "
                    f"dañado(s) (tamaño distinto al de la versión de referencia), incluyendo cualquier "
                    f"corrección de código ya aplicada en el último build de este USB.")
    else:
        log.append("[ARCHIVOS] Todos los archivos del programa coinciden con la versión de referencia.")


# ---------------------------------------------------------------------- #
# Orquestación
# ---------------------------------------------------------------------- #
def _rotar_reporte_si_hace_falta(reporte_path: str) -> None:
    if os.path.isfile(reporte_path) and os.path.getsize(reporte_path) > LIMITE_REPORTE_BYTES:
        archivado = reporte_path + f".{datetime.now().strftime('%Y%m%d_%H%M%S')}.viejo"
        os.replace(reporte_path, archivado)


def ejecutar_mantenimiento(carpeta_instalacion: str, tipo_instalacion: str = "MAESTRO") -> list:
    log = [f"=== Reporte de Mantenimiento — {_timestamp()} ===",
           f"Instalación objetivo: {carpeta_instalacion} ({tipo_instalacion})"]
    carpeta_usb_dev = get_base_path()
    db_path = os.path.join(carpeta_instalacion, "database", "stock.db")

    verificar_espacio_disco(carpeta_instalacion, log)
    verificar_y_reparar_db(carpeta_instalacion, log)
    aplicar_migraciones_esquema(db_path, log)
    corregir_datos_invalidos(db_path, log)
    if tipo_instalacion == "MAESTRO":
        verificar_servicio_windows(carpeta_instalacion, log)
    restaurar_config(carpeta_instalacion, carpeta_usb_dev, log)
    limpiar_logs_viejos(carpeta_instalacion, log)
    reparar_archivos_app(carpeta_instalacion, tipo_instalacion, carpeta_usb_dev, log)

    log.append("=== Fin del mantenimiento ===")

    reporte_path = os.path.join(carpeta_usb_dev, "reporte_mantenimiento.txt")
    _rotar_reporte_si_hace_falta(reporte_path)
    with open(reporte_path, "a", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n\n")
    log.append(f"(Reporte guardado también en: {reporte_path})")
    return log


def ejecutar_reparacion_automatica_completa() -> list:
    """Detecta todas las instalaciones de Otter en esta PC/unidades
    conectadas y corre el mantenimiento completo en cada una — el flujo
    pensado para "conectar el USB y que se solucione solo", con todo lo
    que se hizo (o lo que no se pudo resolver) registrado en el informe."""
    log_general = [f"=== REPARACIÓN AUTOMÁTICA COMPLETA — {_timestamp()} ==="]
    instalaciones = detectar_instalaciones()
    if not instalaciones:
        log_general.append("No se detectó ninguna instalación de Otter en esta PC ni en las unidades "
                            "conectadas. Si está en una carpeta no estándar, usá 'Reparar una carpeta "
                            "específica' más abajo.")
        return log_general

    log_general.append(f"Instalaciones detectadas: {len(instalaciones)}")
    for inst in instalaciones:
        log_general.append(f"  - {inst['etiqueta']}")
    log_general.append("")

    for inst in instalaciones:
        log_general.append(f"--- {inst['etiqueta']} ---")
        try:
            log_general.extend(ejecutar_mantenimiento(inst["carpeta"], inst["tipo"]))
        except Exception as e:
            log_general.append(f"ERROR inesperado reparando {inst['etiqueta']}: {e}")
        log_general.append("")

    log_general.append("=== FIN DE LA REPARACIÓN AUTOMÁTICA COMPLETA ===")
    return log_general


class AppMantenimiento(tk.Tk):
    def __init__(self):
        super().__init__()
        aplicar_tema(self)
        self.title("Otter Mantenimiento")
        self.geometry("860x680")

        tk.Button(self, text="🔧  REPARAR TODO AUTOMÁTICAMENTE  (detecta todo solo)",
                  bg="#27ae60", fg="white", font=("", 13, "bold"),
                  command=self._reparar_todo).pack(fill="x", padx=8, pady=(10, 6))

        avanzado = ttk.LabelFrame(self, text="Avanzado: reparar una única carpeta a mano", padding=8)
        avanzado.pack(fill="x", padx=8, pady=4)
        self.carpeta_var = tk.StringVar()
        tk.Entry(avanzado, textvariable=self.carpeta_var, width=50).pack(side="left", padx=(0, 6))
        tk.Button(avanzado, text="Elegir...", command=self._elegir_carpeta).pack(side="left")
        tk.Button(avanzado, text="Reparar esta carpeta", command=self._ejecutar).pack(side="left", padx=6)

        self.salida = scrolledtext.ScrolledText(self, height=30)
        self.salida.pack(fill="both", expand=True, padx=8, pady=8)

    def _elegir_carpeta(self):
        carpeta = filedialog.askdirectory()
        if carpeta:
            self.carpeta_var.set(carpeta)

    def _reparar_todo(self):
        self.salida.delete("1.0", "end")
        self.salida.insert("end", "Detectando instalaciones y reparando, un momento...\n")
        self.update()
        try:
            log = ejecutar_reparacion_automatica_completa()
        except Exception as e:
            messagebox.showerror("Error inesperado", str(e))
            return
        self.salida.delete("1.0", "end")
        self.salida.insert("end", "\n".join(log))

    def _ejecutar(self):
        carpeta = self.carpeta_var.get().strip()
        if not carpeta or not os.path.isdir(carpeta):
            messagebox.showwarning("Falta la carpeta", "Elegí la carpeta de instalación a reparar.")
            return
        self.salida.delete("1.0", "end")
        try:
            log = ejecutar_mantenimiento(carpeta, "MAESTRO")
        except Exception as e:
            messagebox.showerror("Error inesperado", str(e))
            return
        self.salida.insert("end", "\n".join(log))


if __name__ == "__main__":
    AppMantenimiento().mainloop()
