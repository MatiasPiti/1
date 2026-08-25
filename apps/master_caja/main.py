"""Sistema de Caja - Maestro (PC fija del local).

Pantalla de cobro: buscar producto por código/nombre, armar carrito,
cobrar. El stock NUNCA se muestra ni se puede editar desde acá
(requisito "Stock Invisible").
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pos_core.db import init_db
from pos_core import sales

ORIGEN = "MAESTRO"
USUARIO = os.environ.get("USERNAME", "cajero")


class AppCaja(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Caja - Sistema Maestro")
        self.geometry("900x600")
        self.carrito = []  # list[{codigo,nombre,cantidad,precio_unitario}]

        self._construir_ui()
        self.buscador.focus_set()

    def _construir_ui(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Buscar (código o nombre):").pack(side="left")
        self.buscador = ttk.Entry(top, width=40)
        self.buscador.pack(side="left", padx=6)
        self.buscador.bind("<Return>", self._on_buscar)
        ttk.Button(top, text="Buscar", command=self._on_buscar).pack(side="left")

        self.resultados = ttk.Treeview(self, columns=("codigo", "nombre", "precio"), show="headings", height=6)
        for col, txt, w in [("codigo", "Código", 120), ("nombre", "Nombre", 400), ("precio", "Precio", 100)]:
            self.resultados.heading(col, text=txt)
            self.resultados.column(col, width=w)
        self.resultados.pack(fill="x", padx=8)
        self.resultados.bind("<Double-1>", self._agregar_al_carrito)

        ttk.Separator(self).pack(fill="x", pady=6)

        ttk.Label(self, text="Carrito", font=("", 12, "bold")).pack(anchor="w", padx=8)
        self.carrito_tree = ttk.Treeview(
            self, columns=("codigo", "nombre", "cant", "precio", "subtotal"), show="headings", height=12)
        for col, txt, w in [("codigo", "Código", 100), ("nombre", "Nombre", 320),
                             ("cant", "Cant.", 60), ("precio", "P. Unit.", 90), ("subtotal", "Subtotal", 100)]:
            self.carrito_tree.heading(col, text=txt)
            self.carrito_tree.column(col, width=w)
        self.carrito_tree.pack(fill="both", expand=True, padx=8)

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        self.lbl_total = ttk.Label(bottom, text="TOTAL: $0.00", font=("", 16, "bold"))
        self.lbl_total.pack(side="left")

        ttk.Label(bottom, text="Pago:").pack(side="left", padx=(20, 4))
        self.metodo_pago = ttk.Combobox(bottom, values=["EFECTIVO", "TARJETA", "TRANSFERENCIA", "MIXTO"],
                                         state="readonly", width=15)
        self.metodo_pago.set("EFECTIVO")
        self.metodo_pago.pack(side="left")

        ttk.Button(bottom, text="Quitar línea", command=self._quitar_linea).pack(side="right", padx=4)
        ttk.Button(bottom, text="COBRAR (F12)", command=self._cobrar).pack(side="right", padx=4)
        self.bind("<F12>", lambda e: self._cobrar())

    def _on_buscar(self, event=None):
        termino = self.buscador.get().strip()
        for row in self.resultados.get_children():
            self.resultados.delete(row)
        if not termino:
            return
        for p in sales.buscar_productos(termino):
            self.resultados.insert("", "end", values=(p["codigo"], p["nombre"], f"{p['precio_venta']:.2f}"))

    def _agregar_al_carrito(self, event=None):
        sel = self.resultados.selection()
        if not sel:
            return
        codigo, nombre, precio = self.resultados.item(sel[0], "values")
        precio = float(precio)
        for item in self.carrito:
            if item["codigo"] == codigo:
                item["cantidad"] += 1
                break
        else:
            self.carrito.append({"codigo": codigo, "nombre": nombre, "cantidad": 1, "precio_unitario": precio})
        self._refrescar_carrito()

    def _quitar_linea(self):
        sel = self.carrito_tree.selection()
        if not sel:
            return
        codigo = self.carrito_tree.item(sel[0], "values")[0]
        self.carrito = [i for i in self.carrito if i["codigo"] != codigo]
        self._refrescar_carrito()

    def _refrescar_carrito(self):
        for row in self.carrito_tree.get_children():
            self.carrito_tree.delete(row)
        total = 0.0
        for item in self.carrito:
            subtotal = item["cantidad"] * item["precio_unitario"]
            total += subtotal
            self.carrito_tree.insert("", "end", values=(
                item["codigo"], item["nombre"], item["cantidad"],
                f"{item['precio_unitario']:.2f}", f"{subtotal:.2f}"))
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

        if resultado["fallas_stock"]:
            detalle = "\n".join(f"- {f['codigo']}: {f['error']}" for f in resultado["fallas_stock"])
            messagebox.showwarning(
                "Venta registrada con avisos",
                f"El ticket se cobró OK, pero hubo problemas descontando stock:\n{detalle}\n"
                f"El servicio de stock reintentará automáticamente.")
        else:
            messagebox.showinfo("Venta cobrada", f"Ticket {resultado['venta_uuid'][:8]}... "
                                                  f"por ${resultado['total']:.2f}")
        self.carrito = []
        self._refrescar_carrito()
        self.buscador.delete(0, "end")
        self.buscador.focus_set()


if __name__ == "__main__":
    from pos_core.paths import set_base_override_to_parent_dir
    # Caja y Dueño Maestro comparten UNA sola DB (carpeta padre de instalación).
    set_base_override_to_parent_dir()
    init_db()
    AppCaja().mainloop()
