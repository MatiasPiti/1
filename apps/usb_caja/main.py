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
from tkinter import ttk, messagebox, simpledialog, filedialog

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pos_core.db import init_db
from pos_core import sales, sync_export, audit, ticket_printer, excel_import
from apps.theme import (COLORS, aplicar_tema, estriar_treeview, tag_fila,
                         habilitar_copiar_pegar_global, abrir_dialogo_impresora)
from apps.caja_carrito import CarritoTecladoMixin

ORIGEN = "USB_CAJA"
USUARIO = os.environ.get("USERNAME", "cajero_emergencia")

# Definidos en pos_core.sales para que la Caja y el núcleo que descuenta
# el stock compartan exactamente el mismo código reservado.
from pos_core.sales import CODIGO_SIN_BARRA, NOMBRE_SIN_BARRA


class AppUsbCaja(CarritoTecladoMixin, tk.Tk):
    def __init__(self):
        super().__init__()
        aplicar_tema(self)
        self.title("Otter Caja (Emergencia)")
        self.geometry("980x800")
        self._init_carrito()

        banner = tk.Label(self, text="⚠  MODO EMERGENCIA PORTÁTIL - DATOS NO SINCRONIZADOS  ⚠",
                           bg=COLORS["danger"], fg="white", font=("Segoe UI", 12, "bold"), pady=8)
        banner.pack(fill="x")

        self._construir_ui()
        # El USB de emergencia nunca factura con ARCA (no hay conexión
        # garantizada): F12 y F5 hacen lo mismo acá. Estas ventas se
        # facturan después desde el Maestro, ya conciliadas.
        self.bind("<F12>", lambda e: self._cobrar_sin_facturar())
        self.bind("<F5>", lambda e: self._cobrar_sin_facturar())
        self._configurar_teclado_carrito()
        habilitar_copiar_pegar_global(self)
        self._ir_a_buscador()

    def _construir_ui(self):
        top = ttk.Frame(self, padding=(16, 14))
        top.pack(fill="x")
        fila = ttk.Frame(top)
        fila.pack(fill="x")
        ttk.Label(fila, text="Buscar o escanear:").pack(side="left")
        self.buscador = ttk.Entry(fila, width=40, font=("Segoe UI", 12))
        self.buscador.pack(side="left", padx=8, ipady=3)
        ttk.Button(fila, text="Buscar", command=self._on_buscar).pack(side="left")
        ttk.Button(fila, text="Historial de hoy", command=self._abrir_historial).pack(side="right", padx=4)
        ttk.Button(fila, text="Preparar sincronización", command=self._preparar_sync
                   ).pack(side="right", padx=4)
        ttk.Button(fila, text="Configurar impresora", command=self._configurar_impresora
                   ).pack(side="right", padx=4)
        ttk.Button(fila, text="Cargar productos (Excel)", command=self._cargar_productos
                   ).pack(side="right", padx=4)

        cuerpo = ttk.Frame(self, padding=(16, 0))
        cuerpo.pack(fill="both", expand=True)

        self.resultados = ttk.Treeview(cuerpo, columns=("codigo", "nombre", "precio"), show="headings", height=5)
        for col, txt, w in [("codigo", "Código", 130), ("nombre", "Nombre", 420), ("precio", "Precio", 110)]:
            self.resultados.heading(col, text=txt)
            self.resultados.column(col, width=w)
        estriar_treeview(self.resultados)
        self.resultados.pack(fill="x", pady=(4, 12))

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

        self.metodo_pago = ttk.Combobox(bottom, values=["EFECTIVO", "TARJETA", "TRANSFERENCIA", "MIXTO"],
                                         state="readonly", width=15)
        self.metodo_pago.set("EFECTIVO")
        self.metodo_pago.pack(side="left", padx=16)
        ttk.Button(bottom, text="Quitar línea (Supr)", command=self._quitar_linea).pack(side="right", padx=4)
        ttk.Button(bottom, text="COBRAR SIN FACTURAR (F12 / F5)", style="Accent.TButton",
                   command=self._cobrar_sin_facturar).pack(side="right")

        self.lbl_ayuda = ttk.Label(self, style="Muted.TLabel",
                                    text=self.TEXTO_AYUDA_TECLADO + "   ·   F5 / F12 cobrar")
        self.lbl_ayuda.pack(fill="x", padx=16, pady=(0, 8))

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

        if termino == CODIGO_SIN_BARRA:
            self.buscador.delete(0, "end")
            self._agregar_articulo_sin_codigo()
            return

        productos = sales.buscar_productos(termino)
        self._refrescar_resultados(productos)

        if len(productos) == 1 and productos[0]["codigo"] == termino:
            self._agregar_producto(productos[0]["codigo"], productos[0]["nombre"], productos[0]["precio_venta"])
            self.buscador.delete(0, "end")
            self._refrescar_resultados([])

        self.buscador.focus_set()

    def _usuario_origen(self):
        return USUARIO, ORIGEN

    def _configurar_impresora(self):
        top = abrir_dialogo_impresora(self)
        top.bind("<Destroy>", lambda e: self.buscador.focus_set() if e.widget is top else None)

    def _cargar_productos(self):
        # El USB Caja tiene su propia base de datos, independiente de la
        # del USB Dueño y de la del Maestro: sin esto, no habría ninguna
        # forma de meterle un catálogo de productos.
        ruta = filedialog.askopenfilename(filetypes=[("Excel/CSV", "*.xlsx *.xlsm *.csv")])
        if not ruta:
            return
        try:
            resultado = excel_import.cargar_masivo(ruta, usuario=USUARIO, origen=ORIGEN)
        except Exception as e:
            messagebox.showerror("Error cargando archivo", str(e))
            self.buscador.focus_set()
            return
        mensaje = f"Creados: {resultado['creados']}  Actualizados: {resultado['actualizados']}"
        if resultado["errores"]:
            mensaje += f"\n\n{len(resultado['errores'])} fila(s) con error:\n"
            mensaje += "\n".join(f"  Fila {e['fila']}: {e['error']}" for e in resultado["errores"][:10])
        messagebox.showinfo("Productos cargados", mensaje)
        self.buscador.focus_set()

    def _agregar_articulo_sin_codigo(self):
        importe = simpledialog.askfloat(
            "Artículo sin código", "Importe a cobrar:", parent=self, minvalue=0.01)
        self._ir_a_buscador()
        if not importe:
            return
        self.carrito.append({"codigo": CODIGO_SIN_BARRA, "nombre": NOMBRE_SIN_BARRA,
                              "cantidad": 1, "precio_unitario": importe})
        self._refrescar_grilla_carrito()

    def _cobrar_sin_facturar(self):
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

        messagebox.showinfo("Venta cobrada (offline)",
                             f"Ticket {resultado['venta_uuid'][:8]}... por ${resultado['total']:.2f}\n"
                             f"Recordá 'Preparar sincronización' antes de sacar el USB.")
        self._init_carrito()
        self._refrescar_grilla_carrito()
        self.buscador.delete(0, "end")
        self._ir_a_buscador()

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
            return
        if enviado:
            messagebox.showinfo("Ticket impreso", f"Enviado a la impresora ({detalle}).")
        else:
            messagebox.showwarning(
                "Sin impresora configurada",
                f"No se encontró una impresora de tickets; se guardó como archivo:\n{detalle}")

    def _abrir_historial(self):
        top = tk.Toplevel(self)
        aplicar_tema(top)
        top.title("Otter - Historial de hoy")
        top.geometry("380x520")
        top.transient(self)

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
            fila = sel[0] if sel else tree.focus()
            if not fila:
                messagebox.showinfo("Elegí una venta",
                                     "Elegí una venta de la lista con las flechas ↑↓ (o con un clic) "
                                     "y presioná Enter.")
                return
            self._imprimir_venta(fila)

        tree.bind("<Double-1>", _reimprimir)
        tree.bind("<Return>", _reimprimir)
        tree.bind("<KP_Enter>", _reimprimir)
        top.bind("<Escape>", lambda e: top.destroy())
        ttk.Button(top, text="Imprimir (Enter)", style="Accent.TButton", command=_reimprimir).pack(pady=12)
        ttk.Label(top, text="↑↓ elegir   ·   Enter imprimir   ·   Esc cerrar",
                  style="Muted.TLabel").pack(pady=(0, 8))
        # Se abre con la lista ya enfocada para poder usarla sin mouse.
        if ventas:
            tree.focus_set()
            tree.selection_set(ventas[0]["uuid_unico"])
            tree.focus(ventas[0]["uuid_unico"])
        habilitar_copiar_pegar_global(top)
        top.bind("<Destroy>", lambda e: self.buscador.focus_set() if e.widget is top else None)

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
