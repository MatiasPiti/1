"""Instalador de Otter — hace en un botón todo lo que antes era una lista
de 20 pasos a mano en la PC del cliente.

Hay dos instalaciones posibles, y el instalador pregunta cuál es al
arrancar porque son muy distintas:

  LOCAL (la PC del negocio, donde está la caja)
      Copia Caja + Dueño + Servicio a una misma carpeta (tienen que ser
      subcarpetas hermanas para compartir UNA sola base de datos), crea
      la base, opcionalmente carga el Excel de productos, escribe el
      config.ini con un token nuevo, instala y arranca el servicio de
      Windows desde su ruta definitiva, y deja los accesos directos.

  DUEÑO REMOTO (la laptop del dueño)
      Copia solo DuenoRemoto, guarda la dirección y el token del local, y
      prueba la conexión antes de dar por terminada la instalación.

El error más caro que este instalador evita: poner el Panel del Dueño
MAESTRO en la laptop del dueño. Esa app abre perfecto y no da ningún
error, pero se crea su propia base vacía y nunca muestra una venta del
negocio. Por eso la laptop recibe DuenoRemoto y nunca MaestroDueno.
"""

import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from apps.theme import COLORS, aplicar_tema, habilitar_copiar_pegar_global

DESTINO_LOCAL_POR_DEFECTO = r"C:\SistemaDual"
DESTINO_REMOTO_POR_DEFECTO = r"C:\Otter"
NOMBRE_SERVICIO = "SistemaDualStockService"

# Lo que se copia en cada modo. En el local van los tres juntos a
# propósito: paths.set_base_override_to_parent_dir() hace que los tres
# miren la carpeta PADRE, así que compartir base depende de que queden
# como subcarpetas hermanas del mismo destino.
APPS_LOCAL = ["MaestroCaja", "MaestroDueno", "StockService"]
APPS_REMOTO = ["DuenoRemoto"]


def es_administrador() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def buscar_origen() -> str:
    """Carpeta que contiene las apps ya compiladas (MaestroCaja, etc.).

    Se busca al lado del propio instalador y después un nivel más arriba,
    para que funcione tanto si el .exe quedó en dist\\OtterInstalador\\
    como si alguien copió todo suelto a la raíz de un pendrive.
    """
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


def generar_token() -> str:
    import secrets
    return "otter-" + secrets.token_urlsafe(24)


class Instalador(tk.Tk):

    def __init__(self):
        super().__init__()
        aplicar_tema(self)
        self.title("Instalador de Otter")
        self.geometry("760x640")
        self.origen = buscar_origen()
        self.modo = tk.StringVar(value="local")

        self._armar_ui()
        habilitar_copiar_pegar_global(self)
        self._log(f"Archivos de instalación encontrados en:\n   {self.origen}\n")
        if not es_administrador():
            self._log("AVISO: no estás como administrador. Todo funciona igual, salvo\n"
                       "instalar el servicio de Windows. Si vas a instalar en la PC del\n"
                       "local, cerrá y volvé a abrir este instalador con botón derecho ->\n"
                       "'Ejecutar como administrador'.\n")

    # ------------------------------------------------------------------ #
    def _armar_ui(self):
        ttk.Label(self, text="Instalador de Otter", style="Header.TLabel"
                  ).pack(anchor="w", padx=18, pady=(16, 2))
        ttk.Label(self, text="Elegí qué PC estás instalando:", style="Muted.TLabel"
                  ).pack(anchor="w", padx=18)

        marco_modo = ttk.Frame(self, padding=(18, 10))
        marco_modo.pack(fill="x")
        ttk.Radiobutton(marco_modo, text="PC del LOCAL  (caja + panel del dueño + servicio)",
                        variable=self.modo, value="local", command=self._cambiar_modo
                        ).pack(anchor="w")
        ttk.Radiobutton(marco_modo, text="LAPTOP DEL DUEÑO  (solo Dueño Remoto, se conecta al local)",
                        variable=self.modo, value="remoto", command=self._cambiar_modo
                        ).pack(anchor="w", pady=(4, 0))

        self.form = ttk.Frame(self, padding=(18, 4))
        self.form.pack(fill="x")

        # --- campos del modo LOCAL ---
        self.campos_local = ttk.Frame(self.form)
        f = self.campos_local
        ttk.Label(f, text="Carpeta de instalación:").grid(row=0, column=0, sticky="w", pady=4)
        self.destino_local = ttk.Entry(f, width=46)
        self.destino_local.insert(0, DESTINO_LOCAL_POR_DEFECTO)
        self.destino_local.grid(row=0, column=1, sticky="w", padx=8)

        ttk.Label(f, text="Nombre del negocio (sale en el ticket):").grid(row=1, column=0, sticky="w", pady=4)
        self.nombre_local = ttk.Entry(f, width=46)
        self.nombre_local.insert(0, "El Galpón Del Nono")
        self.nombre_local.grid(row=1, column=1, sticky="w", padx=8)

        ttk.Label(f, text="Excel de productos (opcional):").grid(row=2, column=0, sticky="w", pady=4)
        fila_excel = ttk.Frame(f)
        fila_excel.grid(row=2, column=1, sticky="w", padx=8)
        self.ruta_excel = ttk.Entry(fila_excel, width=36)
        self.ruta_excel.pack(side="left")
        ttk.Button(fila_excel, text="Buscar...", command=self._elegir_excel).pack(side="left", padx=6)

        self.var_servicio = tk.BooleanVar(value=True)
        self.var_accesos = tk.BooleanVar(value=True)
        self.var_remoto = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Instalar y arrancar el servicio de stock (necesita administrador)",
                        variable=self.var_servicio).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(f, text="Habilitar el acceso del Dueño Remoto (genera el token)",
                        variable=self.var_remoto).grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(f, text="Crear accesos directos en el Escritorio",
                        variable=self.var_accesos).grid(row=5, column=0, columnspan=2, sticky="w")

        # --- campos del modo REMOTO ---
        self.campos_remoto = ttk.Frame(self.form)
        g = self.campos_remoto
        ttk.Label(g, text="Carpeta de instalación:").grid(row=0, column=0, sticky="w", pady=4)
        self.destino_remoto = ttk.Entry(g, width=46)
        self.destino_remoto.insert(0, DESTINO_REMOTO_POR_DEFECTO)
        self.destino_remoto.grid(row=0, column=1, sticky="w", padx=8)

        ttk.Label(g, text="Dirección de la PC del local:").grid(row=1, column=0, sticky="w", pady=4)
        self.url_remota = ttk.Entry(g, width=46)
        self.url_remota.insert(0, "http://100.")
        self.url_remota.grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(g, text="(la IP de Tailscale de la PC del local, con el puerto :8765)",
                  style="Muted.TLabel").grid(row=2, column=1, sticky="w", padx=8)

        ttk.Label(g, text="Token:").grid(row=3, column=0, sticky="w", pady=4)
        self.token_remoto = ttk.Entry(g, width=46)
        self.token_remoto.grid(row=3, column=1, sticky="w", padx=8)
        ttk.Label(g, text="(el que mostró este mismo instalador en la PC del local)",
                  style="Muted.TLabel").grid(row=4, column=1, sticky="w", padx=8)

        self.var_accesos_remoto = tk.BooleanVar(value=True)
        ttk.Checkbutton(g, text="Crear acceso directo en el Escritorio",
                        variable=self.var_accesos_remoto).grid(row=5, column=0, columnspan=2,
                                                                sticky="w", pady=(8, 0))

        self.boton = ttk.Button(self, text="INSTALAR", style="Accent.TButton", command=self._instalar)
        self.boton.pack(anchor="w", padx=18, pady=12)

        self.texto = tk.Text(self, height=18, bg="#FFFFFF", relief="flat",
                              highlightthickness=1, highlightbackground=COLORS["border"],
                              padx=8, pady=8, wrap="word")
        self.texto.pack(fill="both", expand=True, padx=18, pady=(0, 16))

        self._cambiar_modo()

    def _cambiar_modo(self):
        self.campos_local.pack_forget()
        self.campos_remoto.pack_forget()
        if self.modo.get() == "local":
            self.campos_local.pack(fill="x")
        else:
            self.campos_remoto.pack(fill="x")

    def _elegir_excel(self):
        ruta = filedialog.askopenfilename(filetypes=[("Excel/CSV", "*.xlsx *.xlsm *.csv")])
        if ruta:
            self.ruta_excel.delete(0, "end")
            self.ruta_excel.insert(0, ruta)

    def _log(self, texto: str):
        self.texto.insert("end", texto + "\n")
        self.texto.see("end")
        self.update_idletasks()

    # ------------------------------------------------------------------ #
    def _instalar(self):
        self.boton.config(state="disabled")
        self.texto.delete("1.0", "end")
        # En un hilo aparte para que la ventana no se congele durante la
        # copia (son varios cientos de MB) ni durante el install del
        # servicio.
        threading.Thread(target=self._instalar_en_hilo, daemon=True).start()

    def _instalar_en_hilo(self):
        try:
            if self.modo.get() == "local":
                self._instalar_local()
            else:
                self._instalar_remoto()
        except Exception as e:
            self._log(f"\n*** LA INSTALACIÓN SE DETUVO ***\n{type(e).__name__}: {e}")
            self.after(0, lambda: messagebox.showerror("Error en la instalación", str(e)))
        finally:
            self.after(0, lambda: self.boton.config(state="normal"))

    # ------------------------------------------------------------------ #
    def _copiar_apps(self, destino: str, apps: list):
        os.makedirs(destino, exist_ok=True)
        for app in apps:
            origen_app = os.path.join(self.origen, app)
            if not os.path.isdir(origen_app):
                raise FileNotFoundError(
                    f"No se encontró la carpeta '{app}' en {self.origen}. "
                    f"¿Copiaste la carpeta 'dist' completa al pendrive?")
            destino_app = os.path.join(destino, app)
            self._log(f"Copiando {app}...")
            # dirs_exist_ok: reinstalar encima de una versión anterior es
            # un caso normal (actualizaciones), no un error.
            shutil.copytree(origen_app, destino_app, dirs_exist_ok=True)
        self._log("Archivos copiados.\n")

    def _crear_acceso_directo(self, nombre: str, ruta_exe: str):
        try:
            import win32com.client
            escritorio = os.path.join(os.path.expanduser("~"), "Desktop")
            shell = win32com.client.Dispatch("WScript.Shell")
            acceso = shell.CreateShortCut(os.path.join(escritorio, f"{nombre}.lnk"))
            acceso.TargetPath = ruta_exe
            acceso.WorkingDirectory = os.path.dirname(ruta_exe)
            acceso.IconLocation = ruta_exe
            acceso.Description = nombre
            acceso.save()
            self._log(f"   Acceso directo creado: {nombre}")
        except Exception as e:
            self._log(f"   (no se pudo crear el acceso directo '{nombre}': {e})")

    def _configurar(self, destino: str, cambios: dict):
        """Escribe config.ini en la carpeta de instalación.

        Se apunta paths al destino ANTES de tocar la config para que
        pos_core escriba en la instalación nueva y no al lado del
        instalador.
        """
        from pos_core import paths, config
        paths.set_base_override(destino)
        config.actualizar_config_dict(cambios)

    # ------------------------------------------------------------------ #
    def _instalar_local(self):
        destino = self.destino_local.get().strip() or DESTINO_LOCAL_POR_DEFECTO
        self._log(f"=== INSTALACIÓN EN LA PC DEL LOCAL ===\nDestino: {destino}\n")

        self._copiar_apps(destino, APPS_LOCAL)

        # --- base de datos ---
        from pos_core import paths
        paths.set_base_override(destino)
        from pos_core.db import init_db
        init_db()
        self._log(f"Base de datos creada en {os.path.join(destino, 'database', 'stock.db')}\n")

        # --- configuración ---
        cambios = {"general": {"nombre_local": self.nombre_local.get().strip() or "Mi Negocio"}}
        token = None
        if self.var_remoto.get():
            token = generar_token()
            cambios["remoto"] = {"habilitado": "true", "puerto": "8765", "token": token}
        self._configurar(destino, cambios)
        self._log("config.ini escrito.\n")

        # --- catálogo de productos ---
        ruta_excel = self.ruta_excel.get().strip()
        if ruta_excel:
            self._log("Cargando el Excel de productos (puede tardar un minuto)...")
            from pos_core import excel_import
            resultado = excel_import.cargar_masivo(ruta_excel, usuario="instalador")
            self._log(f"   Creados: {resultado['creados']}   Actualizados: {resultado['actualizados']}"
                       f"   Errores: {len(resultado['errores'])}")
            for e in resultado["errores"][:5]:
                self._log(f"      fila {e['fila']}: {e['error']}")
            self._log("")

        # --- servicio de Windows ---
        if self.var_servicio.get():
            self._instalar_servicio(destino)

        # --- accesos directos ---
        if self.var_accesos.get():
            self._crear_acceso_directo("Otter Caja", os.path.join(destino, "MaestroCaja", "MaestroCaja.exe"))
            self._crear_acceso_directo("Otter Dueno", os.path.join(destino, "MaestroDueno", "MaestroDueno.exe"))
            self._log("")

        self._log("=== INSTALACIÓN TERMINADA ===\n")
        if token:
            self._log("ANOTÁ ESTE TOKEN — lo necesitás para instalar el Dueño Remoto\n"
                       "en la laptop del dueño (no se vuelve a mostrar acá):\n")
            self._log(f"      {token}\n")
            self._log(f"(también queda guardado en {os.path.join(destino, 'config.ini')})\n")
            self._mostrar_ip_tailscale()
        self._log("Falta hacer a mano, desde el Panel del Dueño:\n"
                   "  - Configurar la impresora de tickets (botón 'Configurar impresora')\n"
                   "  - Cargar el bot de Telegram (pestaña Alertas)\n"
                   "  - Cargar el stock físico real (arranca todo en 0)")

    def _instalar_servicio(self, destino: str):
        exe = os.path.join(destino, "StockService", "StockService.exe")
        if not es_administrador():
            self._log("SERVICIO: NO se instaló porque este instalador no está corriendo como\n"
                       "administrador. Cerralo y volvé a abrirlo con botón derecho ->\n"
                       "'Ejecutar como administrador', o instalalo después con:\n"
                       f"   {exe} install\n   {exe} start\n")
            return

        # Un servicio ya instalado apunta a la ruta donde estaba el .exe en
        # ese momento. Si se reinstala en otra carpeta y no se saca el
        # viejo, Windows sigue arrancando el de antes.
        self._log("Instalando el servicio de stock...")
        subprocess.run([exe, "stop"], capture_output=True, text=True, timeout=60)
        subprocess.run([exe, "remove"], capture_output=True, text=True, timeout=60)

        r = subprocess.run([exe, "install"], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"No se pudo instalar el servicio:\n{r.stdout}\n{r.stderr}")
        r = subprocess.run([exe, "start"], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"El servicio se instaló pero no arrancó:\n{r.stdout}\n{r.stderr}")

        estado = subprocess.run(["sc", "query", NOMBRE_SERVICIO], capture_output=True, text=True, timeout=30)
        if "RUNNING" in estado.stdout.upper():
            self._log("   Servicio instalado y corriendo.\n")
        else:
            self._log(f"   ATENCIÓN: el servicio quedó instalado pero no figura como corriendo.\n"
                       f"   {estado.stdout.strip()}\n")

    def _mostrar_ip_tailscale(self):
        try:
            r = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=15)
            ip = (r.stdout or "").strip().split("\n")[0].strip()
        except Exception:
            ip = ""
        if ip:
            self._log(f"Dirección para cargar en la laptop del dueño:\n"
                       f"      http://{ip}:8765\n")
        else:
            self._log("No se pudo leer la IP de Tailscale (¿está instalado y conectado?).\n"
                       "Cuando lo esté, sacala con:  tailscale ip -4\n")

    # ------------------------------------------------------------------ #
    def _instalar_remoto(self):
        destino = self.destino_remoto.get().strip() or DESTINO_REMOTO_POR_DEFECTO
        url = self.url_remota.get().strip()
        token = self.token_remoto.get().strip()
        if not url or not token:
            raise ValueError("Hacen falta la dirección de la PC del local y el token.")

        self._log(f"=== INSTALACIÓN EN LA LAPTOP DEL DUEÑO ===\nDestino: {destino}\n")
        self._log("(Acá va SOLO el Dueño Remoto: la base de datos vive en la PC del local\n"
                   " y tiene que haber una sola.)\n")

        self._copiar_apps(destino, APPS_REMOTO)
        self._configurar(destino, {"conexion_remota": {"url": url, "token": token}})
        self._log("Dirección y token guardados.\n")

        self._log("Probando la conexión con la PC del local...")
        from pos_core.dueno_backend import RemoteBackend
        backend = RemoteBackend(url, token)
        if backend.verificar_conexion():
            self._log("   CONECTADO. La laptop ve la PC del local correctamente.\n")
        else:
            self._log("   NO SE PUDO CONECTAR. La instalación quedó hecha igual; revisá:\n"
                       "     - que la PC del local esté prendida y con el servicio corriendo\n"
                       "     - que Tailscale esté conectado en las DOS computadoras\n"
                       "     - que la dirección y el token sean los correctos\n")

        if self.var_accesos_remoto.get():
            self._crear_acceso_directo("Otter Dueno Remoto",
                                        os.path.join(destino, "DuenoRemoto", "DuenoRemoto.exe"))
            self._log("")

        self._log("=== INSTALACIÓN TERMINADA ===")


def main():
    Instalador().mainloop()


if __name__ == "__main__":
    main()
