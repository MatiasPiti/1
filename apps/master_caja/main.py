"""Sistema de Caja - Maestro (PC fija del local).

Pantalla de cobro: buscar producto por código/nombre, armar carrito,
cobrar. El stock NUNCA se muestra ni se puede editar desde acá
(requisito "Stock Invisible").

El carrito se pensó para que lo pueda leer con comodidad el cliente
parado frente al mostrador: nombre del producto en mayúsculas y
negrita, subtotal en negrita y más grande que el resto, y el total en
un cartel grande de alto contraste.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pos_core.db import init_db
from pos_core import sales
from apps.theme import COLORS, aplicar_tema, estriar_treeview, tag_fila

ORIGEN = "MAESTRO"
USUARIO = os.environ.get("USERNAME", "cajero")


class AppCaja(tk.Tk):
    def __init__(self):
        super().__init__()
        aplicar_tema(self)
        self.title("Caja - Sistema Maestro")
        self.geometry("980x760")
        self.carrito = []  # list[{codigo,nombre,cantidad,precio_unitario}]
        self.carrito_seleccionado = None  # código de la línea elegida para "Quitar línea"

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

        self.resultados = ttk.Treeview(cuerpo, columns=("codigo", "nombre", "precio"), show="headings", height=5)
        for col, txt, w in [("codigo", "Código", 130), ("nombre", "Nombre", 420), ("precio", "Precio", 110)]:
            self.resultados.heading(col, text=txt)
            self.resultados.column(col, width=w)
        estriar_treeview(self.resultados)
        self.resultados.pack(fill="x", pady=(4, 12))
        self.resultados.bind("<Double-1>", self._agregar_al_carrito)

        ttk.Label(cuerpo, text="Carrito", style="Header.TLabel").pack(anchor="w", pady=(0, 6))
        self._armar_grilla_carrito(cuerpo)

        bottom = ttk.Frame(self, padding=16, style="Card.TFrame")
        bottom.pack(fill="x", side="bottom")

        total_box = tk.Frame(bottom, bg=COLORS["accent"])
        total_box.pack(side="left")
        tk.Label(total_box, text="TOTAL A PAGAR", bg=COLORS["accent"], fg="white",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=20, pady=(10, 0))
        self.lbl_total = tk.Label(total_box, text="$0.00", bg=COLORS["accent"], fg="white",
                                   font=("Segoe UI", 30, "bold"))
        self.lbl_total.pack(anchor="w", padx=20, pady=(0, 12))

        ttk.Label(bottom, text="Pago:").pack(side="left", padx=(28, 6))
        self.metodo_pago = ttk.Combobox(bottom, values=["EFECTIVO", "TARJETA", "TRANSFERENCIA", "MIXTO"],
                                         state="readonly", width=15)
        self.metodo_pago.set("EFECTIVO")
        self.metodo_pago.pack(side="left")

        ttk.Button(bottom, text="Quitar línea", command=self._quitar_linea).pack(side="right", padx=4)
        ttk.Button(bottom, text="COBRAR (F12)", style="Accent.TButton", command=self._cobrar
                   ).pack(side="right", padx=4)
        self.bind("<F12>", lambda e: self._cobrar())

    # ------------------------------------------------------------------ #
    # Grilla del carrito armada a mano (Treeview no permite negrita en
    # una sola columna manteniendo el resto normal dentro de la misma fila)
    # ------------------------------------------------------------------ #
    def _armar_grilla_carrito(self, parent):
        contenedor = ttk.Frame(parent)
        contenedor.pack(fill="both", expand=True, pady=(0, 4))

        canvas = tk.Canvas(contenedor, bg=COLORS["surface"], highlightthickness=1,
                            highlightbackground=COLORS["border"])
        vsb = ttk.Scrollbar(contenedor, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.carrito_grid = tk.Frame(canvas, bg=COLORS["surface"])
        canvas.create_window((0, 0), window=self.carrito_grid, anchor="nw")
        self.carrito_grid.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        self.carrito_grid.grid_columnconfigure(1, weight=1)

        encabezados = [("CÓDIGO", "w"), ("PRODUCTO", "w"), ("CANT.", "center"), ("P. UNIT.", "e"), ("SUBTOTAL", "e")]
        for col, (texto, anchor) in enumerate(encabezados):
            tk.Label(self.carrito_grid, text=texto, bg=COLORS["accent"], fg="white",
                     font=("Segoe UI", 10, "bold"), padx=10, pady=8, anchor=anchor
                     ).grid(row=0, column=col, sticky="nsew")

    def _refrescar_grilla_carrito(self):
        for w in list(self.carrito_grid.grid_slaves()):
            if int(w.grid_info()["row"]) > 0:
                w.destroy()

        total = 0.0
        for i, item in enumerate(self.carrito, start=1):
            subtotal = item["cantidad"] * item["precio_unitario"]
            total += subtotal
            seleccionada = item["codigo"] == self.carrito_seleccionado
            bg = COLORS["accent_light"] if seleccionada else (COLORS["stripe"] if i % 2 == 0 else COLORS["surface"])

            celdas = [
                (item["codigo"], ("Segoe UI", 10), COLORS["muted"], "w"),
                (item["nombre"].upper(), ("Segoe UI", 12, "bold"), COLORS["text"], "w"),
                (str(item["cantidad"]), ("Segoe UI", 11), COLORS["text"], "center"),
                (f"${item['precio_unitario']:.2f}", ("Segoe UI", 10), COLORS["muted"], "e"),
                (f"${subtotal:.2f}", ("Segoe UI", 15, "bold"), COLORS["accent"], "e"),
            ]
            for col, (texto, font, color, anchor) in enumerate(celdas):
                lbl = tk.Label(self.carrito_grid, text=texto, bg=bg, fg=color, font=font,
                               padx=10, pady=7, anchor=anchor)
                lbl.grid(row=i, column=col, sticky="nsew")
                lbl.bind("<Button-1>", lambda e, c=item["codigo"]: self._seleccionar_linea(c))

        self.lbl_total.config(text=f"${total:.2f}")
        return total

    def _seleccionar_linea(self, codigo):
        self.carrito_seleccionado = codigo
        self._refrescar_grilla_carrito()

    # ------------------------------------------------------------------ #
    def _refrescar_resultados(self, productos):
        for row in self.resultados.get_children():
            self.resultados.delete(row)
        for i, p in enumerate(productos):
            self.resultados.insert("", "end", values=(p["codigo"], p["nombre"].upper(), f"{p['precio_venta']:.2f}"),
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
        self._refrescar_grilla_carrito()

    def _quitar_linea(self):
        if not self.carrito_seleccionado:
            messagebox.showinfo("Elegí un producto",
                                 "Hacé clic sobre una línea del carrito y después presioná 'Quitar línea'.")
            return
        self.carrito = [i for i in self.carrito if i["codigo"] != self.carrito_seleccionado]
        self.carrito_seleccionado = None
        self._refrescar_grilla_carrito()

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
        self.carrito_seleccionado = None
        self._refrescar_grilla_carrito()
        self.buscador.delete(0, "end")
        self.buscador.focus_set()


if __name__ == "__main__":
    from pos_core.paths import set_base_override_to_parent_dir
    # Caja y Dueño Maestro comparten UNA sola DB (carpeta padre de instalación).
    set_base_override_to_parent_dir()
    init_db()
    AppCaja().mainloop()
