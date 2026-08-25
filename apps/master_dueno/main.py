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
from pos_core import stock_service, bulk_edit, pdf_import, excel_import, filters, config
from pos_core.paths import sync_dir
from pos_core import reconciliation

USUARIO = os.environ.get("USERNAME", "dueño")
ORIGEN = "MAESTRO"


class AppDueno(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Panel del Dueño - Sistema Maestro")
        self.geometry("1100x720")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

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
        nb.add(self.tab_alertas, text="Alertas Telegram")

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
        top = ttk.Frame(frame, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="Actualizar", command=lambda: self._refrescar_dashboard()).pack(side="left")

        self.lbl_resumen = ttk.Label(top, text="", font=("", 11))
        self.lbl_resumen.pack(side="left", padx=20)

        self.chart_frame = ttk.Frame(frame)
        self.chart_frame.pack(fill="both", expand=True, padx=8, pady=8)
        self._refrescar_dashboard()

    def _refrescar_dashboard(self):
        conn = get_connection()
        total_hoy = conn.execute(
            "SELECT COALESCE(SUM(total),0) t, COUNT(*) c FROM Ventas "
            "WHERE date(fecha_hora) = date('now','localtime') AND anulada = 0"
        ).fetchone()
        self.lbl_resumen.config(
            text=f"Ventas de hoy: {total_hoy['c']}  |  Total: ${total_hoy['t']:.2f}")

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

            fig = Figure(figsize=(9, 5), dpi=100)
            ax = fig.add_subplot(111)
            nombres = [r["producto_nombre"][:18] for r in top_productos] or ["(sin ventas aún)"]
            cantidades = [r["cant"] for r in top_productos] or [0]
            ax.barh(nombres, cantidades, color="#3b6fa0")
            ax.set_title("Productos más vendidos (histórico)")
            ax.invert_yaxis()
            fig.tight_layout()
            FigureCanvasTkAgg(fig, master=self.chart_frame).get_tk_widget().pack(fill="both", expand=True)
        except ImportError:
            ttk.Label(self.chart_frame, text="(instalá matplotlib para ver gráficos: pip install matplotlib)"
                       ).pack(pady=20)

    # ------------------------------------------------------------------ #
    # Stock manual / lector
    # ------------------------------------------------------------------ #
    def _armar_stock(self, frame):
        form = ttk.LabelFrame(frame, text="Movimiento manual de stock", padding=10)
        form.pack(fill="x", padx=8, pady=8)

        ttk.Label(form, text="Código:").grid(row=0, column=0, sticky="w")
        self.stock_codigo = ttk.Entry(form, width=25)
        self.stock_codigo.grid(row=0, column=1, padx=6)

        ttk.Label(form, text="Cantidad:").grid(row=0, column=2, sticky="w")
        self.stock_cantidad = ttk.Entry(form, width=10)
        self.stock_cantidad.insert(0, "1")
        self.stock_cantidad.grid(row=0, column=3, padx=6)

        ttk.Button(form, text="+ Sumar (Entrada)", command=self._sumar_stock).grid(row=0, column=4, padx=4)
        ttk.Button(form, text="- Restar (Salida)", command=self._restar_stock).grid(row=0, column=5, padx=4)

        lector = ttk.LabelFrame(frame, text="Lector USB (foco acá y escaneá para restar 1 unidad)", padding=10)
        lector.pack(fill="x", padx=8, pady=8)
        self.lector_entry = ttk.Entry(lector, width=40, font=("", 14))
        self.lector_entry.pack(side="left", padx=6)
        self.lector_entry.bind("<Return>", self._on_lectura_scanner)
        ttk.Button(lector, text="Enfocar lector", command=lambda: self.lector_entry.focus_set()
                   ).pack(side="left")

        self.stock_log = tk.Text(frame, height=16)
        self.stock_log.pack(fill="both", expand=True, padx=8, pady=8)

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
    # Filtros + edición masiva
    # ------------------------------------------------------------------ #
    def _armar_bulk(self, frame):
        top = ttk.LabelFrame(frame, text="Filtro simple (campo / operador / valor)", padding=10)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Label(top, text="Campo:").grid(row=0, column=0)
        self.f_campo = ttk.Combobox(top, values=["marca", "proveedor", "categoria", "nombre", "codigo"],
                                     state="readonly", width=15)
        self.f_campo.current(0)
        self.f_campo.grid(row=0, column=1, padx=4)
        ttk.Label(top, text="Valor:").grid(row=0, column=2)
        self.f_valor = ttk.Entry(top, width=20)
        self.f_valor.grid(row=0, column=3, padx=4)
        ttk.Button(top, text="Aplicar filtro", command=self._aplicar_filtro).grid(row=0, column=4, padx=8)

        self.bulk_tree = ttk.Treeview(frame, columns=("codigo", "nombre", "precio"), show="headings", height=14)
        for col, txt, w in [("codigo", "Código", 120), ("nombre", "Nombre", 400), ("precio", "Precio", 100)]:
            self.bulk_tree.heading(col, text=txt)
            self.bulk_tree.column(col, width=w)
        self.bulk_tree.pack(fill="both", expand=True, padx=8, pady=4)

        acciones = ttk.LabelFrame(frame, text="Aplicar a la selección", padding=10)
        acciones.pack(fill="x", padx=8, pady=8)
        ttk.Label(acciones, text="% (ej. 3 o -5):").grid(row=0, column=0)
        self.bulk_pct = ttk.Entry(acciones, width=8)
        self.bulk_pct.grid(row=0, column=1, padx=4)
        ttk.Button(acciones, text="Aplicar %", command=self._aplicar_bulk_pct).grid(row=0, column=2, padx=8)

        ttk.Label(acciones, text="$ fijo (ej. 100 o -50):").grid(row=0, column=3)
        self.bulk_fijo = ttk.Entry(acciones, width=8)
        self.bulk_fijo.grid(row=0, column=4, padx=4)
        ttk.Button(acciones, text="Aplicar $", command=self._aplicar_bulk_fijo).grid(row=0, column=5, padx=8)

    def _aplicar_filtro(self):
        definicion = {"campo": self.f_campo.get(), "operador": "LIKE", "valor": self.f_valor.get()}
        for row in self.bulk_tree.get_children():
            self.bulk_tree.delete(row)
        try:
            for p in filters.aplicar_filtro(definicion):
                self.bulk_tree.insert("", "end", values=(p["codigo"], p["nombre"], f"{p['precio_venta']:.2f}"))
        except Exception as e:
            messagebox.showerror("Error de filtro", str(e))

    def _codigos_seleccionados(self):
        sel = self.bulk_tree.selection() or self.bulk_tree.get_children()
        return [self.bulk_tree.item(i, "values")[0] for i in sel]

    def _aplicar_bulk_pct(self):
        codigos = self._codigos_seleccionados()
        if not codigos:
            return
        try:
            pct = float(self.bulk_pct.get())
        except ValueError:
            messagebox.showerror("Error", "Porcentaje inválido")
            return
        resultados = bulk_edit.aplicar_ajuste_masivo(codigos, porcentaje=pct, usuario=USUARIO, origen=ORIGEN)
        self._mostrar_resultado_bulk(resultados)
        self._aplicar_filtro()

    def _aplicar_bulk_fijo(self):
        codigos = self._codigos_seleccionados()
        if not codigos:
            return
        try:
            monto = float(self.bulk_fijo.get())
        except ValueError:
            messagebox.showerror("Error", "Monto inválido")
            return
        resultados = bulk_edit.aplicar_ajuste_masivo(codigos, monto_fijo=monto, usuario=USUARIO, origen=ORIGEN)
        self._mostrar_resultado_bulk(resultados)
        self._aplicar_filtro()

    def _mostrar_resultado_bulk(self, resultados):
        ok = [r for r in resultados if r["ok"]]
        detalle = "\n".join(f"{r['codigo']}: ${r['precio_anterior']:.2f} -> ${r['precio_nuevo']:.2f}" for r in ok)
        messagebox.showinfo("Edición masiva aplicada", f"{len(ok)} productos actualizados.\n\n{detalle[:1500]}")

    # ------------------------------------------------------------------ #
    # PDF
    # ------------------------------------------------------------------ #
    def _armar_pdf(self, frame):
        ttk.Button(frame, text="Subir factura PDF...", command=self._subir_pdf).pack(padx=8, pady=8, anchor="w")
        self.pdf_log = tk.Text(frame, height=25)
        self.pdf_log.pack(fill="both", expand=True, padx=8, pady=8)

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
                  padding=8).pack(anchor="w")
        ttk.Button(frame, text="Subir Excel/CSV...", command=self._subir_excel).pack(padx=8, pady=4, anchor="w")
        self.excel_log = tk.Text(frame, height=25)
        self.excel_log.pack(fill="both", expand=True, padx=8, pady=8)

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
    # Alertas Telegram
    # ------------------------------------------------------------------ #
    def _armar_alertas(self, frame):
        cfg = config.cargar_config()
        form = ttk.LabelFrame(frame, text="Configuración del Bot de Telegram", padding=10)
        form.pack(fill="x", padx=8, pady=8)

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

        ttk.Button(form, text="Guardar", command=self._guardar_config_telegram).grid(row=3, column=1, pady=8, sticky="w")

        umbrales = ttk.LabelFrame(frame, text="Umbral global por defecto", padding=10)
        umbrales.pack(fill="x", padx=8, pady=8)
        ttk.Label(umbrales, text="Stock mínimo (stoploss):").grid(row=0, column=0)
        self.um_min = ttk.Entry(umbrales, width=8)
        self.um_min.grid(row=0, column=1, padx=4)
        ttk.Label(umbrales, text="Stock máximo (sobre-stock):").grid(row=0, column=2)
        self.um_max = ttk.Entry(umbrales, width=8)
        self.um_max.grid(row=0, column=3, padx=4)
        ttk.Button(umbrales, text="Guardar umbrales globales", command=self._guardar_umbrales
                   ).grid(row=0, column=4, padx=8)

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
