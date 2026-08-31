"""Panel del Dueño - Maestro (PC fija o PC del dueño).

Acceso total: dashboard, stock (manual/PDF/lector), filtros + edición
masiva, carga Excel inicial, configuración de alertas de Telegram, y el
módulo oculto de sincronización/conciliación (Ctrl+Shift+M) para cuando
se conecta un USB de emergencia.

Todo el acceso a datos pasa por `self.backend` (ver
pos_core/dueno_backend.py) en vez de importar los módulos de pos_core
directamente: así el mismo código de esta ventana sirve sin cambios para
el Maestro local (LocalBackend, el uso de siempre) y para el Dueño
Remoto (RemoteBackend, ver apps/dueno_remoto/main.py) que habla con el
Maestro por HTTP a través de una VPN privada tipo Tailscale.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pos_core.db import init_db
from pos_core.dueno_backend import LocalBackend, RemoteError
from pos_core import arca
from apps.theme import aplicar_tema, estriar_treeview, tag_fila, habilitar_copiar_pegar_global

USUARIO = os.environ.get("USERNAME", "dueño")
ORIGEN = "MAESTRO"


class AppDueno(tk.Tk):
    def __init__(self, backend=None):
        super().__init__()
        aplicar_tema(self)
        self.backend = backend or LocalBackend()
        self.es_remoto = not isinstance(self.backend, LocalBackend)
        self.title("Otter Dueño")
        self.geometry("1180x760")

        # Estado del filtro que se está armando/editando en la pestaña de
        # Filtros: lista ordenada de códigos elegidos a mano por el dueño.
        self.filtro_codigos = []
        self.filtro_nombres = {}  # codigo -> (nombre, precio_venta), cache para no reconsultar la DB

        if self.es_remoto:
            self.lbl_conexion = tk.Label(self, text="", font=("Segoe UI", 11, "bold"), pady=6)
            self.lbl_conexion.pack(fill="x")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        self.nb = nb

        self.tab_dashboard = ttk.Frame(nb)
        self.tab_stock = ttk.Frame(nb)
        self.tab_bulk = ttk.Frame(nb)
        self.tab_ofertas = ttk.Frame(nb)
        self.tab_pdf = ttk.Frame(nb)
        self.tab_excel = ttk.Frame(nb)
        self.tab_arca = ttk.Frame(nb)
        self.tab_alertas = ttk.Frame(nb)
        self.tab_auditoria = ttk.Frame(nb)

        nb.add(self.tab_dashboard, text="Dashboard")
        nb.add(self.tab_stock, text="Stock")
        nb.add(self.tab_bulk, text="Filtros / Edición Masiva")
        nb.add(self.tab_ofertas, text="Ofertas")
        nb.add(self.tab_pdf, text="Facturas PDF")
        nb.add(self.tab_excel, text="Carga Excel")
        nb.add(self.tab_arca, text="Facturación ARCA")
        nb.add(self.tab_alertas, text="Alertas")
        nb.add(self.tab_auditoria, text="Auditoría")

        self._armar_dashboard(self.tab_dashboard)
        self._armar_stock(self.tab_stock)
        self._armar_bulk(self.tab_bulk)
        self._armar_ofertas(self.tab_ofertas)
        self._armar_pdf(self.tab_pdf)
        self._armar_excel(self.tab_excel)
        self._armar_arca(self.tab_arca)
        self._armar_alertas(self.tab_alertas)
        self._armar_auditoria(self.tab_auditoria)
        habilitar_copiar_pegar_global(self)

        nb.bind("<<NotebookTabChanged>>", self._on_cambio_pestana)

        if not self.es_remoto:
            # Módulo oculto de sincronización/conciliación (desarrollador):
            # necesita el USB físico insertado en ESTA pc, no tiene sentido
            # en modo remoto.
            self.bind_all("<Control-Shift-M>", self._abrir_panel_sync)

            # Bot de Telegram: revisa umbrales cada 5 minutos mientras el
            # panel esté abierto (independiente del monitoreo 24/7 que corre
            # en el servicio oculto de stock en el Maestro). En modo remoto
            # ya lo hace el Maestro por su cuenta; correrlo también acá
            # sería redundante (y no tendría base de datos local que mirar).
            self._iniciar_monitor_alertas()
        else:
            self._verificar_conexion_periodica()

    def _verificar_conexion_periodica(self):
        # Nunca asumas que lo que ves está actualizado: si se corta la
        # conexión con el local (wifi, datos móviles, lo que sea), esto lo
        # deja bien claro en vez de mostrar datos viejos como si fueran en
        # vivo. Apenas vuelve la conexión, el cartel se pone verde solo —
        # no hace falta "sincronizar" nada, es la misma base de datos.
        #
        # El chequeo (HTTP con timeout de varios segundos) se hace en un
        # hilo aparte: si se hiciera en el hilo de Tk, una conexión lenta
        # o caída (wifi, datos móviles) congelaría toda la ventana varios
        # segundos cada 15 segundos.
        import threading

        def _chequear():
            conectado = self.backend.verificar_conexion()
            self.after(0, lambda: self._actualizar_cartel_conexion(conectado))

        threading.Thread(target=_chequear, daemon=True).start()
        self.after(15000, self._verificar_conexion_periodica)

    def _actualizar_cartel_conexion(self, conectado: bool):
        if not self.winfo_exists():
            return
        if conectado:
            self.lbl_conexion.config(text="🟢 Conectado con el local en vivo",
                                      bg="#27ae60", fg="white")
        else:
            self.lbl_conexion.config(
                text="🔴 Sin conexión con el local — lo que ves puede estar desactualizado",
                bg="#c0392b", fg="white")

    def _iniciar_monitor_alertas(self):
        # El bot es "best effort": si falla al iniciar (falta una librería,
        # lo que sea), el Panel del Dueño tiene que abrir igual.
        self._monitor_alertas = None
        try:
            from pos_core.telegram_bot import MonitorAlertas
            self._monitor_alertas = MonitorAlertas(intervalo_segundos=300)
            self._monitor_alertas.start()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Dashboard
    # ------------------------------------------------------------------ #
    def _armar_dashboard(self, frame):
        top = ttk.Frame(frame, padding=(4, 4, 4, 12))
        top.pack(fill="x")
        ttk.Button(top, text="Actualizar", command=lambda: self._refrescar_dashboard()).pack(side="left")

        self.lbl_resumen = ttk.Label(top, text="", style="Header.TLabel")
        self.lbl_resumen.pack(side="left", padx=20)

        metodos = ttk.LabelFrame(frame, text="Dinero movido hoy por método de pago", padding=10)
        metodos.pack(fill="x", pady=(0, 10))
        self.tree_metodos_pago = ttk.Treeview(
            metodos, columns=("metodo", "cantidad", "total"), show="headings", height=4)
        self.tree_metodos_pago.heading("metodo", text="Método")
        self.tree_metodos_pago.heading("cantidad", text="Ventas")
        self.tree_metodos_pago.heading("total", text="Total")
        self.tree_metodos_pago.column("metodo", width=160)
        self.tree_metodos_pago.column("cantidad", width=100, anchor="center")
        self.tree_metodos_pago.column("total", width=140, anchor="e")
        estriar_treeview(self.tree_metodos_pago)
        self.tree_metodos_pago.pack(fill="x")

        self.chart_frame = ttk.Frame(frame, style="Card.TFrame")
        self.chart_frame.pack(fill="both", expand=True)
        self._refrescar_dashboard()

    def _refrescar_dashboard(self):
        resumen = self.backend.reports.resumen_dashboard()
        self.lbl_resumen.config(
            text=f"Ventas de hoy: {resumen['ventas_hoy']}   ·   Total: ${resumen['total_hoy']:.2f}")

        for row in self.tree_metodos_pago.get_children():
            self.tree_metodos_pago.delete(row)
        for i, m in enumerate(self.backend.reports.totales_por_metodo_pago()):
            self.tree_metodos_pago.insert("", "end", values=(m["metodo_pago"], m["cantidad"],
                                                               f"${m['total']:.2f}"), tags=(tag_fila(i),))

        top_productos = resumen["top_productos"]

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
        alta = ttk.LabelFrame(frame, text="Alta de producto nuevo (no registrado antes)", padding=12)
        alta.pack(fill="x", pady=(0, 10))
        ttk.Label(alta, text="Código:").grid(row=0, column=0, sticky="w")
        self.alta_codigo = ttk.Entry(alta, width=16)
        self.alta_codigo.grid(row=0, column=1, padx=4)
        ttk.Label(alta, text="Nombre:").grid(row=0, column=2, sticky="w")
        self.alta_nombre = ttk.Entry(alta, width=26)
        self.alta_nombre.grid(row=0, column=3, padx=4)
        ttk.Label(alta, text="Precio:").grid(row=0, column=4, sticky="w")
        self.alta_precio = ttk.Entry(alta, width=10)
        self.alta_precio.grid(row=0, column=5, padx=4)
        ttk.Label(alta, text="Stock inicial:").grid(row=0, column=6, sticky="w")
        self.alta_stock = ttk.Entry(alta, width=8)
        self.alta_stock.insert(0, "0")
        self.alta_stock.grid(row=0, column=7, padx=4)

        ttk.Label(alta, text="Proveedor:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.alta_proveedor = ttk.Entry(alta, width=16)
        self.alta_proveedor.grid(row=1, column=1, padx=4, pady=(6, 0))
        ttk.Label(alta, text="Marca:").grid(row=1, column=2, sticky="w", pady=(6, 0))
        self.alta_marca = ttk.Entry(alta, width=16)
        self.alta_marca.grid(row=1, column=3, padx=4, pady=(6, 0))
        ttk.Label(alta, text="Categoría:").grid(row=1, column=4, sticky="w", pady=(6, 0))
        self.alta_categoria = ttk.Entry(alta, width=16)
        self.alta_categoria.grid(row=1, column=5, padx=4, pady=(6, 0))
        ttk.Button(alta, text="Agregar producto", style="Accent.TButton",
                   command=self._crear_producto_nuevo).grid(row=1, column=7, padx=4, pady=(6, 0))

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

        stock_actual = ttk.LabelFrame(frame, text="Stock actual", padding=12)
        stock_actual.pack(fill="both", expand=True)

        buscador_fila = ttk.Frame(stock_actual)
        buscador_fila.pack(fill="x", pady=(0, 8))
        ttk.Label(buscador_fila, text="Buscar:").pack(side="left")
        self.stock_actual_buscar = ttk.Entry(buscador_fila)
        self.stock_actual_buscar.pack(side="left", fill="x", expand=True, padx=6)
        self.stock_actual_buscar.bind("<KeyRelease>", lambda e: self._refrescar_stock_actual())

        self.tree_stock_actual = ttk.Treeview(
            stock_actual, columns=("codigo", "nombre", "stock"), show="headings", height=16)
        self.tree_stock_actual.heading("codigo", text="Código")
        self.tree_stock_actual.heading("nombre", text="Nombre")
        self.tree_stock_actual.heading("stock", text="Stock actual")
        self.tree_stock_actual.column("codigo", width=130)
        self.tree_stock_actual.column("nombre", width=420)
        self.tree_stock_actual.column("stock", width=110, anchor="center")
        estriar_treeview(self.tree_stock_actual)
        self.tree_stock_actual.pack(fill="both", expand=True)

        self._refrescar_stock_actual()

    def _on_cambio_pestana(self, event=None):
        if self.nb.select() == str(self.tab_stock):
            self._refrescar_stock_actual()

    def _refrescar_stock_actual(self):
        for row in self.tree_stock_actual.get_children():
            self.tree_stock_actual.delete(row)
        termino = self.stock_actual_buscar.get().strip()
        rows = self.backend.products.listar_stock(termino or None)
        for i, r in enumerate(rows):
            self.tree_stock_actual.insert("", "end", values=(r["codigo"], r["nombre"], r["stock"]),
                                           tags=(tag_fila(i),))

    def _sumar_stock(self):
        try:
            self.backend.stock_service.sumar_stock_manual(
                self.stock_codigo.get().strip(), int(self.stock_cantidad.get()),
                usuario=USUARIO, origen=ORIGEN)
            self._refrescar_stock_actual()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _restar_stock(self):
        try:
            self.backend.stock_service.restar_stock_manual(
                self.stock_codigo.get().strip(), int(self.stock_cantidad.get()),
                usuario=USUARIO, origen=ORIGEN)
            self._refrescar_stock_actual()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _on_lectura_scanner(self, event=None):
        codigo = self.lector_entry.get().strip()
        self.lector_entry.delete(0, "end")
        if not codigo:
            return
        try:
            self.backend.stock_service.restar_stock_por_lector(codigo, usuario=USUARIO, origen=ORIGEN)
            self._refrescar_stock_actual()
        except Exception as e:
            messagebox.showerror("Error con el lector", f"{codigo}: {e}")

    def _crear_producto_nuevo(self):
        try:
            precio = float(self.alta_precio.get() or 0)
            stock_inicial = int(self.alta_stock.get() or 0)
        except ValueError:
            messagebox.showerror("Error", "Precio y stock inicial tienen que ser números.")
            return
        codigo = self.alta_codigo.get().strip()
        try:
            self.backend.products.crear_producto(
                codigo=codigo, nombre=self.alta_nombre.get(),
                precio_venta=precio, stock_inicial=stock_inicial,
                proveedor=self.alta_proveedor.get().strip(), marca=self.alta_marca.get().strip(),
                categoria=self.alta_categoria.get().strip(), usuario=USUARIO, origen=ORIGEN)
        except (ValueError, RemoteError) as e:
            messagebox.showerror("No se pudo crear el producto", str(e))
            return
        for entry in (self.alta_codigo, self.alta_nombre, self.alta_precio, self.alta_proveedor,
                      self.alta_marca, self.alta_categoria):
            entry.delete(0, "end")
        self.alta_stock.delete(0, "end")
        self.alta_stock.insert(0, "0")
        self._refrescar_stock_actual()
        messagebox.showinfo("Producto creado", "El producto nuevo ya está disponible en la Caja.")

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
        rows = self.backend.products.listar_para_filtro(termino or None)
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
        for f in self.backend.filters.listar_filtros_guardados():
            self.lista_filtros.insert("end", f["nombre"])

    def _cargar_filtro_guardado(self):
        sel = self.lista_filtros.curselection()
        if not sel:
            return
        nombre = self.lista_filtros.get(sel[0])
        guardados = {f["nombre"]: f["definicion"] for f in self.backend.filters.listar_filtros_guardados()}
        definicion = guardados.get(nombre)
        if definicion is None:
            return
        try:
            productos = self.backend.filters.aplicar_filtro(definicion)
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
        self.backend.filters.eliminar_filtro(nombre)
        self._refrescar_lista_filtros()

    def _guardar_filtro_actual(self):
        nombre = self.nombre_filtro.get().strip()
        if not nombre:
            messagebox.showwarning("Falta el nombre", "Ponele un nombre al filtro antes de guardarlo.")
            return
        if not self.filtro_codigos:
            messagebox.showwarning("Filtro vacío", "Agregá al menos un producto (doble clic en la lista de la izquierda).")
            return
        self.backend.filters.guardar_filtro_manual(nombre, self.filtro_codigos)
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
        resultados = self.backend.bulk_edit.aplicar_ajuste_masivo(codigos, porcentaje=pct, usuario=USUARIO, origen=ORIGEN)
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
        resultados = self.backend.bulk_edit.aplicar_ajuste_masivo(codigos, monto_fijo=monto, usuario=USUARIO, origen=ORIGEN)
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
            resultado = self.backend.subir_y_llamar("pdf_import", "parsear_factura_pdf", ruta)
        except Exception as e:
            messagebox.showerror("Error leyendo el PDF", str(e))
            return

        if resultado["es_pdf_escaneado"]:
            messagebox.showwarning(
                "PDF sin texto (posible imagen escaneada)",
                "No se pudo extraer texto del PDF. Puede ser una imagen escaneada.\n"
                "Cargá los ítems manualmente desde la pestaña 'Stock'.")
            return

        self.pdf_log.delete("1.0", "end")
        items = resultado["items"]
        self.pdf_log.insert("end", f"Ítems detectados: {len(items)}\n")
        for i in items:
            self.pdf_log.insert("end", f"  {i['codigo']} | {i['nombre']} | x{i['cantidad']} | ${i['precio_compra']}\n")
        if resultado["lineas_no_reconocidas"]:
            self.pdf_log.insert("end", f"\nLíneas NO reconocidas ({len(resultado['lineas_no_reconocidas'])}), "
                                        f"revisar/cargar manualmente:\n")
            for l in resultado["lineas_no_reconocidas"]:
                self.pdf_log.insert("end", f"  ? {l}\n")

        if items and messagebox.askyesno("Confirmar", f"¿Aplicar {len(items)} ítems al stock?"):
            resultados = self.backend.stock_service.sumar_stock_por_factura_pdf(
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
        botones = ttk.Frame(frame)
        botones.pack(pady=(0, 8), anchor="w")
        ttk.Button(botones, text="Subir Excel/CSV...", style="Accent.TButton",
                   command=self._subir_excel).pack(side="left")
        ttk.Button(botones, text="Exportar lista de precios (Excel)",
                   command=self._exportar_precios).pack(side="left", padx=8)
        self.excel_log = tk.Text(frame, height=25, bg="#FFFFFF", relief="flat",
                                  highlightthickness=1, highlightbackground="#E3E6ED", padx=8, pady=8)
        self.excel_log.pack(fill="both", expand=True)

    def _subir_excel(self):
        ruta = filedialog.askopenfilename(filetypes=[("Excel/CSV", "*.xlsx *.xlsm *.csv")])
        if not ruta:
            return
        try:
            resultado = self.backend.subir_y_llamar("excel_import", "cargar_masivo", ruta,
                                                      usuario=USUARIO, origen=ORIGEN)
        except Exception as e:
            messagebox.showerror("Error cargando archivo", str(e))
            return
        self.excel_log.delete("1.0", "end")
        self.excel_log.insert("end", f"Creados: {resultado['creados']}  Actualizados: {resultado['actualizados']}\n")
        for e in resultado["errores"]:
            self.excel_log.insert("end", f"  Fila {e['fila']}: {e['error']}\n")

    def _exportar_precios(self):
        ruta = filedialog.asksaveasfilename(
            title="Guardar lista de precios", defaultextension=".xlsx",
            initialfile="Otter_Lista_Precios.xlsx", filetypes=[("Excel", "*.xlsx")])
        if not ruta:
            return
        try:
            cantidad = self.backend.llamar_y_descargar("excel_import", "exportar_lista_precios", ruta)
        except Exception as e:
            messagebox.showerror("Error exportando", str(e))
            return
        messagebox.showinfo(
            "Lista de precios exportada",
            f"Se exportaron {cantidad} productos a:\n{ruta}\n\n"
            f"Llevá ese archivo a cada USB de emergencia y cargalo desde 'Carga Excel' "
            f"(en USB_Dueño) para actualizar sus precios.")

    # ------------------------------------------------------------------ #
    # Facturación ARCA: configuración de credenciales + qué se cobró
    # con factura y qué se cobró sin facturar (ver apps/master_caja,
    # botones/atajos F12 "Cobrar y facturar" y F5 "Cobrar sin facturar").
    # ------------------------------------------------------------------ #
    def _armar_arca(self, frame):
        cfg = self.backend.config.obtener_config_dict("arca").get("arca", {})
        form = ttk.LabelFrame(frame, text="Configuración de ARCA (ex AFIP)", padding=12)
        form.pack(fill="x", pady=(0, 10))

        ttk.Label(form, text="CUIT:").grid(row=0, column=0, sticky="w")
        self.arca_cuit = ttk.Entry(form, width=20)
        self.arca_cuit.insert(0, cfg.get("cuit", ""))
        self.arca_cuit.grid(row=0, column=1, sticky="w", padx=6)

        ttk.Label(form, text="Punto de venta:").grid(row=0, column=2, sticky="w")
        self.arca_pto_venta = ttk.Entry(form, width=10)
        self.arca_pto_venta.insert(0, cfg.get("punto_venta", ""))
        self.arca_pto_venta.grid(row=0, column=3, sticky="w", padx=6)

        ttk.Label(form, text="Tipo de comprobante:").grid(row=1, column=0, sticky="w")
        self.arca_tipo = ttk.Combobox(form, values=["B", "C"], state="readonly", width=6)
        self.arca_tipo.set(cfg.get("tipo_comprobante", "B") or "B")
        self.arca_tipo.grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(form, text="Ambiente:").grid(row=1, column=2, sticky="w")
        self.arca_ambiente = ttk.Combobox(form, values=["homologacion", "produccion"],
                                           state="readonly", width=14)
        self.arca_ambiente.set(cfg.get("ambiente", "homologacion") or "homologacion")
        self.arca_ambiente.grid(row=1, column=3, sticky="w", padx=6)

        ttk.Label(form, text="Certificado (.crt/.pem):").grid(row=2, column=0, sticky="w")
        self.arca_cert = ttk.Entry(form, width=48)
        self.arca_cert.insert(0, cfg.get("certificado_path", ""))
        self.arca_cert.grid(row=2, column=1, columnspan=2, sticky="w", padx=6)
        ttk.Button(form, text="Elegir...",
                   command=lambda: self._elegir_archivo_arca(self.arca_cert)).grid(row=2, column=3, sticky="w")

        ttk.Label(form, text="Clave privada (.key/.pem):").grid(row=3, column=0, sticky="w")
        self.arca_clave = ttk.Entry(form, width=48)
        self.arca_clave.insert(0, cfg.get("clave_privada_path", ""))
        self.arca_clave.grid(row=3, column=1, columnspan=2, sticky="w", padx=6)
        ttk.Button(form, text="Elegir...",
                   command=lambda: self._elegir_archivo_arca(self.arca_clave)).grid(row=3, column=3, sticky="w")

        self.arca_habilitado = tk.BooleanVar(
            value=cfg.get("habilitado", "false").strip().lower() in ("true", "1", "si", "sí"))
        ttk.Checkbutton(form, text="Habilitado (si está destildado, F12 en la Caja avisa y no intenta facturar)",
                         variable=self.arca_habilitado).grid(row=4, column=1, columnspan=3, sticky="w", pady=(6, 0))

        ttk.Button(form, text="Guardar configuración", style="Accent.TButton",
                   command=self._guardar_config_arca).grid(row=5, column=1, pady=(10, 0), sticky="w")
        ttk.Label(form, text="El certificado y la clave los genera tu cliente desde el portal de ARCA "
                              "(no algo que este programa pueda crear). Antes de una factura real, "
                              "probar a fondo en ambiente 'homologacion'.",
                  style="Muted.TLabel", wraplength=680, justify="left").grid(
            row=6, column=0, columnspan=4, sticky="w", pady=(8, 0))

        resumen = ttk.LabelFrame(frame, text="Qué se cobró hoy con factura y qué sin facturar", padding=12)
        resumen.pack(fill="both", expand=True)

        barra = ttk.Frame(resumen)
        barra.pack(fill="x", pady=(0, 6))
        ttk.Button(barra, text="Actualizar", command=self._refrescar_resumen_arca).pack(side="left")
        ttk.Button(barra, text="Reintentar facturación de la venta seleccionada",
                   command=self._reintentar_facturacion).pack(side="left", padx=8)
        self.lbl_resumen_arca = ttk.Label(barra, text="", style="Header.TLabel")
        self.lbl_resumen_arca.pack(side="left", padx=16)

        self.tree_arca = ttk.Treeview(
            resumen, columns=("hora", "total", "facturada", "comprobante", "cae", "error"),
            show="headings", height=14)
        for col, txt, w in [("hora", "Hora", 90), ("total", "Total", 90), ("facturada", "Facturada", 80),
                             ("comprobante", "Comprobante", 130), ("cae", "CAE", 140), ("error", "Motivo", 220)]:
            self.tree_arca.heading(col, text=txt)
            self.tree_arca.column(col, width=w, anchor="e" if col == "total" else "w")
        estriar_treeview(self.tree_arca)
        self.tree_arca.pack(fill="both", expand=True)

        self._refrescar_resumen_arca()

    def _elegir_archivo_arca(self, entry: ttk.Entry):
        ruta = filedialog.askopenfilename(filetypes=[("Certificados/claves", "*.crt *.pem *.key"),
                                                       ("Todos los archivos", "*.*")])
        if ruta:
            entry.delete(0, "end")
            entry.insert(0, ruta)

    def _guardar_config_arca(self):
        self.backend.config.actualizar_config_dict({"arca": {
            "cuit": self.arca_cuit.get().strip(),
            "punto_venta": self.arca_pto_venta.get().strip(),
            "tipo_comprobante": self.arca_tipo.get(),
            "ambiente": self.arca_ambiente.get(),
            "certificado_path": self.arca_cert.get().strip(),
            "clave_privada_path": self.arca_clave.get().strip(),
            "habilitado": "true" if self.arca_habilitado.get() else "false",
        }})
        messagebox.showinfo("Configuración guardada", "La configuración de ARCA quedó guardada.")

    def _refrescar_resumen_arca(self):
        for row in self.tree_arca.get_children():
            self.tree_arca.delete(row)
        resumen = self.backend.sales.resumen_facturacion()
        for v in resumen["ventas"]:
            hora = v["fecha_hora"][11:19] if len(v["fecha_hora"]) >= 19 else v["fecha_hora"]
            comprobante = f"{v['tipo_comprobante']} Nº {v['numero_comprobante']}" if v["facturada"] else ""
            self.tree_arca.insert("", "end", iid=v["uuid_unico"], values=(
                hora, f"${v['total']:.2f}", "Sí" if v["facturada"] else "No",
                comprobante, v.get("cae") or "", v.get("arca_error") or ""))
        self.lbl_resumen_arca.config(
            text=f"Facturado hoy: {len(resumen['facturadas'])} venta(s) por ${resumen['total_facturado']:.2f}   |   "
                 f"Sin facturar: {len(resumen['sin_facturar'])} venta(s) por ${resumen['total_sin_facturar']:.2f}")

    def _reintentar_facturacion(self):
        sel = self.tree_arca.selection()
        if not sel:
            messagebox.showinfo("Elegí una venta", "Hacé clic sobre una venta sin facturar de la lista.")
            return
        venta_uuid = sel[0]
        from pos_core import arca
        try:
            self.backend.sales.facturar_venta_arca(venta_uuid)
        except (arca.ArcaError, RemoteError) as e:
            messagebox.showwarning("No se pudo facturar", str(e))
            self._refrescar_resumen_arca()
            return
        messagebox.showinfo("Facturada", "La venta se facturó correctamente con ARCA.")
        self._refrescar_resumen_arca()

    # ------------------------------------------------------------------ #
    # Alertas: Telegram + umbral global + umbral personalizado por producto
    # ------------------------------------------------------------------ #
    def _armar_alertas(self, frame):
        cfg = self.backend.config.obtener_config_dict("telegram").get("telegram", {})
        form = ttk.LabelFrame(frame, text="Configuración del Bot de Telegram", padding=12)
        form.pack(fill="x", pady=(0, 10))

        ttk.Label(form, text="Bot Token:").grid(row=0, column=0, sticky="w")
        self.tg_token = ttk.Entry(form, width=50)
        self.tg_token.insert(0, cfg.get("bot_token", ""))
        self.tg_token.grid(row=0, column=1, padx=6)

        ttk.Label(form, text="Chat ID por defecto:").grid(row=1, column=0, sticky="w")
        self.tg_chat = ttk.Entry(form, width=30)
        self.tg_chat.insert(0, cfg.get("chat_id_default", ""))
        self.tg_chat.grid(row=1, column=1, sticky="w", padx=6)

        self.tg_habilitado = tk.BooleanVar(value=cfg.get("habilitado", "false") == "true")
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
        self.backend.config.actualizar_config_dict({"telegram": {
            "bot_token": self.tg_token.get().strip(),
            "chat_id_default": self.tg_chat.get().strip(),
            "habilitado": "true" if self.tg_habilitado.get() else "false",
        }})
        messagebox.showinfo("Guardado", "Configuración de Telegram guardada.")

    def _guardar_umbrales(self):
        try:
            self.backend.alerts.set_umbral_global(int(self.um_min.get() or 0), int(self.um_max.get() or 0))
            messagebox.showinfo("Guardado", "Umbrales globales guardados.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _refrescar_umbrales_producto(self):
        for row in self.tree_umbrales.get_children():
            self.tree_umbrales.delete(row)
        for i, u in enumerate(self.backend.alerts.listar_umbrales_por_producto()):
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
            self.backend.alerts.set_umbral_producto(codigo, minimo, maximo)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        self._refrescar_umbrales_producto()
        messagebox.showinfo("Guardado", f"Umbral propio de '{codigo}' guardado (mín. {minimo} / máx. {maximo}).")

    def _quitar_umbral_producto(self):
        codigo = self.um_prod_codigo.get().strip()
        if not codigo:
            return
        self.backend.alerts.quitar_umbral_producto(codigo)
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
    # Ofertas / promociones temporales. El precio "de lista" nunca se
    # toca: mientras la oferta está vigente el precio efectivo se calcula
    # al vuelo (ver pos_core.ofertas), así que al vencer la duración en
    # días, la Caja vuelve sola a cobrar el precio normal sin que nadie
    # tenga que "deshacer" nada.
    # ------------------------------------------------------------------ #
    def _armar_ofertas(self, frame):
        form = ttk.LabelFrame(frame, text="Crear oferta / promoción", padding=12)
        form.pack(fill="x", pady=(0, 10))

        ttk.Label(form, text="Código de producto:").grid(row=0, column=0, sticky="w")
        self.oferta_codigo = ttk.Entry(form, width=16)
        self.oferta_codigo.grid(row=0, column=1, padx=4)

        ttk.Label(form, text="Tipo de descuento:").grid(row=0, column=2, sticky="w")
        self.oferta_tipo = ttk.Combobox(
            form, state="readonly", width=22,
            values=["% de descuento", "$ fijo de descuento", "Precio fijo promocional"])
        self.oferta_tipo.current(0)
        self.oferta_tipo.grid(row=0, column=3, padx=4)

        ttk.Label(form, text="Valor:").grid(row=0, column=4, sticky="w")
        self.oferta_valor = ttk.Entry(form, width=10)
        self.oferta_valor.grid(row=0, column=5, padx=4)

        ttk.Label(form, text="Duración (días):").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.oferta_dias = ttk.Entry(form, width=8)
        self.oferta_dias.insert(0, "7")
        self.oferta_dias.grid(row=1, column=1, padx=4, pady=(6, 0))

        ttk.Label(form, text="Detalles de la promoción:").grid(row=1, column=2, sticky="w", pady=(6, 0))
        self.oferta_descripcion = ttk.Entry(form, width=40)
        self.oferta_descripcion.grid(row=1, column=3, columnspan=2, sticky="ew", padx=4, pady=(6, 0))

        ttk.Button(form, text="Crear oferta", style="Accent.TButton",
                   command=self._crear_oferta_nueva).grid(row=1, column=5, padx=4, pady=(6, 0))

        listado = ttk.LabelFrame(frame, text="Ofertas (activas, programadas, vencidas y canceladas)", padding=10)
        listado.pack(fill="both", expand=True)
        self.tree_ofertas = ttk.Treeview(
            listado, columns=("producto", "descuento", "descripcion", "desde", "hasta", "estado"),
            show="headings", height=14)
        for col, txt, w in [("producto", "Producto", 180), ("descuento", "Descuento", 130),
                             ("descripcion", "Detalles", 220), ("desde", "Desde", 90),
                             ("hasta", "Hasta", 90), ("estado", "Estado", 100)]:
            self.tree_ofertas.heading(col, text=txt)
            self.tree_ofertas.column(col, width=w)
        estriar_treeview(self.tree_ofertas)
        self.tree_ofertas.pack(fill="both", expand=True, pady=(0, 8))

        ttk.Button(listado, text="Cancelar oferta seleccionada", style="Danger.TButton",
                   command=self._cancelar_oferta_seleccionada).pack(anchor="w")

        self._refrescar_ofertas()

    def _tipo_descuento_interno(self) -> str:
        return {"% de descuento": "PORCENTAJE", "$ fijo de descuento": "MONTO_FIJO",
                "Precio fijo promocional": "PRECIO_FIJO"}[self.oferta_tipo.get()]

    def _crear_oferta_nueva(self):
        try:
            valor = float(self.oferta_valor.get())
            dias = int(self.oferta_dias.get())
        except ValueError:
            messagebox.showerror("Error", "Valor y duración tienen que ser números.")
            return
        try:
            vence = self.backend.ofertas.crear_oferta(
                codigo=self.oferta_codigo.get().strip(), tipo_descuento=self._tipo_descuento_interno(),
                valor=valor, descripcion=self.oferta_descripcion.get().strip(),
                dias=dias, usuario=USUARIO)
        except (ValueError, RemoteError) as e:
            messagebox.showerror("No se pudo crear la oferta", str(e))
            return
        self.oferta_codigo.delete(0, "end")
        self.oferta_valor.delete(0, "end")
        self.oferta_descripcion.delete(0, "end")
        self._refrescar_ofertas()
        messagebox.showinfo("Oferta creada", f"Vigente hasta el {vence}. Después, el precio vuelve solo a la normalidad.")

    def _refrescar_ofertas(self):
        for row in self.tree_ofertas.get_children():
            self.tree_ofertas.delete(row)
        etiquetas_tipo = {"PORCENTAJE": "%", "MONTO_FIJO": "$ desc.", "PRECIO_FIJO": "precio $"}
        for i, o in enumerate(self.backend.ofertas.listar_ofertas()):
            descuento = f"{o['valor']} {etiquetas_tipo[o['tipo_descuento']]} (-> ${o['precio_con_descuento']:.2f})"
            self.tree_ofertas.insert(
                "", "end", iid=str(o["id"]),
                values=(o["producto_nombre"], descuento, o["descripcion"] or "",
                        o["fecha_inicio"], o["fecha_fin"], o["estado"]),
                tags=(tag_fila(i),))

    def _cancelar_oferta_seleccionada(self):
        sel = self.tree_ofertas.selection()
        if not sel:
            messagebox.showinfo("Elegí una oferta", "Hacé clic sobre una oferta de la lista.")
            return
        if not messagebox.askyesno("Confirmar", "¿Cancelar esta oferta? El producto vuelve al precio normal ya mismo."):
            return
        self.backend.ofertas.cancelar_oferta(int(sel[0]))
        self._refrescar_ofertas()

    # ------------------------------------------------------------------ #
    # Auditoría anti-robo: cada "Quitar línea" hecho en cualquier Caja.
    # Solo visible acá, en el Panel del Dueño.
    # ------------------------------------------------------------------ #
    def _armar_auditoria(self, frame):
        top = ttk.Frame(frame, padding=(0, 0, 0, 10))
        top.pack(fill="x")
        self.lbl_contador_eliminadas = ttk.Label(top, text="", style="Header.TLabel")
        self.lbl_contador_eliminadas.pack(side="left")
        ttk.Button(top, text="Actualizar", command=self._refrescar_auditoria).pack(side="right")

        self.tree_auditoria = ttk.Treeview(
            frame, columns=("fecha", "codigo", "producto", "cant", "precio", "subtotal", "usuario"),
            show="headings", height=20)
        for col, txt, w in [("fecha", "Fecha y hora", 160), ("codigo", "Código", 100),
                             ("producto", "Producto", 240), ("cant", "Cant.", 60),
                             ("precio", "P. Unit.", 90), ("subtotal", "Subtotal", 100),
                             ("usuario", "Usuario", 120)]:
            self.tree_auditoria.heading(col, text=txt)
            self.tree_auditoria.column(col, width=w)
        estriar_treeview(self.tree_auditoria)
        self.tree_auditoria.pack(fill="both", expand=True)

        self._refrescar_auditoria()

    def _refrescar_auditoria(self):
        for row in self.tree_auditoria.get_children():
            self.tree_auditoria.delete(row)
        registros = self.backend.audit.listar_lineas_eliminadas()
        self.lbl_contador_eliminadas.config(
            text=f"Líneas quitadas del carrito (total histórico): {len(registros)}")
        for i, r in enumerate(registros):
            fecha = r["fecha_hora"][:19].replace("T", " ")
            self.tree_auditoria.insert(
                "", "end",
                values=(fecha, r["producto_codigo"], r["producto_nombre"], r["cantidad"],
                        f"${r['precio_unitario']:.2f}", f"${r['subtotal']:.2f}", r["usuario"]),
                tags=(tag_fila(i),))

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
