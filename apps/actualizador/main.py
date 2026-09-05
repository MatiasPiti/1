"""Actualizador de Otter — pone al día una instalación que ya funciona.

Es lo que se corre en el local cuando hay una versión nueva, en vez de
reinstalar todo: encuentra sola la instalación existente, hace una copia
de seguridad de la base, reemplaza los programas, pone la base al día si
el esquema cambió, y vuelve a dejar el servicio corriendo.

Lo que NUNCA toca:
  - La base de datos con las ventas y el stock (solo le agrega columnas
    nuevas si hicieran falta, algo que no borra ni cambia ningún dato).
  - El config.ini: ahí viven el token del Dueño Remoto, el CUIT y el
    certificado de ARCA, y el bot de Telegram. Pisarlo obligaría a
    reconfigurar todo y a reinstalar el Dueño Remoto en la otra PC.

Sirve para las dos instalaciones y se da cuenta solo de cuál es:
  - PC del local  -> MaestroCaja + MaestroDueno + StockService
  - Laptop del dueño -> DuenoRemoto
"""

import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from apps.theme import COLORS, aplicar_tema, habilitar_copiar_pegar_global, ajustar_ventana

NOMBRE_SERVICIO = "SistemaDualStockService"

# Dónde suele estar instalado, para no hacer buscar la carpeta a mano.
CANDIDATOS_LOCAL = [r"C:\SistemaDual", r"C:\Otter", r"C:\Program Files\SistemaDual"]
CANDIDATOS_REMOTO = [r"C:\Otter", r"C:\DuenoRemoto"]

APPS_LOCAL = ["MaestroCaja", "MaestroDueno", "StockService"]
APPS_REMOTO = ["DuenoRemoto"]


def es_administrador() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def buscar_origen() -> str:
    """Carpeta con las apps NUEVAS ya compiladas (al lado del actualizador)."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for candidato in (base, os.path.dirname(base), os.path.join(base, "dist"),
                      os.path.join(os.path.dirname(base), "dist")):
        if os.path.isdir(os.path.join(candidato, "MaestroCaja")) or \
                os.path.isdir(os.path.join(candidato, "DuenoRemoto")):
            return candidato
    return base


def detectar_instalacion() -> tuple:
    """Encuentra la instalación existente. Devuelve (carpeta, modo)."""
    for carpeta in CANDIDATOS_LOCAL:
        if os.path.isdir(os.path.join(carpeta, "MaestroCaja")):
            return carpeta, "local"
    for carpeta in CANDIDATOS_REMOTO:
        if os.path.isdir(os.path.join(carpeta, "DuenoRemoto")):
            return carpeta, "remoto"
    return "", ""


class Actualizador(tk.Tk):

    def __init__(self):
        super().__init__()
        aplicar_tema(self)
        self.title("Actualizador de Otter")
        ajustar_ventana(self, 760, 620, minimo=(620, 460))
        self.origen = buscar_origen()
        detectado, modo = detectar_instalacion()
        self.modo_detectado = modo
        # Todo lo que quiera mostrar el hilo de actualización pasa por acá:
        # Tk no es seguro entre hilos (ver apps/instalador/main.py).
        self._cola = queue.Queue()

        self._armar_ui()
        self._bombear()
        habilitar_copiar_pegar_global(self)

        if detectado:
            self.destino.delete(0, "end")
            self.destino.insert(0, detectado)
            cual = "la PC del local" if modo == "local" else "la laptop del dueño"
            self._log(f"Se encontró una instalación de {cual} en:\n   {detectado}\n")
        else:
            self._log("No se encontró una instalación automáticamente.\n"
                       "Elegí a mano la carpeta donde está instalado Otter (la que tiene\n"
                       "adentro las carpetas MaestroCaja / DuenoRemoto).\n")
        self._log(f"Programas nuevos encontrados en:\n   {self.origen}\n")
        if not es_administrador():
            self._log("AVISO: no estás como administrador. Todo se actualiza igual, pero el\n"
                       "servicio de stock no se va a poder reiniciar solo. Si esta es la PC\n"
                       "del local, conviene cerrar y abrir con botón derecho ->\n"
                       "'Ejecutar como administrador'.\n")

    def _armar_ui(self):
        ttk.Label(self, text="Actualizador de Otter", style="Header.TLabel"
                  ).pack(anchor="w", padx=18, pady=(16, 2))
        ttk.Label(self, text="Actualiza una instalación que ya funciona. No toca las ventas, "
                              "el stock ni la configuración.", style="Muted.TLabel",
                  wraplength=700, justify="left").pack(anchor="w", padx=18)

        marco = ttk.Frame(self, padding=(18, 14))
        marco.pack(fill="x")
        ttk.Label(marco, text="Carpeta instalada:").grid(row=0, column=0, sticky="w")
        self.destino = ttk.Entry(marco, width=52)
        self.destino.grid(row=0, column=1, padx=8, sticky="we")
        ttk.Button(marco, text="Buscar...", command=self._elegir_carpeta).grid(row=0, column=2)
        marco.grid_columnconfigure(1, weight=1)

        self.var_backup = tk.BooleanVar(value=True)
        ttk.Checkbutton(marco, text="Hacer copia de seguridad de la base antes de actualizar "
                                     "(recomendado)", variable=self.var_backup
                        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))

        self.boton = ttk.Button(self, text="ACTUALIZAR", style="Accent.TButton",
                                 command=self._actualizar)
        self.boton.pack(anchor="w", padx=18, pady=(6, 12))

        self.texto = tk.Text(self, height=16, bg="#FFFFFF", relief="flat",
                              padx=8, pady=8, wrap="word")
        self.texto.pack(fill="both", expand=True, padx=18, pady=(0, 16))

    def _elegir_carpeta(self):
        carpeta = filedialog.askdirectory(title="Carpeta donde está instalado Otter")
        if carpeta:
            self.destino.delete(0, "end")
            self.destino.insert(0, carpeta.replace("/", os.sep))

    # ------------------------------------------------------------------ #
    def _log(self, texto):
        """Se puede llamar desde cualquier hilo: solo encola."""
        self._cola.put(texto)

    def _bombear(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                pendiente = self._cola.get_nowait()
                if callable(pendiente):
                    try:
                        pendiente()
                    except Exception:
                        pass
                else:
                    self.texto.insert("end", str(pendiente) + "\n")
                    self.texto.see("end")
        except queue.Empty:
            pass
        self.after(100, self._bombear)

    # ------------------------------------------------------------------ #
    def _actualizar(self):
        destino = self.destino.get().strip()
        if not destino or not os.path.isdir(destino):
            messagebox.showerror("Falta la carpeta",
                                  "Elegí la carpeta donde está instalado Otter.")
            return
        self.boton.config(state="disabled")
        self.texto.delete("1.0", "end")
        datos = {"destino": destino, "backup": bool(self.var_backup.get())}
        threading.Thread(target=self._actualizar_en_hilo, args=(datos,), daemon=True).start()

    def _actualizar_en_hilo(self, datos):
        try:
            self._hacer_actualizacion(datos)
        except Exception as e:
            self._log(f"\n*** LA ACTUALIZACIÓN SE DETUVO ***\n{type(e).__name__}: {e}\n"
                       f"La instalación anterior sigue funcionando: no se borró nada.")
            mensaje = f"{type(e).__name__}: {e}"
            self._cola.put(lambda: messagebox.showerror("Error al actualizar", mensaje))
        finally:
            self._cola.put(lambda: self.boton.config(state="normal"))

    def _hacer_actualizacion(self, datos):
        destino = datos["destino"]
        modo = "local" if os.path.isdir(os.path.join(destino, "MaestroCaja")) else "remoto"
        apps = APPS_LOCAL if modo == "local" else APPS_REMOTO
        cual = "PC DEL LOCAL" if modo == "local" else "LAPTOP DEL DUEÑO"
        self._log(f"=== ACTUALIZANDO LA {cual} ===\n{destino}\n")

        faltantes = [a for a in apps if not os.path.isdir(os.path.join(self.origen, a))]
        if faltantes:
            raise FileNotFoundError(
                f"No se encontraron los programas nuevos: {', '.join(faltantes)}.\n"
                f"¿Copiaste la carpeta 'dist' completa al pendrive?")

        # 1) Copia de seguridad de la base ANTES de tocar nada.
        if datos["backup"] and modo == "local":
            self._respaldar_base(destino)

        # 2) El servicio tiene el .exe abierto: hay que pararlo para poder
        #    reemplazarlo (si no, Windows no deja escribir encima).
        servicio_estaba = False
        if modo == "local":
            servicio_estaba = self._parar_servicio(destino)

        # 3) Reemplazar los programas. La base, el config y los tickets
        #    viven FUERA de estas carpetas, así que no los toca.
        for app in apps:
            self._log(f"Actualizando {app}...")
            origen_app = os.path.join(self.origen, app)
            destino_app = os.path.join(destino, app)
            anterior = destino_app + ".anterior"
            if os.path.exists(anterior):
                shutil.rmtree(anterior, ignore_errors=True)
            if os.path.isdir(destino_app):
                # Se mueve la vieja en vez de borrarla: si la copia nueva
                # falla a la mitad, todavía existe con qué volver atrás.
                os.rename(destino_app, anterior)
            try:
                shutil.copytree(origen_app, destino_app)
            except Exception:
                if os.path.isdir(anterior) and not os.path.isdir(destino_app):
                    os.rename(anterior, destino_app)   # volver a la anterior
                raise
            shutil.rmtree(anterior, ignore_errors=True)
        self._log("Programas actualizados.\n")

        # 4) Poner la base al día (agrega columnas nuevas; no borra datos).
        if modo == "local":
            self._migrar_base(destino)

        # 5) Volver a dejar el servicio como estaba.
        if modo == "local" and servicio_estaba:
            self._arrancar_servicio(destino)

        self._log("=== ACTUALIZACIÓN TERMINADA ===")
        self._log("Abrí la Caja y hacé una venta de prueba para confirmar que quedó todo bien.")

    # ------------------------------------------------------------------ #
    def _respaldar_base(self, destino):
        base = os.path.join(destino, "database", "stock.db")
        if not os.path.isfile(base):
            self._log("(no hay base de datos todavía: no hace falta respaldar)\n")
            return
        sello = datetime.now().strftime("%Y%m%d_%H%M%S")
        copia = os.path.join(destino, "database", f"stock.db.backup_{sello}")
        # Se usa la API de backup de SQLite y no una copia de archivo: con
        # WAL, copiar el .db suelto puede llevarse una base a medio escribir.
        try:
            import sqlite3
            origen = sqlite3.connect(base)
            respaldo = sqlite3.connect(copia)
            with respaldo:
                origen.backup(respaldo)
            respaldo.close()
            origen.close()
            mb = os.path.getsize(copia) / (1024 * 1024)
            self._log(f"Copia de seguridad hecha ({mb:.1f} MB):\n   {copia}\n")
        except Exception as e:
            raise RuntimeError(f"No se pudo respaldar la base, se corta acá para no arriesgarla: {e}")

    def _migrar_base(self, destino):
        self._log("Poniendo la base de datos al día...")
        try:
            from pos_core import paths
            paths.set_base_override(destino)
            from pos_core.db import aplicar_migraciones
            cambios = aplicar_migraciones()
        except Exception as e:
            raise RuntimeError(f"No se pudo actualizar la estructura de la base: {e}")
        if cambios:
            for c in cambios:
                self._log(f"   {c}")
        else:
            self._log("   La base ya estaba al día.")
        self._log("")

    def _estado_servicio(self) -> str:
        """'corriendo' | 'parado' | 'desconocido'. Nunca lanza.

        Preguntarle a Windows por el servicio no puede ser lo que voltee
        una actualización: si `sc` no está o contesta cualquier cosa, se
        sigue adelante y se avisa.
        """
        try:
            r = subprocess.run(["sc", "query", NOMBRE_SERVICIO],
                                capture_output=True, text=True, timeout=30)
            salida = (r.stdout or "").upper()
            if "RUNNING" in salida:
                return "corriendo"
            if "STOPPED" in salida or "1060" in salida:   # 1060 = no existe
                return "parado"
        except Exception:
            pass
        return "desconocido"

    def _parar_servicio(self, destino) -> bool:
        """Para el servicio para poder reemplazar su .exe. True si estaba corriendo."""
        exe = os.path.join(destino, "StockService", "StockService.exe")
        if not os.path.isfile(exe):
            return False

        estado = self._estado_servicio()
        if estado == "corriendo" and not es_administrador():
            # Se corta ACÁ, antes de tocar un solo archivo: sin parar el
            # servicio, Windows no deja reemplazar su .exe y la
            # actualización quedaría a medias. Nada cambió todavía.
            raise PermissionError(
                "El servicio de stock está corriendo y hay que pararlo para poder\n"
                "reemplazarlo, pero esto no está corriendo como administrador.\n"
                "Cerrá y volvé a abrir con botón derecho -> 'Ejecutar como administrador'.")
        if estado == "parado":
            self._log("(el servicio de stock no estaba corriendo)\n")
            return False

        self._log("Parando el servicio de stock...")
        try:
            subprocess.run([exe, "stop"], capture_output=True, text=True, timeout=60)
            import time
            for _ in range(20):
                if self._estado_servicio() != "corriendo":
                    break
                time.sleep(0.5)
            self._log("   Servicio detenido.\n")
        except Exception as e:
            self._log(f"   (no se pudo parar el servicio: {e})\n")
        return True

    def _arrancar_servicio(self, destino):
        """Deja el servicio andando de nuevo. Nunca voltea la actualización.

        Si algo falla acá, los programas YA quedaron actualizados: cortar
        con un error dejaría al cliente pensando que se rompió todo. Se
        avisa con letras claras y se sigue.
        """
        exe = os.path.join(destino, "StockService", "StockService.exe")
        self._log("Volviendo a arrancar el servicio de stock...")
        try:
            # Se reinstala apuntando al .exe nuevo: el servicio registrado
            # guarda la ruta del ejecutable, así que reinstalar deja el
            # registro coherente con lo que se acaba de copiar.
            subprocess.run([exe, "remove"], capture_output=True, text=True, timeout=60)
            r = subprocess.run([exe, "install"], capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                r = subprocess.run([exe, "start"], capture_output=True, text=True, timeout=120)
            if self._estado_servicio() == "corriendo":
                self._log("   Servicio corriendo de nuevo.\n")
                return
            detalle = f"{(r.stdout or '').strip()} {(r.stderr or '').strip()}".strip()
        except Exception as e:
            detalle = str(e)

        self._log("   ATENCIÓN: los programas quedaron actualizados, pero el servicio de\n"
                   "   stock no volvió a arrancar solo. Las alertas de Telegram y el acceso\n"
                   "   del Dueño Remoto no van a andar hasta que arranque. Probá con:\n"
                   f"      {exe} install\n      {exe} start\n"
                   "   (en una consola como administrador), o reiniciá la PC.\n"
                   f"   Detalle: {detalle}\n")


def main():
    Actualizador().mainloop()


if __name__ == "__main__":
    main()
