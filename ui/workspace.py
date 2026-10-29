#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui/workspace.py -- Ecran de resultats PlanBA Pro (maquette Figma).

Structure :
  en-tete projet (nom + statut + duree) + barre d'actions livrables
  bandeau d'avertissements
  zone haute :  Synthèse (KPI + donut + barres + limites)  |  Aperçu du plan
  zone basse :  ÉLÉMENTS | ARMATURES | DÉCOUPE | SURFACES | AUDIT | JOURNAL
  barre d'etat

Le module ne calcule rien : il peint le view-model de
`core.bet_view_model.build_view_model` et delegue les actions a l'app.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import tkinter as tk

import customtkinter as ctk

from core.bet_view_model import build_view_model
from ui.design import (Badge, Banner, Card, DataTable, Donut, HBarChart,
                       StatCard, T, font, mono)


class ResultsWorkspace(ctk.CTkFrame):
    def __init__(self, master, on_action: Optional[Callable[[str], None]] = None,
                 **kwargs):
        kwargs.setdefault("fg_color", T["bg"])
        super().__init__(master, **kwargs)
        self.on_action = on_action or (lambda *_: None)
        self.vm: Dict[str, Any] = build_view_model(None)
        self._plan_path: Optional[str] = None
        self._overlay_paths: List[str] = []
        self._preview_img = None
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # --- En-tete -------------------------------------------------
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        head.grid_columnconfigure(0, weight=1)

        left = ctk.CTkFrame(head, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")
        line1 = ctk.CTkFrame(left, fg_color="transparent")
        line1.pack(anchor="w")
        self.lbl_projet = ctk.CTkLabel(line1, text="Projet", font=font(17, "bold"),
                                       text_color=T["text"])
        self.lbl_projet.pack(side="left")
        self.badge_statut = Badge(line1, "Terminé", "ok", size=10)
        self.badge_statut.pack(side="left", padx=(10, 0))
        self.lbl_duree = ctk.CTkLabel(line1, text="", font=font(10),
                                      text_color=T["text_muted"])
        self.lbl_duree.pack(side="left", padx=(10, 0))
        self.lbl_sous_titre = ctk.CTkLabel(
            left, text="", font=font(10), text_color=T["text_muted"])
        self.lbl_sous_titre.pack(anchor="w", pady=(2, 0))

        actions = ctk.CTkFrame(head, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="e")
        self.actions: Dict[str, ctk.CTkButton] = {}
        # Retour au menu des plans (historique local)
        ctk.CTkButton(
            actions, text="← Mes plans", font=font(11), height=30, width=0,
            corner_radius=5, fg_color="transparent", hover_color=T["bg_hover"],
            border_width=1, border_color=T["border"], text_color=T["text_dim"],
            command=lambda: self.on_action("plans")).pack(side="left",
                                                          padx=(0, 6))
        for key, label, primary in (
                ("metre", "📂  Métré BA (Excel)", True),
                ("surfaces", "📐  Surfaces", False),
                ("overlay", "🔍  Overlay", False),
                ("corriger", "✏️  Corriger", False),
                ("dossier", "📁  Dossier", False)):
            btn = ctk.CTkButton(
                actions, text=label, font=font(11), height=30, width=0,
                corner_radius=5,
                fg_color=T["accent"] if primary else T["bg_card"],
                hover_color=T["accent_dark"] if primary else T["bg_hover"],
                border_width=0 if primary else 1,
                border_color=T["border"],
                text_color="#ffffff" if primary else T["text_dim"],
                text_color_disabled=T["text_muted"],
                command=lambda k=key: self.on_action(k))
            btn.pack(side="left", padx=(6, 0))
            if key in ("metre", "surfaces", "overlay", "corriger"):
                btn.configure(state="disabled")
                if primary:
                    btn.configure(fg_color=T["accent_soft"],
                                  hover_color=T["accent_soft"],
                                  text_color=T["text_muted"])
            self.actions[key] = btn

        self.banner = Banner(self, "", "warn")
        self.banner.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))

        # --- Corps : haut (synthese / apercu) + bas (tables) ----------
        # height=0 : sinon CTkFrame reclame 200 px (x1.25) et gonfle la rangee.
        body = ctk.CTkFrame(self, fg_color="transparent", height=0)
        body.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 6))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1, minsize=390)
        body.grid_rowconfigure(2, weight=0, minsize=180)

        top_tabs = ctk.CTkSegmentedButton(
            body, values=["Synthèse", "Aperçu du plan"], font=font(11),
            height=26, corner_radius=4,
            selected_color=T["accent_soft"], selected_hover_color=T["accent_soft"],
            unselected_color=T["bg_panel"], unselected_hover_color=T["bg_hover"],
            text_color=T["text"], fg_color=T["bg_panel"],
            command=self._switch_top)
        top_tabs.set("Synthèse")
        top_tabs.grid(row=0, column=0, sticky="w")

        self.top_host = ctk.CTkFrame(body, fg_color="transparent", height=0)
        self.top_host.grid(row=1, column=0, sticky="nsew", pady=(8, 8))
        self.top_host.grid_columnconfigure(0, weight=1)
        self.top_host.grid_rowconfigure(0, weight=1)

        self.view_synthese = self._build_synthese(self.top_host)
        self.view_apercu = self._build_apercu(self.top_host)
        self.view_synthese.grid(row=0, column=0, sticky="nsew")
        self.view_apercu.grid(row=0, column=0, sticky="nsew")
        # Les deux vues partagent la meme cellule : l'apercu est masque au
        # demarrage (sinon il recouvre la synthese).
        self.view_apercu.grid_remove()

        self.tables_host = ctk.CTkFrame(body, fg_color="transparent", height=0)
        self.tables_host.grid(row=2, column=0, sticky="nsew")
        self.tables_host.grid_columnconfigure(0, weight=1)
        self.tables_host.grid_rowconfigure(1, weight=1)

        self.TAB_NAMES = ["ÉLÉMENTS", "ARMATURES", "DÉCOUPE", "SURFACES",
                          "AUDIT", "JOURNAL"]
        tabbar = ctk.CTkFrame(self.tables_host, fg_color=T["bg_panel"],
                              corner_radius=0)
        tabbar.grid(row=0, column=0, sticky="ew")
        self._tab_labels: Dict[str, ctk.CTkLabel] = {}
        self._tab_underlines: Dict[str, tk.Frame] = {}
        for name in self.TAB_NAMES:
            # tk.Frame : CTkFrame impose sa largeur par defaut dans pack()
            col = tk.Frame(tabbar, bg=T["bg_panel"], highlightthickness=0)
            lbl = ctk.CTkLabel(col, text=name, font=font(9, "bold"),
                               text_color=T["text_muted"])
            lbl.pack(padx=12, pady=5)
            line = tk.Frame(col, height=2, bg=T["bg_panel"],
                            highlightthickness=0)
            line.pack(fill="x")
            col.pack(side="left")
            lbl.bind("<Button-1>", lambda _e, n=name: self._switch_bottom(n))
            lbl.configure(cursor="hand2")
            self._tab_labels[name] = lbl
            self._tab_underlines[name] = line
        self._current_table = "ÉLÉMENTS"
        self._set_active_tab("ÉLÉMENTS")

        self.tbl_host = ctk.CTkFrame(self.tables_host, fg_color=T["bg_card"],
                                     corner_radius=0, height=0)
        self.tbl_host.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.tbl_host.grid_columnconfigure(0, weight=1)
        self.tbl_host.grid_rowconfigure(0, weight=1)

        self.tbl_elements = DataTable(self.tbl_host, [
            ("repere", "Repère", 90, "w"),
            ("type", "Type", 110, "w"),
            ("axes", "Axe / File", 90, "center"),
            ("dimensions", "Dimensions", 150, "w"),
            ("quantite", "Nbe", 50, "center"),
            ("aciers", "Armatures", 200, "w"),
            ("niveau", "Niveau", 90, "w"),
        ], height=4)
        self.tbl_armatures = DataTable(self.tbl_host, [
            ("repere", "Repère", 110, "w"),
            ("categorie", "Type", 120, "w"),
            ("marque", "Armature", 180, "w"),
            ("diametre", "Ø (mm)", 60, "center"),
            ("nb", "Nb barres", 70, "center"),
            ("longueur", "Long. unit. (m)", 100, "e"),
            ("total", "Long. totale (m)", 110, "e"),
        ], height=4)
        self.tbl_decoupe = DataTable(self.tbl_host, [
            ("diametre", "Diamètre", 90, "w"),
            ("barres", "Barres de 12 m", 110, "center"),
            ("achetee", "Longueur achetée", 130, "e"),
            ("utile", "Longueur utile", 120, "e"),
            ("chute", "Chute", 90, "e"),
            ("taux", "Taux de chute", 100, "e"),
            ("poids", "Poids acheté (kg)", 120, "e"),
        ], height=4)
        self.tbl_surfaces = DataTable(self.tbl_host, [
            ("niveau", "Étage", 80, "w"),
            ("piece", "Pièce", 190, "w"),
            ("surface", "Surface (m²)", 100, "e"),
            ("perimetre", "Périmètre (m)", 100, "e"),
            ("source", "Source", 130, "w"),
            ("confiance", "Confiance", 90, "center"),
            ("observation", "Observations", 220, "w"),
        ], height=4)
        self.tbl_audit = DataTable(self.tbl_host, [
            ("severite", "Niveau", 90, "w"),
            ("element", "Élément", 120, "w"),
            ("regle", "Règle", 200, "w"),
            ("message", "Constat", 420, "w"),
        ], height=4)
        self.tbl_journal = ctk.CTkTextbox(
            self.tbl_host, font=mono(10), fg_color=T["bg_card"],
            text_color=T["text_dim"], border_width=0, corner_radius=0)
        self._tables = {
            "ÉLÉMENTS": self.tbl_elements, "ARMATURES": self.tbl_armatures,
            "DÉCOUPE": self.tbl_decoupe, "SURFACES": self.tbl_surfaces,
            "AUDIT": self.tbl_audit, "JOURNAL": self.tbl_journal,
        }
        self._current_table = "ÉLÉMENTS"
        self.tbl_elements.grid(row=0, column=0, sticky="nsew")

        # --- Barre d'etat -------------------------------------------
        status = ctk.CTkFrame(self, fg_color=T["bg_panel"], corner_radius=0,
                              height=24)
        status.grid(row=3, column=0, sticky="ew")
        status.grid_columnconfigure(1, weight=1)
        self.lbl_status_left = ctk.CTkLabel(
            status, text="Analyse terminée", font=font(9),
            text_color=T["text_dim"])
        self.lbl_status_left.grid(row=0, column=0, sticky="w", padx=12, pady=3)
        self.lbl_status_right = ctk.CTkLabel(
            status, text="100 % local · BAEL 91 · RPS 2000 v2011 · NM 01.4.097",
            font=font(9), text_color=T["text_muted"])
        self.lbl_status_right.grid(row=0, column=2, sticky="e", padx=12, pady=3)

    def _build_synthese(self, parent) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color="transparent", height=0)
        frame.grid_columnconfigure((0, 1, 2), weight=1, uniform="kpi")
        frame.grid_rowconfigure(2, weight=1, minsize=160)

        self.kpis: Dict[str, StatCard] = {}
        cards = [
            ("beton", "Béton armé", "0", "m³"),
            ("acier", "Acier total", "0", "kg"),
            ("ratio", "Ratio acier / béton", "0", "kg/m³"),
            ("proprete", "Béton de propreté", "0", "m³"),
            ("elements", "Éléments métrés", "0", ""),
            ("surfaces", "Surfaces", "—", "m²"),
        ]
        for idx, (key, label, value, unit) in enumerate(cards):
            row, col = divmod(idx, 3)
            card = StatCard(frame, label, value, unit)
            card.grid(row=row, column=col, sticky="nsew",
                      padx=(0 if col == 0 else 6, 0),
                      pady=(0, 6))
            self.kpis[key] = card

        charts = ctk.CTkFrame(frame, fg_color="transparent", height=0)
        charts.grid(row=2, column=0, columnspan=3, sticky="nsew",
                    pady=(2, 0))
        charts.grid_columnconfigure((0, 1, 2), weight=1, uniform="chart")
        charts.grid_rowconfigure(0, weight=1)

        card1 = Card(charts)
        card1.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ctk.CTkLabel(card1, text="BÉTON PAR FAMILLE", font=font(9, "bold"),
                     text_color=T["text_muted"]).pack(anchor="w", padx=10,
                                                     pady=(8, 0))
        self.donut = Donut(card1)
        self.donut.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        card2 = Card(charts)
        card2.grid(row=0, column=1, sticky="nsew", padx=6)
        ctk.CTkLabel(card2, text="ACIER PAR DIAMÈTRE", font=font(9, "bold"),
                     text_color=T["text_muted"]).pack(anchor="w", padx=10,
                                                     pady=(8, 0))
        self.bars = HBarChart(card2, height=170)
        self.bars.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        card3 = Card(charts)
        card3.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        ctk.CTkLabel(card3, text="LIMITES DÉTECTÉES", font=font(9, "bold"),
                     text_color=T["text_muted"]).pack(anchor="w", padx=10,
                                                     pady=(8, 0))
        self.limites = ctk.CTkFrame(card3, fg_color="transparent", height=0)
        self.limites.pack(fill="both", expand=True, padx=8, pady=(2, 6))
        return frame

    def _build_apercu(self, parent) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color=T["bg_card"], corner_radius=8,
                             border_width=1, border_color=T["border"])
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(frame, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(bar, text="Aperçu du plan", font=font(9, "bold"),
                     text_color=T["text_muted"]).grid(row=0, column=0, sticky="w")
        self.lbl_apercu = ctk.CTkLabel(bar, text="", font=font(9),
                                       text_color=T["text_muted"])
        self.lbl_apercu.grid(row=0, column=2, sticky="e")
        self.preview = ctk.CTkLabel(frame, text="Aucun aperçu disponible",
                                    font=font(10), text_color=T["text_muted"],
                                    fg_color="#0d0d0d", corner_radius=6)
        self.preview.grid(row=1, column=0, sticky="nsew", padx=10, pady=8)
        return frame

    # ------------------------------------------------------------- actions
    def _switch_top(self, value: str):
        if value == "Synthèse":
            self.view_apercu.grid_remove()
            self.view_synthese.grid(row=0, column=0, sticky="nsew")
        else:
            self.view_synthese.grid_remove()
            self.view_apercu.grid(row=0, column=0, sticky="nsew")
            self._render_preview()

    def _switch_bottom(self, value: str):
        current = self._tables.get(self._current_table)
        if current is not None:
            current.grid_remove()
        self._current_table = value
        self._set_active_tab(value)
        widget = self._tables.get(value)
        if widget is not None:
            widget.grid(row=0, column=0, sticky="nsew")

    def _set_active_tab(self, value: str):
        for name, lbl in self._tab_labels.items():
            active = name == value
            lbl.configure(text_color=T["text"] if active else T["text_muted"])
            self._tab_underlines[name].configure(
                bg=T["accent"] if active else T["bg_panel"])

    def set_audit_count(self, n: int):
        lbl = self._tab_labels.get("AUDIT")
        if lbl is not None:
            lbl.configure(text="AUDIT" + (f" ({n})" if n else ""))

    def select_bottom(self, value: str):
        """Selectionne un onglet bas depuis l'exterieur (shim du worker)."""
        if value in self._tables:
            self._switch_bottom(value)

    def set_action_state(self, key: str, enabled: bool):
        btn = self.actions.get(key)
        if btn is None:
            return
        btn.configure(state="normal" if enabled else "disabled")
        if key == "metre":
            btn.configure(
                fg_color=T["accent"] if enabled else T["accent_soft"],
                hover_color=T["accent_dark"] if enabled else T["accent_soft"],
                text_color="#ffffff" if enabled else T["text_muted"])

    # -------------------------------------------------------------- donnees
    def show_results(self, plan_data: Optional[dict], surfaces: Optional[dict],
                     projet: str = "", fichier: str = "",
                     duree_s: float = 0.0, log_text: str = "",
                     vm: Optional[Dict[str, Any]] = None):
        self.vm = vm or build_view_model(plan_data, surfaces, projet)
        self._plan_path = fichier or self._plan_path
        self.lbl_projet.configure(text=projet or "Projet")
        self.lbl_sous_titre.configure(
            text=f"{Path(fichier).name}  ·  100 % local  ·  aucune donnée envoyée"
            if fichier else "100 % local  ·  aucune donnée envoyée")
        self.lbl_duree.configure(text="")

        tot = self.vm.get("totaux", {})
        ano = self.vm.get("anomalies", [])
        n_bloq = sum(1 for a in ano if a.get("severite") == "BLOQUANT")
        duree_txt = f" · {duree_s:.0f} s" if duree_s else ""

        if n_bloq:
            self.banner.set(
                f"⚠  {len(ano)} point(s) à vérifier avant tout usage "
                f"contractuel — dont {n_bloq} bloquant(s) "
                f"(voir l'onglet AUDIT)", "err")
            self._set_badge(f"À vérifier{duree_txt}", "err")
        elif ano:
            self.banner.set(
                f"⚠  {len(ano)} avertissement(s) à vérifier avant tout usage "
                f"contractuel (voir l'onglet AUDIT)", "warn")
            self._set_badge(f"À vérifier{duree_txt}", "warn")
        elif self.vm.get("lignes") or self.vm.get("surfaces", {}).get("lignes"):
            self.banner.hide()
            self._set_badge(f"Terminé{duree_txt}", "ok")
        else:
            self.banner.set("Aucun élément exploitable : vérifiez le plan fourni.",
                            "warn")
            self._set_badge(f"Vide{duree_txt}", "neutral")

        # KPI
        self.kpis["beton"].set(f"{tot.get('beton_arme', 0):.2f}".replace(".", ","),
                               "m³")
        self.kpis["acier"].set(
            f"{tot.get('acier', 0):,.0f}".replace(",", " "), "kg")
        ratio = tot.get("ratio", 0.0)
        self.kpis["ratio"].set(f"{ratio:.1f}".replace(".", ","), "kg/m³",
                               kind=("ok" if 60 <= ratio <= 160 else "warn")
                               if ratio else None)
        self.kpis["proprete"].set(
            f"{tot.get('proprete', 0):.2f}".replace(".", ","), "m³")
        elements = self.vm.get("elements", [])
        self.kpis["elements"].set(str(tot.get("n_ouvrages", len(elements))),
                                  "ouvrages", caption=self._elements_caption())
        surf = self.vm.get("surfaces", {})
        if surf.get("lignes"):
            self.kpis["surfaces"].set(f"{surf.get('total', 0):.2f}".replace(
                ".", ","), "m²", caption=f"{surf.get('n_pieces', 0)} pièces")
        else:
            self.kpis["surfaces"].set("—", "m²", caption="Plan sans surfaces")
        self.kpis["surfaces"].grid()

        # Graphiques
        familles = self.vm.get("familles", [])
        self.donut.set_data(familles, tot.get("beton_arme", 0.0))
        self.bars.set_data([(str(d).replace("HA", "T"), v)
                            for d, v in self.vm.get("diametres", [])])
        self._fill_limites(ano)
        self.set_audit_count(len(ano))

        # Tableaux
        self.tbl_elements.set_rows(self.vm.get("elements", []))
        self.tbl_armatures.set_rows(self.vm.get("armatures", []))
        self.tbl_decoupe.set_rows(self._decoupe_rows())
        self.tbl_surfaces.set_rows(self._surface_rows())
        self.tbl_audit.set_rows(ano)
        if log_text:
            self.tbl_journal.delete("1.0", "end")
            self.tbl_journal.insert("1.0", log_text)
        self._render_preview()

    def _set_badge(self, text: str, kind: str):
        from ui.design import KIND_COLORS

        fg, bg = KIND_COLORS.get(kind, KIND_COLORS["neutral"])
        self.badge_statut.configure(text=text, fg_color=bg, text_color=fg)

    def _elements_caption(self) -> str:
        """Repartition courte par famille, ex. '6 semelles · 3 poteaux'."""
        counts: Dict[str, int] = {}
        for el in self.vm.get("elements", []):
            t = str(el.get("type", "")).strip()
            if t:
                counts[t] = counts.get(t, 0) + 1
        if not counts:
            return "Aucun ouvrage détecté"
        parts = [f"{n} {t.lower()}" for t, n in
                 sorted(counts.items(), key=lambda kv: -kv[1])[:2]]
        return " · ".join(parts)

    def _fill_limites(self, anomalies: List[dict]):
        for child in self.limites.winfo_children():
            child.destroy()
        if not anomalies:
            ctk.CTkLabel(self.limites, text="Aucune limite détectée.",
                         font=font(9), text_color=T["text_muted"]).pack(
                anchor="w", pady=4)
            return
        for a in anomalies[:3]:
            kind = "err" if a.get("severite") == "BLOQUANT" else "warn"
            fg = T["danger"] if kind == "err" else T["warning"]
            msg = str(a.get("message", ""))
            # La synthese reste lisible : diagnostic seul, la reference
            # normative complete est dans l'onglet AUDIT.
            msg = msg.split(" (")[0].split(" — ")[0].strip()
            if len(msg) > 58:
                msg = msg[:57].rstrip(" ,;.") + "…"
            row = tk.Frame(self.limites, bg=T["bg_card"], highlightthickness=0)
            row.pack(fill="x", pady=2)
            tk.Frame(row, width=2, bg=fg, highlightthickness=0).pack(
                side="left", fill="y", padx=(0, 7))
            ctk.CTkLabel(row, text=f"{a.get('element', '')} : {msg}",
                         font=font(9), text_color=fg, anchor="w",
                         justify="left", wraplength=240).pack(
                side="left", fill="x", expand=True)
        reste = len(anomalies) - 3
        if reste > 0:
            ctk.CTkLabel(self.limites,
                         text=f"+ {reste} autre(s) — onglet AUDIT",
                         font=font(8), text_color=T["text_muted"]).pack(
                anchor="w", pady=(2, 0))

    def _decoupe_rows(self) -> List[dict]:
        rows = []
        for diam, data in (self.vm.get("stocks") or {}).items():
            rows.append({
                "diametre": f"HA{diam}",
                "barres": data.get("nb_stock_bars_12m", 0),
                "achetee": f"{data.get('purchased_length_m', 0):.2f} m",
                "utile": f"{data.get('useful_length_m', 0):.2f} m",
                "chute": f"{data.get('waste_length_m', 0):.2f} m",
                "taux": f"{data.get('waste_pct', 0):.1f} %",
                "poids": f"{data.get('purchased_weight_kg', 0):.2f}",
            })
        return rows

    def _surface_rows(self) -> List[dict]:
        rows = []
        for s in self.vm.get("surfaces", {}).get("lignes", []):
            conf = s.get("confiance", 0.0)
            rows.append({
                "niveau": s.get("niveau", ""),
                "piece": s.get("piece", ""),
                "surface": f"{s.get('surface', 0):.2f}",
                "perimetre": (f"{s.get('perimetre'):.2f}"
                              if isinstance(s.get("perimetre"), (int, float))
                              else "—"),
                "source": s.get("source", ""),
                "confiance": f"{conf:.2f}",
                "observation": s.get("observation", "") or "OK",
                "_tag": s.get("_tag", ""),
            })
        return rows

    # -------------------------------------------------------------- apercu
    def set_preview_paths(self, plan_path: Optional[str] = None,
                          overlays: Optional[List[str]] = None):
        if plan_path:
            self._plan_path = plan_path
        if overlays:
            self._overlay_paths = overlays
        self._render_preview()

    def _render_preview(self):
        path = self._plan_path
        if path and self._overlay_paths:
            path = self._overlay_paths[0]
        if not path or not Path(path).exists():
            self.preview.configure(image=None, text="Aucun aperçu disponible")
            self.lbl_apercu.configure(text="")
            return
        try:
            from PIL import Image
            p = Path(path)
            img = None
            if p.suffix.lower() == ".pdf":
                import fitz
                doc = fitz.open(str(p))
                page = doc[0]
                pix = page.get_pixmap(dpi=110)
                doc.close()
                from io import BytesIO
                img = Image.open(BytesIO(pix.tobytes("png")))
            elif p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp",
                                      ".tif", ".tiff", ".webp"):
                img = Image.open(str(p))
            if img is None:
                self.preview.configure(image=None, text="Aperçu indisponible")
                return
            w = max(320, self.top_host.winfo_width() - 40)
            h = max(200, self.top_host.winfo_height() - 70)
            img.thumbnail((w, h))
            ctkimg = ctk.CTkImage(light_image=img, dark_image=img,
                                  size=img.size)
            self._preview_img = ctkimg
            self.preview.configure(image=ctkimg, text="")
            self.lbl_apercu.configure(text=f"{p.name}")
        except Exception as exc:
            self.preview.configure(image=None,
                                   text=f"Aperçu indisponible ({type(exc).__name__})")
