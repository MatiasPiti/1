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
from apps.theme import aplicar_tema, estriar_treeview, tag_fila

ORIGEN = "MAESTRO"
USUARIO = os.environ.get("USERNAME", "cajero")


class AppCaja(tk.Tk):
    def __init__(self):
        super().__init__()
        aplicar_tema(self)
        self.title("Caja - Sistema Maestro")
        self.geometry("960x640")
        self.carrito = []  # list[{codigo,nombre,cantidad,precio_unitario}]

        self._construir_ui()
        self.buscador.focus_set()

    def _construir_ui(self):
        top = ttk.Frame(self, padding=(16, 14))
        top.pack(fill="x")
        ttk.Label(top, text="Buscar o escanear (código o nombre)", style="Header.TLabel").pack(anchor="w")
        fila_buscar = ttk.Frame(top)
        fila_buscar.pack(fill="x", pady=(8, 0))
        self.buscador = ttk.Entry(fila_buscar, width=46, font=("Segoe UI", 12))
        self.buscador.pack(side="left", ipady=3)
        self.buscador.bind("<Return>", self._on_buscar)
        ttk.Button(fila_buscar, text="Buscar", command=self._on_buscar).pack(side="left", padx=8)

        cuerpo = ttk.Frame(self, padding=(16, 0))
        cuerpo.pack(fill="both", expand=True)

        self.resultados = ttk.Treeview(cuerpo, columns=("codigo", "nombre", "precio"), show="headings", height=6)
        for col, txt, w in [("codigo", "Código", 130), ("nombre", "Nombre", 420), ("precio", "Precio", 110)]:
            self.resultados.heading(col, text=txt)
            self.resultados.column(col, width=w)
        estriar_treeview(self.resultados)
        self.resultados.pack(fill="x", pady=(4, 12))
        self.resultados.bind("<Double-1>", self._agregar_al_carrito)

        ttk.Label(cuerpo, text="Carrito", style="Header.TLabel").pack(anchor="w", pady=(0, 6))
        self.carrito_tree = ttk.Treeview(
            cuerpo, columns=("codigo", "nombre", "cant", "precio", "subtotal"), show="headings", height=11)
        for col, txt, w in [("codigo", "Código", 110), ("nombre", "Nombre", 340),
                             ("cant", "Cant.", 70), ("precio", "P. Unit.", 100), ("subtotal", "Subtotal", 110)]:
            self.carrito_tree.heading(col, text=txt)
            self.carrito_tree.column(col, width=w)
        estriar_treeview(self.carrito_tree)
        self.carrito_tree.pack(fill="both", expand=True)

        bottom = ttk.Frame(self, padding=16, style="Card.TFrame")
        bottom.pack(fill="x", side="bottom")
        self.lbl_total = ttk.Label(bottom, text="TOTAL: $0.00", style="Total.TLabel")
        self.lbl_total.pack(side="left")

        ttk.Label(bottom, text="Pago:").pack(side="left", padx=(28, 6))
        self.metodo_pago = ttk.Combobox(bottom, values=["EFECTIVO", "TARJETA", "TRANSFERENCIA", "MIXTO"],
                                         state="readonly", width=15)
        self.metodo_pago.set("EFECTIVO")
        self.metodo_pago.pack(side="left")

        ttk.Button(bottom, text="Quitar línea", command=self._quitar_linea).pack(side="right", padx=4)
        ttk.Button(bottom, text="COBRAR (F12)", style="Accent.TButton", command=self._cobrar
                   ).pack(side="right", padx=4)
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

        # Lector de código de barras: coincidencia exacta de código con un
        # único resultado -> se agrega directo al carrito, sin esperar el
        # doble clic (así se comporta una caja real ante un escaneo).
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
