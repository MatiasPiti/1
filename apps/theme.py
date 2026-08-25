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
