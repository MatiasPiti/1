"""Dueño Remoto - Panel del Dueño para usar desde otra PC (por ejemplo,
la casa del dueño), conectado en vivo a la PC del local por una VPN
privada tipo Tailscale.

Es la MISMA ventana que el Panel del Dueño Maestro (apps/master_dueno) —
ni una sola pestaña ni un solo botón menos — pero en vez de tocar un
archivo de base de datos local, cada acción viaja por HTTP hasta el
servicio oculto de stock que corre en la PC del local (ver
services/remote_api.py). Ver pos_core/dueno_backend.py para el mecanismo
que hace esto transparente para el resto del código.

Requisitos para poder usar esto:
  1. En la PC del local: pestaña "Facturación..." no, mejor dicho, en
     config.ini de esa PC, sección [remoto], habilitado=true — y el
     servicio oculto de stock corriendo (se arranca solo con Windows).
  2. Una VPN privada tipo Tailscale instalada en AMBAS PCs (la del local
     y esta), para que se puedan ver entre sí sin abrir nada a internet
     en general.
  3. La URL de la PC del local dentro de esa red privada (Tailscale le da
     una IP fija tipo 100.x.x.x, o un nombre tipo "pc-local.tailXXXX.ts.net")
     y el token que se generó en su config.ini, sección [remoto] -> token.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pos_core import config
from pos_core.dueno_backend import RemoteBackend
from apps.theme import aplicar_tema, habilitar_copiar_pegar_global


class PantallaConexion(tk.Tk):
    """Se muestra antes que nada: pide la URL y el token de la PC del
    local. Si ya están guardados de una vez anterior, los precarga y
    prueba la conexión sola."""

    def __init__(self):
        super().__init__()
        aplicar_tema(self)
        self.title("Otter Dueño Remoto - Conexión")
        self.geometry("520x320")
        self.resizable(False, False)

        ttk.Label(self, text="Conectar con la PC del local", style="Header.TLabel"
                  ).pack(anchor="w", padx=20, pady=(20, 10))

        form = ttk.Frame(self, padding=(20, 0))
        form.pack(fill="x")
        ttk.Label(form, text="Dirección (Tailscale):").grid(row=0, column=0, sticky="w", pady=6)
        self.entry_url = ttk.Entry(form, width=42)
        self.entry_url.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(form, text="ej: http://100.101.102.103:8765", style="Muted.TLabel"
                  ).grid(row=1, column=1, sticky="w", padx=8)

        ttk.Label(form, text="Token:").grid(row=2, column=0, sticky="w", pady=6)
        self.entry_token = ttk.Entry(form, width=42, show="•")
        self.entry_token.grid(row=2, column=1, sticky="w", padx=8)
        ttk.Label(form, text="(config.ini del local, sección [remoto] -> token)",
                  style="Muted.TLabel").grid(row=3, column=1, sticky="w", padx=8)

        self.lbl_estado = ttk.Label(self, text="", style="Muted.TLabel", wraplength=460, justify="left")
        self.lbl_estado.pack(anchor="w", padx=20, pady=(10, 0))

        ttk.Button(self, text="Conectar", style="Accent.TButton", command=self._conectar
                   ).pack(anchor="w", padx=20, pady=16)

        cfg = config.obtener_config_dict().get("conexion_remota", {})
        if cfg.get("url"):
            self.entry_url.insert(0, cfg["url"])
        if cfg.get("token"):
            self.entry_token.insert(0, cfg["token"])

        self.backend_listo = None
        habilitar_copiar_pegar_global(self)

        if cfg.get("url") and cfg.get("token"):
            self.after(200, self._conectar)

    def _conectar(self):
        url = self.entry_url.get().strip()
        token = self.entry_token.get().strip()
        if not url or not token:
            self.lbl_estado.config(text="Completá la dirección y el token.")
            return

        self.lbl_estado.config(text="Probando conexión...")
        self.update()

        backend = RemoteBackend(url, token)
        if not backend.verificar_conexion():
            self.lbl_estado.config(
                text="No se pudo conectar. Revisá que: la PC del local esté prendida, el servicio "
                     "de stock corriendo, la API remota habilitada en su config.ini, la VPN "
                     "(Tailscale) conectada en las dos PCs, y que la dirección/token sean correctos.")
            return

        config.actualizar_config_dict({"conexion_remota": {"url": url, "token": token}})
        self.backend_listo = backend
        self.destroy()


def main():
    pantalla = PantallaConexion()
    pantalla.mainloop()
    if pantalla.backend_listo is None:
        return  # se cerró la ventana sin conectar

    from apps.master_dueno.main import AppDueno
    AppDueno(backend=pantalla.backend_listo).mainloop()


if __name__ == "__main__":
    main()
