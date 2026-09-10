#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui/correction_view.py — Panneau de correction manuelle des elements extraits.

Affiche les elements extrait dans un tableau editable permettant a l'utilisateur
de corriger les dimensions, quantites, et autrees avant la generation Excel.
"""
from __future__ import annotations

import re
from typing import Optional

import customtkinter as ctk


# Couleurs
COLORS = {
    "primary": "#2F5496",
    "success": "#28A745",
    "warning": "#FFC107",
    "danger": "#DC3545",
    "bg_dark": "#1a1a2e",
    "bg_card": "#16213e",
    "bg_row": "#1e2d4a",
    "bg_row_alt": "#1a2640",
    "text_light": "#E8E8E8",
    "text_muted": "#A0A0A0",
    "border": "#2a3a5a",
}

# Familles a afficher
FAMILLES = [
    ("poteaux", "POTEAUX", "P"),
    ("poutres", "POUTRES", "N"),
    ("semelles", "SEMELLES", "S"),
    ("longrines", "LONGRINES", "LG"),
    ("chainages", "CHAINAGES", "CH"),
    ("murs", "MURS (BN)", "BN"),
    ("voiles", "VOILES", "V"),
    ("dalles", "DALLES", "D"),
    ("escaliers", "ESCALIERS", "ESC"),
]


class CorrectionView(ctk.CTkToplevel):
    """Fenetre de correction manuelle des elements extraits."""

    def __init__(self, master, plan_data: dict, on_validate=None, **kwargs):
        super().__init__(master, **kwargs)
        self.plan_data = plan_data
        self.on_validate = on_validate
        self.corrected_data = None

        self.title("PlanBA — Correction des Elements Extraits")
        self.geometry("1100x700")
        self.minsize(900, 500)
        self.configure(fg_color=COLORS["bg_dark"])

        # Rendre modale
        self.transient(master)
        self.grab_set()

        self._build_ui()
        self._populate_table()

    def _build_ui(self):
        """Construit l'interface de correction."""
        # Header
        header = ctk.CTkFrame(self, fg_color=COLORS["primary"], height=60)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text="Correction des Elements Extraits",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="white"
        ).pack(side="left", padx=20, pady=10)

        ctk.CTkLabel(
            header,
            text="Modifiez les valeurs puis cliquez 'Valider' pour generer l'Excel",
            font=ctk.CTkFont(size=11),
            text_color="#C8D9F0"
        ).pack(side="left", padx=20)

        # Boutons header
        btn_frame = ctk.CTkFrame(header, fg_color="transparent")
        btn_frame.pack(side="right", padx=20)

        ctk.CTkButton(
            btn_frame, text="Annuler",
            font=ctk.CTkFont(size=12),
            width=100, height=32,
            fg_color=COLORS["danger"],
            hover_color="#A71D2A",
            command=self._on_cancel
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_frame, text="Valider et Generer",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=150, height=32,
            fg_color=COLORS["success"],
            hover_color="#1E8449",
            command=self._on_validate
        ).pack(side="left")

        # Onglets par famille
        self.tabview = ctk.CTkTabview(
            self, fg_color=COLORS["bg_card"],
            segmented_button_fg_color=COLORS["bg_dark"],
            segmented_button_selected_color=COLORS["primary"])
        self.tabview.pack(fill="both", expand=True, padx=15, pady=10)

        # Créer les onglets
        self.tabs = {}
        self.tables = {}
        for cat_key, cat_label, prefix in FAMILLES:
            elements = self.plan_data.get("catalogue_types", {}).get(cat_key, {})
            if not elements:
                continue
            tab = self.tabview.add(f"{cat_label} ({len(elements)})")
            self.tabs[cat_key] = tab
            self.tables[cat_key] = self._build_table(tab, cat_key, elements)

        # Info footer
        footer = ctk.CTkFrame(self, fg_color="transparent", height=40)
        footer.pack(fill="x", padx=15, pady=(0, 10))

        self.info_label = ctk.CTkLabel(
            footer,
            text="Double-cliquez sur une cellule pour la modifier. "
                 "Les modifications sont sauvegardees automatiquement.",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"])
        self.info_label.pack(side="left")

    def _build_table(self, parent, cat_key: str, elements: dict):
        """Construit un tableau editable avec CRUD (Ajout/Suppression)."""
        # En-tetes selon la famille
        headers = ["Reference", "Section (cm)", "a (m)", "b (m)"]
        if cat_key == "poutres":
            headers.extend(["h (m)", "Longueur (m)"])
        elif cat_key == "poteaux":
            headers.extend(["Hauteur (m)"])
        elif cat_key == "semelles":
            headers.extend(["h (m)"])
        headers.append("Nombre")

        # Frame avec scroll
        scroll_frame = ctk.CTkScrollableFrame(
            parent, fg_color="transparent")
        scroll_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Container principal
        container = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        container.pack(fill="x", pady=5)

        # Header : Titre + Bouton Ajouter
        header_frame = ctk.CTkFrame(container, fg_color="transparent")
        header_frame.pack(fill="x", pady=(0, 5))

        btn_add = ctk.CTkButton(
            header_frame, text="+ Ajouter ligne", width=130, height=28,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["success"], hover_color="#1E8449",
            command=lambda: self._ajouter_ligne(
                grid_frame, lignes_widgets, entries, cat_key, headers, {}))
        btn_add.pack(side="right")

        # Table frame (grid)
        grid_frame = ctk.CTkFrame(container, fg_color="transparent")
        grid_frame.pack(fill="x")

        # ALIGNEMENT : uniform="col_group" force toutes les colonnes a la meme largeur
        for i in range(len(headers)):
            grid_frame.grid_columnconfigure(i, weight=1, uniform="col_group")
        # Colonne Delete (etroite)
        grid_frame.grid_columnconfigure(
            len(headers), weight=0, minsize=40)

        # En-tetes
        for col, header in enumerate(headers):
            lbl = ctk.CTkLabel(
                grid_frame, text=header,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=COLORS["primary"])
            lbl.grid(row=0, column=col, sticky="w", padx=4, pady=4)

        ctk.CTkLabel(
            grid_frame, text="",
            font=ctk.CTkFont(size=11, weight="bold"),
        ).grid(row=0, column=len(headers), padx=4, pady=4)

        lignes_widgets = []
        entries = {}

        # Peupler avec les donnees extraites
        for ref in sorted(elements.keys()):
            spec = elements[ref]
            valeurs = {"Reference": ref, "Section (cm)": spec.get("section_str", "?")}
            # Remplir les dimensions
            dim_keys = [k for k in headers if k not in ("Reference", "Section (cm)", "Nombre")]
            for dk in dim_keys:
                # Mapper le nom de colonne vers la cle du dict
                key_map = {
                    "a (m)": "a", "b (m)": "b", "h (m)": "h",
                    "Hauteur (m)": "hauteur_p", "Longueur (m)": "longueur",
                }
                raw = spec.get(key_map.get(dk, dk), "")
                if isinstance(raw, float):
                    valeurs[dk] = f"{raw:.3f}"
                elif raw is None or raw == "":
                    valeurs[dk] = ""
                else:
                    valeurs[dk] = str(raw)
            valeurs["Nombre"] = str(spec.get("nombre", 1))
            valeurs["_ref"] = ref
            valeurs["_spec"] = spec

            self._ajouter_ligne(
                grid_frame, lignes_widgets, entries, cat_key, headers, valeurs)

        return entries

    def _populate_table(self):
        """Charge les donnees dans le tableau."""
        pass  # Les donnees sont chargees lors de la construction

    def _ajouter_ligne(self, grid_frame, lignes_widgets, entries,
                       cat_key, headers, valeurs):
        """Ajoute une ligne editable au tableau."""
        row_idx = len(lignes_widgets) + 1  # +1 pour la ligne d'en-tete
        widgets_ligne = {}

        for col_idx, header in enumerate(headers):
            val = valeurs.get(header, "")

            entry = ctk.CTkEntry(
                grid_frame,
                font=ctk.CTkFont(size=11),
                fg_color=COLORS["bg_card"],
                text_color=COLORS["text_light"],
                border_color=COLORS["border"])
            entry.insert(0, str(val))
            entry.grid(row=row_idx, column=col_idx, sticky="ew", padx=2, pady=2)
            widgets_ligne[header] = entry

        # Bouton Delete
        ref = valeurs.get("Reference", "")
        btn_del = ctk.CTkButton(
            grid_frame, text="X", width=28, height=24,
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["danger"], hover_color="#A71D2A",
            command=lambda r=row_idx, w=widgets_ligne, re=ref: (
                self._supprimer_ligne(grid_frame, lignes_widgets, entries, re, w, r)))
        btn_del.grid(row=row_idx, column=len(headers), padx=4, pady=2)

        widgets_ligne["_del_btn"] = btn_del
        lignes_widgets.append(widgets_ligne)

        # Enregistrer dans entries
        ref_val = valeurs.get("Reference", "")
        spec = valeurs.get("_spec", {})
        entries[ref_val] = {
            "spec": spec,
            "fields": {h: widgets_ligne[h] for h in headers},
        }

    def _supprimer_ligne(self, grid_frame, lignes_widgets, entries,
                         ref, widgets, row_idx):
        """Supprime une ligne du tableau."""
        for w in widgets.values():
            w.destroy()
        if widgets in lignes_widgets:
            lignes_widgets.remove(widgets)
        if ref in entries:
            del entries[ref]

    def _on_cancel(self):
        """Annule la correction."""
        self.corrected_data = None
        self.destroy()

    def _on_validate(self):
        """Valide les corrections et genere les donnees corrigees."""
        self.corrected_data = self.plan_data.copy()
        cat = self.corrected_data.get("catalogue_types", {})

        # Mapping colonne -> cle du spec
        key_map = {
            "a (m)": "a", "b (m)": "b", "h (m)": "h",
            "Hauteur (m)": "hauteur_p", "Longueur (m)": "longueur",
        }

        for cat_key, entries in self.tables.items():
            if cat_key not in cat:
                continue

            # Reconstruire le dict depuis les entries
            new_elements = {}
            for ref, entry_data in entries.items():
                spec = entry_data["spec"].copy() if entry_data["spec"] else {}
                fields = entry_data["fields"]

                for header, entry in fields.items():
                    if header in ("Reference", "Section (cm)"):
                        continue
                    val_str = entry.get().strip()
                    if not val_str:
                        continue
                    try:
                        if header == "Nombre":
                            spec["nombre"] = int(val_str)
                        else:
                            spec[key_map.get(header, header)] = float(val_str)
                    except ValueError:
                        pass

                # Mettre a jour section_str si les dimensions ont change
                a = spec.get("a", 0)
                b = spec.get("b", 0)
                if a > 0 and b > 0:
                    spec["section_str"] = f"{a*100:.0f}x{b*100:.0f}"

                # Utiliser la reference du champ Reference si modifiee
                ref_entry = fields.get("Reference")
                final_ref = ref_entry.get().strip() if ref_entry else ref
                if final_ref:
                    new_elements[final_ref] = spec

            cat[cat_key] = new_elements

        if self.on_validate:
            self.on_validate(self.corrected_data)
        self.destroy()


def show_correction_dialog(master, plan_data: dict, on_validate=None):
    """Affiche la boite de dialogue de correction.
    
    Retourne un objet CorrectionView (non bloquant).
    """
    return CorrectionView(master, plan_data, on_validate=on_validate)
