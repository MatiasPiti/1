"""USB Caja - Sistema Portátil de Emergencia.

Idéntico en función a la Caja Maestra, pero:
- Usa su propia base de datos SQLite portable (USB_CAJA/database/stock.db)
  ubicada con rutas relativas (funciona en cualquier letra de unidad).
- Cada venta lleva un UUID único.
- Tiene el botón "Preparar sincronización" que exporta a SYNC_DATA/.
- Muestra siempre el cartel rojo de modo emergencia.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pos_core.db import init_db
from pos_core import sales, sync_export
from apps.theme import COLORS, aplicar_tema, estriar_treeview, tag_fila

ORIGEN = "USB_CAJA"
USUARIO = os.environ.get("USERNAME", "cajero_emergencia")


class AppUsbCaja(tk.Tk):
    def __init__(self):
        super().__init__()
        aplicar_tema(self)
        self.title("Caja - USB EMERGENCIA")
        self.geometry("960x680")
        self.carrito = []

        banner = tk.Label(self, text="⚠  MODO EMERGENCIA PORTÁTIL - DATOS NO SINCRONIZADOS  ⚠",
                           bg=COLORS["danger"], fg="white", font=("Segoe UI", 12, "bold"), pady=8)
        banner.pack(fill="x")

        self._construir_ui()
        self.buscador.focus_set()

    def _construir_ui(self):
        top = ttk.Frame(self, padding=(16, 14))
        top.pack(fill="x")
        fila = ttk.Frame(top)
        fila.pack(fill="x")
        ttk.Label(fila, text="Buscar o escanear:").pack(side="left")
        self.buscador = ttk.Entry(fila, width=40, font=("Segoe UI", 12))
        self.buscador.pack(side="left", padx=8, ipady=3)
        self.buscador.bind("<Return>", self._on_buscar)
        ttk.Button(fila, text="Buscar", command=self._on_buscar).pack(side="left")
        ttk.Button(fila, text="Preparar sincronización", command=self._preparar_sync
                   ).pack(side="right")

        cuerpo = ttk.Frame(self, padding=(16, 0))
        cuerpo.pack(fill="both", expand=True)

        self.resultados = ttk.Treeview(cuerpo, columns=("codigo", "nombre", "precio"), show="headings", height=6)
        for col, txt, w in [("codigo", "Código", 130), ("nombre", "Nombre", 420), ("precio", "Precio", 110)]:
            self.resultados.heading(col, text=txt)
            self.resultados.column(col, width=w)
        estriar_treeview(self.resultados)
        self.resultados.pack(fill="x", pady=(4, 12))
        self.resultados.bind("<Double-1>", self._agregar_al_carrito)

        self.carrito_tree = ttk.Treeview(
            cuerpo, columns=("codigo", "nombre", "cant", "precio", "subtotal"), show="headings", height=11)
        for col, txt, w in [("codigo", "Código", 100), ("nombre", "Nombre", 320),
                             ("cant", "Cant.", 60), ("precio", "P. Unit.", 90), ("subtotal", "Subtotal", 100)]:
            self.carrito_tree.heading(col, text=txt)
            self.carrito_tree.column(col, width=w)
        estriar_treeview(self.carrito_tree)
        self.carrito_tree.pack(fill="both", expand=True)

        bottom = ttk.Frame(self, padding=16, style="Card.TFrame")
        bottom.pack(fill="x", side="bottom")
        self.lbl_total = ttk.Label(bottom, text="TOTAL: $0.00", style="Total.TLabel")
        self.lbl_total.pack(side="left")
        self.metodo_pago = ttk.Combobox(bottom, values=["EFECTIVO", "TARJETA", "TRANSFERENCIA", "MIXTO"],
                                         state="readonly", width=15)
        self.metodo_pago.set("EFECTIVO")
        self.metodo_pago.pack(side="left", padx=16)
        ttk.Button(bottom, text="COBRAR (F12)", style="Accent.TButton", command=self._cobrar).pack(side="right")
        self.bind("<F12>", lambda e: self._cobrar())

    def _refrescar_resultados(self, productos):
        for row in self.resultados.get_children():
            self.resultados.delete(row)
        for i, p in enumerate(productos):
            self.resultados.insert("", "end", values=(p["codigo"], p["nombre"], f"{p['precio_venta']:.2f}"),
                                    tags=(tag_fila(i),))

    def _on_buscar(self, event=None):
        termino = self.buscador.get().strip()
        if not termino:
            self._refrescar_resultados([])
            return
        productos = sales.buscar_productos(termino)
        self._refrescar_resultados(productos)

        if len(productos) == 1 and productos[0]["codigo"] == termino:
            self._agregar_producto(productos[0]["codigo"], productos[0]["nombre"], productos[0]["precio_venta"])
            self.buscador.delete(0, "end")
            self._refrescar_resultados([])

    def _agregar_al_carrito(self, event=None):
        sel = self.resultados.selection()
        if not sel:
            return
        codigo, nombre, precio = self.resultados.item(sel[0], "values")
        self._agregar_producto(codigo, nombre, float(precio))

    def _agregar_producto(self, codigo: str, nombre: str, precio: float):
        for item in self.carrito:
            if item["codigo"] == codigo:
                item["cantidad"] += 1
                break
        else:
            self.carrito.append({"codigo": codigo, "nombre": nombre, "cantidad": 1, "precio_unitario": precio})
        self._refrescar_carrito()

    def _refrescar_carrito(self):
        for row in self.carrito_tree.get_children():
            self.carrito_tree.delete(row)
        total = 0.0
        for i, item in enumerate(self.carrito):
            subtotal = item["cantidad"] * item["precio_unitario"]
            total += subtotal
            self.carrito_tree.insert("", "end", values=(
                item["codigo"], item["nombre"], item["cantidad"],
                f"{item['precio_unitario']:.2f}", f"{subtotal:.2f}"), tags=(tag_fila(i),))
        self.lbl_total.config(text=f"TOTAL: ${total:.2f}")

    def _cobrar(self):
        if not self.carrito:
            messagebox.showwarning("Carrito vacío", "Agregá al menos un producto antes de cobrar.")
            return
        try:
            resultado = sales.cerrar_ticket(
                self.carrito, metodo_pago=self.metodo_pago.get(), usuario=USUARIO, origen=ORIGEN)
        except Exception as e:
            messagebox.showerror("Error al cobrar", str(e))
            return
        messagebox.showinfo("Venta cobrada (offline)",
                             f"Ticket {resultado['venta_uuid'][:8]}... por ${resultado['total']:.2f}\n"
                             f"Recordá 'Preparar sincronización' antes de sacar el USB.")
        self.carrito = []
        self._refrescar_carrito()
        self.buscador.delete(0, "end")
        self.buscador.focus_set()

    def _preparar_sync(self):
        try:
            ruta = sync_export.exportar_caja()
        except Exception as e:
            messagebox.showerror("Error exportando", str(e))
            return
        messagebox.showinfo("Sincronización preparada",
                             f"Se generó:\n{ruta}\n\n"
                             f"Llevá el USB a la PC del desarrollador y usá Ctrl+Shift+M "
                             f"en el Sistema Maestro para conciliar.")


if __name__ == "__main__":
    init_db()
    AppUsbCaja().mainloop()
