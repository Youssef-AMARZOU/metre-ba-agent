#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui/home.py -- Ecran d'accueil "Projets récents" (maquette Figma).

Cartes projets lues dans `core.history` (100 % local) : aperçu schématique,
statut, fichier source + date, 3 indicateurs (béton, acier, chute) et bouton
"Ouvrir" qui recharge `plan_data.json` du dossier de sortie.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk

from core.history import load_history
from ui.design import Badge, Card, T, font


class ProjectCard(Card):
    """Carte projet : aperçu, statut, nom, source, KPI, action."""

    def __init__(self, master, entry: Dict[str, Any],
                 on_open: Callable[[Dict[str, Any]], None], **kwargs):
        super().__init__(master, **kwargs)
        self.entry = entry
        self.grid_columnconfigure(0, weight=1)

        # Aperçu (grille de plan schematique)
        preview = tk.Canvas(self, height=104, bg="#e9e9e9",
                            highlightthickness=0)
        preview.grid(row=0, column=0, sticky="ew", padx=1, pady=1)
        preview.bind("<Configure>", lambda e, c=preview: self._draw_grid(c))

        statut = entry.get("statut", "Terminé")
        kind = "ok" if statut.lower().startswith("termin") else "warn"
        self.badge = Badge(self, statut, kind, size=8)
        self.badge.place(relx=1.0, x=-8, y=8, anchor="ne")

        ctk.CTkLabel(self, text=entry.get("nom", "Projet"), font=font(14, "bold"),
                     text_color=T["text"], anchor="w").grid(
            row=1, column=0, sticky="w", padx=12, pady=(10, 0))
        ctk.CTkLabel(
            self, text=f"{entry.get('fichier', '')} · {entry.get('date', '')}",
            font=font(9), text_color=T["text_muted"], anchor="w").grid(
            row=2, column=0, sticky="w", padx=12, pady=(2, 8))

        kpis = ctk.CTkFrame(self, fg_color="transparent")
        kpis.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 10))
        kpis.grid_columnconfigure((0, 1, 2), weight=1, uniform="k")
        valeurs = [
            (f"{entry.get('beton', 0):.2f}".replace(".", ","), "m³", "Béton"),
            (f"{entry.get('acier', 0):,.0f}".replace(",", " "), "kg", "Acier"),
            (f"{entry.get('chute', 0):.1f}".replace(".", ","), "%", "Chute"),
        ]
        for idx, (val, unit, label) in enumerate(valeurs):
            cell = ctk.CTkFrame(kpis, fg_color="transparent", corner_radius=4,
                                border_width=1, border_color=T["border"])
            cell.grid(row=0, column=idx, sticky="ew",
                      padx=(0 if idx == 0 else 5, 0))
            line = ctk.CTkFrame(cell, fg_color="transparent")
            line.pack(anchor="w", padx=7, pady=(4, 0))
            ctk.CTkLabel(line, text=val, font=font(12, "bold"),
                         text_color=T["text"]).pack(side="left")
            ctk.CTkLabel(line, text=unit, font=font(8),
                         text_color=T["text_dim"]).pack(side="left",
                                                        padx=(3, 0),
                                                        pady=(2, 0))
            ctk.CTkLabel(cell, text=label, font=font(8),
                         text_color=T["text_muted"]).pack(anchor="w", padx=7,
                                                          pady=(0, 4))

        ctk.CTkButton(
            self, text="Ouvrir", font=font(10, "bold"), height=26, width=90,
            corner_radius=4, fg_color=T["accent"], hover_color=T["accent_dark"],
            command=lambda: on_open(self.entry)).grid(
            row=4, column=0, sticky="w", padx=12, pady=(0, 10))

    def _draw_grid(self, canvas: tk.Canvas):
        canvas.delete("all")
        w = max(canvas.winfo_width(), 10)
        h = max(canvas.winfo_height(), 10)
        step = 16
        for x in range(0, w, step):
            canvas.create_line(x, 0, x, h, fill="#dcdcdc")
        for y in range(0, h, step):
            canvas.create_line(0, y, w, y, fill="#dcdcdc")
        # volumes schematiques (jamais de cote inventee : simple vignette)
        canvas.create_rectangle(w * 0.12, h * 0.22, w * 0.42, h * 0.62,
                                outline="#9aa4ae", width=2)
        canvas.create_rectangle(w * 0.50, h * 0.30, w * 0.84, h * 0.70,
                                outline="#9aa4ae", width=2)
        canvas.create_line(w * 0.12, h * 0.86, w * 0.84, h * 0.86,
                           fill="#b9c0c7")


class HomeView(ctk.CTkFrame):
    """Accueil : titre, recherche, grille de cartes projets."""

    def __init__(self, master, on_new: Callable[[], None],
                 on_open_plan: Callable[[], None],
                 on_open_project: Callable[[Dict[str, Any]], None], **kwargs):
        kwargs.setdefault("fg_color", T["bg"])
        super().__init__(master, **kwargs)
        self.on_open_project = on_open_project
        self._entries: List[Dict[str, Any]] = []
        self._build(on_new, on_open_plan)
        self.refresh()

    def _build(self, on_new, on_open_plan):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 10))
        head.grid_columnconfigure(0, weight=1)
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(titles, text="Projets récents", font=font(24, "bold"),
                     text_color=T["text"]).pack(anchor="w")
        ctk.CTkLabel(titles, text="Reprenez une analyse locale.",
                     font=font(10), text_color=T["text_muted"]).pack(
            anchor="w", pady=(3, 0))

        right = ctk.CTkFrame(head, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        search_box = ctk.CTkFrame(right, fg_color=T["bg_input"],
                                  corner_radius=6, border_width=1,
                                  border_color=T["border"])
        search_box.pack()
        ctk.CTkLabel(search_box, text="⌕", font=font(13),
                     text_color=T["text_muted"]).pack(side="left", padx=(9, 0))
        self.search = ctk.CTkEntry(
            search_box, placeholder_text="Rechercher…", width=210, height=30,
            font=font(10), fg_color=T["bg_input"], border_width=0,
            corner_radius=0)
        self.search.pack(side="left", padx=(4, 6))
        self.search.bind("<KeyRelease>", lambda _e: self._render())

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=22, pady=(0, 10))
        self._cols = 2
        self._apply_col_weights()
        try:
            self.scroll._parent_canvas.configure(yscrollincrement=4)
        except Exception:
            pass
        self.scroll.bind("<Configure>", self._on_resize)
        self.on_open_plan = on_open_plan
        self._entries: List[Dict[str, Any]] = []
        self._resize_job = None

    def _on_resize(self, _event=None):
        """Debounce : un rendu par rafale de redimensionnements."""
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.after(160, self._apply_columns)

    def _apply_columns(self):
        """2 colonnes sous 1120 px de contenu, 3 au-dela (maquette)."""
        self._resize_job = None
        width = self.scroll.winfo_width()
        if width < 80:
            return
        cols = 3 if width >= 1120 else 2
        if cols != self._cols:
            self._cols = cols
            self._apply_col_weights()
            self._render()

    def _apply_col_weights(self):
        for i in range(3):
            self.scroll.grid_columnconfigure(
                i, weight=1 if i < self._cols else 0,
                uniform="cards" if i < self._cols else "")

    # ------------------------------------------------------------------
    def refresh(self, entries: Optional[List[Dict[str, Any]]] = None):
        self._entries = entries if entries is not None else load_history()
        self._render()

    def _render(self):
        for child in self.scroll.winfo_children():
            child.destroy()
        needle = ""
        try:
            needle = (self.search.get() or "").strip().lower()
        except Exception:
            pass
        entries = [e for e in self._entries
                   if not needle
                   or needle in str(e.get("nom", "")).lower()
                   or needle in str(e.get("fichier", "")).lower()]

        if not entries:
            empty = Card(self.scroll, height=220)
            empty.grid(row=0, column=0, columnspan=self._cols, sticky="ew",
                       pady=4)
            ctk.CTkLabel(empty, text="Aucun projet pour l'instant",
                         font=font(13, "bold"), text_color=T["text"]).pack(
                pady=(50, 4))
            ctk.CTkLabel(
                empty, text="Lancez un nouveau métré : le projet apparaîtra ici "
                            "avec ses indicateurs.", font=font(10),
                text_color=T["text_muted"]).pack()
            ctk.CTkButton(
                empty, text="＋  Nouveau métré", font=font(11, "bold"),
                height=32, corner_radius=5, fg_color=T["accent"],
                hover_color=T["accent_dark"],
                command=self.on_open_plan).pack(pady=14)
            return

        cols = self._cols
        for idx, entry in enumerate(entries):
            card = ProjectCard(self.scroll, entry, self.on_open_project)
            card.grid(row=idx // cols, column=idx % cols, sticky="nsew",
                      padx=8, pady=8, ipadx=0)
