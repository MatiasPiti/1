"""Panel oculto de Sincronización y Conciliación (Ctrl+Shift+M).

Busca los export_*.json dentro de cualquier unidad conectada que tenga
una carpeta SYNC_DATA/ (es decir, cualquier USB de emergencia insertado),
muestra un resumen de diferencias (dry-run) y, solo si el desarrollador
confirma, aplica los cambios a la DB maestra.
"""

import glob
import os
import string
import tkinter as tk
from tkinter import ttk, messagebox

from pos_core import reconciliation
from apps.theme import aplicar_tema

USUARIO_DEV = os.environ.get("USERNAME", "dev")


def _detectar_unidades_con_sync_data():
    """Windows: recorre las letras de unidad A: - Z: buscando SYNC_DATA/.
    En Linux/Mac (desarrollo/testing) recorre /media y /mnt."""
    encontradas = []
    if os.name == "nt":
        for letra in string.ascii_uppercase:
            carpeta = f"{letra}:\\SYNC_DATA"
            if os.path.isdir(carpeta):
                encontradas.append(carpeta)
    else:
        for base in ("/media", "/mnt", os.path.expanduser("~")):
            encontradas.extend(glob.glob(os.path.join(base, "**", "SYNC_DATA"), recursive=True))
    return encontradas


class PanelSincronizacion(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        aplicar_tema(self)
        self.title("[DEV] Sincronización y Conciliación")
        self.geometry("800x600")
        self.grab_set()

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="Buscar USBs conectados", command=self._buscar).pack(side="left")
        ttk.Button(top, text="Elegir JSON manualmente...", command=self._elegir_manual).pack(side="left", padx=6)

        self.lista = tk.Listbox(self, height=6)
        self.lista.pack(fill="x", padx=8, pady=4)
        self.lista.bind("<<ListboxSelect>>", self._on_seleccion)

        self.texto = tk.Text(self, height=22)
        self.texto.pack(fill="both", expand=True, padx=8, pady=4)

        botones = ttk.Frame(self, padding=8)
        botones.pack(fill="x")
        ttk.Button(botones, text="Aplicar cambios", command=self._aplicar).pack(side="right")

        self.archivos = []
        self.archivo_actual = None
        self._buscar()

    def _buscar(self):
        self.archivos = []
        for carpeta in _detectar_unidades_con_sync_data():
            for nombre in ("export_caja.json", "export_dueño.json", "export_dueno.json"):
                ruta = os.path.join(carpeta, nombre)
                if os.path.isfile(ruta):
                    self.archivos.append(ruta)
        self.lista.delete(0, "end")
        for a in self.archivos:
            self.lista.insert("end", a)
        if not self.archivos:
            self.texto.delete("1.0", "end")
            self.texto.insert("end", "No se encontró ningún export_*.json en carpetas SYNC_DATA/ de "
                                      "unidades conectadas.\nUsá 'Elegir JSON manualmente' si hace falta.")

    def _elegir_manual(self):
        from tkinter import filedialog
        ruta = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if ruta:
            self.archivos.append(ruta)
            self.lista.insert("end", ruta)

    def _on_seleccion(self, event=None):
        sel = self.lista.curselection()
        if not sel:
            return
        self.archivo_actual = self.archivos[sel[0]]
        self._mostrar_dry_run(self.archivo_actual)

    def _es_export_caja(self, ruta):
        return "caja" in os.path.basename(ruta).lower()

    def _mostrar_dry_run(self, ruta):
        self.texto.delete("1.0", "end")
        try:
            if self._es_export_caja(ruta):
                resumen = reconciliation.analizar_export_caja(ruta)
            else:
                resumen = reconciliation.analizar_export_dueno(ruta)
        except Exception as e:
            self.texto.insert("end", f"Error leyendo el archivo: {e}")
            return

        self.texto.insert("end", f"Archivo: {ruta}\n\n")
        self.texto.insert("end", f"Ventas nuevas a importar: {resumen.ventas_nuevas}\n")
        self.texto.insert("end", f"Ventas duplicadas (se omiten): {resumen.ventas_omitidas_duplicadas}\n")
        self.texto.insert("end", f"Movimientos de stock a aplicar: {resumen.movimientos_aplicados}\n")
        self.texto.insert("end", f"Movimientos duplicados (se omiten): {resumen.movimientos_omitidos_duplicados}\n")
        self.texto.insert("end", f"Productos nuevos: {resumen.productos_nuevos}\n")
        self.texto.insert("end", f"Precios a actualizar: {resumen.precios_actualizados}\n")
        if resumen.conflictos_precio:
            self.texto.insert("end", "\nConflictos de precio detectados:\n")
            for c in resumen.conflictos_precio:
                self.texto.insert("end", f"  {c['codigo']}: maestro=${c['precio_maestro']} "
                                          f"usb=${c['precio_usb']} -> gana: {c['gana']}\n")
        if resumen.detalle:
            self.texto.insert("end", "\nDetalle:\n")
            for d in resumen.detalle[:50]:
                self.texto.insert("end", f"  {d}\n")

    def _aplicar(self):
        if not self.archivo_actual:
            messagebox.showwarning("Nada seleccionado", "Elegí primero un archivo de la lista.")
            return
        if not messagebox.askyesno("Confirmar", "¿Aplicar estos cambios a la base de datos maestra? "
                                                 "Esta acción no se puede deshacer."):
            return
        try:
            if self._es_export_caja(self.archivo_actual):
                resumen = reconciliation.aplicar_export_caja(self.archivo_actual, usuario_dev=USUARIO_DEV)
            else:
                resumen = reconciliation.aplicar_export_dueno(self.archivo_actual, usuario_dev=USUARIO_DEV)
        except Exception as e:
            messagebox.showerror("Error aplicando sincronización", str(e))
            return

        msg = (f"Ventas nuevas: {resumen.ventas_nuevas}\n"
               f"Movimientos aplicados: {resumen.movimientos_aplicados}\n"
               f"Precios actualizados: {resumen.precios_actualizados}\n"
               f"Productos nuevos: {resumen.productos_nuevos}\n"
               f"Errores: {len(resumen.errores)}")
        messagebox.showinfo("Sincronización aplicada", msg)
        self._mostrar_dry_run(self.archivo_actual)
