#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui/desktop_app.py — Interface Desktop moderne pour Métré BA Agent.

Application customtkinter avec thème Dark/Light, drag & drop,
barre de progression, et gestion d'erreurs human-in-the-loop.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from typing import Optional

# Securisation des flux si mode windowed (--noconsole)
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
else:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
else:
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import customtkinter as ctk

# Ajouter le racine au path
sys.path.insert(0, str(Path(__file__).parent.parent))

# NOTE -- Imports differs : les modules lourds (pymupdf, openpyxl, reportlab,
# pydantic, ezdxf) sont charges dans les workers/CLI au moment du besoin,
# jamais en en-tete, pour que la fenetre s'affiche immediatement.
from core.paths import get_output_dir

# Drag & Drop natif Windows (tkdnd) — degrade gracieusement si absent :
# l'application reste utilisable via "Parcourir" sans tkinterdnd2.
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    TKDND_AVAILABLE = True
except Exception:
    TkinterDnD = None
    DND_FILES = None
    TKDND_AVAILABLE = False

# Bases de la fenetre principale : CTk + mixin DnD (mixin pur, sans __init__)
_DND_BASES = (ctk.CTk, TkinterDnD.DnDWrapper) if TKDND_AVAILABLE else (ctk.CTk,)

# ============================================================================
# Constantes
# ============================================================================

APP_TITLE = "PlanBA — Métré Extracteur"
APP_SUBTITLE = (
    "Plan PDF de fondations en béton armé → métré Excel, rapport PDF "
    "d'audit et optimisation de découpe — 100 % en local, sans API. "
    "Chaque quantité est sourcée (texte natif, vecteurs, vision) et "
    "chaque écart est documenté : aucune valeur inventée."
)


def clean_dropped_path(raw) -> str:
    """Nettoie un chemin recu du filedialog ou du Drag & Drop.

    Germe les formats natifs Windows / TkinterDnD :
      - accolades : '{C:/Mes Plans/PLAN BA final.pdf}'
      - chemins multiples : '{C:/a.pdf} {C:/b.pdf}' -> premier
      - guillemets simples/doubles et espaces parasites
    """
    if not raw:
        return ""
    s = str(raw).strip()
    # Groupes {chemin} eventuels (Drag & Drop multi-fichiers)
    groups = re.findall(r"\{([^{}]+)\}", s)
    if groups:
        s = groups[0].strip()
    else:
        s = s.strip("{}").strip()
        s = s.strip('"').strip("'").strip()
    s = s.strip()
    return os.path.normpath(s) if s else ""
APP_SIZE = "900x720"
SUPPORTED_FORMATS = [
    ("Fichiers BA", "*.pdf *.dxf *.dwg *.png *.jpg *.jpeg *.tiff *.tif *.bmp *.webp"),
    ("PDF", "*.pdf"),
    ("AutoCAD DXF", "*.dxf"),
    ("AutoCAD DWG", "*.dwg"),
    ("Images", "*.png *.jpg *.jpeg *.tiff *.tif *.bmp *.webp"),
]

BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"

# Couleurs thème
COLORS = {
    "primary": "#2F5496",
    "success": "#28A745",
    "warning": "#FFC107",
    "danger": "#DC3545",
    "bg_dark": "#1a1a2e",
    "bg_card": "#16213e",
    "text_light": "#E8E8E8",
    "text_muted": "#A0A0A0",
}


# ============================================================================
# Widget personnalisé : Zone de dépôt
# ============================================================================

class DropZone(ctk.CTkFrame):
    """Zone de dépôt de fichier avec sélection par bouton."""

    def __init__(self, master, on_file_selected=None, **kwargs):
        super().__init__(master, **kwargs)
        self.on_file_selected = on_file_selected
        self.file_path: Optional[str] = None

        self.configure(fg_color=COLORS["bg_card"], corner_radius=12,
                       border_width=2, border_color=COLORS["primary"])

        # Icône et texte
        self.label_icon = ctk.CTkLabel(
            self, text="📄", font=ctk.CTkFont(size=40))
        self.label_icon.pack(pady=(20, 5))

        self.label_title = ctk.CTkLabel(
            self, text="Déposez un plan ici",
            font=ctk.CTkFont(size=16, weight="bold"))
        self.label_title.pack(pady=(0, 2))

        self.label_subtitle = ctk.CTkLabel(
            self, text="PDF, DXF, DWG, PNG, JPG, TIFF, BMP, WEBP",
            font=ctk.CTkFont(size=11), text_color=COLORS["text_muted"])
        self.label_subtitle.pack(pady=(0, 10))

        # Bouton parcourir
        self.btn_browse = ctk.CTkButton(
            self, text="Parcourir...",
            font=ctk.CTkFont(size=12),
            width=160, height=32,
            fg_color=COLORS["primary"],
            hover_color="#1F3A70",
            command=self._browse_file)
        self.btn_browse.pack(pady=(0, 15))

        # Label du fichier sélectionné
        self.label_file = ctk.CTkLabel(
            self, text="Aucun fichier sélectionné",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"],
            wraplength=500)
        self.label_file.pack(pady=(0, 10))

        # Label de statut de validation (pre-flight)
        self.label_validation = ctk.CTkLabel(
            self, text="",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["text_muted"],
            wraplength=500)
        self.label_validation.pack(pady=(0, 10))

    def _browse_file(self):
        from tkinter import filedialog
        filetypes = [
            ("Tous les fichiers supportés", "*.pdf *.dxf *.dwg *.png *.jpg *.jpeg *.tiff *.tif *.bmp *.webp"),
            ("Documents PDF (*.pdf)", "*.pdf"),
            ("Dessins AutoCAD (*.dxf, *.dwg)", "*.dxf *.dwg"),
            ("Images (*.png *.jpg *.jpeg *.tiff *.tif *.bmp *.webp)",
             "*.png *.jpg *.jpeg *.tiff *.tif *.bmp *.webp"),
            ("Tous les fichiers (*.*)", "*.*"),
        ]
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            self.set_file(path)

    def set_file(self, path: str):
        """Affichage NEUTRE avant validation, puis delegation au callback
        de l'application (source de verite unique pour le chemin)."""
        self._reset_validation()
        self.file_path = path

        if self.on_file_selected:
            self.on_file_selected(path)

    def _reset_validation(self):
        """Remet a zero le statut de validation avant un nouveau fichier."""
        self.label_file.configure(
            text="Aucun fichier sélectionné",
            text_color=COLORS["text_muted"])
        self.label_validation.configure(text="", text_color=COLORS["text_muted"])
        self.label_icon.configure(text="📄")
        self.label_title.configure(text="Déposez un plan ici")

    def show_valid(self, name: str, size_kb: int, source_label: str,
                   message: str):
        """Fichier valide : label vert + verdict."""
        self.label_file.configure(
            text=f"✅ {name}  [{size_kb} Ko — {source_label}]",
            text_color=COLORS["success"])
        self.label_validation.configure(
            text=f"✅ Plan technique validé — {message}",
            text_color=COLORS["success"])
        self.label_title.configure(text="Fichier chargé")
        self.label_icon.configure(text="✅")

    def show_rejected(self, name: str, message: str):
        """Fichier rejeté : label rouge, jamais de vert trompeur."""
        self.label_file.configure(
            text=f"❌ {name}" if name else "❌ Document rejeté",
            text_color=COLORS["danger"])
        self.label_validation.configure(
            text=f"❌ Document rejeté : {message}",
            text_color=COLORS["danger"])
        self.label_title.configure(text="Document rejeté")
        self.label_icon.configure(text="🚫")


# ============================================================================
# Widget personnalisé : Cartouche options
# ============================================================================

class OptionsPanel(ctk.CTkFrame):
    """Panneau d'options : nom du projet, clé API, template."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color="transparent")

        # Nom du projet
        self.label_nom = ctk.CTkLabel(
            self, text="Nom du projet (optionnel) :",
            font=ctk.CTkFont(size=12))
        self.label_nom.pack(anchor="w", padx=10)

        self.entry_nom = ctk.CTkEntry(
            self, placeholder_text="Ex: Construction Immeuble R+4",
            width=400, height=32,
            font=ctk.CTkFont(size=12))
        self.entry_nom.pack(anchor="w", padx=10, pady=(2, 8))

        # Clé API Vision
        self.label_api = ctk.CTkLabel(
            self, text="Clé API Vision (OpenAI / Anthropic) :",
            font=ctk.CTkFont(size=12))
        self.label_api.pack(anchor="w", padx=10)

        api_frame = ctk.CTkFrame(self, fg_color="transparent")
        api_frame.pack(fill="x", padx=10, pady=(2, 4))

        self.entry_api_key = ctk.CTkEntry(
            api_frame, placeholder_text="sk-... ou sk-ant-...",
            width=320, height=32, show="*",
            font=ctk.CTkFont(size=12))
        self.entry_api_key.pack(side="left")

        self.btn_toggle = ctk.CTkButton(
            api_frame, text="👁", width=36, height=32,
            font=ctk.CTkFont(size=12),
            fg_color="#555555", hover_color="#444444",
            command=self._toggle_visibility)
        self.btn_toggle.pack(side="left", padx=(4, 0))
        self._api_visible = False

        # Charger depuis .env si disponible
        self._load_from_env()

    def _toggle_visibility(self):
        self._api_visible = not self._api_visible
        self.entry_api_key.configure(
            show="" if self._api_visible else "*")
        self.btn_toggle.configure(
            text="🙈" if self._api_visible else "👁")

    def _load_from_env(self):
        """Charge la clé API depuis .env ou les variables d'environnement."""
        # 1. Variable d'environnement
        api_key = os.environ.get("OPENAI_API_KEY") or \
                  os.environ.get("ANTHROPIC_API_KEY") or ""
        if api_key:
            self.entry_api_key.insert(0, api_key)
            return

        # 2. Fichier .env
        env_path = BASE_DIR / ".env"
        if env_path.exists():
            try:
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("OPENAI_API_KEY="):
                            val = line.split("=", 1)[1].strip().strip('"')
                            self.entry_api_key.insert(0, val)
                            return
                        elif line.startswith("ANTHROPIC_API_KEY="):
                            val = line.split("=", 1)[1].strip().strip('"')
                            self.entry_api_key.insert(0, val)
                            return
            except Exception:
                pass

    def get_nom(self) -> str:
        return self.entry_nom.get().strip() or None

    def get_api_key(self) -> str:
        return self.entry_api_key.get().strip() or None


# ============================================================================
# Widget personnalisé : Barre de progression
# ============================================================================

class ProgressPanel(ctk.CTkFrame):
    """Barre de progression avec état en temps réel."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color="transparent")

        self.label_step = ctk.CTkLabel(
            self, text="",
            font=ctk.CTkFont(size=12))
        self.label_step.pack(anchor="w", padx=10)

        self.progressbar = ctk.CTkProgressBar(
            self, width=500, height=16,
            progress_color=COLORS["primary"])
        self.progressbar.pack(fill="x", padx=10, pady=(4, 0))
        self.progressbar.set(0)

    def set_step(self, step: str, progress: float):
        self.label_step.configure(text=step)
        self.progressbar.set(progress)
        self.update_idletasks()

    def reset(self):
        self.label_step.configure(text="")
        self.progressbar.set(0)


# ============================================================================
# Widget personnalisé : Tableau récapitulatif
# ============================================================================

class SummaryTable(ctk.CTkFrame):
    """Tableau récapitulatif des résultats."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color=COLORS["bg_card"], corner_radius=10)

        self.label_title = ctk.CTkLabel(
            self, text="📊 Résumé de l'analyse",
            font=ctk.CTkFont(size=14, weight="bold"))
        self.label_title.pack(anchor="w", padx=15, pady=(10, 5))

        # Grille de résultats
        self.grid_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.grid_frame.pack(fill="x", padx=15, pady=(0, 10))

        # Labels de résultats
        self.labels = {}
        rows = [
            ("semelles", "Semelles", "0"),
            ("poteaux", "Poteaux", "0"),
            ("poutres", "Poutres", "0"),
            ("total", "Total éléments", "0"),
            ("acier", "Poids acier estimé", "— kg"),
        ]

        for i, (key, label, default) in enumerate(rows):
            lbl = ctk.CTkLabel(
                self.grid_frame, text=f"{label} :",
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text_muted"])
            lbl.grid(row=i, column=0, sticky="w", padx=5, pady=3)

            val = ctk.CTkLabel(
                self.grid_frame, text=default,
                font=ctk.CTkFont(size=13, weight="bold"))
            val.grid(row=i, column=1, sticky="w", padx=10, pady=3)

            self.labels[key] = val

    def update_values(self, data: dict):
        sem = len(data.get("catalogue_types", {}).get("semelles", {}))
        pot = len(data.get("catalogue_types", {}).get("poteaux", {}))
        pou = len(data.get("catalogue_types", {}).get("poutres", {}))
        total = sem + pot + pou

        self.labels["semelles"].configure(text=str(sem))
        self.labels["poteaux"].configure(text=str(pot))
        self.labels["poutres"].configure(text=str(pou))
        self.labels["total"].configure(text=str(total))

        # Estimation poids acier (basique : somme des nb × phi²/162)
        poids = 0.0
        for cat in ["semelles", "poteaux", "poutres"]:
            for dims in data.get("catalogue_types", {}).get(cat, {}).values():
                for arm_key in ["ferr_x", "ferr_y"]:
                    arm = dims.get(arm_key, {})
                    nb = arm.get("nb", 0)
                    phi = arm.get("phi", 0)
                    if nb and phi:
                        # Estimation longueur moyenne = 1.5m
                        poids += nb * 1.5 * phi * phi / 162
                for lb in dims.get("long_bars", []):
                    nb = lb.get("nb", 0)
                    phi = lb.get("phi", 0)
                    if nb and phi:
                        poids += nb * 3.0 * phi * phi / 162
                c = dims.get("cadres", {})
                if c.get("phi"):
                    poids += 20 * 3.0 * c["phi"] * c["phi"] / 162
                for fi in dims.get("filants_inf", []):
                    nb = fi.get("nb", 0)
                    phi = fi.get("phi", 0)
                    if nb and phi:
                        poids += nb * 5.0 * phi * phi / 162

        self.labels["acier"].configure(text=f"{poids:.1f} kg")


# ============================================================================
# Fenêtre principale
# ============================================================================

class PlanBAMetreApp(*_DND_BASES):
    """Application principale PlanBA Métré Extracteur.

    Herite du mixin DnDWrapper (tkinterdnd2) : toute la fenetre accepte
    le glisser-deposer de fichiers (curseur autorise, zone cible visuelle).
    """

    def __init__(self):
        super().__init__()

        # Configuration fenêtre
        self.title(APP_TITLE)
        self.geometry(APP_SIZE)
        self.minsize(800, 600)
        self.configure(fg_color=COLORS["bg_dark"])

        # Thème
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Variable etat -- SOURCE DE VERITE UNIQUE pour le chemin du fichier
        self.selected_file_path: Optional[str] = None
        self.plan_data: Optional[dict] = None

        self._build_ui()

        # Drag & Drop : chargement du package Tcl tkdnd sur la racine,
        # puis enregistrement de la fenetre entiere comme cible de depot.
        if TKDND_AVAILABLE:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                pass  # tkdnd indisponible -> "Parcourir" reste fonctionnel

        # Filet de securite : le splash natif PyInstaller reste au premier
        # plan et bloque tous les clics tant que pyi_splash.close() n'est
        # pas appele. On le ferme automatiquement peu apres le rendu.
        self.after(200, self._dismiss_splash)

    def _on_drop(self, event):
        """Reception d'un fichier depose (Drag & Drop tkdnd).

        event.data est au format Tcl : '{C:/chemin avec espaces.pdf}' ou
        '{a.pdf} {b.pdf}'. clean_dropped_path gere accolades, guillemets
        et selection du premier fichier ; _on_file_selected fait le reste
        (validation + source de verite unique).
        """
        raw = getattr(event, "data", "") or ""
        self._on_file_selected(raw)

    def _dismiss_splash(self):
        """Ferme le splash natif PyInstaller (fenetre figee si oubliee)."""
        try:
            import pyi_splash
            if pyi_splash.is_alive():
                pyi_splash.close()
        except Exception:
            pass  # mode script (pyi_splash absent) ou deja ferme

    def _build_ui(self):
        """Construit l'interface utilisateur."""

        # --- Header ---
        header = ctk.CTkFrame(self, fg_color=COLORS["primary"], height=104)
        header.pack(fill="x")
        header.pack_propagate(False)

        titre_zone = ctk.CTkFrame(header, fg_color="transparent")
        titre_zone.pack(side="left", padx=20, pady=6)
        ctk.CTkLabel(
            titre_zone, text="PlanBA — Métré Extracteur",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="white").pack(anchor="w")
        ctk.CTkLabel(
            titre_zone, text=APP_SUBTITLE,
            font=ctk.CTkFont(size=11),
            text_color="#C8D9F0", wraplength=900,
            justify="left").pack(anchor="w")

        # Thème toggle
        self.theme_switch = ctk.CTkSwitch(
            header, text="☀️",
            font=ctk.CTkFont(size=11),
            command=self._toggle_theme,
            width=50)
        self.theme_switch.pack(side="right", padx=20)
        self.theme_switch.select()

        # --- Conteneur principal scrollable ---
        self.main_frame = ctk.CTkScrollableFrame(
            self, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=15)

        # 1. Zone de dépôt
        self.drop_zone = DropZone(
            self.main_frame, height=180,
            on_file_selected=self._on_file_selected)
        self.drop_zone.pack(fill="x", pady=(0, 15))

        # 2. Options
        self.options_panel = OptionsPanel(self.main_frame)
        self.options_panel.pack(fill="x", pady=(0, 15))

        # 3. Bouton d'action
        self.btn_action = ctk.CTkButton(
            self.main_frame,
            text="🚀  Lancer l'Analyse & Générer le Métré",
            font=ctk.CTkFont(size=15, weight="bold"),
            height=50,
            fg_color=COLORS["success"],
            hover_color="#1E8449",
            command=self._run_analysis)
        self.btn_action.pack(fill="x", pady=(0, 10))

        # 4. Barre de progression
        self.progress_panel = ProgressPanel(self.main_frame)
        self.progress_panel.pack(fill="x", pady=(0, 15))

        # 5. Tableau récapitulatif
        self.summary_table = SummaryTable(self.main_frame)
        self.summary_table.pack(fill="x", pady=(0, 15))

        # 6. Boutons d'action finaux
        actions_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        actions_frame.pack(fill="x")

        self.btn_open_excel = ctk.CTkButton(
            actions_frame,
            text="📂  Ouvrir le fichier Excel généré",
            font=ctk.CTkFont(size=12),
            height=36,
            fg_color=COLORS["primary"],
            hover_color="#1F3A70",
            state="disabled",
            command=self._open_excel)
        self.btn_open_excel.pack(side="left", padx=(0, 10), expand=True, fill="x")

        self.btn_open_folder = ctk.CTkButton(
            actions_frame,
            text="📁  Ouvrir le dossier de destination",
            font=ctk.CTkFont(size=12),
            height=36,
            fg_color="#555555",
            hover_color="#444444",
            command=self._open_output_folder)
        self.btn_open_folder.pack(side="left", expand=True, fill="x")

        # 7. Zone de log
        self.log_text = ctk.CTkTextbox(
            self.main_frame, height=120,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color=COLORS["bg_card"],
            text_color=COLORS["text_muted"])
        self.log_text.pack(fill="x", pady=(10, 0))

    def _toggle_theme(self):
        if self.theme_switch.get():
            ctk.set_appearance_mode("dark")
        else:
            ctk.set_appearance_mode("light")

    def _on_file_selected(self, path: str):
        """SOURCE DE VERITE UNIQUE pour le chemin du fichier.

        Appele par le DropZone (parcourir ou depot). Nettoie le chemin
        (accolades TkinterDnD, guillemets, espaces), valide via
        PlanSanityValidator, met a jour self.selected_file_path et
        synchronise l'affichage (vert/rouge) + l'etat du bouton.
        """
        from core.validator import PlanSanityValidator

        path = clean_dropped_path(path)

        # 1. Guard : chemin inexistant ou illisible -> rejet immediat
        if not path or not os.path.exists(path):
            self.selected_file_path = None
            self.drop_zone.show_rejected(
                Path(path).name if path else "", "Fichier introuvable.")
            self.btn_action.configure(state="disabled")
            self._log("⚠ Fichier introuvable ou chemin vide.")
            return

        abspath = os.path.abspath(path)
        is_valid, message, score = PlanSanityValidator.validate_file(abspath)
        name = os.path.basename(abspath)
        size_kb = os.path.getsize(abspath) // 1024
        ext = Path(abspath).suffix.upper().replace(".", "")

        if is_valid:
            self.selected_file_path = abspath
            self.drop_zone.show_valid(name, size_kb, ext, message)
            self.btn_action.configure(state="normal")
            self._log(
                f"Fichier : {name} [{size_kb} Ko — {ext}]\n"
                f"✅ Plan technique validé ({score}/100) : {message}")
        else:
            self.selected_file_path = None
            self.drop_zone.show_rejected(name, message)
            self.btn_action.configure(state="disabled")
            self._log(f"⚠ Document rejeté ({score}/100) : {message}")

    def _log(self, msg: str):
        self.log_text.insert("end", f"▸ {msg}\n")
        self.log_text.see("end")
        self.update_idletasks()

    def _run_analysis(self):
        """Lance l'analyse dans un thread séparé.

        Lit directement self.selected_file_path (source de verite unique)
        et le passe en ARGUMENT au worker (pas d'etat partage mutable).
        """
        path = getattr(self, "selected_file_path", None)
        if not path:
            self._show_error("Veuillez sélectionner un fichier d'abord.")
            return

        self.btn_action.configure(state="disabled", text="⏳ Analyse en cours...")
        self.btn_open_excel.configure(state="disabled")
        self.btn_open_folder.configure(state="disabled")

        thread = threading.Thread(target=self._analysis_worker,
                                  args=(path,), daemon=True)
        thread.start()

    def _analysis_worker(self, file_path: str):
        """Worker d'analyse exécuté dans un thread séparé.

        Recoit le chemin en ARGUMENT (source de verite unique figée au
        demarrage du thread). Extraction 100% locale et réelle (multi-pages,
        find_tables + spatial). Aucun catalogue fictif n'est jamais injecté :
        si rien n'est détecté, l'échec est explicite.
        """
        success = False
        try:
            # Étape 1 : Extraction spatiale réelle, page par page
            self.after(0, lambda: self.progress_panel.set_step(
                "1/5 : Parsage vectoriel du plan (page 1)...", 0.1))

            from core.local_extractor import (
                VectorPlanExtractor, RasterPlanExtractor, is_raster_pdf,
                ExtractionError, extract_plan_auto)

            extractor = VectorPlanExtractor()
            ext = Path(file_path).suffix.lower()

            def cb(page_num, total, role):
                frac = 0.1 + 0.4 * (page_num / max(total, 1))
                self.after(0, lambda f=frac, p=page_num, t=total:
                           self.progress_panel.set_step(
                               f"1/5 : Traitement : Page {p} / {t}...", f))
                if role != "skip":
                    self.after(0, lambda p=page_num, t=total, r=role:
                               self._log(f"Page {p}/{t} : {r}"))

            if ext == ".pdf":
                self.after(0, lambda: self._log(
                    "PDF hybride détecté — texte vectoriel + OCR ciblé"))
                self.plan_data = extract_plan_auto(
                    file_path, progress_callback=cb)
            else:
                from core.ingestion import UniversalPlanIngestor
                ingestor = UniversalPlanIngestor(file_path)
                ingestion_result = ingestor.ingest()
                self.after(0, lambda: self._log(
                    f"Source : {ingestion_result.source.value} | "
                    f"Blocs texte : {len(ingestion_result.text_blocks)} | "
                    f"Éléments IR : {len(ingestion_result.drawing_elements)}"))
                if ingestion_result.errors:
                    for error in ingestion_result.errors:
                        self.after(0, lambda error=error:
                                   self._log(f"⚠ Adaptateur : {error}"))
                if ingestion_result.status.value == "error":
                    from core.local_extractor import partial_plan_data
                    self.plan_data = partial_plan_data(
                        "; ".join(ingestion_result.errors)
                        or "Adaptateur indisponible")
                else:
                    self.plan_data = extractor.extract_from_text_blocks(
                        ingestion_result.text_blocks)

            nom_saisi = None
            try:
                nom_saisi = self.options_panel.get_nom()
            except Exception:
                pass
            if nom_saisi:
                self.plan_data.setdefault("projet", {})["nom"] = nom_saisi

            c = self.plan_data["catalogue_types"]
            impl = self.plan_data["implantations"]
            self.after(0, lambda: self._log(
                f"Extraction réelle : "
                f"{len(impl['semelles'])} semelles positionnées "
                f"({len(c['semelles'])} types), "
                f"{len(c['poteaux'])} poteaux, {len(c['poutres'])} poutres"))
            for a in self.plan_data.get("_meta", {}).get("avertissements", []):
                self.after(0, lambda a=a: self._log(f"⚠ {a}"))
            for h in self.plan_data.get("_meta", {}).get("hypotheses", []):
                self.after(0, lambda h=h: self._log(f"• Hypothèse : {h}"))

            # Garde-fou partage CLI/UI (source de verite unique)
            from core.local_extractor import verifier_livrables_ou_lever
            verifier_livrables_ou_lever(self.plan_data)

            self.after(0, lambda: self.progress_panel.set_step(
                "2/5 : Calcul déterministe des métrés et ferraillages...", 0.7))
            time.sleep(0.2)

            # Étape 3 : Génération Excel
            self.after(0, lambda: self.progress_panel.set_step(
                "3/5 : Injection dans le classeur Excel...", 0.8))
            time.sleep(0.2)

            output = get_output_dir()
            json_path = output / "plan_data.json"
            xlsx_path = output / "metre_genere.xlsx"

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.plan_data, f, ensure_ascii=False, indent=2)

            # Générer l'Excel
            from build_metre import MetreGenerator
            gen = MetreGenerator(self.plan_data)
            gen.generer(str(xlsx_path))

            # Générer l'optimisation
            self.after(0, lambda: self.progress_panel.set_step(
                "3/5 : Optimisation de la découpe des barres...", 0.82))
            from optimisation_chantiers import generer_optimisation
            optim_xlsx = output / "optimisation_chantiers.xlsx"
            generer_optimisation(self.plan_data, str(optim_xlsx))

            # Générer le rapport PDF
            self.after(0, lambda: self.progress_panel.set_step(
                "4/5 : Génération du rapport d'audit PDF...", 0.90))
            from rapport_metre import generer_rapport
            rapport_pdf = output / "rapport_metre.pdf"
            generer_rapport(self.plan_data, str(rapport_pdf))

            # Générer la note de calculs chantier (mêmes données que l'Excel)
            self.after(0, lambda: self.progress_panel.set_step(
                "5/5 : Note de calculs chantier...", 0.95))
            from build_metre import adapter_plan_vers_injecteur
            from generators.generate_pdf_note import \
                generer_note_calcul_chantier
            donnees_note = adapter_plan_vers_injecteur(self.plan_data)
            note_pdf = output / "note_calculs_chantier.pdf"
            generer_note_calcul_chantier(donnees_note, str(note_pdf))

            self.after(0, lambda: self.progress_panel.set_step(
                "✅ Terminé ! Métré généré avec succès.", 1.0))

            # Mettre à jour le tableau récap
            self.after(0, lambda: self.summary_table.update_values(self.plan_data))
            self.after(0, lambda: self.btn_open_excel.configure(state="normal"))
            self.after(0, lambda: self.btn_open_folder.configure(state="normal"))
            self.after(0, lambda: self._log(
                f"✅ Fichiers générés :\n"
                f"   → {json_path}\n"
                f"   → {xlsx_path}\n"
                f"   → {optim_xlsx}\n"
                f"   → {rapport_pdf}\n"
                f"   → {note_pdf}"))
            success = True

        except Exception as e:
            tb = traceback.format_exc()
            self.after(0, lambda: self._log(
                f"❌ Erreur ({type(e).__name__}) :\n{tb}"))
            self.after(0, lambda message=str(e):
                       self.progress_panel.set_step(
                           f"❌ Erreur : {message}", 0))
            self.after(0, lambda message=str(e), details=tb:
                       self._show_error(
                           f"{message}\n\nDétails techniques :\n{details}"))
        finally:
            self.after(0, lambda: self.btn_action.configure(
                state="normal", text="🚀  Lancer l'Analyse & Générer le Métré"))
            if not success:
                self.after(0, lambda: self.btn_open_excel.configure(
                    state="disabled"))
                self.after(0, lambda: self.btn_open_folder.configure(
                    state="disabled"))

    def _show_error(self, msg: str):
        """Affiche une boîte de dialogue d'erreur."""
        from tkinter import messagebox
        messagebox.showerror("Erreur", msg)

    def _open_excel(self):
        """Ouvre le fichier Excel généré."""
        xlsx_path = get_output_dir() / "metre_genere.xlsx"
        if xlsx_path.exists():
            os.startfile(str(xlsx_path))
        else:
            self._show_error(f"Fichier introuvable : {xlsx_path}")

    def _open_output_folder(self):
        """Ouvre le dossier de sortie."""
        output = get_output_dir()
        os.startfile(str(output))


# ============================================================================
# Point d'entrée
# ============================================================================

def launch_app():
    """Lance l'application Desktop.

    Ordre obligatoire (mode frozen avec --splash PyInstaller) :
      1. Creer la fenetre
      2. app.update() -> force le rendu graphique initial
      3. Fermer le splash natif (sinon il reste au premier plan et
         intercepte tous les clics)
      4. mainloop()
    """
    app = PlanBAMetreApp()
    app.update()               # rendu initial de la fenetre principale
    app._dismiss_splash()      # fermeture immediate du splash PyInstaller
    app.mainloop()


if __name__ == "__main__":
    launch_app()
