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
        if "impresora" not in cfg:
            cfg["impresora"] = {}
        cfg.set("impresora", "nombre", "" if elegida == PREDETERMINADA else elegida)
        config.guardar_config(cfg)
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
