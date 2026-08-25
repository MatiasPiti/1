"""Panel del Dueño - Maestro (PC fija o PC del dueño).

Acceso total: dashboard, stock (manual/PDF/lector), filtros + edición
masiva, carga Excel inicial, configuración de alertas de Telegram, y el
módulo oculto de sincronización/conciliación (Ctrl+Shift+M) para cuando
se conecta un USB de emergencia.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pos_core.db import init_db, get_connection
from pos_core import stock_service, bulk_edit, pdf_import, excel_import, filters, config, alerts
from pos_core.paths import sync_dir
from pos_core import reconciliation
from apps.theme import aplicar_tema, estriar_treeview, tag_fila

USUARIO = os.environ.get("USERNAME", "dueño")
ORIGEN = "MAESTRO"


class AppDueno(tk.Tk):
    def __init__(self):
        super().__init__()
        aplicar_tema(self)
        self.title("Panel del Dueño - Sistema Maestro")
        self.geometry("1180x760")

        # Estado del filtro que se está armando/editando en la pestaña de
        # Filtros: lista ordenada de códigos elegidos a mano por el dueño.
        self.filtro_codigos = []
        self.filtro_nombres = {}  # codigo -> (nombre, precio_venta), cache para no reconsultar la DB

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        self.tab_dashboard = ttk.Frame(nb)
        self.tab_stock = ttk.Frame(nb)
        self.tab_bulk = ttk.Frame(nb)
        self.tab_pdf = ttk.Frame(nb)
        self.tab_excel = ttk.Frame(nb)
        self.tab_alertas = ttk.Frame(nb)

        nb.add(self.tab_dashboard, text="Dashboard")
        nb.add(self.tab_stock, text="Stock")
        nb.add(self.tab_bulk, text="Filtros / Edición Masiva")
        nb.add(self.tab_pdf, text="Facturas PDF")
        nb.add(self.tab_excel, text="Carga Excel")
        nb.add(self.tab_alertas, text="Alertas")

        self._armar_dashboard(self.tab_dashboard)
        self._armar_stock(self.tab_stock)
        self._armar_bulk(self.tab_bulk)
        self._armar_pdf(self.tab_pdf)
        self._armar_excel(self.tab_excel)
        self._armar_alertas(self.tab_alertas)

        # Módulo oculto de sincronización/conciliación (desarrollador)
        self.bind_all("<Control-Shift-M>", self._abrir_panel_sync)

    # ------------------------------------------------------------------ #
    # Dashboard
    # ------------------------------------------------------------------ #
    def _armar_dashboard(self, frame):
        top = ttk.Frame(frame, padding=(4, 4, 4, 12))
        top.pack(fill="x")
        ttk.Button(top, text="Actualizar", command=lambda: self._refrescar_dashboard()).pack(side="left")

        self.lbl_resumen = ttk.Label(top, text="", style="Header.TLabel")
        self.lbl_resumen.pack(side="left", padx=20)

        self.chart_frame = ttk.Frame(frame, style="Card.TFrame")
        self.chart_frame.pack(fill="both", expand=True)
        self._refrescar_dashboard()

    def _refrescar_dashboard(self):
        conn = get_connection()
        total_hoy = conn.execute(
            "SELECT COALESCE(SUM(total),0) t, COUNT(*) c FROM Ventas "
            "WHERE date(fecha_hora) = date('now','localtime') AND anulada = 0"
        ).fetchone()
        self.lbl_resumen.config(
            text=f"Ventas de hoy: {total_hoy['c']}   ·   Total: ${total_hoy['t']:.2f}")

        top_productos = conn.execute(
            """SELECT producto_nombre, SUM(cantidad) cant FROM Detalle_Ventas
               GROUP BY producto_codigo ORDER BY cant DESC LIMIT 8"""
        ).fetchall()

        for w in self.chart_frame.winfo_children():
            w.destroy()
        try:
            import matplotlib
            matplotlib.use("TkAgg")
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
            from apps.theme import COLORS

            fig = Figure(figsize=(9, 5), dpi=100, facecolor=COLORS["surface"])
            ax = fig.add_subplot(111, facecolor=COLORS["surface"])
            nombres = [r["producto_nombre"][:18] for r in top_productos] or ["(sin ventas aún)"]
            cantidades = [r["cant"] for r in top_productos] or [0]
            ax.barh(nombres, cantidades, color=COLORS["accent"])
            ax.set_title("Productos más vendidos (histórico)", color=COLORS["text"])
            ax.tick_params(colors=COLORS["text"])
            for spine in ax.spines.values():
                spine.set_color(COLORS["border"])
            ax.invert_yaxis()
            fig.tight_layout()
            FigureCanvasTkAgg(fig, master=self.chart_frame).get_tk_widget().pack(fill="both", expand=True)
        except ImportError:
            ttk.Label(self.chart_frame, text="(instalá matplotlib para ver gráficos: pip install matplotlib)",
                      style="Muted.TLabel").pack(pady=20)

    # ------------------------------------------------------------------ #
    # Stock manual / lector
    # ------------------------------------------------------------------ #
    def _armar_stock(self, frame):
        form = ttk.LabelFrame(frame, text="Movimiento manual de stock", padding=12)
        form.pack(fill="x", pady=(0, 10))

        ttk.Label(form, text="Código:").grid(row=0, column=0, sticky="w")
        self.stock_codigo = ttk.Entry(form, width=25)
        self.stock_codigo.grid(row=0, column=1, padx=6)

        ttk.Label(form, text="Cantidad:").grid(row=0, column=2, sticky="w")
        self.stock_cantidad = ttk.Entry(form, width=10)
        self.stock_cantidad.insert(0, "1")
        self.stock_cantidad.grid(row=0, column=3, padx=6)

        ttk.Button(form, text="+ Sumar (Entrada)", command=self._sumar_stock).grid(row=0, column=4, padx=4)
        ttk.Button(form, text="− Restar (Salida)", style="Danger.TButton", command=self._restar_stock
                   ).grid(row=0, column=5, padx=4)

        lector = ttk.LabelFrame(frame, text="Lector USB (foco acá y escaneá para restar 1 unidad)", padding=12)
        lector.pack(fill="x", pady=(0, 10))
        self.lector_entry = ttk.Entry(lector, width=40, font=("Segoe UI", 13))
        self.lector_entry.pack(side="left", padx=6, ipady=2)
        self.lector_entry.bind("<Return>", self._on_lectura_scanner)
        ttk.Button(lector, text="Enfocar lector", command=lambda: self.lector_entry.focus_set()
                   ).pack(side="left")

        self.stock_log = tk.Text(frame, height=14, bg="#FFFFFF", relief="flat",
                                  highlightthickness=1, highlightbackground="#E3E6ED", padx=8, pady=8)
        self.stock_log.pack(fill="both", expand=True)

    def _log_stock(self, msg):
        self.stock_log.insert("end", msg + "\n")
        self.stock_log.see("end")

    def _sumar_stock(self):
        try:
            nuevo = stock_service.sumar_stock_manual(
                self.stock_codigo.get().strip(), int(self.stock_cantidad.get()),
                usuario=USUARIO, origen=ORIGEN)
            self._log_stock(f"[+] {self.stock_codigo.get()} -> nuevo stock: {nuevo}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _restar_stock(self):
        try:
            nuevo = stock_service.restar_stock_manual(
                self.stock_codigo.get().strip(), int(self.stock_cantidad.get()),
                usuario=USUARIO, origen=ORIGEN)
            self._log_stock(f"[-] {self.stock_codigo.get()} -> nuevo stock: {nuevo}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _on_lectura_scanner(self, event=None):
        codigo = self.lector_entry.get().strip()
        self.lector_entry.delete(0, "end")
        if not codigo:
            return
        try:
            nuevo = stock_service.restar_stock_por_lector(codigo, usuario=USUARIO, origen=ORIGEN)
            self._log_stock(f"[lector] {codigo} -> nuevo stock: {nuevo}")
        except Exception as e:
            self._log_stock(f"[lector] ERROR con {codigo}: {e}")

    # ------------------------------------------------------------------ #
    # Filtros 100% manuales (el dueño elige producto por producto) +
    # edición masiva sobre el filtro armado/cargado
    # ------------------------------------------------------------------ #
    def _armar_bulk(self, frame):
        guardados = ttk.LabelFrame(frame, text="Filtros guardados", padding=10)
        guardados.pack(fill="x", pady=(0, 10))
        self.lista_filtros = tk.Listbox(guardados, height=4, exportselection=False,
                                         relief="flat", highlightthickness=1,
                                         highlightbackground="#E3E6ED")
        self.lista_filtros.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ttk.Button(guardados, text="Cargar", command=self._cargar_filtro_guardado).pack(side="left", padx=2)
        ttk.Button(guardados, text="Nuevo (vaciar)", command=self._nuevo_filtro).pack(side="left", padx=2)
        ttk.Button(guardados, text="Eliminar", style="Danger.TButton", command=self._eliminar_filtro_guardado
                   ).pack(side="left", padx=2)
        self._refrescar_lista_filtros()

        picker = ttk.Frame(frame)
        picker.pack(fill="both", expand=True, pady=(0, 10))

        izq = ttk.LabelFrame(picker, text="Todos los productos  (doble clic = agregar)", padding=8)
        izq.pack(side="left", fill="both", expand=True, padx=(0, 6))
        buscador_fila = ttk.Frame(izq)
        buscador_fila.pack(fill="x", pady=(0, 6))
        self.picker_buscar = ttk.Entry(buscador_fila)
        self.picker_buscar.pack(side="left", fill="x", expand=True)
        self.picker_buscar.bind("<KeyRelease>", lambda e: self._refrescar_picker_todos())
        self.picker_todos = ttk.Treeview(izq, columns=("codigo", "nombre"), show="headings", height=12)
        self.picker_todos.heading("codigo", text="Código")
        self.picker_todos.heading("nombre", text="Nombre")
        self.picker_todos.column("codigo", width=110)
        self.picker_todos.column("nombre", width=260)
        estriar_treeview(self.picker_todos)
        self.picker_todos.pack(fill="both", expand=True)
        self.picker_todos.bind("<Double-1>", self._agregar_a_filtro)

        der = ttk.LabelFrame(picker, text="En este filtro  (doble clic = quitar)", padding=8)
        der.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.bulk_tree = ttk.Treeview(der, columns=("codigo", "nombre", "precio"), show="headings", height=13)
        for col, txt, w in [("codigo", "Código", 100), ("nombre", "Nombre", 220), ("precio", "Precio", 90)]:
            self.bulk_tree.heading(col, text=txt)
            self.bulk_tree.column(col, width=w)
        estriar_treeview(self.bulk_tree)
        self.bulk_tree.pack(fill="both", expand=True)
        self.bulk_tree.bind("<Double-1>", self._quitar_de_filtro)

        guardar_fila = ttk.Frame(frame)
        guardar_fila.pack(fill="x", pady=(0, 10))
        ttk.Label(guardar_fila, text="Nombre del filtro:").pack(side="left")
        self.nombre_filtro = ttk.Entry(guardar_fila, width=30)
        self.nombre_filtro.pack(side="left", padx=6)
        ttk.Button(guardar_fila, text="Guardar filtro", style="Accent.TButton",
                   command=self._guardar_filtro_actual).pack(side="left")

        acciones = ttk.LabelFrame(frame, text="Aplicar a los productos del filtro (o a los que selecciones)",
                                   padding=10)
        acciones.pack(fill="x")
        ttk.Label(acciones, text="% (ej. 3 o -5):").grid(row=0, column=0)
        self.bulk_pct = ttk.Entry(acciones, width=8)
        self.bulk_pct.grid(row=0, column=1, padx=4)
        ttk.Button(acciones, text="Aplicar %", style="Accent.TButton", command=self._aplicar_bulk_pct
                   ).grid(row=0, column=2, padx=8)

        ttk.Label(acciones, text="$ fijo (ej. 100 o -50):").grid(row=0, column=3)
        self.bulk_fijo = ttk.Entry(acciones, width=8)
        self.bulk_fijo.grid(row=0, column=4, padx=4)
        ttk.Button(acciones, text="Aplicar $", command=self._aplicar_bulk_fijo).grid(row=0, column=5, padx=8)

        self._refrescar_picker_todos()

    def _refrescar_picker_todos(self):
        termino = self.picker_buscar.get().strip()
        for row in self.picker_todos.get_children():
            self.picker_todos.delete(row)
        conn = get_connection()
        if termino:
            like = f"%{termino}%"
            rows = conn.execute(
                "SELECT codigo, nombre, precio_venta FROM Productos WHERE activo=1 "
                "AND (codigo LIKE ? OR nombre LIKE ?) ORDER BY nombre LIMIT 200", (like, like)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT codigo, nombre, precio_venta FROM Productos WHERE activo=1 ORDER BY nombre LIMIT 200"
            ).fetchall()
        for i, p in enumerate(rows):
            self.filtro_nombres[p["codigo"]] = (p["nombre"], p["precio_venta"])
            self.picker_todos.insert("", "end", values=(p["codigo"], p["nombre"]), tags=(tag_fila(i),))

    def _refrescar_bulk_tree(self):
        for row in self.bulk_tree.get_children():
            self.bulk_tree.delete(row)
        for i, codigo in enumerate(self.filtro_codigos):
            nombre, precio = self.filtro_nombres.get(codigo, ("?", 0))
            self.bulk_tree.insert("", "end", values=(codigo, nombre, f"{precio:.2f}"), tags=(tag_fila(i),))

    def _agregar_a_filtro(self, event=None):
        sel = self.picker_todos.selection()
        if not sel:
            return
        codigo = self.picker_todos.item(sel[0], "values")[0]
        if codigo not in self.filtro_codigos:
            self.filtro_codigos.append(codigo)
            self._refrescar_bulk_tree()

    def _quitar_de_filtro(self, event=None):
        sel = self.bulk_tree.selection()
        if not sel:
            return
        codigo = self.bulk_tree.item(sel[0], "values")[0]
        self.filtro_codigos = [c for c in self.filtro_codigos if c != codigo]
        self._refrescar_bulk_tree()

    def _nuevo_filtro(self):
        self.filtro_codigos = []
        self.nombre_filtro.delete(0, "end")
        self._refrescar_bulk_tree()

    def _refrescar_lista_filtros(self):
        self.lista_filtros.delete(0, "end")
        for f in filters.listar_filtros_guardados():
            self.lista_filtros.insert("end", f["nombre"])

    def _cargar_filtro_guardado(self):
        sel = self.lista_filtros.curselection()
        if not sel:
            return
        nombre = self.lista_filtros.get(sel[0])
        guardados = {f["nombre"]: f["definicion"] for f in filters.listar_filtros_guardados()}
        definicion = guardados.get(nombre)
        if definicion is None:
            return
        try:
            productos = filters.aplicar_filtro(definicion)
        except Exception as e:
            messagebox.showerror("Error cargando filtro", str(e))
            return
        self.filtro_codigos = [p["codigo"] for p in productos]
        for p in productos:
            self.filtro_nombres[p["codigo"]] = (p["nombre"], p["precio_venta"])
        self.nombre_filtro.delete(0, "end")
        self.nombre_filtro.insert(0, nombre)
        self._refrescar_bulk_tree()

    def _eliminar_filtro_guardado(self):
        sel = self.lista_filtros.curselection()
        if not sel:
            return
        nombre = self.lista_filtros.get(sel[0])
        if not messagebox.askyesno("Confirmar", f"¿Eliminar el filtro '{nombre}'?"):
            return
        filters.eliminar_filtro(nombre)
        self._refrescar_lista_filtros()

    def _guardar_filtro_actual(self):
        nombre = self.nombre_filtro.get().strip()
        if not nombre:
            messagebox.showwarning("Falta el nombre", "Ponele un nombre al filtro antes de guardarlo.")
            return
        if not self.filtro_codigos:
            messagebox.showwarning("Filtro vacío", "Agregá al menos un producto (doble clic en la lista de la izquierda).")
            return
        filters.guardar_filtro_manual(nombre, self.filtro_codigos)
        self._refrescar_lista_filtros()
        messagebox.showinfo("Guardado", f"Filtro '{nombre}' guardado con {len(self.filtro_codigos)} productos.")

    def _codigos_seleccionados(self):
        sel = self.bulk_tree.selection()
        if sel:
            return [self.bulk_tree.item(i, "values")[0] for i in sel]
        return list(self.filtro_codigos)

    def _aplicar_bulk_pct(self):
        codigos = self._codigos_seleccionados()
        if not codigos:
            messagebox.showwarning("Nada para aplicar", "Agregá productos al filtro primero.")
            return
        try:
            pct = float(self.bulk_pct.get())
        except ValueError:
            messagebox.showerror("Error", "Porcentaje inválido")
            return
        resultados = bulk_edit.aplicar_ajuste_masivo(codigos, porcentaje=pct, usuario=USUARIO, origen=ORIGEN)
        self._mostrar_resultado_bulk(resultados)

    def _aplicar_bulk_fijo(self):
        codigos = self._codigos_seleccionados()
        if not codigos:
            messagebox.showwarning("Nada para aplicar", "Agregá productos al filtro primero.")
            return
        try:
            monto = float(self.bulk_fijo.get())
        except ValueError:
            messagebox.showerror("Error", "Monto inválido")
            return
        resultados = bulk_edit.aplicar_ajuste_masivo(codigos, monto_fijo=monto, usuario=USUARIO, origen=ORIGEN)
        self._mostrar_resultado_bulk(resultados)

    def _mostrar_resultado_bulk(self, resultados):
        ok = [r for r in resultados if r["ok"]]
        detalle = "\n".join(f"{r['codigo']}: ${r['precio_anterior']:.2f} -> ${r['precio_nuevo']:.2f}" for r in ok)
        messagebox.showinfo("Edición masiva aplicada", f"{len(ok)} productos actualizados.\n\n{detalle[:1500]}")
        for codigo in [r["codigo"] for r in ok]:
            if codigo in self.filtro_nombres:
                nombre, _ = self.filtro_nombres[codigo]
                nuevo = next(r["precio_nuevo"] for r in ok if r["codigo"] == codigo)
                self.filtro_nombres[codigo] = (nombre, nuevo)
        self._refrescar_bulk_tree()

    # ------------------------------------------------------------------ #
    # PDF
    # ------------------------------------------------------------------ #
    def _armar_pdf(self, frame):
        ttk.Button(frame, text="Subir factura PDF...", style="Accent.TButton",
                   command=self._subir_pdf).pack(pady=(0, 10), anchor="w")
        self.pdf_log = tk.Text(frame, height=25, bg="#FFFFFF", relief="flat",
                                highlightthickness=1, highlightbackground="#E3E6ED", padx=8, pady=8)
        self.pdf_log.pack(fill="both", expand=True)

    def _subir_pdf(self):
        ruta = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if not ruta:
            return
        try:
            resultado = pdf_import.parsear_factura_pdf(ruta)
        except Exception as e:
            messagebox.showerror("Error leyendo el PDF", str(e))
            return

        if resultado.es_pdf_escaneado:
            messagebox.showwarning(
                "PDF sin texto (posible imagen escaneada)",
                "No se pudo extraer texto del PDF. Puede ser una imagen escaneada.\n"
                "Cargá los ítems manualmente desde la pestaña 'Stock'.")
            return

        self.pdf_log.delete("1.0", "end")
        items = [{"codigo": i.codigo, "cantidad": i.cantidad, "precio_compra": i.precio_compra}
                 for i in resultado.items]
        self.pdf_log.insert("end", f"Ítems detectados: {len(items)}\n")
        for i in resultado.items:
            self.pdf_log.insert("end", f"  {i.codigo} | {i.nombre} | x{i.cantidad} | ${i.precio_compra}\n")
        if resultado.lineas_no_reconocidas:
            self.pdf_log.insert("end", f"\nLíneas NO reconocidas ({len(resultado.lineas_no_reconocidas)}), "
                                        f"revisar/cargar manualmente:\n")
            for l in resultado.lineas_no_reconocidas:
                self.pdf_log.insert("end", f"  ? {l}\n")

        if items and messagebox.askyesno("Confirmar", f"¿Aplicar {len(items)} ítems al stock?"):
            resultados = stock_service.sumar_stock_por_factura_pdf(
                items, usuario=USUARIO, factura_nombre=os.path.basename(ruta), origen=ORIGEN)
            fallidos = [r for r in resultados if not r["ok"]]
            self.pdf_log.insert("end", f"\nAplicado. Fallos: {len(fallidos)}\n")
            for r in fallidos:
                self.pdf_log.insert("end", f"  ERROR {r['codigo']}: {r['error']}\n")

    # ------------------------------------------------------------------ #
    # Excel
    # ------------------------------------------------------------------ #
    def _armar_excel(self, frame):
        ttk.Label(frame, text="Columnas esperadas: Código | Nombre | Precio Venta | Stock Inicial | Proveedor",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Button(frame, text="Subir Excel/CSV...", style="Accent.TButton",
                   command=self._subir_excel).pack(pady=(0, 8), anchor="w")
        self.excel_log = tk.Text(frame, height=25, bg="#FFFFFF", relief="flat",
                                  highlightthickness=1, highlightbackground="#E3E6ED", padx=8, pady=8)
        self.excel_log.pack(fill="both", expand=True)

    def _subir_excel(self):
        ruta = filedialog.askopenfilename(filetypes=[("Excel/CSV", "*.xlsx *.xlsm *.csv")])
        if not ruta:
            return
        try:
            resultado = excel_import.cargar_masivo(ruta, usuario=USUARIO, origen=ORIGEN)
        except Exception as e:
            messagebox.showerror("Error cargando archivo", str(e))
            return
        self.excel_log.delete("1.0", "end")
        self.excel_log.insert("end", f"Creados: {resultado['creados']}  Actualizados: {resultado['actualizados']}\n")
        for e in resultado["errores"]:
            self.excel_log.insert("end", f"  Fila {e['fila']}: {e['error']}\n")

    # ------------------------------------------------------------------ #
    # Alertas: Telegram + umbral global + umbral personalizado por producto
    # ------------------------------------------------------------------ #
    def _armar_alertas(self, frame):
        cfg = config.cargar_config()
        form = ttk.LabelFrame(frame, text="Configuración del Bot de Telegram", padding=12)
        form.pack(fill="x", pady=(0, 10))

        ttk.Label(form, text="Bot Token:").grid(row=0, column=0, sticky="w")
        self.tg_token = ttk.Entry(form, width=50)
        self.tg_token.insert(0, cfg.get("telegram", "bot_token", fallback=""))
        self.tg_token.grid(row=0, column=1, padx=6)

        ttk.Label(form, text="Chat ID por defecto:").grid(row=1, column=0, sticky="w")
        self.tg_chat = ttk.Entry(form, width=30)
        self.tg_chat.insert(0, cfg.get("telegram", "chat_id_default", fallback=""))
        self.tg_chat.grid(row=1, column=1, sticky="w", padx=6)

        self.tg_habilitado = tk.BooleanVar(value=cfg.get("telegram", "habilitado", fallback="false") == "true")
        ttk.Checkbutton(form, text="Habilitado", variable=self.tg_habilitado).grid(row=2, column=1, sticky="w")

        ttk.Button(form, text="Guardar", style="Accent.TButton", command=self._guardar_config_telegram
                   ).grid(row=3, column=1, pady=(8, 0), sticky="w")

        umbrales = ttk.LabelFrame(frame, text="Umbral global por defecto (aplica a todo producto sin umbral propio)",
                                   padding=12)
        umbrales.pack(fill="x", pady=(0, 10))
        ttk.Label(umbrales, text="Stock mínimo (stoploss):").grid(row=0, column=0)
        self.um_min = ttk.Entry(umbrales, width=8)
        self.um_min.grid(row=0, column=1, padx=4)
        ttk.Label(umbrales, text="Stock máximo (sobre-stock):").grid(row=0, column=2)
        self.um_max = ttk.Entry(umbrales, width=8)
        self.um_max.grid(row=0, column=3, padx=4)
        ttk.Button(umbrales, text="Guardar umbrales globales", command=self._guardar_umbrales
                   ).grid(row=0, column=4, padx=8)

        personalizado = ttk.LabelFrame(frame, text="Umbral personalizado por producto (pisa el global)", padding=12)
        personalizado.pack(fill="both", expand=True)
        fila = ttk.Frame(personalizado)
        fila.pack(fill="x", pady=(0, 8))
        ttk.Label(fila, text="Código:").pack(side="left")
        self.um_prod_codigo = ttk.Entry(fila, width=18)
        self.um_prod_codigo.pack(side="left", padx=4)
        ttk.Label(fila, text="Mínimo:").pack(side="left", padx=(10, 0))
        self.um_prod_min = ttk.Entry(fila, width=8)
        self.um_prod_min.pack(side="left", padx=4)
        ttk.Label(fila, text="Máximo:").pack(side="left", padx=(10, 0))
        self.um_prod_max = ttk.Entry(fila, width=8)
        self.um_prod_max.pack(side="left", padx=4)
        ttk.Button(fila, text="Guardar umbral de este producto", style="Accent.TButton",
                   command=self._guardar_umbral_producto).pack(side="left", padx=10)
        ttk.Button(fila, text="Quitar umbral propio", style="Danger.TButton",
                   command=self._quitar_umbral_producto).pack(side="left")

        self.tree_umbrales = ttk.Treeview(personalizado, columns=("codigo", "nombre", "min", "max"),
                                           show="headings", height=8)
        for col, txt, w in [("codigo", "Código", 110), ("nombre", "Nombre", 280),
                             ("min", "Mínimo", 90), ("max", "Máximo", 90)]:
            self.tree_umbrales.heading(col, text=txt)
            self.tree_umbrales.column(col, width=w)
        estriar_treeview(self.tree_umbrales)
        self.tree_umbrales.pack(fill="both", expand=True)
        self.tree_umbrales.bind("<Double-1>", self._cargar_umbral_seleccionado)

        self._refrescar_umbrales_producto()

    def _guardar_config_telegram(self):
        cfg = config.cargar_config()
        cfg["telegram"]["bot_token"] = self.tg_token.get().strip()
        cfg["telegram"]["chat_id_default"] = self.tg_chat.get().strip()
        cfg["telegram"]["habilitado"] = "true" if self.tg_habilitado.get() else "false"
        config.guardar_config(cfg)
        messagebox.showinfo("Guardado", "Configuración de Telegram guardada.")

    def _guardar_umbrales(self):
        try:
            from pos_core.db import transaction
            with transaction() as conn:
                conn.execute(
                    """INSERT INTO Configuracion_Alertas (producto_codigo, stock_minimo, stock_maximo, activo)
                       VALUES (NULL, ?, ?, 1)
                       ON CONFLICT(producto_codigo) DO UPDATE SET
                          stock_minimo = excluded.stock_minimo, stock_maximo = excluded.stock_maximo""",
                    (int(self.um_min.get() or 0), int(self.um_max.get() or 0)),
                )
            messagebox.showinfo("Guardado", "Umbrales globales guardados.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _refrescar_umbrales_producto(self):
        for row in self.tree_umbrales.get_children():
            self.tree_umbrales.delete(row)
        for i, u in enumerate(alerts.listar_umbrales_por_producto()):
            self.tree_umbrales.insert("", "end", values=(u["codigo"], u["nombre"], u["stock_minimo"], u["stock_maximo"]),
                                       tags=(tag_fila(i),))

    def _guardar_umbral_producto(self):
        codigo = self.um_prod_codigo.get().strip()
        if not codigo:
            messagebox.showwarning("Falta el código", "Ingresá el código del producto.")
            return
        try:
            minimo = int(self.um_prod_min.get() or 0)
            maximo = int(self.um_prod_max.get() or 0)
        except ValueError:
            messagebox.showerror("Error", "Mínimo/Máximo tienen que ser números enteros.")
            return
        try:
            alerts.set_umbral_producto(codigo, minimo, maximo)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        self._refrescar_umbrales_producto()
        messagebox.showinfo("Guardado", f"Umbral propio de '{codigo}' guardado (mín. {minimo} / máx. {maximo}).")

    def _quitar_umbral_producto(self):
        codigo = self.um_prod_codigo.get().strip()
        if not codigo:
            return
        alerts.quitar_umbral_producto(codigo)
        self._refrescar_umbrales_producto()

    def _cargar_umbral_seleccionado(self, event=None):
        sel = self.tree_umbrales.selection()
        if not sel:
            return
        codigo, _nombre, minimo, maximo = self.tree_umbrales.item(sel[0], "values")
        self.um_prod_codigo.delete(0, "end")
        self.um_prod_codigo.insert(0, codigo)
        self.um_prod_min.delete(0, "end")
        self.um_prod_min.insert(0, minimo)
        self.um_prod_max.delete(0, "end")
        self.um_prod_max.insert(0, maximo)

    # ------------------------------------------------------------------ #
    # Módulo oculto de sincronización (Ctrl+Shift+M)
    # ------------------------------------------------------------------ #
    def _abrir_panel_sync(self, event=None):
        from apps.master_dueno.panel_sync import PanelSincronizacion
        PanelSincronizacion(self)


if __name__ == "__main__":
    from pos_core.paths import set_base_override_to_parent_dir
    # Caja y Dueño Maestro comparten UNA sola DB (carpeta padre de instalación).
    set_base_override_to_parent_dir()
    init_db()
    AppDueno().mainloop()
