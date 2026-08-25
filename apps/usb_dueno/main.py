"""USB Dueño - Sistema Portátil de Emergencia (Panel de Control completo).

Reutiliza la app completa del Panel del Dueño Maestro (dashboard, stock,
filtros/bulk, PDF, Excel, alertas) parcheando el ORIGEN a 'USB_DUENO' para
que cada movimiento quede marcado como pendiente de sincronizar, agrega
el cartel rojo de emergencia y el botón "Preparar sincronización", y
deshabilita el atajo Ctrl+Shift+M: el módulo de conciliación SOLO corre
en el Sistema Maestro (sección 5 del spec).
"""

import os
import sys
import tkinter as tk
from tkinter import messagebox

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import apps.master_dueno.main as maestro_mod
from pos_core.db import init_db
from pos_core import sync_export

maestro_mod.ORIGEN = "USB_DUENO"
maestro_mod.USUARIO = os.environ.get("USERNAME", "dueño_emergencia")


class AppUsbDueno(maestro_mod.AppDueno):
    def __init__(self):
        super().__init__()
        self.title("Otter Dueño (Emergencia)")

        # el módulo oculto de conciliación es exclusivo del Sistema Maestro
        self.unbind_all("<Control-Shift-M>")

        primer_hijo = self.winfo_children()[0]
        banner = tk.Label(self, text="⚠ MODO EMERGENCIA PORTÁTIL - DATOS NO SINCRONIZADOS ⚠",
                           bg="#c0392b", fg="white", font=("", 12, "bold"), pady=6)
        banner.pack(fill="x", before=primer_hijo)

        boton_sync = tk.Button(self, text="Preparar sincronización", command=self._preparar_sync,
                                bg="#2c3e50", fg="white", pady=4)
        boton_sync.pack(fill="x", before=primer_hijo)

    def _preparar_sync(self):
        try:
            ruta = sync_export.exportar_dueno()
        except Exception as e:
            messagebox.showerror("Error exportando", str(e))
            return
        messagebox.showinfo("Sincronización preparada",
                             f"Se generó:\n{ruta}\n\n"
                             f"Llevá el USB a la PC del desarrollador y usá Ctrl+Shift+M "
                             f"en el Sistema Maestro para conciliar.")


if __name__ == "__main__":
    init_db()
    AppUsbDueno().mainloop()
