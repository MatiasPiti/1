"""OtterBlindaje: dejar la PC del local a prueba de las caidas conocidas.

Un boton. Adentro corre TODO lo que hasta ahora se hacia a mano, pegando
comandos en PowerShell mientras el negocio esperaba:

  - El servicio de stock arranca solo con Windows y se reintenta.
  - La PC no se suspende: todos los planes de energia, la hibernacion, el
    boton de encendido y la tapa.
  - La placa de red no se apaga sola "para ahorrar energia".
  - Un watchdog cada 5 minutos que vigila EL PUERTO, no el estado del
    servicio.
  - Tailscale en modo unattended.
  - Opcional: soporte remoto por SSH sobre Tailscale, para que la proxima
    vez no haya que viajar hasta el local.

Por que ejecuta los .ps1 en vez de reimplementarlos: son la misma
herramienta, y duplicar la logica es garantizar que dentro de tres meses
una mitad este arreglada y la otra no. Los .ps1 siguen sirviendo solos
—se bajan de GitHub y se corren en una consola— y este .exe es la forma
comoda de correrlos. Una sola fuente de verdad.
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from apps.theme import (COLORS, aplicar_tema, habilitar_copiar_pegar_global,
                        ajustar_ventana)
from pos_core.paths import get_resource_path

USUARIO_SOPORTE = "otter_soporte"


def es_administrador() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def buscar_script(nombre: str) -> str:
    """Ubica un .ps1 esté empaquetado o corriendo desde el repo."""
    raiz = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for candidato in (get_resource_path(os.path.join("scripts", nombre)),
                      os.path.join(raiz, "scripts", nombre)):
        if os.path.isfile(candidato):
            return candidato
    return ""


def _decodificar(crudo: bytes) -> str:
    """La salida de PowerShell viene en la codepage de la consola, no en
    UTF-8. Se prueban las dos antes de romper acentos."""
    for codificacion in ("utf-8", "cp1252", "cp850"):
        try:
            return crudo.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return crudo.decode("utf-8", errors="replace")


class AppBlindaje(tk.Tk):

    def __init__(self):
        super().__init__()
        aplicar_tema(self)
        self.title("Otter - Blindar la PC del local")
        # Tk NO es seguro entre hilos: el trabajo corre en un hilo aparte y
        # todo lo que quiera aparecer en pantalla pasa por esta cola, que
        # vacia el hilo de Tk (ver _bombear).
        self._cola = queue.Queue()
        self._corriendo = False
        # Copia de todo lo que se fue mostrando. El informe se arma DESDE
        # ACA y no leyendo el widget: leerlo seria tocar Tk desde el hilo
        # de trabajo, que es exactamente lo que este proyecto ya aprendió
        # a no hacer (ver CLAUDE.md). list.append es atómico, así que se
        # puede llenar desde cualquier hilo sin candado.
        self._historial = []

        self._armar_ui()
        ajustar_ventana(self, 820, 660)
        habilitar_copiar_pegar_global(self)
        self._bombear()

        if not es_administrador():
            self._log("!!! NO ESTAS COMO ADMINISTRADOR !!!")
            self._log("Cerra esta ventana y volve a abrirla con boton derecho ->")
            self._log("'Ejecutar como administrador'. Sin eso no se puede tocar")
            self._log("ni el servicio, ni la energia, ni el firewall.\n")
        else:
            self._log("Listo para blindar. Apreta el boton cuando quieras.\n")
            self._log("Se puede correr con el negocio abierto: no cierra la caja")
            self._log("ni toca la base de datos. La red se corta un instante\n")
            self._log("cuando ajusta la placa.\n")

    # ------------------------------------------------------------------ #
    def _armar_ui(self):
        ttk.Label(self, text="Blindar la PC del local", style="Header.TLabel"
                  ).pack(anchor="w", padx=18, pady=(16, 2))
        ttk.Label(self,
                  text="Cierra las causas por las que el Dueño Remoto se caía solo.",
                  style="Muted.TLabel").pack(anchor="w", padx=18)

        marco = ttk.Frame(self, padding=(18, 12))
        marco.pack(fill="x")

        self.var_ssh = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            marco,
            text="Instalar también el soporte remoto por SSH (para no tener que volver al local)",
            variable=self.var_ssh, command=self._cambiar_ssh
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        self.lbl_pass = ttk.Label(marco, text=f"Contraseña para el usuario '{USUARIO_SOPORTE}':")
        self.lbl_pass.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.entrada_pass = ttk.Entry(marco, width=34, show="*")
        self.entrada_pass.grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))
        self.lbl_aviso_pass = ttk.Label(
            marco,
            text="Sin caracteres confundibles (nada de l I 1 O 0): la vas a tipear en el celular.",
            style="Muted.TLabel")
        self.lbl_aviso_pass.grid(row=2, column=0, columnspan=2, sticky="w")
        self._cambiar_ssh()

        self.boton = tk.Button(self, text="BLINDAR ESTA PC", command=self._arrancar,
                               bg=COLORS.get("accent", "#2d7d46"), fg="white",
                               font=("Segoe UI", 14, "bold"), height=2, cursor="hand2")
        self.boton.pack(fill="x", padx=18, pady=(6, 10))

        marco_texto = ttk.Frame(self)
        marco_texto.pack(fill="both", expand=True, padx=18, pady=(0, 6))
        self.texto = tk.Text(marco_texto, height=20, wrap="word", bg="#1e1e1e",
                             fg="#d4d4d4", insertbackground="#d4d4d4",
                             font=("Consolas", 9))
        barra = ttk.Scrollbar(marco_texto, command=self.texto.yview)
        self.texto.config(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y")
        self.texto.pack(side="left", fill="both", expand=True)

        self.estado = ttk.Label(self, text="", style="Muted.TLabel")
        self.estado.pack(anchor="w", padx=18, pady=(0, 12))

    def _cambiar_ssh(self):
        estado = "normal" if self.var_ssh.get() else "disabled"
        self.entrada_pass.config(state=estado)

    # ------------------------------------------------------------------ #
    def _log(self, texto):
        """Se puede llamar desde cualquier hilo: solo encola."""
        self._historial.append(str(texto))
        self._cola.put(texto)

    def _bombear(self):
        """Corre siempre en el hilo de Tk: vacía la cola a la pantalla."""
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
    def _arrancar(self):
        if self._corriendo:
            return
        if not es_administrador():
            messagebox.showerror(
                "Falta ejecutar como administrador",
                "Cerrá esta ventana y volvé a abrirla con botón derecho ->\n"
                "'Ejecutar como administrador'.\n\n"
                "Sin eso no se puede cambiar el servicio, la energía ni el firewall.")
            return
        if self.var_ssh.get() and len(self.entrada_pass.get()) < 8:
            messagebox.showerror(
                "Contraseña muy corta",
                "La contraseña del usuario de soporte tiene que tener al menos\n"
                "8 caracteres. Es una cuenta de administrador de la PC que cobra.")
            return

        self._corriendo = True
        self.boton.config(state="disabled", text="TRABAJANDO...")
        self.estado.config(text="Trabajando. No cierres esta ventana.")
        clave = self.entrada_pass.get() if self.var_ssh.get() else ""
        threading.Thread(target=self._trabajar, args=(self.var_ssh.get(), clave),
                         daemon=True).start()

    def _correr_script(self, ruta: str, argumentos=None) -> bool:
        """Ejecuta un .ps1 y va mostrando su salida a medida que sale."""
        comando = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", ruta]
        if argumentos:
            comando += argumentos
        try:
            proceso = subprocess.Popen(
                comando, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                # stdin cerrado: si algún día un script quedara esperando
                # una respuesta, sin esto la ventana se cuelga para siempre
                # sin decir por qué. Así falla rápido y se ve el error.
                stdin=subprocess.DEVNULL,
                # Sin esto, un .exe compilado con --windowed abre una ventana
                # negra de consola por cada script que lanza.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self._log(f"   ERROR: no se pudo lanzar PowerShell: {e}")
            return False

        for linea in iter(proceso.stdout.readline, b""):
            texto = _decodificar(linea).rstrip("\r\n")
            if texto.strip():
                self._log(texto)
        proceso.stdout.close()
        return proceso.wait() == 0

    def _trabajar(self, con_ssh: bool, clave: str):
        inicio = datetime.now()
        problemas = []
        try:
            self._log("=" * 62)
            self._log(f"BLINDAJE - {inicio:%Y-%m-%d %H:%M:%S}")
            self._log("=" * 62 + "\n")

            blindar = buscar_script("blindar_local.ps1")
            if not blindar:
                problemas.append("no se encontró blindar_local.ps1 adentro del programa")
            elif not self._correr_script(blindar):
                problemas.append("el blindaje terminó con errores (mirá el detalle arriba)")

            if con_ssh:
                self._log("\n" + "=" * 62)
                self._log("SOPORTE REMOTO POR SSH")
                self._log("=" * 62 + "\n")
                soporte = buscar_script("soporte_remoto.ps1")
                if not soporte:
                    problemas.append("no se encontró soporte_remoto.ps1 adentro del programa")
                elif not self._correr_script(soporte, ["-Password", clave]):
                    problemas.append("el soporte remoto terminó con errores")

            self._log("\n" + "=" * 62)
            if problemas:
                self._log("QUEDARON COSAS SIN RESOLVER:")
                for p in problemas:
                    self._log(f"  - {p}")
                self._log("\nMandale esta pantalla a Claude junto con el archivo")
                self._log(r"C:\SistemaDual\watchdog\estado_antes_del_blindaje.txt")
            else:
                self._log("TERMINÓ SIN ERRORES.")
                self._log("")
                self._log("Revisá arriba que las filas de la tabla digan SI.")
                self._log("")
                self._log("AHORA LA PRUEBA DE VERDAD, antes de irte:")
                self._log("  1. Apretá el botón de encendido: tiene que APAGARSE, no dormirse.")
                self._log("  2. Prendela, y SIN iniciar sesión, desde el celular abrí")
                self._log("     http://LA-IP-DE-TAILSCALE:8765/health")
                self._log("     Si contesta algo, aunque diga 'token inválido', quedó bien.")
                if con_ssh:
                    self._log(f"  3. Probá el SSH con DATOS MÓVILES (no con el wifi del local):")
                    self._log(f"     ssh {USUARIO_SOPORTE}@LA-IP-DE-TAILSCALE")
            self._log("=" * 62)
            self._guardar_informe()
        except Exception as e:
            self._log(f"\nERROR INESPERADO: {e}")
        finally:
            self._cola.put(self._terminar)

    def _guardar_informe(self):
        """Deja lo que se ve en pantalla en un archivo, para poder mandarlo."""
        try:
            destino = os.path.join(os.path.expanduser("~"), "Desktop",
                                   f"blindaje_{datetime.now():%Y%m%d_%H%M%S}.txt")
            contenido = "\n".join(self._historial)
            with open(destino, "w", encoding="utf-8") as f:
                f.write(contenido)
            self._log(f"\n(Copia de esta pantalla guardada en el Escritorio:\n {destino})")
        except Exception:
            pass   # no poder guardar el informe no invalida el trabajo hecho

    def _terminar(self):
        self._corriendo = False
        self.boton.config(state="normal", text="BLINDAR ESTA PC")
        self.estado.config(text="Terminado. Podés cerrar esta ventana.")


def _preparar():
    if os.name != "nt":
        raise SystemExit("OtterBlindaje solo corre en Windows.")


if __name__ == "__main__":
    _preparar()
    AppBlindaje().mainloop()
