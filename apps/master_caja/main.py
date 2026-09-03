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
                         habilitar_copiar_pegar_global, abrir_dialogo_impresora)
from apps.caja_carrito import CarritoTecladoMixin

ORIGEN = "MAESTRO"
USUARIO = os.environ.get("USERNAME", "cajero")
# Definidos en pos_core.sales para que la Caja y el núcleo que descuenta
# el stock compartan exactamente el mismo código reservado.
from pos_core.sales import CODIGO_SIN_BARRA, NOMBRE_SIN_BARRA


class AppCaja(CarritoTecladoMixin, tk.Tk):
    def __init__(self):
        super().__init__()
        aplicar_tema(self)
        self.title("Otter Caja")
        self.geometry("980x760")
        self._init_carrito()

        self._construir_ui()
        self._configurar_teclado()
        habilitar_copiar_pegar_global(self)
        self._ir_a_buscador()

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
        ttk.Button(fila_buscar, text="Buscar", command=self._on_buscar).pack(side="left", padx=8)

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

        ttk.Label(bottom, text="Pago:").pack(side="left", padx=(28, 6))
        self.metodo_pago = ttk.Combobox(bottom, values=["EFECTIVO", "TARJETA", "TRANSFERENCIA", "MIXTO"],
                                         state="readonly", width=15)
        self.metodo_pago.set("EFECTIVO")
        self.metodo_pago.pack(side="left")

        ttk.Button(bottom, text="Quitar línea (Supr)", command=self._quitar_linea).pack(side="right", padx=4)
        ttk.Button(bottom, text="COBRAR Y FACTURAR (F12)", style="Accent.TButton",
                   command=self._cobrar_y_facturar).pack(side="right", padx=4)
        ttk.Button(bottom, text="COBRAR SIN FACTURAR (F5)", command=self._cobrar_sin_facturar
                   ).pack(side="right", padx=4)

        self.lbl_ayuda = ttk.Label(
            self, style="Muted.TLabel",
            text=self.TEXTO_AYUDA_TECLADO + "   ·   F5 cobrar   ·   F12 cobrar y facturar")
        self.lbl_ayuda.pack(fill="x", padx=16, pady=(0, 8))

    # ------------------------------------------------------------------ #
    # Manejo por teclado: la caja se usa entera sin mouse.
    #
    # Hay tres "zonas" (el buscador, la lista de resultados y el carrito) y
    # las flechas se mueven dentro de la zona activa, pasando de una a otra
    # al llegar al borde. Enter siempre confirma la acción de la zona donde
    # se está parado. Se centraliza acá, a nivel ventana, en vez de repartir
    # binds por widget: así ninguna tecla queda "muerta" según dónde haya
    # quedado el foco.
    # ------------------------------------------------------------------ #
    def _configurar_teclado(self):
        # Cobro: acá F12 SÍ factura con ARCA (a diferencia de la caja del
        # USB de emergencia, que nunca factura en el momento).
        self.bind("<F12>", lambda e: self._cobrar_y_facturar())
        self.bind("<F5>", lambda e: self._cobrar_sin_facturar())
        # El resto (flechas, Enter, Supr, Esc, F2/F3/F4) es común a las dos
        # cajas y vive en apps/caja_carrito.py.
        self._configurar_teclado_carrito()

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

    def _usuario_origen(self):
        return USUARIO, ORIGEN

    def _configurar_impresora(self):
        top = abrir_dialogo_impresora(self)
        top.bind("<Destroy>", lambda e: self._volver_del_dialogo() if e.widget is top else None)

    def _agregar_articulo_sin_codigo(self):
        importe = simpledialog.askfloat(
            "Artículo sin código", "Importe a cobrar:", parent=self, minvalue=0.01)
        self.buscador.focus_set()
        if not importe:
            return
        # Nunca se fusiona con otra línea "código 1": cada una puede tener
        # un importe distinto (dos caramelos de precio distinto, etc.).
        # Cada artículo suelto es su propia línea, con su propio id: nunca
        # se fusiona con otro, porque cada uno tiene su importe.
        self.carrito.append(self._nueva_linea(CODIGO_SIN_BARRA, NOMBRE_SIN_BARRA, importe))
        self._refrescar_grilla_carrito()

    def _cobrar_sin_facturar(self):
        # Si el cajero dejó abierto el campo de cantidad y fue directo a
        # cobrar, se aplica lo que escribió antes de calcular el total.
        self._confirmar_edicion_pendiente()
        if not self.carrito:
            messagebox.showwarning("Carrito vacío", "Agregá al menos un producto antes de cobrar.")
            return
        try:
            resultado = sales.cerrar_ticket(
                self.carrito, metodo_pago=self.metodo_pago.get(), usuario=USUARIO, origen=ORIGEN)
        except Exception as e:
            messagebox.showerror("Error al cobrar", str(e))
            return

        mensaje = f"Ticket {resultado['venta_uuid'][:8]}... por ${resultado['total']:.2f}\n(cobrado sin factura)"
        self._finalizar_cobro(resultado, mensaje)

    def _cobrar_y_facturar(self):
        # Si el cajero dejó abierto el campo de cantidad y fue directo a
        # cobrar, se aplica lo que escribió antes de calcular el total.
        self._confirmar_edicion_pendiente()
        if not self.carrito:
            messagebox.showwarning("Carrito vacío", "Agregá al menos un producto antes de cobrar.")
            return
        try:
            resultado = sales.cerrar_ticket(
                self.carrito, metodo_pago=self.metodo_pago.get(), usuario=USUARIO, origen=ORIGEN)
        except Exception as e:
            messagebox.showerror("Error al cobrar", str(e))
            return

        # La venta ya quedó registrada acá arriba: si la factura falla,
        # NUNCA se deshace el cobro, solo se avisa que hay que facturarla
        # después (ver Panel del Dueño, pestaña "Facturación ARCA").
        try:
            factura = sales.facturar_venta_arca(resultado["venta_uuid"])
            mensaje = (f"Ticket {resultado['venta_uuid'][:8]}... por ${resultado['total']:.2f}\n"
                       f"Factura {factura['tipo_comprobante']} Nº {factura['numero_comprobante']}\n"
                       f"CAE: {factura['cae']}")
        except Exception as e:
            # Cualquier falla facturando (ArcaError o algo inesperado): la
            # venta YA se cobró arriba y no se deshace acá. Atajamos
            # cualquier tipo de excepción, no solo ArcaError, para nunca
            # dejar al cajero con el carrito colgado y sin ningún aviso
            # después de haber cobrado de verdad.
            mensaje = (f"Ticket {resultado['venta_uuid'][:8]}... por ${resultado['total']:.2f}\n\n"
                       f"La venta se cobró OK, pero NO se pudo facturar con ARCA:\n{e}\n\n"
                       f"Se puede reintentar después desde el Panel del Dueño "
                       f"(pestaña 'Facturación ARCA').")

        self._finalizar_cobro(resultado, mensaje)

    def _finalizar_cobro(self, resultado: dict, mensaje: str):
        self._imprimir_venta(resultado["venta_uuid"], silencioso=True)

        if resultado["fallas_stock"]:
            detalle = "\n".join(f"- {f['codigo']}: {f['error']}" for f in resultado["fallas_stock"])
            mensaje += (f"\n\n(Hubo problemas descontando stock; el servicio los reintenta "
                        f"solo:\n{detalle})")
            messagebox.showwarning("Venta registrada con avisos", mensaje)
        else:
            messagebox.showinfo("Venta cobrada", mensaje)

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
        top.bind("<Destroy>", lambda e: self._volver_del_dialogo() if e.widget is top else None)

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
        ttk.Button(top, text="Imprimir (Enter)", style="Accent.TButton", command=_reimprimir
                   ).pack(pady=12)
        ttk.Label(top, text="↑↓ elegir   ·   Enter imprimir   ·   Esc cerrar",
                  style="Muted.TLabel").pack(pady=(0, 8))

        # Se abre con la lista ya enfocada y la primera venta marcada: así
        # se navega con las flechas sin tocar el mouse.
        if ventas:
            tree.focus_set()
            tree.selection_set(ventas[0]["uuid_unico"])
            tree.focus(ventas[0]["uuid_unico"])
        habilitar_copiar_pegar_global(top)


if __name__ == "__main__":
    from pos_core.paths import set_base_override_to_parent_dir
    # Caja y Dueño Maestro comparten UNA sola DB (carpeta padre de instalación).
    set_base_override_to_parent_dir()
    init_db()
    AppCaja().mainloop()
