"""Theming for MicDrop.

Tkinter's native "vista"/"xpnative" ttk themes ignore custom colors on buttons,
so we base everything on the "clam" theme (which honours `configure`/`map` colors)
and drive it from a palette. `apply_theme` returns the active palette dict; the clip
tiles are plain `tk` widgets coloured straight from it.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# -- fonts -------------------------------------------------------------------
FONT_BASE = ("Segoe UI", 10)
FONT_TILE = ("Segoe UI Semibold", 11)
FONT_BADGE = ("Segoe UI", 9)
FONT_HEADING = ("Segoe UI Semibold", 10)

# -- palettes ----------------------------------------------------------------
DARK = {
    "bg": "#1e1f22",
    "surface": "#2b2d31",
    "surface_hover": "#3a3d44",
    "surface_active": "#45484f",
    "text": "#e6e7ea",
    "muted": "#9aa0a6",
    "accent": "#4f8cff",
    "accent_hover": "#6ea0ff",
    "accent_text": "#ffffff",
    "danger": "#e5534b",
    "danger_hover": "#f06a63",
    "border": "#3a3d44",
    "trough": "#3a3d44",
}

LIGHT = {
    "bg": "#f2f3f5",
    "surface": "#ffffff",
    "surface_hover": "#eceef1",
    "surface_active": "#e2e5e9",
    "text": "#1a1c1e",
    "muted": "#5c636b",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_text": "#ffffff",
    "danger": "#dc2626",
    "danger_hover": "#b91c1c",
    "border": "#d5d9de",
    "trough": "#d5d9de",
}

PALETTES = {"dark": DARK, "light": LIGHT}


def palette_for(mode: str) -> dict:
    return dict(PALETTES.get(mode, DARK))


def apply_theme(root: tk.Tk, mode: str) -> dict:
    """Apply the palette for `mode` to `root` and all ttk widgets. Returns the palette."""
    p = palette_for(mode)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=p["bg"])
    # Default font + colors for any tk (non-ttk) widget created later.
    root.option_add("*Font", FONT_BASE)
    root.option_add("*Menu.background", p["surface"])
    root.option_add("*Menu.foreground", p["text"])
    root.option_add("*Menu.activeBackground", p["accent"])
    root.option_add("*Menu.activeForeground", p["accent_text"])
    root.option_add("*Menu.relief", "flat")
    # Combobox dropdown list (a classic Tk listbox under the hood).
    root.option_add("*TCombobox*Listbox.background", p["surface"])
    root.option_add("*TCombobox*Listbox.foreground", p["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", p["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", p["accent_text"])

    style.configure(".", font=FONT_BASE, background=p["bg"], foreground=p["text"])
    style.configure("TFrame", background=p["bg"])
    style.configure("Card.TFrame", background=p["surface"])
    style.configure("TLabel", background=p["bg"], foreground=p["text"])
    style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
    style.configure(
        "Status.TLabel", background=p["surface"], foreground=p["muted"], padding=(8, 3)
    )
    style.configure("Heading.TLabel", background=p["bg"], foreground=p["text"], font=FONT_HEADING)

    # Generic (neutral) button.
    style.configure(
        "TButton",
        background=p["surface"],
        foreground=p["text"],
        bordercolor=p["border"],
        focuscolor=p["accent"],
        relief="flat",
        padding=(10, 6),
    )
    style.map(
        "TButton",
        background=[("active", p["surface_hover"]), ("pressed", p["surface_active"])],
        foreground=[("disabled", p["muted"])],
    )

    # Accent + danger buttons.
    style.configure(
        "Accent.TButton",
        background=p["accent"],
        foreground=p["accent_text"],
        bordercolor=p["accent"],
        relief="flat",
        padding=(10, 6),
    )
    style.map(
        "Accent.TButton",
        background=[("active", p["accent_hover"]), ("pressed", p["accent_hover"])],
        foreground=[("disabled", p["muted"])],
    )
    style.configure(
        "Danger.TButton",
        background=p["danger"],
        foreground=p["accent_text"],
        bordercolor=p["danger"],
        relief="flat",
        padding=(10, 6),
    )
    style.map(
        "Danger.TButton",
        background=[("active", p["danger_hover"]), ("pressed", p["danger_hover"])],
        foreground=[("disabled", p["muted"])],
    )

    # Inputs.
    for name in ("TEntry", "TSpinbox"):
        style.configure(
            name,
            fieldbackground=p["surface"],
            background=p["surface"],
            foreground=p["text"],
            bordercolor=p["border"],
            insertcolor=p["text"],
            arrowcolor=p["text"],
            padding=4,
        )
        style.map(name, fieldbackground=[("readonly", p["surface"])])

    style.configure(
        "TCombobox",
        fieldbackground=p["surface"],
        background=p["surface"],
        foreground=p["text"],
        arrowcolor=p["text"],
        bordercolor=p["border"],
        padding=4,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", p["surface"])],
        foreground=[("readonly", p["text"])],
        selectbackground=[("readonly", p["surface"])],
        selectforeground=[("readonly", p["text"])],
    )

    style.configure("TCheckbutton", background=p["bg"], foreground=p["text"])
    style.map(
        "TCheckbutton",
        background=[("active", p["bg"])],
        foreground=[("disabled", p["muted"])],
    )

    style.configure(
        "TScale", background=p["bg"], troughcolor=p["trough"], bordercolor=p["border"]
    )
    style.configure(
        "Horizontal.TScale", background=p["bg"], troughcolor=p["trough"]
    )

    style.configure(
        "Vertical.TScrollbar",
        background=p["surface"],
        troughcolor=p["bg"],
        bordercolor=p["bg"],
        arrowcolor=p["muted"],
    )
    style.map("Vertical.TScrollbar", background=[("active", p["surface_hover"])])

    return p
