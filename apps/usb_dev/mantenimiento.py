"""Herramienta de Mantenimiento - USB del Desarrollador.

Un único ejecutable para reparaciones urgentes en la PC del cliente:
1. Verifica integridad de la DB del sistema elegido y la repara con
   .dump/.restore si está corrupta (con backup previo).
2. Verifica que el servicio oculto de stock esté corriendo y lo reactiva.
3. Restaura config.ini desde una copia espejo guardada en este USB.
4. Comprime y limpia logs con más de 30 días.
5. Muestra un informe final en pantalla y lo guarda en el USB.
"""

import gzip
import os
import shutil
import sqlite3
import subprocess
import sys
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, scrolledtext

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pos_core.paths import get_base_path
from apps.theme import aplicar_tema

NOMBRE_SERVICIO_WINDOWS = "SistemaDualStockService"


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
        log.append(f"[DB] ERROR reparando la base: {e}. Se conserva el backup para diagnóstico manual.")


def verificar_servicio_windows(log: list) -> None:
    if os.name != "nt":
        log.append("[SERVICIO] Este paso solo aplica en Windows; se omite en este entorno.")
        return
    try:
        estado = subprocess.run(
            ["sc", "query", NOMBRE_SERVICIO_WINDOWS], capture_output=True, text=True, timeout=10
        )
        corriendo = "RUNNING" in estado.stdout
        log.append(f"[SERVICIO] Estado actual: {'corriendo' if corriendo else 'DETENIDO'}")
        if not corriendo:
            subprocess.run(["sc", "start", NOMBRE_SERVICIO_WINDOWS], capture_output=True, text=True, timeout=15)
            log.append("[SERVICIO] Se envió comando de reinicio ('sc start').")
    except Exception as e:
        log.append(f"[SERVICIO] ERROR verificando/reiniciando el servicio: {e}")


def restaurar_config(carpeta_instalacion: str, carpeta_usb_dev: str, log: list) -> None:
    espejo = os.path.join(carpeta_usb_dev, "config_espejo", "config.ini")
    destino = os.path.join(carpeta_instalacion, "config.ini")
    if not os.path.isfile(espejo):
        log.append(f"[CONFIG] No hay copia espejo en {espejo}; se omite este paso.")
        return
    shutil.copy2(espejo, destino)
    log.append(f"[CONFIG] config.ini restaurado desde la copia espejo del USB.")


def limpiar_logs_viejos(carpeta_instalacion: str, log: list, dias: int = 30) -> None:
    carpeta_logs = os.path.join(carpeta_instalacion, "logs")
    if not os.path.isdir(carpeta_logs):
        log.append("[LOGS] No hay carpeta de logs; se omite.")
        return
    limite = datetime.now() - timedelta(days=dias)
    comprimidos, eliminados = 0, 0
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


def ejecutar_mantenimiento(carpeta_instalacion: str) -> list:
    log = [f"=== Reporte de Mantenimiento — {_timestamp()} ===",
           f"Instalación objetivo: {carpeta_instalacion}"]
    carpeta_usb_dev = get_base_path()

    verificar_y_reparar_db(carpeta_instalacion, log)
    verificar_servicio_windows(log)
    restaurar_config(carpeta_instalacion, carpeta_usb_dev, log)
    limpiar_logs_viejos(carpeta_instalacion, log)

    log.append("=== Fin del mantenimiento ===")

    reporte_path = os.path.join(carpeta_usb_dev, "reporte_mantenimiento.txt")
    with open(reporte_path, "a", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n\n")
    log.append(f"(Reporte guardado también en: {reporte_path})")
    return log


class AppMantenimiento(tk.Tk):
    def __init__(self):
        super().__init__()
        aplicar_tema(self)
        self.title("Herramienta de Mantenimiento - USB Desarrollador")
        self.geometry("750x550")

        top = tk.Frame(self, padx=8, pady=8)
        top.pack(fill="x")
        tk.Label(top, text="Carpeta de instalación a reparar (Maestro o USB):").pack(side="left")
        self.carpeta_var = tk.StringVar()
        tk.Entry(top, textvariable=self.carpeta_var, width=50).pack(side="left", padx=6)
        tk.Button(top, text="Elegir...", command=self._elegir_carpeta).pack(side="left")

        tk.Button(self, text="EJECUTAR MANTENIMIENTO", bg="#27ae60", fg="white",
                  font=("", 12, "bold"), command=self._ejecutar).pack(fill="x", padx=8, pady=8)

        self.salida = scrolledtext.ScrolledText(self, height=25)
        self.salida.pack(fill="both", expand=True, padx=8, pady=8)

    def _elegir_carpeta(self):
        carpeta = filedialog.askdirectory()
        if carpeta:
            self.carpeta_var.set(carpeta)

    def _ejecutar(self):
        carpeta = self.carpeta_var.get().strip()
        if not carpeta or not os.path.isdir(carpeta):
            messagebox.showwarning("Falta la carpeta", "Elegí la carpeta de instalación a reparar.")
            return
        self.salida.delete("1.0", "end")
        try:
            log = ejecutar_mantenimiento(carpeta)
        except Exception as e:
            messagebox.showerror("Error inesperado", str(e))
            return
        self.salida.insert("end", "\n".join(log))


if __name__ == "__main__":
    AppMantenimiento().mainloop()
