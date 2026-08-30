"""Sistema de Caja - Maestro (PC fija del local).

Pantalla de cobro: buscar producto por código/nombre, armar carrito,
cobrar. El stock NUNCA se muestra ni se puede editar desde acá
(requisito "Stock Invisible").

El carrito se pensó para que lo pueda leer con comodidad el cliente
parado frente al mostrador: nombre del producto en mayúsculas y
negrita, subtotal en negrita y más grande que el resto, y el total en
un cartel grande de alto contraste.

Cada "Quitar línea" queda registrado en Lineas_Eliminadas (auditoría
anti-robo, solo visible desde el Panel del Dueño) antes de sacarla del
carrito. Al cobrar, se imprime el ticket en la impresora configurada (o
se guarda como respaldo en texto si no hay impresora disponible); desde
"Historial de hoy" se puede reimprimir cualquier venta del día las veces
que haga falta.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pos_core.db import init_db
from pos_core import sales, audit, ticket_printer
from apps.theme import (COLORS, aplicar_tema, estriar_treeview, tag_fila,
                         celda_texto, habilitar_copiar_pegar_global, abrir_dialogo_impresora)

ORIGEN = "MAESTRO"
USUARIO = os.environ.get("USERNAME", "cajero")
CODIGO_SIN_BARRA = "1"  # caramelos sueltos, fiambre, o cualquier artículo sin código propio
NOMBRE_SIN_BARRA = "ARTÍCULO SIN CÓDIGO"


class AppCaja(tk.Tk):
    def __init__(self):
        super().__init__()
        aplicar_tema(self)
        self.title("Otter Caja")
        self.geometry("980x760")
        self.carrito = []  # list[{codigo,nombre,cantidad,precio_unitario}]
        self.carrito_seleccionado = None  # código de la línea elegida para "Quitar línea"

        self._construir_ui()
        habilitar_copiar_pegar_global(self)
        self.buscador.focus_set()

    def _construir_ui(self):
        top = ttk.Frame(self, padding=(16, 14))
        top.pack(fill="x")
        fila_titulo = ttk.Frame(top)
        fila_titulo.pack(fill="x")
        ttk.Label(fila_titulo, text="Buscar o escanear (código o nombre)", style="Header.TLabel"
                  ).pack(side="left", anchor="w")
        ttk.Button(fila_titulo, text="Historial de hoy", command=self._abrir_historial
                   ).pack(side="right")
        ttk.Button(fila_titulo, text="Configurar impresora", command=self._configurar_impresora
                   ).pack(side="right", padx=(0, 8))

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
                celda = celda_texto(self.carrito_grid, texto, font=font, color=color, bg=bg, anchor=anchor)
                celda.grid(row=i, column=col, sticky="nsew", ipady=7, padx=(10 if col == 0 else 0, 10))
                celda.bind("<Button-1>", lambda e, c=item["codigo"]: self._seleccionar_linea(c), add="+")

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
            nombre = p["nombre"].upper()
            if p.get("en_oferta"):
                nombre += "  🔥 OFERTA"
            self.resultados.insert("", "end", values=(p["codigo"], nombre, f"{p['precio_venta']:.2f}"),
                                    tags=(tag_fila(i),))

    def _on_buscar(self, event=None):
        termino = self.buscador.get().strip()
        if not termino:
            self._refrescar_resultados([])
            return

        # Código reservado para artículos sin código de barra propio
        # (caramelos sueltos, fiambre, etc.): pide el importe y agrega
        # una línea nueva con precio libre, sin tocar stock de nada.
        if termino == CODIGO_SIN_BARRA:
            self.buscador.delete(0, "end")
            self._agregar_articulo_sin_codigo()
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

        # Si se disparó con un clic en "Buscar" (no con Enter), el foco
        # queda en el botón y el próximo código escaneado se pierde;
        # siempre lo devolvemos acá al buscador.
        self.buscador.focus_set()

    def _configurar_impresora(self):
        top = abrir_dialogo_impresora(self)
        top.bind("<Destroy>", lambda e: self.buscador.focus_set() if e.widget is top else None)

    def _agregar_al_carrito(self, event=None):
        sel = self.resultados.selection()
        if not sel:
            return
        codigo, nombre, precio = self.resultados.item(sel[0], "values")
        nombre = nombre.replace("  🔥 OFERTA", "")
        self._agregar_producto(codigo, nombre, float(precio))

    def _agregar_producto(self, codigo: str, nombre: str, precio: float):
        for item in self.carrito:
            if item["codigo"] == codigo:
                item["cantidad"] += 1
                break
        else:
            self.carrito.append({"codigo": codigo, "nombre": nombre, "cantidad": 1, "precio_unitario": precio})
        self._refrescar_grilla_carrito()
        # Un clic (doble clic en resultados, clic en una celda del carrito)
        # le saca el foco del teclado al buscador; si no se lo devolvemos,
        # el próximo código escaneado no llega a ningún lado.
        self.buscador.focus_set()

    def _agregar_articulo_sin_codigo(self):
        importe = simpledialog.askfloat(
            "Artículo sin código", "Importe a cobrar:", parent=self, minvalue=0.01)
        self.buscador.focus_set()
        if not importe:
            return
        # Nunca se fusiona con otra línea "código 1": cada una puede tener
        # un importe distinto (dos caramelos de precio distinto, etc.).
        self.carrito.append({"codigo": CODIGO_SIN_BARRA, "nombre": NOMBRE_SIN_BARRA,
                              "cantidad": 1, "precio_unitario": importe})
        self._refrescar_grilla_carrito()

    def _quitar_linea(self):
        if not self.carrito_seleccionado:
            messagebox.showinfo("Elegí un producto",
                                 "Hacé clic sobre una línea del carrito y después presioná 'Quitar línea'.")
            return
        item = next((i for i in self.carrito if i["codigo"] == self.carrito_seleccionado), None)
        self.carrito = [i for i in self.carrito if i["codigo"] != self.carrito_seleccionado]
        self.carrito_seleccionado = None
        self._refrescar_grilla_carrito()
        if item:
            try:
                audit.registrar_linea_eliminada(
                    codigo=item["codigo"], nombre=item["nombre"], cantidad=item["cantidad"],
                    precio_unitario=item["precio_unitario"], usuario=USUARIO, origen=ORIGEN)
            except Exception:
                pass  # la auditoría nunca debe bloquear el trabajo del cajero
        self.buscador.focus_set()

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

        self._imprimir_venta(resultado["venta_uuid"], silencioso=True)

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

    def _imprimir_venta(self, venta_uuid: str, *, silencioso: bool = False):
        try:
            info = sales.obtener_venta_con_detalle(venta_uuid)
            texto = ticket_printer.formatear_ticket(info["venta"], info["detalle"])
            enviado, detalle = ticket_printer.imprimir_ticket(texto, venta_uuid=venta_uuid)
        except Exception as e:
            if not silencioso:
                messagebox.showerror("Error al imprimir", str(e))
            return
        if silencioso:
            return  # el cobro ya se confirmó con su propio mensaje; no duplicar avisos
        if enviado:
            messagebox.showinfo("Ticket impreso", f"Enviado a la impresora ({detalle}).")
        else:
            messagebox.showwarning(
                "Sin impresora configurada",
                f"No se encontró una impresora de tickets; se guardó como archivo:\n{detalle}")

    # ------------------------------------------------------------------ #
    # Historial del día: SOLO fecha/hora + reimprimir, nada más. La
    # consulta siempre filtra por la fecha de hoy, así que un día nuevo
    # automáticamente deja de mostrar lo de ayer.
    # ------------------------------------------------------------------ #
    def _abrir_historial(self):
        top = tk.Toplevel(self)
        aplicar_tema(top)
        top.title("Otter - Historial de hoy")
        top.geometry("380x520")
        top.transient(self)
        top.bind("<Destroy>", lambda e: self.buscador.focus_set() if e.widget is top else None)

        ttk.Label(top, text="Ventas de hoy", style="Header.TLabel").pack(anchor="w", padx=16, pady=(16, 8))

        tree = ttk.Treeview(top, columns=("hora", "total"), show="headings", height=16)
        tree.heading("hora", text="Hora")
        tree.heading("total", text="Total")
        tree.column("hora", width=140)
        tree.column("total", width=140, anchor="e")
        estriar_treeview(tree)
        tree.pack(fill="both", expand=True, padx=16)

        ventas = sales.listar_ventas_de_hoy()
        for i, v in enumerate(ventas):
            hora = v["fecha_hora"][11:19] if len(v["fecha_hora"]) >= 19 else v["fecha_hora"]
            tree.insert("", "end", iid=v["uuid_unico"], values=(hora, f"${v['total']:.2f}"), tags=(tag_fila(i),))

        if not ventas:
            ttk.Label(top, text="Todavía no hay ventas hoy.", style="Muted.TLabel").pack(pady=8)

        def _reimprimir(event=None):
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("Elegí una venta", "Hacé clic sobre una venta de la lista.")
                return
            self._imprimir_venta(sel[0])

        tree.bind("<Double-1>", _reimprimir)
        ttk.Button(top, text="Imprimir", style="Accent.TButton", command=_reimprimir
                   ).pack(pady=12)
        habilitar_copiar_pegar_global(top)


if __name__ == "__main__":
    from pos_core.paths import set_base_override_to_parent_dir
    # Caja y Dueño Maestro comparten UNA sola DB (carpeta padre de instalación).
    set_base_override_to_parent_dir()
    init_db()
    AppCaja().mainloop()
