"""Tema visual compartido por las 5 apps: minimalista, con acentos de
color, pensado para verse bien tanto en la Caja como en el Panel del
Dueño. Un solo lugar para no repetir estilos en cada app.

Uso: `from apps.theme import aplicar_tema; aplicar_tema(self)` como
primera línea del __init__ de la ventana principal.
"""

import os
import tkinter as tk
from tkinter import ttk

NOMBRE_PRODUCTO = "Otter"
_ICONO_RUTA = os.path.join("apps", "assets", "logo_otter_128.png")
_icono_cache = None  # PhotoImage: hay que mantener una referencia viva o Tk la descarta

COLORS = {
    "bg": "#F6F7FB",
    "surface": "#FFFFFF",
    "border": "#E3E6ED",
    "text": "#1F2430",
    "muted": "#6B7280",
    "accent": "#4F46E5",
    "accent_dark": "#4338CA",
    "accent_light": "#E7E5FF",
    "success": "#16A34A",
    "success_light": "#DCFCE7",
    "danger": "#DC2626",
    "danger_dark": "#B91C1C",
    "warning": "#D97706",
    "stripe": "#F1F2F7",
}

FONT_FAMILY = "Segoe UI"


def set_icon(root: tk.Misc) -> None:
    """Pone el logo de Otter como ícono de la ventana (esquina y barra de
    tareas). Si por algún motivo no se encuentra el archivo, la app sigue
    funcionando igual sin ícono — nunca debe romper el arranque por esto.
    """
    global _icono_cache
    try:
        from pos_core.paths import get_resource_path
        if _icono_cache is None:
            _icono_cache = tk.PhotoImage(file=get_resource_path(_ICONO_RUTA))
        root.iconphoto(True, _icono_cache)
    except Exception:
        pass


def aplicar_tema(root: tk.Misc) -> ttk.Style:
    root.configure(background=COLORS["bg"])
    set_icon(root)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # base que sí respeta colores custom en Windows
    except tk.TclError:
        pass

    base_font = (FONT_FAMILY, 10)
    root.option_add("*Font", base_font)

    style.configure(".", background=COLORS["bg"], foreground=COLORS["text"], font=base_font)
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Card.TFrame", background=COLORS["surface"])

    style.configure("TLabelframe", background=COLORS["bg"], bordercolor=COLORS["border"], relief="solid")
    style.configure("TLabelframe.Label", background=COLORS["bg"], foreground=COLORS["muted"],
                     font=(FONT_FAMILY, 9, "bold"))

    style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("Header.TLabel", background=COLORS["bg"], foreground=COLORS["text"],
                     font=(FONT_FAMILY, 13, "bold"))
    style.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=(FONT_FAMILY, 9))
    style.configure("Total.TLabel", background=COLORS["bg"], foreground=COLORS["accent"],
                     font=(FONT_FAMILY, 22, "bold"))
    style.configure("Success.TLabel", background=COLORS["bg"], foreground=COLORS["success"],
                     font=(FONT_FAMILY, 10, "bold"))

    style.configure("TButton", background=COLORS["surface"], foreground=COLORS["text"],
                     bordercolor=COLORS["border"], focuscolor=COLORS["accent"], padding=(12, 7))
    style.map("TButton", background=[("active", COLORS["stripe"])])

    style.configure("Accent.TButton", background=COLORS["accent"], foreground="#FFFFFF",
                     padding=(18, 9), font=(FONT_FAMILY, 10, "bold"))
    style.map("Accent.TButton", background=[("active", COLORS["accent_dark"])])

    style.configure("Danger.TButton", background=COLORS["danger"], foreground="#FFFFFF", padding=(12, 7))
    style.map("Danger.TButton", background=[("active", COLORS["danger_dark"])])

    style.configure("TEntry", fieldbackground=COLORS["surface"], bordercolor=COLORS["border"], padding=6)
    style.configure("TCombobox", fieldbackground=COLORS["surface"], padding=4)

    style.configure("Treeview", background=COLORS["surface"], fieldbackground=COLORS["surface"],
                     foreground=COLORS["text"], rowheight=27, borderwidth=0)
    style.configure("Treeview.Heading", background=COLORS["accent"], foreground="#FFFFFF",
                     font=(FONT_FAMILY, 9, "bold"), relief="flat", padding=(8, 6))
    style.map("Treeview.Heading", background=[("active", COLORS["accent_dark"])])
    style.map("Treeview", background=[("selected", COLORS["accent_light"])],
              foreground=[("selected", COLORS["accent_dark"])])

    style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", background=COLORS["stripe"], foreground=COLORS["muted"],
                     padding=(16, 9), font=(FONT_FAMILY, 10))
    style.map("TNotebook.Tab", background=[("selected", COLORS["surface"])],
              foreground=[("selected", COLORS["accent"])])

    style.configure("TCheckbutton", background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("TSeparator", background=COLORS["border"])
    return style


def estriar_treeview(tree: ttk.Treeview) -> None:
    """Colores alternados por fila (ttk no lo hace solo)."""
    tree.tag_configure("odd", background=COLORS["stripe"])
    tree.tag_configure("even", background=COLORS["surface"])


def tag_fila(indice: int) -> str:
    return "odd" if indice % 2 else "even"


def celda_texto(parent, texto: str, *, font, color: str, bg: str, anchor: str = "w") -> tk.Entry:
    """Celda de una grilla armada a mano (carrito, etc.) que SE PUEDE
    seleccionar con el mouse y copiar (Ctrl+C / clic derecho): un
    tk.Entry de solo lectura en vez de un tk.Label, que visualmente se
    ve idéntico pero sí permite arrastrar el mouse sobre el texto.
    """
    justify = {"w": "left", "e": "right", "center": "center"}.get(anchor, "left")
    entry = tk.Entry(parent, font=font, fg=color, bg=bg, readonlybackground=bg,
                      relief="flat", bd=0, justify=justify, highlightthickness=0)
    entry.insert(0, texto)
    entry.config(state="readonly")
    habilitar_menu_contextual(entry)
    return entry


def _seleccionar_todo(widget) -> None:
    try:
        if isinstance(widget, tk.Text):
            widget.tag_add("sel", "1.0", "end")
        else:
            widget.select_range(0, "end")
    except tk.TclError:
        pass


def habilitar_menu_contextual(widget) -> None:
    """Clic derecho con Cortar/Copiar/Pegar/Seleccionar todo sobre un
    Entry/Combobox/Text — Tkinter no lo agrega solo como sí hacen los
    controles nativos de Windows. Ctrl+C/Ctrl+V ya andan por default en
    estos widgets; esto suma la opción visible de clic derecho."""
    menu = tk.Menu(widget, tearoff=0)
    menu.add_command(label="Cortar", command=lambda: widget.event_generate("<<Cut>>"))
    menu.add_command(label="Copiar", command=lambda: widget.event_generate("<<Copy>>"))
    menu.add_command(label="Pegar", command=lambda: widget.event_generate("<<Paste>>"))
    menu.add_separator()
    menu.add_command(label="Seleccionar todo", command=lambda: _seleccionar_todo(widget))

    def _mostrar(event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    widget.bind("<Button-3>", _mostrar)


def habilitar_copiar_treeview(tree: ttk.Treeview) -> None:
    """Clic derecho sobre una grilla (Treeview) con "Copiar fila(s)" —
    ttk.Treeview no permite seleccionar texto letra por letra, así que
    esta es la forma de poder copiar lo que se ve en una lista."""
    menu = tk.Menu(tree, tearoff=0)

    def _copiar():
        sel = tree.selection()
        if not sel:
            return
        filas = ["\t".join(str(v) for v in tree.item(i, "values")) for i in sel]
        tree.clipboard_clear()
        tree.clipboard_append("\n".join(filas))

    menu.add_command(label="Copiar fila(s) seleccionada(s)", command=_copiar)

    def _mostrar(event):
        fila = tree.identify_row(event.y)
        if fila and fila not in tree.selection():
            tree.selection_set(fila)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    tree.bind("<Button-3>", _mostrar)


def habilitar_copiar_pegar_global(root: tk.Misc) -> None:
    """Recorre TODOS los widgets ya construidos de una ventana y les
    agrega los menús de arriba según el tipo — Entry/Combobox/Text
    reciben Cortar/Copiar/Pegar, los Treeview reciben Copiar fila.
    Se llama una sola vez, al final de armar cada ventana (o Toplevel).
    """
    def _recorrer(widget):
        clase = widget.winfo_class()
        if clase in ("TEntry", "Entry", "TCombobox"):
            habilitar_menu_contextual(widget)
        elif clase == "Text":
            habilitar_menu_contextual(widget)
        elif clase == "Treeview":
            habilitar_copiar_treeview(widget)
        for hijo in widget.winfo_children():
            _recorrer(hijo)

    _recorrer(root)


def abrir_dialogo_impresora(parent: tk.Misc) -> tk.Toplevel:
    """Diálogo para elegir a qué impresora de Windows van los tickets de
    venta. Se guarda en config.ini (portable, propio de cada instalación:
    Maestro y cada USB pueden apuntar a una impresora distinta) y queda
    ahí hasta que alguien lo vuelva a cambiar — no hay que elegirla en
    cada venta. Devuelve el Toplevel para que quien lo abra pueda, por
    ejemplo, atar la devolución del foco a su cierre."""
    from pos_core import config, ticket_printer

    PREDETERMINADA = "(Predeterminada de Windows)"

    top = tk.Toplevel(parent)
    aplicar_tema(top)
    top.title("Configurar impresora de tickets")
    top.geometry("440x260")
    top.transient(parent)
    top.grab_set()

    cfg = config.cargar_config()
    actual = cfg.get("impresora", "nombre", fallback="")

    ttk.Label(top, text="Impresora para los tickets de venta:", style="Header.TLabel"
              ).pack(anchor="w", padx=16, pady=(16, 6))

    impresoras = [PREDETERMINADA] + ticket_printer.listar_impresoras()
    seleccion = tk.StringVar(value=actual if actual in impresoras else PREDETERMINADA)
    combo = ttk.Combobox(top, textvariable=seleccion, values=impresoras, state="readonly", width=48)
    combo.pack(padx=16, fill="x")

    if len(impresoras) == 1:
        ttk.Label(top, text="No se detectó ninguna impresora instalada en este sistema (o no es "
                             "Windows): mientras tanto se va a usar la predeterminada.",
                  style="Muted.TLabel", wraplength=400, justify="left").pack(padx=16, pady=(8, 0), anchor="w")

    def _probar():
        from tkinter import messagebox
        texto = ("PRUEBA DE IMPRESION\nOTTER\n" + "-" * 32 +
                 "\nSi ve este texto, la\nimpresora quedo bien\nconfigurada.\n\n\n")
        elegida = seleccion.get()
        nombre = None if elegida == PREDETERMINADA else elegida
        enviado, detalle = ticket_printer.imprimir_ticket(texto, venta_uuid="prueba", nombre_impresora=nombre)
        if enviado:
            messagebox.showinfo("Prueba enviada", f"Se envió la prueba a: {detalle}")
        else:
            messagebox.showwarning("No se pudo imprimir",
                                    f"No se pudo enviar a esa impresora; se guardó como archivo:\n{detalle}")

    def _guardar():
        from tkinter import messagebox
        elegida = seleccion.get()
        # Se escribe SOLO la clave de impresora, sobre la config recién
        # releída: guardar el `cfg` cargado al abrir el diálogo pisaría
        # cualquier otro cambio hecho mientras esta ventana estuvo abierta
        # (por ejemplo el token de ARCA o el del bot de Telegram).
        config.actualizar_config_dict(
            {"impresora": {"nombre": "" if elegida == PREDETERMINADA else elegida}})
        messagebox.showinfo("Impresora configurada",
                             f"A partir de ahora los tickets se imprimen en:\n{elegida}\n\n"
                             f"Esto queda guardado — no hace falta elegirla de nuevo.")
        top.destroy()

    botones = ttk.Frame(top)
    botones.pack(fill="x", padx=16, pady=16, side="bottom")
    ttk.Button(botones, text="Imprimir prueba", command=_probar).pack(side="left")
    ttk.Button(botones, text="Cancelar", command=top.destroy).pack(side="right")
    ttk.Button(botones, text="Guardar", style="Accent.TButton", command=_guardar).pack(side="right", padx=6)

    return top


# ====================================================================== #
# Tamaño y posición de ventanas
#
# El problema real que resuelve esto: en la PC del cliente (pantalla de
# 1366x768) la ventana del Panel del Dueño pedía 760px de alto + la barra
# de título, y el resultado quedaba TAPADO por la barra de tareas de
# Windows — botones importantes de cada pestaña caían abajo del todo y no
# se podían tocar. Ahora ninguna ventana se pide más grande que el área
# realmente usable del escritorio.
# ====================================================================== #
def area_util_pantalla(root: tk.Misc) -> tuple:
    """(x, y, ancho, alto) del escritorio SIN la barra de tareas.

    En Windows se le pregunta al sistema por el "work area" real (que ya
    descuenta la barra de tareas, esté donde esté: abajo, arriba o al
    costado). Si eso falla — o no es Windows — se cae a la pantalla
    completa menos un margen prudente, que es peor pero nunca rompe.
    """
    try:
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        SPI_GETWORKAREA = 0x0030
        if ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            ancho = rect.right - rect.left
            alto = rect.bottom - rect.top
            if ancho > 200 and alto > 200:      # sanidad: valores absurdos se descartan
                return (rect.left, rect.top, ancho, alto)
    except Exception:
        pass
    return (0, 0, root.winfo_screenwidth(), max(root.winfo_screenheight() - 48, 200))


def ajustar_ventana(root: tk.Misc, ancho: int, alto: int, *,
                    minimo: tuple = (860, 520), margen: int = 8) -> None:
    """Da a la ventana el tamaño pedido, pero nunca más que la pantalla.

    `ancho`/`alto` son el tamaño IDEAL (el que se veía bien en el equipo
    de desarrollo). Si el escritorio del cliente es más chico, se recorta
    a lo que entra y la ventana queda centrada dentro del área usable.

    `minimo` es hasta dónde se puede encoger antes de que la ventana deje
    de tener sentido; si ni eso entra, gana la pantalla — más vale una
    ventana apretada que una con los botones abajo de la barra de tareas.
    """
    x0, y0, area_ancho, area_alto = area_util_pantalla(root)

    ancho_final = min(ancho, area_ancho - margen * 2)
    alto_final = min(alto, area_alto - margen * 2)
    ancho_final = max(ancho_final, min(minimo[0], area_ancho))
    alto_final = max(alto_final, min(minimo[1], area_alto))

    x = x0 + max((area_ancho - ancho_final) // 2, 0)
    y = y0 + max((area_alto - alto_final) // 2, 0)
    root.geometry(f"{int(ancho_final)}x{int(alto_final)}+{int(x)}+{int(y)}")
    try:
        root.minsize(min(minimo[0], area_ancho), min(minimo[1], area_alto))
    except Exception:
        pass


def enlazar_rueda_mouse(canvas: tk.Canvas, widget: tk.Misc = None) -> None:
    """Hace que la rueda del mouse desplace ese canvas.

    Tk no lo trae de fábrica y además el evento es distinto según el
    sistema: Windows manda <MouseWheel> con un `delta`, y X11 (Linux, y
    los tests) manda Button-4/Button-5. Se atan los tres.

    Se ata con bind_all mientras el puntero está ENCIMA del área: si se
    atara al canvas nada más, la rueda no haría nada al estar el puntero
    sobre una fila (que es un widget hijo, no el canvas).
    """
    objetivo = widget or canvas

    def _scroll(event):
        if not canvas.winfo_exists():
            return
        if getattr(event, "num", None) == 4:
            canvas.yview_scroll(-2, "units")
        elif getattr(event, "num", None) == 5:
            canvas.yview_scroll(2, "units")
        elif getattr(event, "delta", 0):
            # En Windows delta es múltiplo de 120; en Mac es chico.
            pasos = -1 * int(event.delta / 120) if abs(event.delta) >= 120 else -1 * int(event.delta)
            canvas.yview_scroll(pasos * 2, "units")

    def _entrar(_e=None):
        canvas.bind_all("<MouseWheel>", _scroll)
        canvas.bind_all("<Button-4>", _scroll)
        canvas.bind_all("<Button-5>", _scroll)

    def _salir(_e=None):
        canvas.unbind_all("<MouseWheel>")
        canvas.unbind_all("<Button-4>")
        canvas.unbind_all("<Button-5>")

    objetivo.bind("<Enter>", _entrar, add="+")
    objetivo.bind("<Leave>", _salir, add="+")
    canvas.bind("<Destroy>", _salir, add="+")


class MarcoDesplazable(ttk.Frame):
    """Contenedor con scroll vertical automático.

    Se usa para el contenido de cada pestaña del Panel del Dueño: si la
    pantalla del cliente es chica, en vez de que los controles de abajo
    queden fuera de alcance, aparece una barra a la derecha y se llega a
    todo. Si el contenido entra, la barra ni se muestra.

    El contenido va adentro de `.interior` (un ttk.Frame normal).
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._canvas = tk.Canvas(self, bg=COLORS["bg"], highlightthickness=0)
        self._vsb = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._on_scroll_set)

        self._canvas.pack(side="left", fill="both", expand=True)
        # La barra se empaqueta/despaqueta sola según haga falta (_on_scroll_set).
        self._barra_visible = False

        self.interior = ttk.Frame(self._canvas)
        self._ventana = self._canvas.create_window((0, 0), window=self.interior, anchor="nw")

        self._alto_aplicado = None
        self.interior.bind("<Configure>", self._on_interior)
        self._canvas.bind("<Configure>", self._on_canvas)
        enlazar_rueda_mouse(self._canvas, self)

    def _on_interior(self, _event=None):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        self._ajustar_alto()

    def _on_canvas(self, event):
        # El contenido tiene que ocupar TODO el ancho del canvas: si no,
        # cualquier hijo empaquetado con fill="x" se queda del ancho de su
        # propio contenido y la pestaña se ve angosta y desalineada.
        self._canvas.itemconfigure(self._ventana, width=event.width)
        self._ajustar_alto()

    def _ajustar_alto(self):
        """El contenido ocupa, como mínimo, todo el alto visible.

        Sin esto, meter una pestaña adentro de un canvas le sacaría el
        `expand=True` a sus hijos: una lista que hoy crece hasta el fondo
        de la ventana pasaría a quedarse de su alto natural, con un
        hueco vacío abajo en cualquier pantalla grande. Forzando el alto
        de la ventana del canvas al mayor entre "lo que el contenido
        pide" y "lo que se ve", en pantallas grandes todo sigue
        expandiéndose igual que antes, y en pantallas chicas aparece la
        barra y se llega a todo.
        """
        if not self._canvas.winfo_exists():
            return
        natural = self.interior.winfo_reqheight()
        visible = self._canvas.winfo_height()
        alto = max(natural, visible)
        # Sin este guard, cambiar el alto vuelve a disparar <Configure>
        # del interior y se entra en un bucle de redibujado.
        if alto != self._alto_aplicado:
            self._alto_aplicado = alto
            self._canvas.itemconfigure(self._ventana, height=alto)

    def _on_scroll_set(self, primero, ultimo):
        hace_falta = not (float(primero) <= 0.0 and float(ultimo) >= 1.0)
        if hace_falta and not self._barra_visible:
            self._vsb.pack(side="right", fill="y")
            self._barra_visible = True
        elif not hace_falta and self._barra_visible:
            self._vsb.pack_forget()
            self._barra_visible = False
        self._vsb.set(primero, ultimo)

    def ver_widget(self, widget: tk.Misc) -> None:
        """Desplaza lo justo para que `widget` quede a la vista."""
        try:
            if not (widget.winfo_exists() and self._canvas.winfo_exists()):
                return
            self._canvas.update_idletasks()
            alto_total = max(self.interior.winfo_height(), 1)
            arriba = widget.winfo_rooty() - self.interior.winfo_rooty()
            abajo = arriba + widget.winfo_height()
            vista_alto = self._canvas.winfo_height()
            vista_arriba = self._canvas.canvasy(0)
            vista_abajo = vista_arriba + vista_alto
            if arriba < vista_arriba:
                self._canvas.yview_moveto(max(arriba - 8, 0) / alto_total)
            elif abajo > vista_abajo:
                self._canvas.yview_moveto(max(abajo - vista_alto + 8, 0) / alto_total)
        except Exception:
            pass   # que no se vea perfecto nunca puede romper la pantalla


def buscar_con_pausa(widget: tk.Misc, entrada: tk.Misc, accion, espera_ms: int = 250):
    """Ata un buscador que espera a que el usuario TERMINE de escribir.

    Sin esto, cada tecla dispara la búsqueda y el redibujado de la lista
    entera: con el catálogo real (4.587 productos) eso son ~67 ms por
    letra, y escribir "COCA COLA" se siente pegajoso, como si la máquina
    no diera abasto. Con una pausa de 250 ms se busca UNA sola vez, al
    terminar la palabra, y la escritura va fluida.

    Se cancela el temporizador anterior en cada tecla, así que solo corre
    la búsqueda de lo último que se escribió.
    """
    estado = {"pendiente": None}

    def _al_teclear(_event=None):
        if estado["pendiente"] is not None:
            try:
                widget.after_cancel(estado["pendiente"])
            except Exception:
                pass
        estado["pendiente"] = widget.after(espera_ms, _correr)

    def _correr():
        estado["pendiente"] = None
        if widget.winfo_exists():
            accion()

    def _ahora(_event=None):
        """Enter no espera: si ya apretó Enter, quiere el resultado ya."""
        if estado["pendiente"] is not None:
            try:
                widget.after_cancel(estado["pendiente"])
            except Exception:
                pass
            estado["pendiente"] = None
        _correr()

    entrada.bind("<KeyRelease>", _al_teclear, add="+")
    entrada.bind("<Return>", _ahora, add="+")
    # Se devuelven los dos handlers para poder probar la lógica de la
    # pausa sin depender de que el entorno de tests entregue eventos de
    # teclado reales (Xvfb no entrega <KeyRelease>).
    return {"al_teclear": _al_teclear, "ahora": _ahora}


def agregar_scroll_vertical(tree: tk.Misc) -> ttk.Scrollbar:
    """Le pone barra de scroll vertical a un Treeview ya creado y empaquetado.

    Los Treeview de Tk se desplazan con la rueda y las flechas, pero sin
    barra visible no hay nada que le diga al dueño que la lista sigue
    para abajo: con 4.587 productos, ver 8 y creer que son todos es un
    problema de verdad. La barra se ata al mismo padre, a la derecha.

    Devuelve la barra (normalmente no hace falta guardarla).
    """
    padre = tree.master
    barra = ttk.Scrollbar(padre, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=barra.set)

    # Se reempaqueta el árbol para que la barra quede al costado y no
    # encima: el orden de pack importa, y el Treeview ya venía empaquetado
    # ocupando todo el ancho.
    try:
        info = tree.pack_info()
        relleno_y = info.get("pady", 0)
        tree.pack_forget()
        barra.pack(side="right", fill="y", pady=relleno_y)
        tree.pack(side="left", fill="both", expand=True, pady=relleno_y)
    except Exception:
        # Si por lo que sea no estaba empaquetado con pack, se deja la
        # lista como estaba: perder la barra es mucho mejor que romper
        # la pantalla entera.
        barra.destroy()
        return None
    return barra
