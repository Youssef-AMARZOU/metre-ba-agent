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

APP_NAME = "PlanBA Pro"
APP_TAGLINE = "Métré Béton Armé & Surfaces"
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
            pieces.append(f"{n_ouvrages} ouvrages")
        self.values["pieces"].configure(text=" / ".join(pieces) if pieces else "—")
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
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent", height=170)
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

    def _build_ui(self):
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

        # --- Conteneur principal scrollable ---
        self.main_frame = ctk.CTkScrollableFrame(
            self, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=12)

        # 1. Zone de dépôt + options cote a cote
        top_row = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        top_row.pack(fill="x", pady=(0, 10))
        top_row.grid_columnconfigure(0, weight=5, uniform="top")
        top_row.grid_columnconfigure(1, weight=6, uniform="top")
        self.drop_zone = DropZone(
            top_row, height=200,
            on_file_selected=self._on_file_selected)
        self.drop_zone.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        opts_card = ctk.CTkFrame(top_row, fg_color=COLORS["bg_card"], corner_radius=10)
        opts_card.grid(row=0, column=1, sticky="nsew")
        ctk.CTkLabel(opts_card, text="⚙️  Paramètres ingénieur",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(8, 2))
        self.options_panel = OptionsPanel(opts_card)
        self.options_panel.pack(fill="x", pady=(0, 8))

        # 2. Bouton d'action
        self.btn_action = ctk.CTkButton(
            self.main_frame,
            text="🚀  Lancer l'Analyse & Générer le Métré",
            font=ctk.CTkFont(size=15, weight="bold"),
            height=50, corner_radius=10,
            fg_color=COLORS["success"],
            hover_color="#1E8449",
            command=self._run_analysis)
        self.btn_action.pack(fill="x", pady=(0, 8))

        # 3. Barre de progression
        self.progress_panel = ProgressPanel(self.main_frame)
        self.progress_panel.pack(fill="x", pady=(0, 10))

        # 4. Indicateurs
        self.kpi_strip = KPIStrip(self.main_frame)
        self.kpi_strip.pack(fill="x", pady=(0, 10))

        # 5. Surfaces (plans d'architecture)
        self.surfaces_panel = SurfacesPanel(self.main_frame)
        self.surfaces_panel.pack(fill="x", pady=(0, 10))

        # 6. Tableau récapitulatif BA
        self.summary_table = SummaryTable(self.main_frame)
        self.summary_table.pack(fill="x", pady=(0, 10))

        # 6b. Rapport métier
        self.rapport_metier = RapportMetierPanel(self.main_frame, height=320)
        self.rapport_metier.pack(fill="x", pady=(0, 10))

        # 7. Livrables
        actions_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        actions_frame.pack(fill="x")
        for c in range(5):
            actions_frame.grid_columnconfigure(c, weight=1, uniform="act")
        f12 = ctk.CTkFont(size=12)

        self.btn_open_excel = ctk.CTkButton(
            actions_frame, text="📂  Métré BA (Excel)", font=f12, height=38,
            fg_color=COLORS["primary"], hover_color=COLORS["primary_dark"],
            state="disabled", command=self._open_excel)
        self.btn_open_excel.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.btn_open_surfaces = ctk.CTkButton(
            actions_frame, text="📐  Surfaces (Excel)", font=f12, height=38,
            fg_color=COLORS["accent"], hover_color="#D9A200", text_color="#1B1B1B",
            state="disabled", command=self._open_surfaces_excel)
        self.btn_open_surfaces.grid(row=0, column=1, sticky="ew", padx=6)

        self.btn_open_overlay = ctk.CTkButton(
            actions_frame, text="🔍  Overlay d'audit", font=f12, height=38,
            fg_color=COLORS["steel"], hover_color="#C4621F",
            state="disabled", command=self._open_overlay)
        self.btn_open_overlay.grid(row=0, column=2, sticky="ew", padx=6)

        self.btn_correct = ctk.CTkButton(
            actions_frame, text="✏️  Corriger les éléments", font=f12, height=38,
            fg_color="#555555", hover_color="#444444",
            state="disabled", command=self._open_correction)
        self.btn_correct.grid(row=0, column=3, sticky="ew", padx=6)

        self.btn_open_folder = ctk.CTkButton(
            actions_frame, text="📁  Dossier de sortie", font=f12, height=38,
            fg_color="#555555", hover_color="#444444",
            command=self._open_output_folder)
        self.btn_open_folder.grid(row=0, column=4, sticky="ew", padx=(6, 0))

        # 8. Journal
        ctk.CTkLabel(self.main_frame, text="📋 Journal d'analyse (traçabilité)",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["text_muted"]).pack(anchor="w", pady=(10, 2))
        self.log_text = ctk.CTkTextbox(
            self.main_frame, height=130,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color=COLORS["bg_card"],
            text_color=COLORS["text_muted"])
        self.log_text.pack(fill="x", pady=(0, 0))

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
                    self.after(0, lambda: self._log(
                        f"Pipeline : erreur {e}"))

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
            if mode in ("auto", "surfaces"):
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
                    self.after(0, lambda s=surfaces: self._log(
                        "✅ Plan d'architecture : livrable = métré des surfaces\n"
                        f"   → {s['xlsx']}\n" + "".join(f"   → {o}\n" for o in s["overlays"])))
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
            h_val = None

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
            self.after(0, lambda: self._log(
                f"❌ Erreur regeneration : {e}\n{tb}"))
        finally:
            self.after(0, lambda: self.btn_action.configure(
                state="normal", text="🚀  Lancer l'Analyse & Generer le Metre"))


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
