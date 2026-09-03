"""Carrito de la Caja: grilla, edición de cantidad y manejo por teclado.

Lo comparten la Caja Maestra (apps/master_caja) y la Caja del USB de
emergencia (apps/usb_caja), que son gemelas en todo lo que hace al cobro.
Está acá y no duplicado en cada una para que no se separen con el tiempo:
si mañana se corrige algo del carrito, se corrige en los dos lados a la
vez (el mismo criterio por el que CODIGO_SIN_BARRA vive en pos_core).

Qué aporta:

- La grilla del carrito, armada a mano con Labels porque un Treeview no
  deja poner en negrita una sola columna de la fila (el nombre y el
  subtotal tienen que leerse desde el otro lado del mostrador).
- Edición de la CANTIDAD en el lugar: se toca la celda (o se para en la
  línea con las flechas y se aprieta Enter), se borra lo que hay y se
  escribe la cantidad nueva. Evita tener que escanear diez veces el mismo
  producto.
- Manejo por teclado de toda la pantalla: la caja se tiene que poder usar
  sin mouse. Tres zonas (buscador, resultados y carrito); las flechas se
  mueven dentro de la zona y saltan a la siguiente al llegar al borde, y
  Enter siempre confirma la acción de la zona donde uno está parado.

Quien lo use tiene que aportar: self.carrito, self.carrito_seleccionado,
self.buscador, self.resultados, self.lbl_total, y los métodos
_on_buscar(), _agregar_al_carrito() y _usuario_origen().
"""

import tkinter as tk
from tkinter import messagebox

from apps.theme import COLORS, celda_texto

from pos_core import audit


class CarritoTecladoMixin:

    # ------------------------------------------------------------------ #
    # Estado inicial (llamar desde el __init__ de la ventana)
    # ------------------------------------------------------------------ #
    def _init_carrito(self):
        # Cada línea lleva un id propio ("_id") y TODO se referencia por ese
        # id, nunca por el código del producto. El motivo: el código NO es
        # único dentro del carrito — todos los "artículo sin código" usan el
        # mismo código reservado, y son líneas distintas a propósito (dos
        # caramelos de precio distinto). Cuando esto se manejaba por código,
        # quitar uno de esos artículos borraba TODOS los sueltos del ticket
        # y se cobraba de menos sin que nadie lo notara.
        self.carrito = []          # list[{_id,codigo,nombre,cantidad,precio_unitario}]
        self.carrito_seleccionado = None   # el _id de la línea elegida
        self.zona = "buscador"     # "buscador" | "resultados" | "carrito"
        self._editor_cantidad = None
        self._editor_linea_id = None
        self._proximo_id_linea = 1

    def _confirmar_edicion_pendiente(self):
        """Si quedó un campo de cantidad abierto, lo confirma AHORA.

        Se llama antes de cobrar. El caso real: el cajero escribe la
        cantidad nueva y va directo al botón COBRAR sin apretar Enter. Que
        el valor tecleado se aplique dependería del orden en que Tk entrega
        el FocusOut del campo y el clic del botón; en vez de confiar en ese
        orden, se cierra la edición explícitamente y se cobra siempre con
        lo que el cajero dejó escrito.
        """
        if self._editor_cantidad is not None:
            self._confirmar_cantidad(self._editor_linea_id, self._editor_cantidad.get())

    def _nueva_linea(self, codigo: str, nombre: str, precio: float, cantidad: int = 1) -> dict:
        linea = {"_id": self._proximo_id_linea, "codigo": codigo, "nombre": nombre,
                 "cantidad": cantidad, "precio_unitario": precio}
        self._proximo_id_linea += 1
        return linea

    def _linea(self, linea_id):
        return next((i for i in self.carrito if i["_id"] == linea_id), None)

    # ------------------------------------------------------------------ #
    # Grilla del carrito
    # ------------------------------------------------------------------ #
    def _armar_grilla_carrito(self, parent):
        contenedor = tk.Frame(parent, bg=COLORS["surface"])
        contenedor.pack(fill="both", expand=True, pady=(0, 4))

        canvas = tk.Canvas(contenedor, bg=COLORS["surface"], highlightthickness=1,
                            highlightbackground=COLORS["border"])
        vsb = tk.Scrollbar(contenedor, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.carrito_grid = tk.Frame(canvas, bg=COLORS["surface"], takefocus=True)
        canvas.create_window((0, 0), window=self.carrito_grid, anchor="nw")
        self.carrito_grid.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        self.carrito_grid.grid_columnconfigure(1, weight=1)

        encabezados = [("CÓDIGO", "w"), ("PRODUCTO", "w"), ("CANT.", "center"),
                        ("P. UNIT.", "e"), ("SUBTOTAL", "e")]
        for col, (texto, anchor) in enumerate(encabezados):
            tk.Label(self.carrito_grid, text=texto, bg=COLORS["accent"], fg="white",
                     font=("Segoe UI", 10, "bold"), padx=10, pady=8, anchor=anchor
                     ).grid(row=0, column=col, sticky="nsew")

    def _refrescar_grilla_carrito(self):
        if self._editor_cantidad is not None:
            # Se está editando una cantidad: redibujar ahora destruiría el
            # campo con lo que el cajero está tecleando.
            return sum(i["cantidad"] * i["precio_unitario"] for i in self.carrito)

        for w in list(self.carrito_grid.grid_slaves()):
            if int(w.grid_info()["row"]) > 0:
                w.destroy()

        total = 0.0
        for i, item in enumerate(self.carrito, start=1):
            subtotal = item["cantidad"] * item["precio_unitario"]
            total += subtotal
            seleccionada = item["_id"] == self.carrito_seleccionado
            bg = COLORS["accent_light"] if seleccionada else (
                COLORS["stripe"] if i % 2 == 0 else COLORS["surface"])

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
                celda.bind("<Button-1>", lambda e, i=item["_id"]: self._seleccionar_linea(i), add="+")
                if col == 2:
                    # La columna CANT. se edita tocándola.
                    celda.config(cursor="xterm")
                    celda.bind("<Button-1>",
                               lambda e, i=item["_id"]: self._editar_cantidad(i), add="+")

        self.lbl_total.config(text=f"${total:.2f}")
        return total

    def _seleccionar_linea(self, linea_id):
        self.zona = "carrito"
        self.carrito_seleccionado = linea_id
        self._refrescar_grilla_carrito()

    # ------------------------------------------------------------------ #
    # Edición de la cantidad dentro del carrito
    # ------------------------------------------------------------------ #
    def _editar_cantidad_seleccionada(self):
        if self.carrito_seleccionado:
            self._editar_cantidad(self.carrito_seleccionado)

    def _editar_cantidad(self, linea_id):
        """Cambia la celda de CANT. por un campo con la cantidad actual ya
        seleccionada: se escribe la nueva encima (o se borra y se escribe),
        Enter confirma y Escape cancela."""
        if self._editor_cantidad is not None:
            return
        idx = next((n for n, i in enumerate(self.carrito) if i["_id"] == linea_id), None)
        if idx is None:
            return

        self.zona = "carrito"
        self.carrito_seleccionado = linea_id
        self._refrescar_grilla_carrito()

        item = self.carrito[idx]
        entry = tk.Entry(self.carrito_grid, width=6, justify="center",
                          font=("Segoe UI", 12, "bold"), relief="solid", bd=2,
                          bg="white", fg=COLORS["text"], insertbackground=COLORS["text"])
        entry.insert(0, str(item["cantidad"]))
        entry.select_range(0, "end")
        entry.grid(row=idx + 1, column=2, sticky="nsew", padx=2, pady=2)
        entry.focus_set()
        self._editor_cantidad = entry
        self._editor_linea_id = linea_id

        entry.bind("<Return>", lambda e: self._confirmar_cantidad(linea_id, entry.get()))
        entry.bind("<KP_Enter>", lambda e: self._confirmar_cantidad(linea_id, entry.get()))
        entry.bind("<Escape>", lambda e: self._cerrar_editor_cantidad())
        entry.bind("<FocusOut>", lambda e: self._confirmar_cantidad(linea_id, entry.get()))

    def _cerrar_editor_cantidad(self):
        editor, self._editor_cantidad = self._editor_cantidad, None
        self._editor_linea_id = None
        if editor is not None:
            editor.destroy()
        self._refrescar_grilla_carrito()
        self.carrito_grid.focus_set()
        return "break"

    def _confirmar_cantidad(self, linea_id, texto):
        if self._editor_cantidad is None:
            return "break"
        self._cerrar_editor_cantidad()

        item = self._linea(linea_id)
        if item is None:
            return "break"

        texto = (texto or "").strip()
        if not texto:
            return "break"  # se dejó vacío: se conserva la cantidad anterior
        try:
            nueva = int(texto)
        except ValueError:
            messagebox.showwarning("Cantidad inválida",
                                    f"'{texto}' no es un número entero de unidades.")
            return "break"
        if nueva <= 0:
            messagebox.showinfo(
                "Para sacar el producto, usá 'Quitar línea'",
                "La cantidad tiene que ser 1 o más.\n\n"
                "Si querés sacar el producto del carrito, seleccionalo y usá "
                "'Quitar línea' (tecla Supr).")
            return "break"

        anterior = item["cantidad"]
        if nueva == anterior:
            return "break"

        if nueva < anterior:
            # Bajar la cantidad saca mercadería del ticket igual que quitar
            # la línea entera, así que se audita igual (misma protección
            # anti-robo): queda registrada la diferencia que se sacó.
            usuario, origen = self._usuario_origen()
            try:
                audit.registrar_linea_eliminada(
                    codigo=item["codigo"], nombre=item["nombre"],
                    cantidad=anterior - nueva, precio_unitario=item["precio_unitario"],
                    usuario=usuario, origen=origen)
            except Exception:
                pass  # la auditoría nunca debe bloquear el trabajo del cajero

        item["cantidad"] = nueva
        self._refrescar_grilla_carrito()
        return "break"

    # ------------------------------------------------------------------ #
    # Navegación por teclado
    # ------------------------------------------------------------------ #
    def _configurar_teclado_carrito(self):
        self.bind("<Down>", self._tecla_abajo)
        self.bind("<Up>", self._tecla_arriba)
        self.bind("<Return>", self._tecla_enter)
        self.bind("<KP_Enter>", self._tecla_enter)
        self.bind("<Delete>", self._tecla_suprimir)
        self.bind("<Escape>", lambda e: self._ir_a_buscador())
        self.bind("<F2>", lambda e: self._enfocar_metodo_pago())
        self.bind("<F3>", lambda e: self._abrir_historial())
        self.bind("<F4>", lambda e: self._configurar_impresora())

        # El buscador tiene su propio Enter (buscar/escanear); el de la
        # ventana no debe dispararse además.
        self.buscador.bind("<Return>", self._on_buscar)
        self.buscador.bind("<FocusIn>", lambda e: setattr(self, "zona", "buscador"))
        self.resultados.bind("<Double-1>", self._agregar_al_carrito)
        self.resultados.bind("<FocusIn>", lambda e: setattr(self, "zona", "resultados"))

    def _ir_a_buscador(self):
        self.zona = "buscador"
        self.buscador.focus_set()
        return "break"

    def _enfocar_metodo_pago(self):
        self.metodo_pago.focus_set()
        return "break"

    def _ir_a_resultados(self):
        hijos = self.resultados.get_children()
        if not hijos:
            return False
        self.zona = "resultados"
        self.resultados.focus_set()
        actual = self.resultados.focus() or hijos[0]
        self.resultados.selection_set(actual)
        self.resultados.focus(actual)
        return True

    def _ir_a_carrito(self):
        if not self.carrito:
            return False
        self.zona = "carrito"
        if self.carrito_seleccionado not in [i["_id"] for i in self.carrito]:
            self.carrito_seleccionado = self.carrito[0]["_id"]
        self.carrito_grid.focus_set()
        self._refrescar_grilla_carrito()
        return True

    def _indice_seleccionado(self):
        for idx, item in enumerate(self.carrito):
            if item["_id"] == self.carrito_seleccionado:
                return idx
        return None

    def _mover_carrito(self, delta: int):
        if not self.carrito:
            return
        idx = self._indice_seleccionado()
        nuevo = 0 if idx is None else idx + delta
        if nuevo < 0:
            # Arriba del todo del carrito: se vuelve a la lista de arriba.
            if not self._ir_a_resultados():
                self._ir_a_buscador()
            return
        nuevo = min(nuevo, len(self.carrito) - 1)
        self.carrito_seleccionado = self.carrito[nuevo]["_id"]
        self._refrescar_grilla_carrito()

    def _tecla_abajo(self, event=None):
        if self._editor_cantidad is not None:
            return None
        if self.zona == "buscador":
            if not self._ir_a_resultados():
                self._ir_a_carrito()
            return "break"
        if self.zona == "resultados":
            hijos = self.resultados.get_children()
            actual = self.resultados.focus()
            if not hijos or (actual and hijos.index(actual) == len(hijos) - 1):
                if self._ir_a_carrito():
                    return "break"
            return None  # el Treeview mueve su propia selección
        if self.zona == "carrito":
            self._mover_carrito(1)
            return "break"
        return None

    def _tecla_arriba(self, event=None):
        if self._editor_cantidad is not None:
            return None
        if self.zona == "resultados":
            hijos = self.resultados.get_children()
            actual = self.resultados.focus()
            if not hijos or (actual and hijos.index(actual) == 0):
                return self._ir_a_buscador()
            return None
        if self.zona == "carrito":
            self._mover_carrito(-1)
            return "break"
        return None

    def _tecla_enter(self, event=None):
        if self._editor_cantidad is not None:
            return None
        if self.zona == "resultados":
            self._agregar_al_carrito()
            return "break"
        if self.zona == "carrito":
            self._editar_cantidad_seleccionada()
            return "break"
        return None

    def _tecla_suprimir(self, event=None):
        if self.zona == "carrito" and self._editor_cantidad is None:
            self._quitar_linea()
            return "break"
        return None

    # ------------------------------------------------------------------ #
    def _quitar_linea(self):
        if not self.carrito_seleccionado:
            messagebox.showinfo("Elegí un producto",
                                 "Elegí una línea del carrito (con las flechas ↑↓ o haciendo clic) "
                                 "y después presioná 'Quitar línea' o la tecla Supr.")
            return
        idx = self._indice_seleccionado()
        item = self._linea(self.carrito_seleccionado)
        # Se saca SOLO esa línea, por su id. Filtrar por código borraría de
        # paso todas las demás líneas que compartan el código.
        self.carrito = [i for i in self.carrito if i["_id"] != self.carrito_seleccionado]
        # Queda seleccionada la línea que ocupó su lugar, para poder seguir
        # borrando con Supr sin volver a elegir con el mouse.
        if self.carrito:
            self.carrito_seleccionado = self.carrito[min(idx or 0, len(self.carrito) - 1)]["_id"]
        else:
            self.carrito_seleccionado = None
        self._refrescar_grilla_carrito()

        if item:
            usuario, origen = self._usuario_origen()
            try:
                audit.registrar_linea_eliminada(
                    codigo=item["codigo"], nombre=item["nombre"], cantidad=item["cantidad"],
                    precio_unitario=item["precio_unitario"], usuario=usuario, origen=origen)
            except Exception:
                pass  # la auditoría nunca debe bloquear el trabajo del cajero

        # Si quedan líneas se sigue en el carrito (para borrar varias
        # seguidas con Supr); si quedó vacío, el foco vuelve al buscador,
        # que es donde tiene que estar esperando el próximo escaneo.
        if self.carrito:
            self.carrito_grid.focus_set()
        else:
            self._ir_a_buscador()

    def _agregar_producto(self, codigo: str, nombre: str, precio: float):
        from pos_core.sales import CODIGO_SIN_BARRA
        # Escanear dos veces el mismo producto suma cantidad en la misma
        # línea. Los "artículo sin código" son la excepción: comparten el
        # código reservado pero cada uno tiene su propio importe, así que
        # nunca se fusionan (ver _agregar_linea_libre).
        if codigo != CODIGO_SIN_BARRA:
            for item in self.carrito:
                if item["codigo"] == codigo:
                    item["cantidad"] += 1
                    break
            else:
                self.carrito.append(self._nueva_linea(codigo, nombre, precio))
        else:
            self.carrito.append(self._nueva_linea(codigo, nombre, precio))
        self._refrescar_grilla_carrito()
        # Un clic (doble clic en resultados, clic en una celda del carrito)
        # le saca el foco del teclado al buscador; si no se lo devolvemos,
        # el próximo código escaneado no llega a ningún lado.
        self._ir_a_buscador()

    def _agregar_al_carrito(self, event=None):
        # Con el mouse la fila queda en selection(); moviéndose con las
        # flechas queda en focus(). Se aceptan las dos.
        sel = self.resultados.selection()
        fila = sel[0] if sel else self.resultados.focus()
        if not fila:
            return
        codigo, nombre, precio = self.resultados.item(fila, "values")
        nombre = nombre.replace("  🔥 OFERTA", "")
        self._agregar_producto(codigo, nombre, float(precio))

    TEXTO_AYUDA_TECLADO = (
        "Teclado:  ↑↓ moverse   ·   Enter: agregar / editar cantidad   ·   Supr: quitar línea   "
        "·   Esc: volver al buscador   ·   F2 pago   ·   F3 historial   ·   F4 impresora")
