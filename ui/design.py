#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui/design.py -- Systeme de design PlanBA Pro (fidele a la maquette Figma).

Tokens (couleurs, typographie, rayons) + widgets reutilisables :
Card, StatCard, Badge, Chip, Banner, DataTable (Treeview sombre),
Donut, HBarChart, SidebarButton, IconButton.

Aucune logique metier ici : uniquement de la presentation.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional, Sequence, Tuple

import customtkinter as ctk

# ============================================================================
# Tokens
# ============================================================================
T = {
    "bg": "#141414",          # fond application
    "bg_panel": "#1b1b1b",    # panneaux / barres
    "bg_card": "#232323",     # cartes
    "bg_input": "#2a2a2a",    # champs
    "bg_hover": "#2f2f2f",
    "border": "#3c3c3c",
    "border_strong": "#4b4b4b",
    "text": "#e3e3e3",
    "text_dim": "#b9b9b9",
    "text_muted": "#9a9a9a",
    "accent": "#378ef0",
    "accent_dark": "#2b74c9",
    "accent_soft": "#132238",
    "success": "#43c59e",
    "success_soft": "#16342a",
    "warning": "#d99b2b",
    "warning_soft": "#332713",
    "danger": "#e86762",
    "danger_soft": "#331a19",
    "purple": "#a78bfa",
    "purple_soft": "#241a3a",
    "canvas": "#e8e8e8",
}
T["accent_soft"] = "#132238"

FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"


def font(size: int = 12, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


def mono(size: int = 11, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=MONO_FAMILY, size=size, weight=weight)


KIND_COLORS = {
    "ok": (T["success"], T["success_soft"]),
    "warn": (T["warning"], T["warning_soft"]),
    "err": (T["danger"], T["danger_soft"]),
    "info": (T["accent"], T["accent_soft"]),
    "hyp": (T["purple"], T["purple_soft"]),
    "neutral": (T["text_dim"], T["bg_input"]),
}


# ============================================================================
# Widgets de base
# ============================================================================
class Card(ctk.CTkFrame):
    """Carte sombre a bord fin."""

    def __init__(self, master, padding: int = 0, **kwargs):
        kwargs.setdefault("fg_color", T["bg_card"])
        kwargs.setdefault("corner_radius", 8)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", T["border"])
        super().__init__(master, **kwargs)
        self._padding = padding


class Badge(ctk.CTkLabel):
    """Petite pastille d'etat (Termine, A verifier, Erreur, Hypothese...)."""

    def __init__(self, master, text: str = "", kind: str = "neutral",
                 size: int = 10, **kwargs):
        fg, bg = KIND_COLORS.get(kind, KIND_COLORS["neutral"])
        kwargs.setdefault("fg_color", bg)
        kwargs.setdefault("text_color", fg)
        kwargs.setdefault("corner_radius", 4)
        kwargs.setdefault("padx", 7)
        kwargs.setdefault("pady", 1)
        super().__init__(master, text=text, font=font(size, "bold"), **kwargs)


class Chip(ctk.CTkButton):
    """Puce cliquable (Vide / Survol / OK / Reserve / Erreur)."""

    def __init__(self, master, text: str, command=None, active: bool = False,
                 **kwargs):
        super().__init__(
            master, text=text, command=command,
            font=font(9),
            height=20, width=max(38, 7 * len(text)),
            corner_radius=3,
            fg_color=T["accent_soft"] if active else T["bg_input"],
            hover_color=T["bg_hover"],
            text_color=T["accent"] if active else T["text_dim"],
            **kwargs)


class StatCard(Card):
    """Carte indicateur : libelle, valeur + unite, mention de source."""

    def __init__(self, master, label: str, value: str = "-", unit: str = "",
                 caption: str = "Calculé · source vérifiable", **kwargs):
        super().__init__(master, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=label.upper(), font=font(9),
                     text_color=T["text_muted"]).grid(
            row=0, column=0, sticky="w", padx=12, pady=(7, 0))
        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.grid(row=1, column=0, sticky="w", padx=12)
        self.lbl_value = ctk.CTkLabel(wrap, text=value, font=font(19, "bold"),
                                      text_color=T["text"])
        self.lbl_value.pack(side="left", pady=(0, 0))
        self.lbl_unit = ctk.CTkLabel(wrap, text=unit, font=font(11),
                                     text_color=T["text_dim"])
        self.lbl_unit.pack(side="left", padx=(5, 0), pady=(6, 0))
        self.lbl_caption = ctk.CTkLabel(self, text=caption, font=font(9),
                                        text_color=T["text_muted"],
                                        anchor="w")
        self.lbl_caption.grid(row=2, column=0, sticky="w", padx=12,
                              pady=(0, 7))

    def set(self, value: str, unit: Optional[str] = None,
            caption: Optional[str] = None, kind: Optional[str] = None):
        self.lbl_value.configure(text=value)
        if unit is not None:
            self.lbl_unit.configure(text=unit)
        if caption is not None:
            self.lbl_caption.configure(text=caption)
        if kind:
            fg = KIND_COLORS.get(kind, (T["text"], None))[0]
            self.lbl_value.configure(text_color=fg)


class Banner(ctk.CTkFrame):
    """Bandeau d'alerte pleine largeur (warning / info / danger / ok)."""

    def __init__(self, master, text: str = "", kind: str = "warn", **kwargs):
        fg, bg = KIND_COLORS.get(kind, KIND_COLORS["warn"])
        kwargs.setdefault("fg_color", bg)
        kwargs.setdefault("corner_radius", 6)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", fg)
        super().__init__(master, **kwargs)
        self._fg = fg
        self.lbl = ctk.CTkLabel(self, text=text, font=font(11, "bold"),
                                text_color=fg, anchor="w", justify="left")
        self.lbl.pack(fill="x", padx=12, pady=6)

    def set(self, text: str, kind: Optional[str] = None):
        if kind:
            fg, bg = KIND_COLORS.get(kind, KIND_COLORS["warn"])
            self.configure(fg_color=bg, border_color=fg)
            self.lbl.configure(text_color=fg)
        self.lbl.configure(text=text)

    def hide(self):
        self.pack_forget()

    def show(self, **pack_kwargs):
        self.pack(**pack_kwargs)


class SidebarButton(ctk.CTkButton):
    """Bouton de la barre laterale (pleine largeur, icone + libelle)."""

    def __init__(self, master, text: str, command=None, primary: bool = False,
                 **kwargs):
        super().__init__(
            master, text=text, command=command,
            font=font(12, "bold" if primary else "normal"),
            height=40, corner_radius=6, anchor="w",
            fg_color=T["accent"] if primary else T["bg_card"],
            hover_color=T["accent_dark"] if primary else T["bg_hover"],
            text_color="#ffffff" if primary else T["text_dim"],
            border_width=0 if primary else 1,
            border_color=T["border"],
            **kwargs)


# ============================================================================
# Tableau de donnees (Treeview sombre, zebra, badges par tag)
# ============================================================================
_TREE_STYLE_READY = False


def _init_tree_style():
    global _TREE_STYLE_READY
    if _TREE_STYLE_READY:
        return
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "PlanBA.Treeview",
        background=T["bg_card"], fieldbackground=T["bg_card"],
        foreground=T["text"], rowheight=26, borderwidth=0,
        font=(FONT_FAMILY, 9))
    style.configure(
        "PlanBA.Treeview.Heading",
        background=T["bg_panel"], foreground=T["text_dim"],
        relief="flat", borderwidth=0, font=(FONT_FAMILY, 9, "bold"))
    style.map("PlanBA.Treeview.Heading",
              background=[("active", T["bg_hover"])])
    style.map("PlanBA.Treeview",
              background=[("selected", T["accent_soft"])],
              foreground=[("selected", T["text"])])
    style.layout("PlanBA.Treeview", [
        ("PlanBA.Treeview.treearea", {"sticky": "nswe"})])
    _TREE_STYLE_READY = True


class DataTable(ctk.CTkFrame):
    """Tableau dense : entetes fixes, lignes zebra, couleurs par statut.

    columns : liste de tuples (cle, titre, largeur, alignement)
    rows    : liste de dicts {cle: valeur, '_tag': 'ok'|'warn'|'err'|...}
    """

    def __init__(self, master, columns: Sequence[Tuple[str, str, int, str]],
                 height: int = 8, **kwargs):
        kwargs.setdefault("fg_color", T["bg_card"])
        kwargs.setdefault("corner_radius", 0)
        # Sans height=0, CTkFrame demande 200 px et gonfle la rangee.
        kwargs.setdefault("height", 0)
        super().__init__(master, **kwargs)
        _init_tree_style()
        self._columns = list(columns)
        self._tags = {
            "ok": (T["success"], None),
            "warn": (T["warning"], None),
            "err": (T["danger"], None),
            "hyp": (T["purple"], None),
            "info": (T["accent"], None),
            "strong": ("#ffffff", "#26313f"),
            "dim": (T["text_muted"], None),
            "group": (T["text_dim"], T["bg_input"]),
        }
        self.tree = ttk.Treeview(
            self, columns=[c[0] for c in self._columns], show="headings",
            height=height, style="PlanBA.Treeview", selectmode="browse")
        for key, title, width, anchor in self._columns:
            self.tree.heading(key, text=title, anchor=anchor.lower())
            self.tree.column(key, width=width, minwidth=40, anchor=anchor.lower(),
                             stretch=True)
        for name, (fg, bg) in self._tags.items():
            if bg:
                self.tree.tag_configure(name, foreground=fg, background=bg)
            else:
                self.tree.tag_configure(name, foreground=fg)
        self.tree.tag_configure("odd", background="#1f1f1f")
        self.tree.tag_configure("even", background=T["bg_card"])

        self.vsb = ctk.CTkScrollbar(self, command=self.tree.yview,
                                    fg_color="transparent", height=0, width=12)
        self.hsb = ctk.CTkScrollbar(self, command=self.tree.xview,
                                    orientation="horizontal",
                                    fg_color="transparent", height=12, width=0)
        self.tree.configure(yscrollcommand=self.vsb.set,
                            xscrollcommand=self.hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.hsb.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

    def clear(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def set_rows(self, rows: Sequence[dict]):
        self.clear()
        for idx, row in enumerate(rows):
            tag = row.get("_tag") or ("even" if idx % 2 == 0 else "odd")
            values = [row.get(c[0], "") for c in self._columns]
            self.tree.insert("", "end", values=values, tags=(tag,))


# ============================================================================
# Graphiques (Canvas natifs, sans dependance)
# ============================================================================
class Donut(ctk.CTkFrame):
    """Donut 'beton par famille' avec total au centre."""

    PALETTE = ("#378ef0", "#f0a03a", "#43c59e", "#8b5cf6", "#d9534f",
               "#6b8afd")

    def __init__(self, master, width: int = 240, height: int = 190, **kwargs):
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)
        self._data = []
        self._total = 0.0
        self._unit = "m³"
        self.canvas = tk.Canvas(self, width=width, height=height,
                                bg=T["bg_card"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)

    def _on_resize(self, _event=None):
        if self._data:
            self.set_data(self._data, self._total, self._unit)

    def set_data(self, data: Sequence[Tuple[str, float]], total: float,
                 unit: str = "m³"):
        self._data = list(data)
        self._total = total
        self._unit = unit
        c = self.canvas
        c.delete("all")
        w = int(c.winfo_width() or c["width"])
        h = int(c.winfo_height() or c["height"])
        # La legende occupe le bas : l'anneau se cale dans l'espace restant.
        n = len(self._data)
        legend_rows = max(1, (n + 2) // 3)
        legend_h = 10 + legend_rows * 13
        area_h = max(48, h - legend_h)
        size = max(44, min(w - 24, area_h - 12, 130))
        cx, cy = w / 2, area_h / 2
        x0, y0 = cx - size / 2, cy - size / 2
        x1, y1 = cx + size / 2, cy + size / 2
        values = [max(0.0, float(v)) for _, v in data]
        s = sum(values)
        if s <= 0:
            c.create_oval(x0, y0, x1, y1, outline=T["border"], width=14)
        else:
            start = 90.0
            for i, (label, v) in enumerate(data):
                if v <= 0:
                    continue
                extent = -360.0 * (v / s)
                c.create_arc(x0, y0, x1, y1, start=start, extent=extent,
                             outline=self.PALETTE[i % len(self.PALETTE)],
                             width=14, style="arc")
                start += extent
        c.create_text(cx, cy - 5, text=f"{total:,.2f}".replace(",", " "),
                      fill=T["text"], font=(FONT_FAMILY, 14, "bold"))
        c.create_text(cx, cy + 11, text=unit, fill=T["text_muted"],
                      font=(FONT_FAMILY, 8))
        # legende horizontale (3 colonnes), sous l'anneau
        lx = 10
        for i, (label, v) in enumerate(data):
            col = i % 3
            row = i // 3
            px = lx + col * ((w - 2 * lx) / 3)
            py = h - 10 - (legend_rows - 1 - row) * 13
            c.create_rectangle(px, py - 4, px + 7, py + 4,
                               fill=self.PALETTE[i % len(self.PALETTE)],
                               outline="")
            c.create_text(px + 11, py, text=label[:13], anchor="w",
                          fill=T["text_muted"], font=(FONT_FAMILY, 8))


class HBarChart(ctk.CTkFrame):
    """Barres horizontales 'acier par diametre' avec valeur a droite."""

    def __init__(self, master, height: int = 190, **kwargs):
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)
        self._data = []
        self._unit = "kg"
        self.canvas = tk.Canvas(self, height=height, bg=T["bg_card"],
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)

    def _on_resize(self, _event=None):
        if self._data:
            self.set_data(self._data, self._unit)

    def set_data(self, data: Sequence[Tuple[str, float]], unit: str = "kg"):
        self._data = list(data)
        self._unit = unit
        c = self.canvas
        c.delete("all")
        w = int(c.winfo_width() or 320)
        h = int(c.winfo_height() or 190)
        if not data:
            c.create_text(w / 2, h / 2, text="Aucune donnée",
                          fill=T["text_muted"], font=(FONT_FAMILY, 9))
            return
        vmax = max(v for _, v in data) or 1.0
        n = len(data)
        # Tout doit tenir dans la hauteur disponible (sinon la derniere barre
        # deborde du cadre) : on reduit la hauteur de ligne si necessaire.
        avail = max(40, h - 12)
        row_h = max(11, min(24, avail / max(n, 1)))
        top = max(4, (h - row_h * n) / 2)
        label_w = 42
        value_w = 64
        bar_max = max(40, w - label_w - value_w - 16)
        for i, (label, v) in enumerate(data):
            y = top + i * row_h + row_h / 2
            c.create_text(8, y, text=label, anchor="w", fill=T["text_dim"],
                          font=(FONT_FAMILY, 9))
            bw = max(2, bar_max * (v / vmax))
            c.create_rectangle(label_w, y - 4, label_w + bw, y + 4,
                               fill=T["accent"], outline="")
            c.create_text(w - 8, y, text=f"{v:,.0f} {unit}".replace(",", " "),
                          anchor="e", fill=T["text"], font=(FONT_FAMILY, 9))
