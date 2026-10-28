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
import tkinter as tk
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

# Systeme de design de la maquette (tokens + widgets sombres).
from ui.design import font, mono

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

APP_NAME = "PlanBA Pro"
APP_TAGLINE = "Métré Béton Armé & Surfaces"
APP_VERSION = "2.0.0"
APP_TITLE = f"{APP_NAME} — {APP_TAGLINE}"
APP_SUBTITLE = (
    "Plans BA (PDF, DXF, scans) et plans d'architecture (images) → métré "
    "béton / acier certifié BET Maroc, surfaces par pièce et par étage, "
    "DQE, optimisation de coupe — 100 % local et gratuit (OpenCV + OCR, "
    "VLM Ollama/Groq optionnel). Chaque quantité est sourcée, chaque écart "
    "est documenté : aucune valeur inventée."
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
APP_SIZE = "1040x780"
SUPPORTED_FORMATS = [
    ("Fichiers BA", "*.pdf *.dxf *.dwg *.png *.jpg *.jpeg *.tiff *.tif *.bmp *.webp"),
    ("PDF", "*.pdf"),
    ("AutoCAD DXF", "*.dxf"),
    ("AutoCAD DWG", "*.dwg"),
    ("Images", "*.png *.jpg *.jpeg *.tiff *.tif *.bmp *.webp"),
]

BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"

# Couleurs theme "genie civil" : bleu structure, jaune securite chantier,
# gris beton, vert validation, orange acier (a verifier), rouge anomalie.
COLORS = {
    "primary": "#1F5FA8",        # bleu structure / plans
    "primary_dark": "#164A85",
    "accent": "#F5B700",         # jaune securite chantier
    "steel": "#E8772E",          # orange acier / a verifier
    "success": "#2EA043",
    "warning": "#F5B700",
    "danger": "#D93025",
    "bg_dark": "#0F1B2D",        # bleu nuit (fond)
    "bg_card": "#172A44",        # carte
    "bg_card_alt": "#1E3555",
    "concrete": "#8A9BB0",       # gris beton (textes secondaires)
    "text_light": "#EEF2F7",
    "text_muted": "#9FB0C4",
}


# ============================================================================
# Widget personnalisé : Zone de dépôt
# ============================================================================


import json
import dataclasses

class PlanBAEncoder(json.JSONEncoder):
    def default(self, obj):
        if dataclasses.is_dataclass(obj):
            return dataclasses.asdict(obj)
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        return str(obj)

class DropZone(ctk.CTkFrame):
    """Zone de depot de plan au theme de la maquette.

    Etats visuels (puces) : Vide -> Survol -> OK / Reserve / Erreur.
    L'etat est pilote par la validation applicative, jamais devine ici.
    """

    STATES = [("vide", "Vide"), ("survol", "Survol"), ("ok", "OK"),
              ("reserve", "Réserve"), ("erreur", "Erreur")]

    def __init__(self, master, on_file_selected=None, **kwargs):
        from ui.design import T as TOKENS

        kwargs.setdefault("height", 190)
        super().__init__(master, **kwargs)
        self.on_file_selected = on_file_selected
        self.file_path: Optional[str] = None
        self._t = TOKENS
        self.configure(fg_color="transparent")

        # Cadre pointille : 4 bandes fines superposees au cadre (un canvas
        # plein cadre intercepterait les clics sur les boutons).
        self.box = ctk.CTkFrame(self, fg_color=TOKENS["bg_input"],
                                corner_radius=8)
        self.box.pack(fill="both", expand=True)
        self._border_color = "#5c5c5c"
        self._edges: dict = {}
        for name in ("top", "bottom", "left", "right"):
            cv = tk.Canvas(self.box, bg=TOKENS["bg_input"], highlightthickness=0,
                           bd=0, height=3)
            self._edges[name] = cv
        self.box.bind("<Configure>", lambda _e: self._layout_edges())

        self.label_icon = ctk.CTkLabel(self.box, text="⬆", font=font(20),
                                       text_color=TOKENS["text_muted"])
        self.label_icon.pack(pady=(22, 0))

        self.label_title = ctk.CTkLabel(
            self.box, text="Glissez votre plan ici", font=font(12, "bold"),
            text_color=TOKENS["text"])
        self.label_title.pack(pady=(6, 0))

        self.label_subtitle = ctk.CTkLabel(
            self.box, text="PDF · DXF · DWG · PNG · JPG",
            font=font(9), text_color=TOKENS["text_muted"])
        self.label_subtitle.pack(pady=(2, 8))

        self.btn_browse = ctk.CTkButton(
            self.box, text="Parcourir…", font=font(10), width=124, height=28,
            corner_radius=5, fg_color=TOKENS["accent"],
            hover_color=TOKENS["accent_dark"], text_color="#ffffff",
            command=self._browse_file)
        self.btn_browse.pack(pady=(0, 6))

        self.label_file = ctk.CTkLabel(
            self.box, text="Aucun fichier sélectionné", font=font(9),
            text_color=TOKENS["text_muted"], wraplength=560)
        self.label_file.pack()

        self.label_validation = ctk.CTkLabel(
            self.box, text="", font=font(9, "bold"),
            text_color=TOKENS["text_muted"], wraplength=560)
        self.label_validation.pack(pady=(0, 10))

        chips = ctk.CTkFrame(self, fg_color="transparent")
        chips.pack(anchor="w", pady=(7, 0))
        self._chips: dict = {}
        for key, text in self.STATES:
            chip = ctk.CTkLabel(chips, text=text, font=font(8, "bold"),
                                corner_radius=3, padx=7, pady=1,
                                fg_color=TOKENS["bg_card"],
                                text_color=TOKENS["text_muted"])
            chip.pack(side="left", padx=(0, 6))
            self._chips[key] = chip
        self._set_state("vide")

    def _layout_edges(self):
        """Aligne les 4 bandes sur les bords du cadre (rayon 8 px)."""
        w = self.box.winfo_width()
        h = self.box.winfo_height()
        if w < 24 or h < 24:
            return
        r = 8
        self._edges["top"].place(x=r, y=0, width=w - 2 * r, height=3)
        self._edges["bottom"].place(x=r, y=h - 3, width=w - 2 * r, height=3)
        self._edges["left"].place(x=0, y=r, width=3, height=h - 2 * r)
        self._edges["right"].place(x=w - 3, y=r, width=3, height=h - 2 * r)
        for cv in self._edges.values():
            # Canvas.lift() est un alias de tag_raise (items) : on hisse le
            # widget via Tcl pour qu'il passe au-dessus du fond du cadre.
            cv.tk.call("raise", cv._w)
        # Les largeurs ne sont connues qu'apres la pose : on redessine a vide.
        self.after_idle(lambda: self._draw_border(self._border_color))

    def _draw_border(self, color: str):
        self._border_color = color
        for name, cv in self._edges.items():
            cv.delete("all")
            w = cv.winfo_width()
            h = cv.winfo_height()
            if w < 4 or h < 1:
                continue
            if name in ("top", "bottom"):
                cv.create_line(0, h / 2, w, h / 2, fill=color, dash=(4, 4))
            else:
                cv.create_line(w / 2, 0, w / 2, h, fill=color, dash=(4, 4))

    # -------------------------------------------------------------- etats
    def _set_state(self, key: str):
        """Une seule puce active : l'etat reel de l'import."""
        from ui.design import KIND_COLORS

        kind = {"vide": "neutral", "survol": "info", "ok": "ok",
                "reserve": "warn", "erreur": "err"}.get(key, "neutral")
        fg, bg = KIND_COLORS[kind]
        for name, chip in self._chips.items():
            if name == key:
                chip.configure(fg_color=bg, text_color=fg)
            else:
                chip.configure(fg_color=self._t["bg_card"],
                               text_color=self._t["text_muted"])

    def _highlight(self, on: bool):
        """Retour visuel du survol (drag) : bord accent."""
        self._border_color = self._t["accent"] if on else "#5c5c5c"
        self._draw_border(self._border_color)

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
            text_color=self._t["text_muted"])
        self.label_validation.configure(text="",
                                        text_color=self._t["text_muted"])
        self.label_icon.configure(text="⬆", text_color=self._t["text_muted"])
        self.label_title.configure(text="Glissez un plan ici")
        self._set_state("vide")

    def show_valid(self, name: str, size_kb: int, source_label: str,
                   message: str):
        """Fichier valide : label vert + verdict."""
        t = self._t
        self.label_file.configure(
            text=f"✅ {name}  [{size_kb} Ko — {source_label}]",
            text_color=t["success"])
        self.label_validation.configure(
            text=f"✅ Plan technique validé — {message}",
            text_color=t["success"])
        self.label_title.configure(text="Fichier chargé")
        self.label_icon.configure(text="✅", text_color=t["success"])
        self._set_state("ok")

    def show_rejected(self, name: str, message: str):
        """Fichier rejeté : label rouge, jamais de vert trompeur."""
        t = self._t
        self.label_file.configure(
            text=f"❌ {name}" if name else "❌ Document rejeté",
            text_color=t["danger"])
        self.label_validation.configure(
            text=f"❌ Document rejeté : {message}",
            text_color=t["danger"])
        self.label_title.configure(text="Document rejeté")
        self.label_icon.configure(text="🚫", text_color=t["danger"])
        self._set_state("erreur")


# ============================================================================
# Widget personnalisé : Cartouche options
# ============================================================================

class OptionsPanel(ctk.CTkFrame):
    """Panneau d'options ingenieur : projet, mode d'analyse, vision
    freemium (Ollama local / Groq free), calibration des surfaces."""

    MODES = ["Auto (structure BA + surfaces)", "Structure BA uniquement",
             "Surfaces architecture uniquement"]
    VLM_CHOICES = ["Désactivé (OCR local seul)", "Ollama local (qwen2.5-vl)",
                   "Groq Free Tier (clé requise)"]

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color="transparent")
        f12 = ctk.CTkFont(size=12)
        f11b = ctk.CTkFont(size=11, weight="bold")

        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack(fill="x", padx=10)
        grid.grid_columnconfigure((0, 1), weight=1, uniform="opt")

        # --- Colonne 1 : projet + mode ---
        ctk.CTkLabel(grid, text="NOM DU PROJET", font=f11b,
                     text_color=COLORS["text_muted"]).grid(
            row=0, column=0, sticky="w", padx=(0, 8))
        self.entry_nom = ctk.CTkEntry(
            grid, placeholder_text="Ex : Villa R+1 — Lot Gros Œuvre",
            height=32, font=f12)
        self.entry_nom.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(2, 8))

        ctk.CTkLabel(grid, text="MODE D'ANALYSE", font=f11b,
                     text_color=COLORS["text_muted"]).grid(
            row=2, column=0, sticky="w", padx=(0, 8))
        self.mode_menu = ctk.CTkOptionMenu(
            grid, values=self.MODES, height=32, font=f12,
            fg_color=COLORS["bg_card"], button_color=COLORS["primary"],
            button_hover_color=COLORS["primary_dark"])
        self.mode_menu.set(self.MODES[0])
        self.mode_menu.grid(row=3, column=0, sticky="ew", padx=(0, 8), pady=(2, 8))

        # --- Colonne 2 : vision freemium ---
        ctk.CTkLabel(grid, text="VISION IA (100 % GRATUIT, OPTIONNEL)", font=f11b,
                     text_color=COLORS["text_muted"]).grid(
            row=0, column=1, sticky="w")
        self.vlm_menu = ctk.CTkOptionMenu(
            grid, values=self.VLM_CHOICES, height=32, font=f12,
            fg_color=COLORS["bg_card"], button_color=COLORS["primary"],
            button_hover_color=COLORS["primary_dark"],
            command=self._on_vlm_change)
        self.vlm_menu.grid(row=1, column=1, sticky="ew", pady=(2, 8))

        key_frame = ctk.CTkFrame(grid, fg_color="transparent")
        key_frame.grid(row=3, column=1, sticky="ew", pady=(2, 8))
        key_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(grid, text="CLÉ GROQ (gsk_…) — jamais envoyée ailleurs",
                     font=f11b, text_color=COLORS["text_muted"]).grid(
            row=2, column=1, sticky="w")
        self.entry_api_key = ctk.CTkEntry(
            key_frame, placeholder_text="gsk_…", height=32, show="*", font=f12)
        self.entry_api_key.grid(row=0, column=0, sticky="ew")
        self.btn_toggle = ctk.CTkButton(
            key_frame, text="👁", width=36, height=32, font=f12,
            fg_color="#555555", hover_color="#444444",
            command=self._toggle_visibility)
        self.btn_toggle.grid(row=0, column=1, padx=(4, 0))
        self._api_visible = False

        # --- Ligne 3 : calibration surfaces ---
        ctk.CTkLabel(grid, text="LARGEUR HORS-TOUT (m) — calibration échelle",
                     font=f11b, text_color=COLORS["text_muted"]).grid(
            row=4, column=0, sticky="w", padx=(0, 8))
        self.entry_largeur = ctk.CTkEntry(
            grid, placeholder_text="Ex : 18.90 (vide = lecture des cotes)",
            height=32, font=f12)
        self.entry_largeur.grid(row=5, column=0, sticky="ew", padx=(0, 8), pady=(2, 4))

        ctk.CTkLabel(grid, text="HAUTEUR SOUS PLAFOND (m) — enduits",
                     font=f11b, text_color=COLORS["text_muted"]).grid(
            row=4, column=1, sticky="w")
        self.entry_hsp = ctk.CTkEntry(grid, height=32, font=f12)
        self.entry_hsp.insert(0, "2.80")
        self.entry_hsp.grid(row=5, column=1, sticky="ew", pady=(2, 4))

        self._load_from_env()
        self._on_vlm_change(self.vlm_menu.get())

    def _toggle_visibility(self):
        self._api_visible = not self._api_visible
        self.entry_api_key.configure(show="" if self._api_visible else "*")
        self.btn_toggle.configure(text="🙈" if self._api_visible else "👁")

    def _on_vlm_change(self, choice):
        groq = choice.startswith("Groq")
        state = "normal" if groq else "disabled"
        self.entry_api_key.configure(state=state)
        self.btn_toggle.configure(state=state)

    def _load_from_env(self):
        """Cle Groq depuis l'environnement ou .env (jamais d'API payante)."""
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            env_path = BASE_DIR / ".env"
            if env_path.exists():
                try:
                    with open(env_path, encoding="utf-8") as f:
                        for line in f:
                            if line.strip().startswith("GROQ_API_KEY="):
                                api_key = line.split("=", 1)[1].strip().strip('"')
                                break
                except Exception:
                    pass
        if api_key:
            self.entry_api_key.insert(0, api_key)
            self.vlm_menu.set(self.VLM_CHOICES[2])

    @staticmethod
    def _float_or_none(entry):
        try:
            v = float(entry.get().strip().replace(",", "."))
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    def get_nom(self) -> str:
        return self.entry_nom.get().strip() or None

    def get_api_key(self) -> str:
        return self.entry_api_key.get().strip() or None

    def get_mode(self) -> str:
        """'auto' | 'ba' | 'surfaces'"""
        idx = self.MODES.index(self.mode_menu.get()) if self.mode_menu.get() in self.MODES else 0
        return ("auto", "ba", "surfaces")[idx]

    def get_vlm_backend(self) -> str:
        """'off' | 'ollama' | 'groq'"""
        c = self.vlm_menu.get()
        return "groq" if c.startswith("Groq") else "ollama" if c.startswith("Ollama") else "off"

    def get_largeur_hors_tout(self):
        return self._float_or_none(self.entry_largeur)

    def get_hsp(self) -> float:
        return self._float_or_none(self.entry_hsp) or 2.80

    def apply_vision_env(self):
        """Propage les choix freemium aux routeurs (variables d'environnement)."""
        os.environ["PLANBA_VLM_BACKEND"] = self.get_vlm_backend()
        key = self.get_api_key()
        if self.get_vlm_backend() == "groq" and key:
            os.environ["GROQ_API_KEY"] = key


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
# Widget personnalisé : Bandeau d'indicateurs (KPI) ingenieur
# ============================================================================

class KPIStrip(ctk.CTkFrame):
    """Cartes : beton (m3), acier (kg), surfaces (m2), anomalies, pieces."""

    CARDS = [
        ("beton", "BÉTON", "m³", "primary"),
        ("acier", "ACIER HA", "kg", "steel"),
        ("surfaces", "SURFACES", "m²", "accent"),
        ("pieces", "PIÈCES / OUVRAGES", "", "concrete"),
        ("alertes", "ALERTES", "", "danger"),
    ]

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color="transparent")
        self.values: dict = {}
        for i, (key, title, unit, color) in enumerate(self.CARDS):
            card = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=10,
                                border_width=2, border_color=COLORS[color])
            card.grid(row=0, column=i, sticky="nsew", padx=4, pady=2)
            self.grid_columnconfigure(i, weight=1, uniform="kpi")
            ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=COLORS["text_muted"]).pack(pady=(8, 0))
            val = ctk.CTkLabel(card, text="—", font=ctk.CTkFont(size=20, weight="bold"),
                               text_color=COLORS[color])
            val.pack()
            ctk.CTkLabel(card, text=unit or " ", font=ctk.CTkFont(size=10),
                         text_color=COLORS["text_muted"]).pack(pady=(0, 8))
            self.values[key] = val

    @staticmethod
    def _beton_acier(plan_data: dict):
        """Totaux indicatifs (le classeur Excel reste la reference)."""
        try:
            from build_metre import adapter_plan_vers_injecteur
            d = adapter_plan_vers_injecteur(plan_data)
        except Exception:
            return None, None, 0
        beton = 0.0
        n = 0
        for s in d.get("semelles", []):
            beton += (s.get("a") or 0) * (s.get("b") or 0) * (s.get("h") or 0)
            n += 1
        for p in d.get("poteaux", []):
            beton += (p.get("a") or 0) * (p.get("b") or 0) * (p.get("hauteur") or 0)
            n += 1
        for b in d.get("poutres", []) + d.get("longrines", []) + d.get("chainages", []):
            beton += (b.get("b") or 0) * (b.get("h") or 0) * (b.get("portee") or 0)
            n += 1
        return round(beton, 2), None, n

    def update_values(self, plan_data: dict, surfaces: dict = None):
        try:
            from core.vision_bridge import plan_data_is_placeholder
            if plan_data and plan_data_is_placeholder(plan_data):
                # Geometrie par defaut du moteur historique : jamais affichee
                plan_data = {"_meta": plan_data.get("_meta", {})}
        except Exception:
            pass
        beton, acier, n_ouvrages = self._beton_acier(plan_data or {})
        meta = (plan_data or {}).get("_meta", {})
        n_alert = len(meta.get("avertissements", []) or [])
        n_rooms = 0
        total_m2 = None
        if surfaces and surfaces.get("xlsx"):
            total_m2 = surfaces.get("total_m2")
            n_rooms = surfaces.get("summary", {}).get("n_rooms", 0)
            n_alert += len(surfaces.get("warnings", []) or [])
        self.values["beton"].configure(text=f"{beton:,.2f}" if beton else "—")
        self.values["acier"].configure(text="voir Excel" if beton else "—")
        self.values["surfaces"].configure(
            text=f"{total_m2:,.2f}" if total_m2 is not None else "—")
        pieces = []
        if n_rooms:
            pieces.append(f"{n_rooms} pièces")
        if n_ouvrages:
            pieces.append(f"{n_ouvrages} ouvr.")
        self.values["pieces"].configure(
            text=" / ".join(pieces) if pieces else "—",
            font=ctk.CTkFont(size=16 if len(pieces) > 1 else 20, weight="bold"))
        self.values["alertes"].configure(
            text=str(n_alert), text_color=COLORS["danger"] if n_alert else COLORS["success"])


# ============================================================================
# Widget personnalisé : Surfaces par piece / etage
# ============================================================================

class SurfacesPanel(ctk.CTkFrame):
    """Tableau des surfaces (etage, piece, source, L x l, m2, perimetre,
    confiance) avec code couleur de fiabilite."""

    HEAD = ["Étage", "Pièce", "Source", "L × l (m)", "Surface (m²)",
            "Périmètre (m)", "Confiance", "Observations"]

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color=COLORS["bg_card"], corner_radius=10)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(top, text="📐 Surfaces par pièce et par étage",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self.lbl_total = ctk.CTkLabel(top, text="", font=ctk.CTkFont(size=12, weight="bold"),
                                      text_color=COLORS["accent"])
        self.lbl_total.pack(side="right")
        # Cadre simple (pas de defilement imbrique : la page entiere defile)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="x", padx=8, pady=(0, 8))
        self._rows: list = []
        self._empty = ctk.CTkLabel(
            self.body, text="Aucune surface calculée (plan de structure BA, ou "
                            "libellés de pièces non lus).",
            font=ctk.CTkFont(size=11), text_color=COLORS["text_muted"])
        self._empty.grid(row=0, column=0, sticky="w", padx=6, pady=6)

    def clear(self):
        for w in self._rows:
            w.destroy()
        self._rows = []
        self.lbl_total.configure(text="")
        self._empty.grid()

    def update_from_result(self, surfaces: dict):
        self.clear()
        reports = surfaces.get("reports") or []
        if not reports:
            return
        self._empty.grid_remove()
        for c, h in enumerate(self.HEAD):
            lbl = ctk.CTkLabel(self.body, text=h, font=ctk.CTkFont(size=11, weight="bold"),
                               text_color=COLORS["text_muted"], anchor="w")
            lbl.grid(row=0, column=c, sticky="w", padx=6, pady=(0, 4))
            self.body.grid_columnconfigure(c, weight=1 if c in (1, 7) else 0)
            self._rows.append(lbl)
        r = 1
        for rep in reports:
            for room in rep.rooms:
                conf = room.confidence or 0.0
                color = (COLORS["success"] if conf >= 0.85
                         else COLORS["steel"] if conf >= 0.5 else COLORS["danger"])
                lxl = (f"{room.length_m:.2f} × {room.width_m:.2f}"
                       if room.length_m and room.width_m else "—")
                area = f"{room.area_m2:.2f}" if room.area_m2 is not None else "NON TROUVÉE"
                per = f"{room.perimeter_m:.2f}" if room.perimeter_m else "—"
                obs = " | ".join(room.warnings)[:90] if room.warnings else "OK"
                vals = [rep.level, room.label, room.source, lxl, area, per,
                        f"{conf:.2f}", obs]
                for c, v in enumerate(vals):
                    lbl = ctk.CTkLabel(
                        self.body, text=v, anchor="w", font=ctk.CTkFont(size=11),
                        text_color=color if c in (4, 6) else COLORS["text_light"])
                    lbl.grid(row=r, column=c, sticky="w", padx=6, pady=1)
                    self._rows.append(lbl)
                r += 1
            sub = ctk.CTkLabel(self.body, text=f"Sous-total {rep.level} : {rep.total_area_m2:.2f} m²",
                               font=ctk.CTkFont(size=11, weight="bold"), text_color=COLORS["accent"])
            sub.grid(row=r, column=0, columnspan=8, sticky="w", padx=6, pady=(2, 6))
            self._rows.append(sub)
            r += 1
        self.lbl_total.configure(
            text=f"TOTAL : {surfaces.get('total_m2', 0):.2f} m²  ·  échelle "
                 f"{surfaces.get('summary', {}).get('scale') or '?'} px/m")


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
# Panneau Rapport Metier (regles metier genie civil)
# ============================================================================

class RapportMetierPanel(ctk.CTkFrame):
    """Panneau d'affichage du rapport metier complet.

    Affiche : ecarts plan/tableaux, lineaires, volumes beton,
    hypotheses reglementaires, sections sans ferraillage, revision.
    """

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color=COLORS["bg_card"], corner_radius=10)

        self.label_title = ctk.CTkLabel(
            self, text="🏗️ Rapport Métier — Génie Civil / Béton Armé",
            font=ctk.CTkFont(size=14, weight="bold"))
        self.label_title.pack(anchor="w", padx=15, pady=(10, 5))

        self.textbox = ctk.CTkTextbox(
            self, height=300,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color=COLORS["bg_card"],
            text_color=COLORS["text_muted"],
            wrap="word")
        self.textbox.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def update_from_data(self, data: dict):
        """Met a jour le panneau avec les donnees du rapport metier."""
        meta = data.get("_meta", {})
        rapport = meta.get("rapport_metier")
        text_analysis = meta.get("text_analysis", {})

        lines: list[str] = []

        # En-tete revision
        if rapport:
            if rapport.get("revision_index"):
                lines.append(f"Indice : {rapport['revision_index']}")
            if rapport.get("revision_date"):
                lines.append(f"Date   : {rapport['revision_date']}")
            if rapport.get("revision_label"):
                lines.append(f"Label  : {rapport['revision_label']}")
            lines.append("")

        # Hypotheses reglementaires
        if rapport and rapport.get("regulatory_hypotheses"):
            lines.append("--- HYPOTHÈSES RÉGLEMENTAIRES ---")
            seen = set()
            for h in rapport["regulatory_hypotheses"]:
                key = (h["category"], h["value"])
                if key not in seen:
                    seen.add(key)
                    lines.append(f"  {h['category']}: {h['value']}")
            lines.append("")

        # Ecarts plan/tableaux
        if rapport and rapport.get("discrepancies"):
            lines.append("--- ÉCARTS PLAN / TABLEAUX RÉCAPITULATIFS ---")
            for d in rapport["discrepancies"]:
                marker = "!!!" if d["severity"] == "ERROR" else "! " if d["severity"] == "WARNING" else "  "
                lines.append(
                    f"{marker}{d['family']} ({d['prefix']}): "
                    f"plan={d['plan_count']} rep., tableau={d['table_count']} lignes"
                )
                if d.get("missing_in_table"):
                    lines.append(
                        f"    Absents du tableau : {', '.join(d['missing_in_table'][:10])}")
                if d.get("missing_in_plan"):
                    lines.append(
                        f"    Absents du plan : {', '.join(d['missing_in_plan'][:10])}")
            lines.append("")

        # Lineaire par type
        if rapport and rapport.get("linears"):
            lines.append("--- LINÉAIRE PAR TYPE ---")
            header = f"  {'Ref':<10} {'Famille':<18} {'Section':<12} {'Niveau':<12} {'Nb':<5} {'Total.m':<8}"
            lines.append(header)
            lines.append("  " + "-" * 70)
            for lin in rapport["linears"]:
                sec = f"{lin['section_cm'][0]}x{lin['section_cm'][1]}" if lin.get("section_cm") else "-"
                total = lin["length_m"] * lin["count"]
                lines.append(
                    f"  {lin['reference']:<10} {lin['family']:<18} {sec:<12} "
                    f"{lin['level']:<12} {lin['count']:<5} {total:<8.2f}"
                )
            lines.append("")

        # Volume beton
        if rapport and rapport.get("concrete_volumes"):
            lines.append("--- VOLUME BÉTON ESTIMÉ ---")
            total_vol = 0.0
            for cv in rapport["concrete_volumes"]:
                if cv.get("volume_m3", 0) > 0:
                    sec = f"{cv['section_cm'][0]}x{cv['section_cm'][1]}" if cv.get("section_cm") else "-"
                    lines.append(
                        f"  {cv['reference']:<10} {cv['family']:<18} {sec:<12} "
                        f"{cv['level']:<12} {cv['count']}x{cv['length_m']:.1f}m = {cv['volume_m3']:.3f} m3"
                    )
                    total_vol += cv["volume_m3"]
            lines.append(f"  {'TOTAL':.<52} {total_vol:.3f} m3")
            lines.append("")

        # Ferraillage manquant
        if rapport and rapport.get("missing_ferraillage"):
            lines.append("--- SECTIONS SANS DÉTAIL DE FERRAILLAGE ---")
            for mf in rapport["missing_ferraillage"]:
                lines.append(
                    f"  ! {mf['reference']} ({mf['family']}) section {mf['section_text']} "
                    f"- niveau {mf['level']}")
            lines.append("")

        # Text analysis summary
        if text_analysis and not text_analysis.get("error"):
            lines.append("--- ANALYSE PROFONDE DU TEXTE ---")
            lines.append(f"  Backend : {text_analysis.get('extraction_backend', '?')}")
            lines.append(f"  Textes inversés corrigés : {text_analysis.get('reversed_texts_corrected', 0)}")
            lines.append(f"  Éléments cartouche exclus : {text_analysis.get('cartouche_excluded', 0)}")
            levels = text_analysis.get("levels_detected", [])
            if levels:
                lines.append(f"  Niveaux détectés : {', '.join(levels)}")
            ambig = text_analysis.get("ambiguous_cases", [])
            if ambig:
                lines.append(f"  Cas ambigus : {len(ambig)}")
                for ac in ambig[:5]:
                    lines.append(f"    - {ac['text']}: {ac['reason']}")
            lines.append("")

        # Geometry analysis (dessins vectoriels)
        geometry_analysis = meta.get("geometry_analysis", {})
        if geometry_analysis and not geometry_analysis.get("error"):
            lines.append("--- ANALYSE GÉOMÉTRIQUE (DESSINS) ---")
            lines.append(f"  Formes détectées : {geometry_analysis.get('total_shapes', 0)}")
            by_fam = geometry_analysis.get("by_family", {})
            if by_fam:
                for fam, count in by_fam.items():
                    lines.append(f"    {fam}: {count}")
            lines.append("")

        # Level clustering (clustering spatial par niveau)
        level_clustering = meta.get("level_clustering", {})
        if level_clustering and not level_clustering.get("error"):
            lines.append("--- CLUSTERING SPATIAL PAR NIVEAU ---")
            lines.append(f"  Confiance : {level_clustering.get('confidence', 0):.0%}")
            zones = level_clustering.get("zones", [])
            if zones:
                for z in zones:
                    if z.get("count", 0) > 0:
                        lines.append(f"    {z['level']}: {z['count']} éléments")
            ambig_lc = level_clustering.get("ambiguous", [])
            if ambig_lc:
                lines.append(f"  Cas ambigus : {len(ambig_lc)}")
            lines.append("")

        # Avertissements
        all_warnings = []
        if rapport:
            all_warnings.extend(rapport.get("warnings", []))
        meta_warnings = meta.get("avertissements", [])
        all_warnings.extend(meta_warnings)
        if all_warnings:
            lines.append("--- AVERTISSEMENTS ---")
            for w in all_warnings[:15]:
                lines.append(f"  [!] {w}")
            if len(all_warnings) > 15:
                lines.append(f"  ... et {len(all_warnings) - 15} autres avertissements")

        if not lines:
            lines.append("Aucun rapport métier disponible.")
            lines.append("Le rapport sera généré après extraction d'un plan PDF.")

        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.insert("1.0", "\n".join(lines))
        self.textbox.configure(state="disabled")


# ============================================================================
# Parametres de calcul de la modale "Nouveau metre"
# ============================================================================

class AnalyseOptions(ctk.CTkFrame):
    """Parametres de calcul compacts (theme de la maquette).

    Meme contrat public que `OptionsPanel` : le worker d'analyse appelle
    get_nom / get_mode / get_largeur_hors_tout / get_hsp / apply_vision_env
    sans savoir quelle interface le porte.
    """

    MODES = OptionsPanel.MODES
    VLM_CHOICES = OptionsPanel.VLM_CHOICES

    def __init__(self, master, nom_getter=None, **kwargs):
        from ui.design import T as TOKENS

        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)
        t = TOKENS
        self._t = t
        self._nom_getter = nom_getter
        self.grid_columnconfigure((0, 1), weight=1, uniform="opt")

        def lbl(row, col, text):
            ctk.CTkLabel(self, text=text, font=font(8, "bold"),
                         text_color=t["text_muted"]).grid(
                row=row, column=col, sticky="w",
                padx=(2, 8) if col == 0 else (12, 0), pady=(0, 2))

        def pad(col):
            return (2, 8) if col == 0 else (12, 2)

        lbl(0, 0, "MODE D'ANALYSE")
        lbl(0, 1, "VISION IA (100 % GRATUIT, OPTIONNEL)")
        self.mode_menu = ctk.CTkOptionMenu(
            self, values=self.MODES, height=30, font=font(10),
            fg_color=t["bg_input"], button_color=t["bg_hover"],
            button_hover_color=t["border"], text_color=t["text"],
            dropdown_fg_color=t["bg_card"], dropdown_text_color=t["text"],
            corner_radius=4)
        self.mode_menu.set(self.MODES[0])
        self.mode_menu.grid(row=1, column=0, sticky="ew", padx=pad(0),
                            pady=(0, 8))
        self.vlm_menu = ctk.CTkOptionMenu(
            self, values=self.VLM_CHOICES, height=30, font=font(10),
            fg_color=t["bg_input"], button_color=t["bg_hover"],
            button_hover_color=t["border"], text_color=t["text"],
            dropdown_fg_color=t["bg_card"], dropdown_text_color=t["text"],
            corner_radius=4, command=self._on_vlm_change)
        self.vlm_menu.set(self.VLM_CHOICES[0])
        self.vlm_menu.grid(row=1, column=1, sticky="ew", padx=pad(1),
                           pady=(0, 8))

        lbl(2, 0, "CLÉ GROQ (gsk_…) — JAMAIS ENVOYÉE AILLEURS")
        lbl(2, 1, "LARGEUR HORS-TOUT (m) — CALIBRATION")
        key_row = ctk.CTkFrame(self, fg_color="transparent")
        key_row.grid(row=3, column=0, sticky="ew", padx=pad(0), pady=(0, 8))
        key_row.grid_columnconfigure(0, weight=1)
        self.entry_api_key = ctk.CTkEntry(
            key_row, placeholder_text="gsk_…", height=30, show="*",
            font=font(10), fg_color=t["bg_input"], border_color=t["border"],
            corner_radius=4)
        self.entry_api_key.grid(row=0, column=0, sticky="ew")
        self.btn_toggle = ctk.CTkButton(
            key_row, text="👁", width=32, height=30, corner_radius=4,
            font=font(10), fg_color=t["bg_input"],
            hover_color=t["bg_hover"], text_color=t["text_dim"],
            command=self._toggle_visibility)
        self.btn_toggle.grid(row=0, column=1, padx=(4, 0))
        self._api_visible = False

        self.entry_largeur = ctk.CTkEntry(
            self, placeholder_text="Ex : 18.90 (vide = cotes du plan)",
            height=30, font=font(10), fg_color=t["bg_input"],
            border_color=t["border"], corner_radius=4)
        self.entry_largeur.grid(row=3, column=1, sticky="ew", padx=pad(1),
                                pady=(0, 8))

        lbl(4, 0, "HAUTEUR SOUS PLAFOND (m) — ENDUITS")
        self.entry_hsp = ctk.CTkEntry(
            self, height=30, font=font(10), fg_color=t["bg_input"],
            border_color=t["border"], corner_radius=4)
        self.entry_hsp.insert(0, "2.80")
        self.entry_hsp.grid(row=5, column=0, sticky="ew", padx=pad(0))

        self._load_from_env()
        self._on_vlm_change(self.vlm_menu.get())

    # ------------------------------------------------------------ etats
    def _toggle_visibility(self):
        self._api_visible = not self._api_visible
        self.entry_api_key.configure(show="" if self._api_visible else "*")
        self.btn_toggle.configure(text="🙈" if self._api_visible else "👁")

    def _on_vlm_change(self, choice):
        groq = str(choice).startswith("Groq")
        state = "normal" if groq else "disabled"
        self.entry_api_key.configure(state=state)
        self.btn_toggle.configure(state=state)

    def _load_from_env(self):
        """Cle Groq depuis l'environnement ou .env (jamais d'API payante)."""
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            env_path = BASE_DIR / ".env"
            if env_path.exists():
                try:
                    with open(env_path, encoding="utf-8") as f:
                        for line in f:
                            if line.strip().startswith("GROQ_API_KEY="):
                                api_key = line.split("=", 1)[1].strip().strip('"')
                                break
                except Exception:
                    pass
        if api_key:
            self.entry_api_key.configure(state="normal")
            self.entry_api_key.insert(0, api_key)
            self.vlm_menu.set(self.VLM_CHOICES[2])
            self._on_vlm_change(self.VLM_CHOICES[2])

    # ----------------------------------------------------------- getters
    @staticmethod
    def _float_or_none(entry):
        try:
            v = float(entry.get().strip().replace(",", "."))
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    def get_nom(self):
        if self._nom_getter is None:
            return None
        try:
            return (self._nom_getter() or "").strip() or None
        except Exception:
            return None

    def get_api_key(self):
        try:
            return self.entry_api_key.get().strip() or None
        except Exception:
            return None

    def get_mode(self) -> str:
        """'auto' | 'ba' | 'surfaces'"""
        current = self.mode_menu.get()
        idx = self.MODES.index(current) if current in self.MODES else 0
        return ("auto", "ba", "surfaces")[idx]

    def get_vlm_backend(self) -> str:
        """'off' | 'ollama' | 'groq'"""
        c = self.vlm_menu.get()
        if c.startswith("Groq"):
            return "groq"
        if c.startswith("Ollama"):
            return "ollama"
        return "off"

    def get_largeur_hors_tout(self):
        return self._float_or_none(self.entry_largeur)

    def get_hsp(self) -> float:
        return self._float_or_none(self.entry_hsp) or 2.80

    def apply_vision_env(self):
        """Propage les choix freemium aux routeurs (variables d'env.)."""
        os.environ["PLANBA_VLM_BACKEND"] = self.get_vlm_backend()
        key = self.get_api_key()
        if self.get_vlm_backend() == "groq" and key:
            os.environ["GROQ_API_KEY"] = key


# ============================================================================
# Adaptateurs : le worker d'analyse (290 lignes, inchange) parle encore
# aux anciens widgets. Ces objets absorbent ses appels sans rien casser.
# ============================================================================

class _Shim:
    """Objet inerte : toute methode appelee devient un no-op."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _KpiShim:
    """Le worker pousse ses totaux ; le workspace les recalcule lui-meme."""

    def __init__(self, app):
        self.app = app

    def update_values(self, plan_data=None, surfaces=None):
        self.app._kpi_input = (plan_data, surfaces)


class _SurfacesShim:
    """Le tableau des surfaces vit dans le workspace (onglet SURFACES)."""

    def __init__(self, app):
        self.app = app

    def clear(self):
        self.app.surfaces_result = None

    def update_from_result(self, surfaces):
        self.app.surfaces_result = surfaces


class _TabsShim:
    """Redirige les onglets de l'ancienne UI vers les onglets du workspace."""

    MAP = {
        "📐  Surfaces": "SURFACES",
        "SURFACES": "SURFACES",
        "🧾  Livrables": "AUDIT",
        "📊  Synthèse": "ÉLÉMENTS",
        "📋  Journal": "JOURNAL",
    }

    def __init__(self, app):
        self.app = app

    def set(self, name):
        target = self.MAP.get(name)
        if not target:
            return
        try:
            self.app.workspace.select_bottom(target)
        except Exception:
            pass


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
        self._fit_to_screen()
        self.minsize(900, 620)
        self.configure(fg_color=COLORS["bg_dark"])
        self._set_window_icon()

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

    def _set_window_icon(self):
        """Icone de fenetre / barre des taches (assets/app_icon.ico), en mode
        script comme en mode PyInstaller (dossier _internal/assets)."""
        candidates = [BASE_DIR / "assets" / "app_icon.ico"]
        if getattr(sys, "_MEIPASS", None):
            candidates.insert(0, Path(sys._MEIPASS) / "assets" / "app_icon.ico")
        for ico in candidates:
            if ico.exists():
                try:
                    self.iconbitmap(str(ico))
                    self.after(250, lambda p=str(ico): self.iconbitmap(p))
                    return
                except Exception:
                    continue

    def _build_ui_legacy(self):  # ancienne mise en page (conservee en reference)
        """Construit l'interface utilisateur."""

        self.configure(fg_color=COLORS["bg_dark"])
        self.surfaces_result = None

        # --- Header "chantier" : titre + bande jaune securite + etapes ---
        header = ctk.CTkFrame(self, fg_color=COLORS["primary"], height=118,
                              corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkFrame(header, fg_color=COLORS["accent"], height=5,
                     corner_radius=0).pack(fill="x", side="bottom")

        right_zone = ctk.CTkFrame(header, fg_color="transparent")
        right_zone.pack(side="right", padx=20, pady=8)

        titre_zone = ctk.CTkFrame(header, fg_color="transparent")
        titre_zone.pack(side="left", padx=20, pady=6, fill="x", expand=True)
        ctk.CTkLabel(
            titre_zone, text=f"🏗️  {APP_NAME} — {APP_TAGLINE}",
            font=ctk.CTkFont(size=21, weight="bold"),
            text_color="white").pack(anchor="w")
        ctk.CTkLabel(
            titre_zone, text=APP_SUBTITLE,
            font=ctk.CTkFont(size=11),
            text_color="#D6E4F5", wraplength=640,
            justify="left").pack(anchor="w")
        ctk.CTkLabel(
            right_zone,
            text="1 PLAN  ›  2 ANALYSE  ›  3 VÉRIFICATION  ›  4 LIVRABLES",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["accent"]).pack(anchor="e")
        ctk.CTkLabel(
            right_zone, text="BAEL 91 · RPS 2000 v2011 · NM 01.4.097 · DGA Maroc",
            font=ctk.CTkFont(size=10), text_color="#D6E4F5").pack(anchor="e")
        self.theme_switch = ctk.CTkSwitch(
            right_zone, text="Thème sombre",
            font=ctk.CTkFont(size=11), text_color="#D6E4F5",
            progress_color=COLORS["accent"],
            command=self._toggle_theme, width=50)
        self.theme_switch.pack(anchor="e", pady=(4, 0))
        self.theme_switch.select()

        # --- Page : haut fixe (plan + parametres + action), resultats en onglets
        self.page = ctk.CTkFrame(self, fg_color="transparent")
        self.page.pack(fill="both", expand=True, padx=20, pady=(10, 12))

        # 1. Zone de dépôt + options cote a cote
        top_row = ctk.CTkFrame(self.page, fg_color="transparent")
        top_row.pack(fill="x", pady=(0, 8))
        top_row.grid_columnconfigure(0, weight=5, uniform="top")
        top_row.grid_columnconfigure(1, weight=6, uniform="top")
        self.drop_zone = DropZone(
            top_row, height=150,
            on_file_selected=self._on_file_selected)
        self.drop_zone.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        opts_card = ctk.CTkFrame(top_row, fg_color=COLORS["bg_card"], corner_radius=10)
        opts_card.grid(row=0, column=1, sticky="nsew")
        ctk.CTkLabel(opts_card, text="⚙️  Paramètres ingénieur",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(6, 0))
        self.options_panel = OptionsPanel(opts_card)
        self.options_panel.pack(fill="x", pady=(0, 4))

        # 2. Action + progression sur une seule ligne (haut de page fixe)
        action_row = ctk.CTkFrame(self.page, fg_color="transparent")
        action_row.pack(fill="x", pady=(0, 6))
        action_row.grid_columnconfigure(1, weight=1)
        self.btn_action = ctk.CTkButton(
            action_row,
            text="🚀  Lancer l'Analyse & Générer le Métré",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=350, height=44, corner_radius=10,
            fg_color=COLORS["success"],
            hover_color="#1E8449",
            command=self._run_analysis)
        self.btn_action.grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.progress_panel = ProgressPanel(action_row)
        self.progress_panel.grid(row=0, column=1, sticky="ew")

        # 3. Onglets de resultats : plus de page unique de 1400 px a faire defiler
        self.tabs = ctk.CTkTabview(
            self.page, fg_color="transparent",
            segmented_button_selected_color=COLORS["primary"],
            segmented_button_selected_hover_color=COLORS["primary_dark"],
            segmented_button_unselected_color=COLORS["bg_card_alt"],
            command=self._on_tab_changed)
        self.tabs.pack(fill="both", expand=True)
        tab_syn = self.tabs.add("📊  Synthèse")
        tab_surf = self.tabs.add("📐  Surfaces")
        tab_liv = self.tabs.add("🧾  Livrables")
        tab_log = self.tabs.add("📋  Journal")

        self.tab_synthese = self._make_scroll(tab_syn)
        self.tab_surfaces = self._make_scroll(tab_surf)
        self.tab_livrables = self._make_scroll(tab_liv)
        self.tab_journal = self._make_scroll(tab_log)
        self.main_frame = self.tab_synthese  # compatibilite

        # 4. Indicateurs + tableau récapitulatif BA (onglet Synthese)
        self.kpi_strip = KPIStrip(self.tab_synthese)
        self.kpi_strip.pack(fill="x", pady=(0, 10))

        self.summary_table = SummaryTable(self.tab_synthese)
        self.summary_table.pack(fill="x", pady=(0, 10))

        # 5. Surfaces (plans d'architecture)
        self.surfaces_panel = SurfacesPanel(self.tab_surfaces)
        self.surfaces_panel.pack(fill="x", pady=(0, 4))

        # 6. Livrables
        actions_frame = ctk.CTkFrame(self.tab_livrables, fg_color="transparent")
        actions_frame.pack(fill="x", pady=(0, 10))
        for c in range(5):
            actions_frame.grid_columnconfigure(c, weight=1, uniform="act")
        f12 = ctk.CTkFont(size=12)

        self.btn_open_excel = ctk.CTkButton(
            actions_frame, text="📂  Métré BA (Excel)", font=f12, height=38,
            fg_color=COLORS["primary"], hover_color=COLORS["primary_dark"],
            text_color_disabled="#B9C7D8",
            state="disabled", command=self._open_excel)
        self.btn_open_excel.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.btn_open_surfaces = ctk.CTkButton(
            actions_frame, text="📐  Surfaces (Excel)", font=f12, height=38,
            fg_color=COLORS["accent"], hover_color="#D9A200", text_color="#1B1B1B",
            text_color_disabled="#5A4A18",
            state="disabled", command=self._open_surfaces_excel)
        self.btn_open_surfaces.grid(row=0, column=1, sticky="ew", padx=6)

        self.btn_open_overlay = ctk.CTkButton(
            actions_frame, text="🔍  Overlay d'audit", font=f12, height=38,
            fg_color=COLORS["steel"], hover_color="#C4621F",
            text_color_disabled="#4A2A12",
            state="disabled", command=self._open_overlay)
        self.btn_open_overlay.grid(row=0, column=2, sticky="ew", padx=6)

        self.btn_correct = ctk.CTkButton(
            actions_frame, text="✏️  Corriger les éléments", font=f12, height=38,
            fg_color="#555555", hover_color="#444444",
            text_color_disabled="#B9C7D8",
            state="disabled", command=self._open_correction)
        self.btn_correct.grid(row=0, column=3, sticky="ew", padx=6)

        self.btn_open_folder = ctk.CTkButton(
            actions_frame, text="📁  Dossier de sortie", font=f12, height=38,
            fg_color="#555555", hover_color="#444444",
            command=self._open_output_folder)
        self.btn_open_folder.grid(row=0, column=4, sticky="ew", padx=(6, 0))

        # 6b. Rapport métier
        self.rapport_metier = RapportMetierPanel(self.tab_livrables, height=360)
        self.rapport_metier.pack(fill="both", expand=True, pady=(0, 0))

        # 7. Journal
        self.log_text = ctk.CTkTextbox(
            self.tab_journal, height=420,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color=COLORS["bg_card"],
            text_color=COLORS["text_muted"])
        self.log_text.pack(fill="both", expand=True, pady=(0, 0))

        # Molette : pas de 4 px par unite et passage automatique du defilement
        # a la page quand une zone de texte est arrivee en butee.
        for tb in (self.log_text, getattr(self.rapport_metier, "textbox", None)):
            self._bind_wheel_passthrough(tb, self.tab_journal if tb is self.log_text
                                         else self.tab_livrables)

    def _make_scroll(self, parent):
        """Cadre defilant fluide (pas de 4 px par unite au lieu de 20)."""
        frame = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        frame.pack(fill="both", expand=True)
        try:
            frame._parent_canvas.configure(yscrollincrement=4)
        except Exception:
            pass
        return frame

    def _on_tab_changed(self):
        """Recalcule la mise en page de l'onglet affiche (evite le gel)."""
        try:
            self.update_idletasks()
        except Exception:
            pass

    def _bind_wheel_passthrough(self, textbox, target):
        if textbox is None or target is None:
            return
        inner = getattr(textbox, "_textbox", textbox)

        def on_wheel(event):
            try:
                first, last = inner.yview()
            except Exception:
                first, last = 0.0, 1.0
            up = event.delta > 0
            at_edge = (first <= 0.0 and up) or (last >= 1.0 and not up)
            if at_edge or (first <= 0.0 and last >= 1.0):
                try:
                    target._parent_canvas.yview("scroll",
                                                -int(event.delta / 6), "units")
                except Exception:
                    pass
                return "break"
            return None

        try:
            inner.bind("<MouseWheel>", on_wheel, add=True)
        except Exception:
            pass

    def _fit_to_screen(self):
        """Ouvre la fenetre a la taille de l'ecran (86 %) sans deborder."""
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            w = min(1320, max(980, int(sw * 0.86)))
            h = min(960, max(680, int(sh * 0.88)))
            x = max(0, (sw - w) // 2)
            y = max(0, int((sh - h) / 3))
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            self.geometry(APP_SIZE)

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
        try:
            self._log_lines.append(msg)
        except AttributeError:
            pass
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
        try:
            self.progress_panel.reset()
            self.progress_panel.grid()
        except Exception:
            pass
        self.btn_open_excel.configure(state="disabled")
        self.btn_open_folder.configure(state="disabled")
        for name in ("btn_open_surfaces", "btn_open_overlay", "btn_correct"):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.configure(state="disabled")
        if hasattr(self, "surfaces_panel"):
            self.surfaces_panel.clear()
        self.surfaces_result = None
        try:
            self.options_panel.apply_vision_env()
        except Exception:
            pass

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
                    try:
                        self.plan_data = extractor.extract_from_text_blocks(
                            ingestion_result.text_blocks)
                    except ExtractionError as e:
                        # Image sans ouvrage BA lisible : on continue vers le
                        # metre des surfaces au lieu d'une erreur fatale.
                        from core.local_extractor import partial_plan_data
                        self.plan_data = partial_plan_data(str(e))
                        self.after(0, lambda e=e: self._log(
                            f"Structure BA : {e} → tentative métré des surfaces"))

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

            # Enrichir avec le pipeline zero-miss
            if ext == ".pdf":
                try:
                    from core.pipeline.runner import PipelineRunner
                    self.after(0, lambda: self._log(
                        "Pipeline zero-miss : analyse approfondie..."))
                    runner = PipelineRunner(verbose=False)
                    pipeline_report = runner.run(file_path)
                    self._merge_pipeline_results(
                        self.plan_data, pipeline_report)
                    self.after(0, lambda: self._log(
                        f"Pipeline : {len(runner.store.instances)} instances, "
                        f"{sum(1 for i in runner.store.instances if i.dimensions)} avec dims"))
                except Exception as e:
                    msg = str(e)
                    self.after(0, lambda m=msg: self._log(
                        f"Pipeline : erreur {m}"))

                # Couche Computer Vision hybride (scans / polices SHX)
                try:
                    from core.vision_bridge import run_vision_hybride
                    self.after(0, lambda: self.progress_panel.set_step(
                        "1/5 : Computer Vision (pages scannées / SHX)...", 0.55))
                    vision = run_vision_hybride(
                        file_path, str(get_output_dir()), nom_saisi,
                        mode="auto",
                        log=lambda m: self.after(0, lambda m=m: self._log(m.strip())))
                    if vision["pages_vision"]:
                        self.plan_data.setdefault("_meta", {})["vision"] = {
                            "pages": vision["pages_vision"],
                            "overlays": vision["overlays"],
                            "xlsx_certifie": vision["xlsx"],
                            "n_records": len(vision["records"]),
                        }
                        c0 = self.plan_data.get("catalogue_types", {})
                        if (vision["plan_data_fallback"] and not any(
                                len(c0.get(k, {})) for k in
                                ("semelles", "poteaux", "poutres"))):
                            self.after(0, lambda: self._log(
                                "Extraction vectorielle vide → bascule sur "
                                "les éléments Computer Vision"))
                            self.plan_data = vision["plan_data_fallback"]
                            c = self.plan_data["catalogue_types"]
                            impl = self.plan_data["implantations"]
                except Exception as e:
                    self.after(0, lambda e=e: self._log(
                        f"Vision : erreur non bloquante ({e})"))
            for a in self.plan_data.get("_meta", {}).get("avertissements", []):
                self.after(0, lambda a=a: self._log(f"⚠ {a}"))
            for h in self.plan_data.get("_meta", {}).get("hypotheses", []):
                self.after(0, lambda h=h: self._log(f"• Hypothèse : {h}"))

            # --- Metre des surfaces (plan d'architecture : pieces / etages) ---
            try:
                mode = self.options_panel.get_mode()
                largeur = self.options_panel.get_largeur_hors_tout()
                hsp = self.options_panel.get_hsp()
            except Exception:
                mode, largeur, hsp = "auto", None, 2.80
            surfaces = {"xlsx": None, "overlays": [], "total_m2": 0.0,
                        "summary": {}, "reports": [], "warnings": []}
            from core.vision_bridge import plan_data_is_placeholder as _is_ph
            # Auto : sur un PDF ou aucun ouvrage BA n'a ete lu (plan d'archi)
            # ou sur une image ; jamais d'OCR surfaces inutile sur un plan BA.
            run_surfaces = (mode == "surfaces" or (
                mode == "auto" and (ext != ".pdf" or _is_ph(self.plan_data))))
            if run_surfaces:
                self.after(0, lambda: self.progress_panel.set_step(
                    "1/5 : Métré des surfaces (pièces / étages)...", 0.58))
                from core.vision_bridge import run_surface_metre
                surfaces = run_surface_metre(
                    file_path, str(get_output_dir()), nom_saisi,
                    known_width_m=largeur, hauteur_sous_plafond_m=hsp,
                    log=lambda m: self.after(0, lambda m=m: self._log(m.strip())))
                self.surfaces_result = surfaces
                if surfaces["xlsx"]:
                    self.after(0, lambda s=surfaces: self.surfaces_panel.update_from_result(s))
                    self.after(0, lambda: self.btn_open_surfaces.configure(state="normal"))
                    self.after(0, lambda: self.btn_open_overlay.configure(state="normal"))

            # Garde-fou partage CLI/UI (source de verite unique)
            from core.local_extractor import verifier_livrables_ou_lever
            from core.vision_bridge import plan_data_is_placeholder
            if mode == "surfaces" or plan_data_is_placeholder(self.plan_data):
                if surfaces["xlsx"]:
                    # Plan d'architecture : le metre des surfaces EST le livrable
                    self.after(0, lambda: self.progress_panel.set_step(
                        "✅ Terminé ! Métré des surfaces généré (aucun ouvrage BA sur ce plan).", 1.0))
                    self.after(0, lambda s=surfaces: self.kpi_strip.update_values(
                        self.plan_data, s))
                    self.after(0, lambda: self.btn_open_folder.configure(state="normal"))
                    self.after(0, lambda: self.tabs.set("📐  Surfaces"))
                    self.after(0, lambda s=surfaces: self._log(
                        "✅ Plan d'architecture : livrable = métré des surfaces\n"
                        f"   → {s['xlsx']}\n" + "".join(f"   → {o}\n" for o in s["overlays"])))
                    self.after(0, lambda: self._on_analysis_done("surfaces"))
                    success = True
                    return
                if mode == "surfaces":
                    raise ExtractionError(
                        "Aucune pièce identifiée sur ce plan (libellés de pièces "
                        "absents ou OCR local indisponible).")
            verifier_livrables_ou_lever(self.plan_data)
            self.after(0, lambda s=surfaces: self.kpi_strip.update_values(
                self.plan_data, s))

            # --- PHASE CORRECTION : Afficher la vue de correction ---
            self.after(0, lambda: self.progress_panel.set_step(
                "2/5 : Verification des elements extraits...", 0.65))
            time.sleep(0.2)

            # Stocker les donnees pour la correction
            self._correction_done = False
            self._corrected_data = None

            def on_correction_validated(corrected_data):
                self._corrected_data = corrected_data
                self._correction_done = True

            self.after(0, lambda: self._show_correction_view(
                on_correction_validated))

            # Attendre la fin de la correction (max 300 secondes)
            wait_start = time.time()
            while not self._correction_done and time.time() - wait_start < 300:
                time.sleep(0.1)

            if self._corrected_data:
                self.plan_data = self._corrected_data

            self.after(0, lambda: self.progress_panel.set_step(
                "3/5 : Calcul deterministe des metres et ferraillages...", 0.7))
            time.sleep(0.2)

            # Étape 3 : Génération Excel
            self.after(0, lambda: self.progress_panel.set_step(
                "3/5 : Injection dans le classeur Excel...", 0.8))
            time.sleep(0.2)

            output = get_output_dir()
            json_path = output / "plan_data.json"
            xlsx_path = output / "metre_genere.xlsx"

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.plan_data, f, ensure_ascii=False, indent=2, cls=PlanBAEncoder)

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

            # Mettre a jour le tableau recap
            self.after(0, lambda: self.summary_table.update_values(self.plan_data))
            self.after(0, lambda: self.rapport_metier.update_from_data(self.plan_data))
            self.after(0, lambda: self.btn_open_excel.configure(state="normal"))
            self.after(0, lambda: self.btn_open_folder.configure(state="normal"))
            self.after(0, lambda: self.btn_correct.configure(state="normal"))
            self.after(0, lambda: self.tabs.set("🧾  Livrables"))
            self.after(0, lambda: self._log(
                f"✅ Fichiers générés :\n"
                f"   → {json_path}\n"
                f"   → {xlsx_path}\n"
                f"   → {optim_xlsx}\n"
                f"   → {rapport_pdf}\n"
                f"   → {note_pdf}"))
            self.after(0, lambda: self._on_analysis_done("ba"))
            success = True

        except Exception as e:
            # `e` est efface a la sortie du bloc except : on fige les valeurs
            # avant de planifier les callbacks (sinon NameError dans Tk).
            tb = traceback.format_exc()
            err_msg = str(e)
            err_name = type(e).__name__
            self.after(0, lambda n=err_name, t=tb: self._log(
                f"❌ Erreur ({n}) :\n{t}"))
            self.after(0, lambda message=err_msg:
                       self.progress_panel.set_step(
                           f"❌ Erreur : {message}", 0))
            self.after(0, lambda message=err_msg, details=tb:
                       self._show_error(
                           f"{message}\n\nDétails techniques :\n{details}"))
        finally:
            self.after(0, lambda: self.btn_action.configure(
                state="normal", text="Lancer l'analyse  (Ctrl+Entrée)"))
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
        """Ouvre le metre BA (classeur gabarit + feuilles BET certifiees)."""
        xlsx_path = get_output_dir() / "metre_genere.xlsx"
        if xlsx_path.exists():
            os.startfile(str(xlsx_path))
        else:
            self._show_error(
                f"Fichier introuvable : {xlsx_path}\n\nLancez d'abord l'analyse "
                f"d'un plan BA (semelles / poteaux / poutres).")

    def _open_surfaces_excel(self):
        """Ouvre le classeur du metre des surfaces."""
        xlsx = (self.surfaces_result or {}).get("xlsx") or str(
            get_output_dir() / "metre_surfaces.xlsx")
        if Path(xlsx).exists():
            os.startfile(str(xlsx))
        else:
            self._show_error(f"Fichier introuvable : {xlsx}")

    def _open_overlay(self):
        """Ouvre le premier overlay d'audit (surfaces ou vision BA)."""
        overlays = list((self.surfaces_result or {}).get("overlays") or [])
        meta = (self.plan_data or {}).get("_meta", {}) if self.plan_data else {}
        overlays += list((meta.get("vision") or {}).get("overlays") or [])
        if not overlays:
            folder = get_output_dir() / "audit_overlays"
            overlays = [str(p) for p in sorted(folder.glob("*.png"))] if folder.exists() else []
        if overlays and Path(overlays[0]).exists():
            os.startfile(str(overlays[0]))
        else:
            self._show_error("Aucun overlay d'audit généré pour ce plan.")

    def _open_output_folder(self):
        """Ouvre le dossier de sortie."""
        output = get_output_dir()
        os.startfile(str(output))

    def _merge_pipeline_results(self, plan_data: dict, report: dict):
        """Fusionne les resultats du pipeline zero-miss dans catalogue_types."""
        catalogue = plan_data.setdefault("catalogue_types", {})

        # Mapping famille pipeline -> cle catalogue
        FAMILY_MAP = {
            "Poteau": "poteaux", "POOTEAU": "poteaux",
            "Poutre": "poutres", "POUTRE": "poutres",
            "Longrine": "longrines", "LONGRINE": "longrines",
            "Chainage": "chainages", "CHAINAGE": "chainages",
            "Semelle": "semelles", "SEMELLE": "semelles",
            "Voile": "voiles", "VOILE": "voiles",
            "Mur": "murs", "MUR": "murs",
            "Dalle": "dalles", "DALLE": "dalles",
            "Console": "poutres", "CONSOLE": "poutres",
            "Raidisseur": "murs", "RAIDISSEUR": "murs",
            "Regard": "dalles", "REGARD": "dalles",
            "Linteau": "longrines", "LINTEAU": "longrines",
            "Bande Noyee": "murs", "BANDE NOYEE": "murs",
        }

        instances = report.get("instances", [])
        for inst in instances:
            repere = inst.get("repere", "")
            family = inst.get("family_type", "")
            dims = inst.get("dimensions", {})

            cat_key = FAMILY_MAP.get(family, "")
            if not cat_key:
                continue

            # Extraire section
            sec_val = ""
            a_val = None
            b_val = None

            sec = dims.get("section")
            if isinstance(sec, dict):
                sec_val = sec.get("value", "")
            elif isinstance(sec, str):
                sec_val = sec

            a_raw = dims.get("a")
            if isinstance(a_raw, dict):
                a_val = a_raw.get("value")
            elif isinstance(a_raw, (int, float)):
                a_val = a_raw

            b_raw = dims.get("b")
            if isinstance(b_raw, dict):
                b_val = b_raw.get("value")
            elif isinstance(b_raw, (int, float)):
                b_val = b_raw

            # Creer l'entree si elle n'existe pas
            cat = catalogue.setdefault(cat_key, {})

            # Pour les poutres, la cle inclut la section
            if cat_key == "poutres":
                key = f"{repere}_{sec_val}" if sec_val else repere
            else:
                key = repere

            if key not in cat or not cat[key].get("a"):
                entry = cat.get(key, {})
                if sec_val and not entry.get("section_str"):
                    entry["section_str"] = sec_val
                if a_val and not entry.get("a"):
                    entry["a"] = a_val
                    if cat_key == "poutres":
                        entry["b"] = a_val
                if b_val and not entry.get("h") and not entry.get("b"):
                    if cat_key in ("poutres", "longrines", "chainages"):
                        entry["h"] = b_val
                    else:
                        entry["b"] = b_val
                if cat_key == "poteaux":
                    entry.setdefault("aciers_longitudinaux", [{"nb": 0, "phi": 0}])
                    entry.setdefault("long_bars", [{"nb": 0, "phi": 0}])
                entry.setdefault("level", "TOIT")
                cat[key] = entry

    def _show_correction_view(self, on_validate):
        """Affiche la vue de correction des elements extraits."""
        from ui.correction_view import CorrectionView
        CorrectionView(self, self.plan_data, on_validate=on_validate)

    def _open_correction(self):
        """Ouvre la vue de correction pour modifier les elements extraits."""
        if not self.plan_data:
            self._show_error("Aucune donnee extraite. Lancez d'abord l'analyse.")
            return

        def on_correction_done(corrected_data):
            self.plan_data = corrected_data
            self._log("Elements corriges par l'utilisateur.")
            # Regenerer les fichiers
            self._regenerate_files()

        self._show_correction_view(on_correction_done)

    def _regenerate_files(self):
        """Regenere les fichiers Excel et PDF avec les donnees corrigees."""
        if not self.plan_data:
            return

        self.btn_action.configure(state="disabled", text="⏳ Regeneration...")
        thread = threading.Thread(target=self._regeneration_worker, daemon=True)
        thread.start()

    def _regeneration_worker(self):
        """Worker de regeneration dans un thread separe."""
        try:
            from core.paths import get_output_dir
            output = get_output_dir()
            xlsx_path = output / "metre_genere.xlsx"

            self.after(0, lambda: self.progress_panel.set_step(
                "Regeneration de l'Excel...", 0.5))

            from build_metre import MetreGenerator
            gen = MetreGenerator(self.plan_data)
            gen.generer(str(xlsx_path))

            self.after(0, lambda: self.progress_panel.set_step(
                "Regeneration du rapport PDF...", 0.7))
            from rapport_metre import generer_rapport
            rapport_pdf = output / "rapport_metre.pdf"
            generer_rapport(self.plan_data, str(rapport_pdf))

            self.after(0, lambda: self.progress_panel.set_step(
                "✅ Termine ! Fichiers regeneres.", 1.0))
            self.after(0, lambda: self._log(
                f"✅ Fichiers regeneres :\n"
                f"   → {xlsx_path}\n"
                f"   → {rapport_pdf}"))

        except Exception as e:
            tb = traceback.format_exc()
            err_msg = str(e)
            self.after(0, lambda m=err_msg, t=tb: self._log(
                f"❌ Erreur regeneration : {m}\n{t}"))
        finally:
            self.after(0, lambda: self.btn_action.configure(
                state="normal", text="🚀  Lancer l'Analyse & Generer le Metre"))


# ============================================================================
# Point d'entrée
# ============================================================================

    # ==================================================================
    # Nouvelle interface (maquette Figma) : sidebar + accueil + workspace
    # ==================================================================
    def _build_ui(self):
        from core.bet_view_model import build_view_model
        from ui.design import T as TOKENS
        from ui.home import HomeView
        from ui.workspace import ResultsWorkspace

        self._T = TOKENS
        self._build_view_model = build_view_model
        self._log_lines = []

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()

        content = ctk.CTkFrame(self, fg_color=TOKENS["bg"], corner_radius=0)
        content.grid(row=0, column=1, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(content, fg_color=TOKENS["bg_panel"], height=36,
                           corner_radius=0)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text="PlanBA Pro — Métré Béton Armé & Surfaces",
                     font=font(11, "bold"), text_color=TOKENS["text_dim"]).grid(
            row=0, column=0, sticky="w", padx=14, pady=6)
        self.lbl_top_etat = ctk.CTkLabel(
            top, text="Prêt", font=font(10), text_color=TOKENS["text_muted"])
        self.lbl_top_etat.grid(row=0, column=2, sticky="e", padx=14)

        self.stack = ctk.CTkFrame(content, fg_color=TOKENS["bg"], corner_radius=0)
        self.stack.grid(row=1, column=0, sticky="nsew")
        self.stack.grid_columnconfigure(0, weight=1)
        self.stack.grid_rowconfigure(0, weight=1)

        self.home = HomeView(
            self.stack, on_new=self._open_metre_modal,
            on_open_plan=self._browse_plan,
            on_open_project=self._open_project)
        self.workspace = ResultsWorkspace(
            self.stack, on_action=self._workspace_action)
        self.home.grid(row=0, column=0, sticky="nsew")
        self.workspace.grid(row=0, column=0, sticky="nsew")
        self.workspace.grid_remove()

        self._build_metre_modal()

        # --- Alias de compatibilite avec le worker d'analyse -----------
        self.btn_open_excel = self.workspace.actions["metre"]
        self.btn_open_surfaces = self.workspace.actions["surfaces"]
        self.btn_open_overlay = self.workspace.actions["overlay"]
        self.btn_correct = self.workspace.actions["corriger"]
        self.btn_open_folder = self.workspace.actions["dossier"]
        self.log_text = self.workspace.tbl_journal
        self.summary_table = _Shim()
        self.rapport_metier = _Shim()
        self.kpi_strip = _KpiShim(self)
        self.surfaces_panel = _SurfacesShim(self)
        self.tabs = _TabsShim(self)
        self.options_panel = self.analyse_options

    def _build_sidebar(self):
        from ui.design import SidebarButton, T as TOKENS
        bar = ctk.CTkFrame(self, fg_color="#101010", width=250, corner_radius=0,
                           border_width=0)
        bar.grid(row=0, column=0, sticky="nsw")
        bar.grid_propagate(False)
        bar.grid_rowconfigure(3, weight=1)

        brand = ctk.CTkFrame(bar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=16, pady=(18, 16))
        ctk.CTkLabel(brand, text="PB", width=40, height=40,
                     fg_color=TOKENS["accent_soft"], corner_radius=8,
                     border_width=1, border_color=TOKENS["accent"],
                     font=font(13, "bold"),
                     text_color=TOKENS["accent"]).pack(anchor="w")
        ctk.CTkLabel(brand, text="PlanBA Pro", font=font(19, "bold"),
                     text_color=TOKENS["text"]).pack(anchor="w", pady=(10, 0))
        ctk.CTkLabel(brand, text="Métré Béton Armé & Surfaces", font=font(9),
                     text_color=TOKENS["text_muted"]).pack(anchor="w",
                                                            pady=(2, 0))

        SidebarButton(bar, "＋  Nouveau métré…", self._open_metre_modal,
                      primary=True).grid(row=1, column=0, sticky="ew",
                                         padx=16, pady=(0, 8))
        SidebarButton(bar, "📁  Ouvrir un plan…", self._browse_plan).grid(
            row=2, column=0, sticky="ew", padx=16)

        spacer = ctk.CTkFrame(bar, fg_color="transparent")
        spacer.grid(row=3, column=0, sticky="nsew")

        local = ctk.CTkFrame(bar, fg_color="transparent")
        local.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 10))
        ctk.CTkLabel(local, text="🛡  100 % local", font=font(10, "bold"),
                     text_color=TOKENS["success"]).pack(anchor="w")
        ctk.CTkLabel(local, text="Aucun envoi sur internet",
                     font=font(9), text_color=TOKENS["text_muted"]).pack(
            anchor="w", pady=(1, 0))

        SidebarButton(bar, "📁  Dossier de sortie", self._open_output_folder).grid(
            row=5, column=0, sticky="ew", padx=16, pady=(0, 4))
        ctk.CTkLabel(bar, text=f"v{APP_VERSION}  ·  BAEL 91 · RPS 2000 v2011",
                     font=font(8), text_color="#6b6b6b").grid(
            row=6, column=0, sticky="w", padx=16, pady=(0, 12))

    def _build_metre_modal(self):
        from ui.design import Badge, Banner, T as TOKENS

        self.modal = ctk.CTkFrame(self, fg_color="#0b0b0b", corner_radius=0)
        self.modal.grid_columnconfigure(0, weight=1)
        self.modal.grid_rowconfigure(0, weight=1)

        card = ctk.CTkFrame(self.modal, fg_color=TOKENS["bg_panel"],
                            corner_radius=10, border_width=1,
                            border_color=TOKENS["border_strong"], width=860)
        card.grid(row=0, column=0)
        card.grid_columnconfigure(0, weight=1, minsize=640)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 10))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Nouveau métré", font=font(14, "bold"),
                     text_color=TOKENS["text"]).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="✕", width=26, height=26, corner_radius=4,
                      fg_color="transparent", hover_color=TOKENS["bg_hover"],
                      text_color=TOKENS["text_dim"],
                      command=self._close_metre_modal).grid(row=0, column=1,
                                                            sticky="e")

        ctk.CTkFrame(card, height=1, fg_color=TOKENS["border"]).grid(
            row=1, column=0, sticky="ew")

        self.drop_zone = DropZone(card, height=190,
                                  on_file_selected=self._on_file_selected)
        self.drop_zone.grid(row=2, column=0, sticky="ew", padx=16, pady=(12, 6))

        self.modal_banner = Banner(card, "", "ok")
        self.modal_banner.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 8))
        self.modal_banner.grid_remove()

        fields = ctk.CTkFrame(card, fg_color="transparent")
        fields.grid(row=4, column=0, sticky="ew", padx=16)
        fields.grid_columnconfigure((0, 1), weight=1, uniform="f")
        ctk.CTkLabel(fields, text="Nom du projet", font=font(9),
                     text_color=TOKENS["text_muted"]).grid(row=0, column=0,
                                                           sticky="w")
        self.entry_nom = ctk.CTkEntry(
            fields, placeholder_text="Ex : Villa R+2 — Fondations", height=32,
            font=font(11), fg_color=TOKENS["bg_input"],
            border_color=TOKENS["border"])
        self.entry_nom.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(2, 8))

        ctk.CTkLabel(fields, text="Dossier de sortie", font=font(9),
                     text_color=TOKENS["text_muted"]).grid(row=0, column=1,
                                                           sticky="w")
        dossier = ctk.CTkFrame(fields, fg_color="transparent")
        dossier.grid(row=1, column=1, sticky="ew", pady=(2, 8))
        dossier.grid_columnconfigure(0, weight=1)
        self.entry_dossier = ctk.CTkEntry(
            dossier, height=32, font=font(10), fg_color=TOKENS["bg_input"],
            border_color=TOKENS["border"])
        self.entry_dossier.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(dossier, text="📁", width=34, height=32, corner_radius=4,
                      fg_color=TOKENS["bg_input"], hover_color=TOKENS["bg_hover"],
                      text_color=TOKENS["text_dim"],
                      command=self._open_output_folder).grid(row=0, column=1,
                                                             padx=(4, 0))

        # --- Parametres de calcul (repliables) ------------------------
        self.btn_params = ctk.CTkButton(
            card, text="▸  Paramètres de calcul", anchor="w", height=32,
            corner_radius=5, font=font(11),
            fg_color=TOKENS["bg_card"], hover_color=TOKENS["bg_hover"],
            border_width=1, border_color=TOKENS["border"],
            text_color=TOKENS["text_dim"], command=self._toggle_params)
        self.btn_params.grid(row=5, column=0, sticky="ew", padx=16)

        self.params_host = ctk.CTkFrame(card, fg_color=TOKENS["bg_card"],
                                        corner_radius=5, border_width=1,
                                        border_color=TOKENS["border"])
        self.analyse_options = AnalyseOptions(
            self.params_host, nom_getter=lambda: self.entry_nom.get())
        self.analyse_options.pack(fill="x", padx=6, pady=6)
        self._params_open = False

        # --- Pied de modale -----------------------------------------
        ctk.CTkFrame(card, height=1, fg_color=TOKENS["border"]).grid(
            row=7, column=0, sticky="ew", pady=(10, 0))
        foot = ctk.CTkFrame(card, fg_color="transparent")
        foot.grid(row=8, column=0, sticky="ew", padx=16, pady=(10, 12))
        foot.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(foot, text="🛡  100 % local · aucune API",
                     font=font(9), text_color=TOKENS["text_muted"]).grid(
            row=0, column=0, sticky="w")
        ctk.CTkButton(foot, text="Annuler", height=32, width=90, corner_radius=4,
                      font=font(11), fg_color=TOKENS["bg_input"],
                      hover_color=TOKENS["bg_hover"], text_color=TOKENS["text_dim"],
                      command=self._close_metre_modal).grid(row=0, column=2,
                                                            padx=(0, 6))
        self.btn_action = ctk.CTkButton(
            foot, text="Lancer l'analyse  (Ctrl+Entrée)", height=32, width=200,
            corner_radius=4, font=font(11, "bold"), state="disabled",
            fg_color=TOKENS["accent"], hover_color=TOKENS["accent_dark"],
            text_color_disabled="#6d7f96", command=self._run_analysis)
        self.btn_action.grid(row=0, column=3)

        # Progression : ligne dediee, masquee tant qu'aucune analyse ne tourne
        self.progress_panel = ProgressPanel(foot)
        self.progress_panel.grid(row=1, column=0, columnspan=4, sticky="ew",
                                 pady=(8, 0))
        self.progress_panel.grid_remove()

        self.bind("<Control-Return>", lambda _e: self._run_analysis())
        self.modal.bind("<Escape>", lambda _e: self._close_metre_modal())

    def _toggle_params(self):
        self._params_open = not self._params_open
        if self._params_open:
            self.params_host.grid(row=6, column=0, sticky="ew", padx=16,
                                  pady=(6, 0))
            self.btn_params.configure(text="▾  Paramètres de calcul")
        else:
            self.params_host.grid_remove()
            self.btn_params.configure(text="▸  Paramètres de calcul")

    def _open_metre_modal(self):
        from core.paths import get_output_dir
        try:
            self.entry_dossier.delete(0, "end")
            self.entry_dossier.insert(0, str(get_output_dir()))
        except Exception:
            pass
        self.modal.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.modal.lift()
        try:
            self.drop_zone.label_title.focus_set()
        except Exception:
            pass

    def _close_metre_modal(self):
        self.modal.place_forget()

    def _browse_plan(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Ouvrir un plan",
            filetypes=[("Plans et images",
                        "*.pdf *.dxf *.dwg *.png *.jpg *.jpeg *.tif *.tiff "
                        "*.bmp *.webp"),
                       ("Tous les fichiers", "*.*")])
        if path:
            self._open_metre_modal()
            self.drop_zone.set_file(path)

    def _show_home(self):
        self.workspace.grid_remove()
        self.home.grid(row=0, column=0, sticky="nsew")
        self.home.refresh()
        self.lbl_top_etat.configure(text="Prêt")

    def _show_workspace(self):
        self.home.grid_remove()
        self._close_metre_modal()
        self.workspace.grid(row=0, column=0, sticky="nsew")
        self.lbl_top_etat.configure(text="Analyse terminée")

    def _workspace_action(self, key: str):
        {"metre": self._open_excel,
         "surfaces": self._open_surfaces_excel,
         "overlay": self._open_overlay,
         "corriger": self._open_correction,
         "dossier": self._open_output_folder}.get(key, lambda: None)()

    def _refresh_workspace(self, plan_data=None, surfaces=None,
                           duree_s: float = 0.0):
        plan_data = plan_data if plan_data is not None else self.plan_data
        surfaces = surfaces if surfaces is not None else getattr(
            self, "surfaces_result", None)
        projet = ""
        try:
            nom_saisi = self.entry_nom.get().strip()
        except Exception:
            nom_saisi = ""
        if nom_saisi:
            projet = nom_saisi
        else:
            try:
                projet = plan_data.get("projet", {})
                projet = projet.get("nom") if isinstance(projet, dict) else projet
            except Exception:
                projet = ""
        projet = projet or "Projet"
        vm = self._build_view_model(plan_data, surfaces, projet)
        self.workspace.show_results(
            plan_data, surfaces, projet=projet,
            fichier=getattr(self, "selected_file_path", "") or "",
            duree_s=duree_s, log_text="\n".join(self._log_lines), vm=vm)
        self._show_workspace()

    def _on_analysis_done(self, mode: str = "ba"):
        """Fin d'analyse : historique local + bascule sur les resultats."""
        from core.history import add_entry
        from core.paths import get_output_dir

        self._refresh_workspace()
        vm = self.workspace.vm
        tot = vm.get("totaux", {})
        chute = 0.0
        stocks = vm.get("stocks") or {}
        if stocks:
            achat = sum(v.get("purchased_length_m", 0.0) for v in stocks.values())
            perdu = sum(v.get("waste_length_m", 0.0) for v in stocks.values())
            chute = round(perdu / achat * 100, 1) if achat else 0.0
        statut = "À vérifier" if vm.get("anomalies") else "Terminé"
        nom = (self.entry_nom.get().strip()
               or (vm.get("meta", {}).get("projet") or "Projet"))
        try:
            add_entry(
                nom, getattr(self, "selected_file_path", ""),
                type_analyse=mode, beton=tot.get("beton_arme", 0.0),
                acier=tot.get("acier", 0.0), chute=chute, statut=statut,
                dossier=str(get_output_dir()),
                surfaces=vm.get("surfaces", {}).get("total", 0.0),
                alertes=len(vm.get("anomalies", [])))
        except Exception:
            pass
        self.home.refresh()
        for key in ("metre", "surfaces", "overlay", "corriger", "dossier"):
            self.workspace.set_action_state(key, True)

    def _open_project(self, entry: dict):
        import json
        from core.paths import get_output_dir
        dossier = Path(entry.get("dossier") or get_output_dir())
        plan_json = dossier / "plan_data.json"
        if not plan_json.exists():
            from tkinter import messagebox
            messagebox.showinfo(
                "Projet indisponible",
                f"Aucune donnée retrouvée dans :\n{dossier}\n\n"
                "Relancez l'analyse du plan.")
            return
        try:
            with open(plan_json, encoding="utf-8") as f:
                self.plan_data = json.load(f)
        except Exception as exc:
            from tkinter import messagebox
            messagebox.showerror("Lecture impossible", str(exc))
            return
        self.selected_file_path = entry.get("chemin_source") or entry.get(
            "fichier", "")
        try:
            self.entry_nom.delete(0, "end")
            self.entry_nom.insert(0, entry.get("nom", ""))
        except Exception:
            pass
        self._refresh_workspace()


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
