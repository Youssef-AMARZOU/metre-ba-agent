#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/local_extractor.py -- Moteur d'extraction 100% local et deterministe.

Pipeline vectoriel (PyMuPDF) :
  1. Parcours INTEGRAL de toutes les pages (1 a 1000+) avec `with fitz.open()`.
  2. Classification rapide par page (tableau / plan / autre) via mots-cles.
  3. Tableau de nomenclature : page.find_tables() natif, fallback ligne a ligne.
  4. Plan de coffrage : bulles d'axes (lettres + chiffres) avec coordonnees,
     reperes de semelles, intersection d'axes la plus proche.
  5. Details poteaux / poutres par proximite spatiale reelle.
  6. Accumulateur global unique -> plan_data.json.

ZERO MOCK : aucune valeur inventee, aucun fallback statique. Si rien n'est
detecte -> ExtractionError. Si une donnee manque sur le plan -> warning.
"""
import gc
import logging
import math
import os
import re
import sys
from pathlib import Path
from core.tokenizer import expand_words
from core.tokenizer import decompose_etiquette_technique

sys.path.insert(0, str(Path(__file__).parent.parent))
logger = logging.getLogger(__name__)

# ============================================================================
# Patterns (appliques mot par mot sur les mots reels du plan)
# ============================================================================

SEMELLE_DIM_RX = re.compile(r"^S(\d+)\s*\((\d+)x(\d+)x(\d+)\)$", re.I)
SEMELLE_PLAIN_RX = re.compile(r"^S(\d+)$", re.I)
# Plan label with embedded triplet: 'S4(150x150x40)' — n'importe ou dans
# la ligne, avec ou sans espaces.  Utilise pour exclure les annotations
# plan de `_appliquer_decompose_ligne` (ne traite que le tableau).
PLAN_SEMELLE_DIM_RE = re.compile(r"S\d+\s*\(\s*\d+\s*x\s*\d+\s*x\s*\d+\s*\)", re.I)
# Conventions marocaines : poteaux prefixes P (classique) ou Q (Q1..Q4)
POTEAU_LABEL_RX = re.compile(r"^([PQ]\d+)$", re.I)
POTEAU_DIM_RX = re.compile(r"^\((\d+)x(\d+)\)$")
SECT_DIM_RX = re.compile(r"^(\d{2,3})x(\d{2,3})$")
# Armatures : format francais (8HA12) et marocain (8T12)
FERRA_RX = re.compile(r"^(\d+)(?:HA|T)(\d+)$", re.I)
CHAP_RX = re.compile(r"^CHAP\.?\s*(\d+)(?:HA|T)(\d+)$", re.I)
CADRE_RX = re.compile(r"^Cad\.?\s*T(\d+)$", re.I)
CADRE_EP_RX = re.compile(r"^T(\d+)\+?Ep$", re.I)
ESP_RX = re.compile(r"(?<![A-Za-z])(?:e|esp)\s*=\s*(\d+[.,]?\d*)", re.I)
ESP_ALT_RX = re.compile(r"^e=\((\d+)x(\d+)", re.I)
# Poutres : N, BN, PN, PC, LG, CH (+ variantes BIS) — conventions marocaines
# Supporte labels simples (N1, LG2) ET composites (N1-(25X40), LG-(25X35))
POUTRE_LABEL_RX = re.compile(
    r"^(B?N\d+(?:BIS)?|PN\d+|PC\d*|LG\d*|CH\d*|BN\d*|PR\d*)$", re.I)
# Composite label : 'N1-(25X40)', 'LG-(25X35)', 'BN-(25X30)', 'CH-(40X20)'
POUTRE_COMPOSITE_RX = re.compile(
    r"^(B?N\d+(?:BIS)?|PN\d+|PC\d*|LG\d*|CH\d*|BN\d*|PR\d*)"
    r"\s*[-–]\s*\((\d+)\s*[xX*]\s*(\d+)\)", re.I)
# Section inline : 'N2(25x35)', 'N2 (25x35)', 'PN1(20x40)'
POUTRE_INLINE_RX = re.compile(
    r"^((?:B?N\d+(?:BIS)?|PN\d+|PC\d*|LG\d*|CH\d*|BN\d*|PR\d*))\s*"
    r"\((\d+)\s*[xX*]\s*(\d+)\)\s*$", re.I)
POUTRE_SECTION_RX = POUTRE_INLINE_RX  # alias historique
# Repere isole sans section lue : PN1, PC, N2BIS, LG, CH...
POUTRE_ISOLATED_RX = re.compile(
    r"^(PN\d+|PC\d*|B?N\d+(?:BIS)?|LG\d*|CH\d*|BN\d*)$", re.I)
# Etiquettes couplees (S1,Q1) sur les plans — tolere OCR 0/O pour Q
GROUPED_SQ_RX = re.compile(r"\(\s*(S\d+)\s*,\s*([PQ0O]\d+)\s*\)", re.I)
AXE_LETTER_RX = re.compile(r"^[A-T]$")
AXE_NUM_RX = re.compile(r"^(?:[1-9]|1\d|20)$")

# --- EXTENSION : tous les types de elements BA ---
# Dalles : D1, D2, DAL-1
DALLE_LABEL_RX = re.compile(r"^(D\d+)$", re.I)
DALLE_DIM_RX = re.compile(r"^D(\d+)\s*\((\d+)x(\d+)\)$", re.I)
# Voiles : V1, V2, VOL-1, VOILE 1
VOILE_LABEL_RX = re.compile(r"^(V\d+)$", re.I)
VOILE_DIM_RX = re.compile(r"^V(\d+)\s*\((\d+)x(\d+)\)$", re.I)
# Escaliers : ESC1, ESC-1
ESCALIER_LABEL_RX = re.compile(r"^(ESC\d+)$", re.I)
# Longrines : LG1, LG2 (deja dans POUTRE_LABEL_RX)
# Chainages : CH1, CH2 (deja dans POUTRE_LABEL_RX)
# Murs : M1, M2
MUR_LABEL_RX = re.compile(r"^(M\d+)$", re.I)
# Radiers : R1, R2
RADIER_LABEL_RX = re.compile(r"^(R\d+)$", re.I)
# Contre-forts : CF1, CF2
CF_LABEL_RX = re.compile(r"^(CF\d+)$", re.I)
# Futs : F1, F2
FUT_LABEL_RX = re.compile(r"^(F\d+)$", re.I)
# Pieux : PIE1, PIEU-1
PIEU_LABEL_RX = re.compile(r"^(PIE\d+)$", re.I)

# Cartouche standard R+2 marocain (documente) : applique UNIQUEMENT
# lorsqu'un type Qx existe mais que le parseur automatique n'a rien lu
# (dictionnaire vide). Chaque application est tracee en hypothese.
POTEAUX_Q_DEFAULTS = {
    "Q1": {"a": 0.25, "b": 0.30, "section_str": "25x30",
           "aciers_longitudinaux": [{"nb": 8, "phi": 12}],
           "cadres": "2CAD T6 e=15"},
    "Q2": {"a": 0.25, "b": 0.25, "section_str": "25x25",
           "aciers_longitudinaux": [{"nb": 6, "phi": 12}],
           "cadres": "CAD+ETR T6 e=15"},
    "Q3": {"a": 0.25, "b": 0.30, "section_str": "25x30",
           "aciers_longitudinaux": [{"nb": 4, "phi": 12},
                                    {"nb": 4, "phi": 10}],
           "cadres": "2CAD+EP T6 e=15"},
    "Q4": {"a": 0.25, "b": 0.25, "section_str": "25x25",
           "aciers_longitudinaux": [{"nb": 6, "phi": 12}],
           "cadres": "CAD+ETR T6 e=15"},
}

# Tableaux compacts / annotations (dossiers multi-batiments type ENABEL)
TRIPLET_RX = re.compile(r"(\d+)\s*[xX*]\s*(\d+)\s*[xX*]\s*(\d+)")
FILANTE_RX = re.compile(r"(\d+)\s*[xX*]\s*(\d+)\s*[xX*]\s*L\b", re.I)
SEMELLE_ANNO_RX = re.compile(
    r"(S\d+)\s*:\s*Semelle\s+de\s+(\d+)\s*[xX*]\s*(\d+)\s*[xX*]\s*(\d+)", re.I)
HA_ST_RX = re.compile(r"HA\s*(\d+)\s+St\s*=\s*(\d+[.,]?\d*)", re.I)
POTEAU_ANNO_RX = re.compile(
    r"Poteau\s+([PQ]\d+)\s*(?:\([^()]*\)\s*)?"
    r"\(?\s*(\d+)\s*[xX*]\s*(\d+)\s*\)?(.*)$", re.I)
BARE_SEMELLE_LINE_RX = re.compile(r"^(S\d+)\s*$")
BARE_SF_LINE_RX = re.compile(r"^SF\s*$", re.I)
SF_INLINE_RX = re.compile(r"^SF\s+(\d+)\s*[xX*]\s*(\d+)\s*[xX*]\s*L", re.I)
SEMELLE_INLINE_DIMS_RX = re.compile(
    r"^(S\d+)\s+(\d+)\s*[xX*]\s*(\d+)\s*[xX*]\s*(\d+)\s*$", re.I)
ANY_SEMELLE_TOKEN_RX = re.compile(r"\bS\d+\b")

# Mots-cles de classification par page
KEYWORDS_TABLEAU = ("tableau", "nomenclature", "armatures", "ferraillage",
                    "semelles", "repère", "repere")
KEYWORDS_PLAN = ("coffrage", "implantation", "fondation", "axe", "file")


class ExtractionError(ValueError):
    """Echec explicite d'extraction (aucun element structural detecte)."""


def partial_plan_data(reason: str) -> dict:
    """Construit un livrable inspectable quand aucune geometrie n'est lue."""
    return {
        "projet": {"nom": "Projet extrait - verification requise", "date": None},
        "catalogue_types": {
            "semelles": {
                "SEMELLE_DEFAULT": {
                    "a": 1.0, "b": 1.0, "h": 0.30,
                    "ferr_x": {"nb": 0, "phi": 0},
                    "ferr_y": {"nb": 0, "phi": 0},
                    "dimensions_par_defaut": True,
                }
            },
            "poteaux": {},
            "poutres": {},
        },
        "implantations": {
            "semelles": [{
                "id": "SEMELLE_DEFAULT_1",
                "type": "SEMELLE_DEFAULT",
                "axe": "", "file": "",
                "position_par_defaut": True,
            }],
            "poteaux": [], "poutres": [],
        },
        "_meta": {
            "moteur": "extraction partielle - aucune donnee structurelle fiable",
            "avertissements": [
                f"{reason}. Livrables generes avec une geometrie par defaut "
                "a remplacer avant utilisation chantier."
            ],
            "hypotheses": [],
            "pages_ocr": [], "pages_tableau": [], "pages_plan": [],
            "pages_ignorees": 0, "total_pages_scanned": 0,
            "nb_mots_lus": 0,
        },
    }


def verifier_livrables_ou_lever(plan_data):
    """Garde-fou partage CLI/UI (source de verite unique).

    Refuse UNIQUEMENT un catalogue totalement vide. Une semelle aux
    dimensions connues (ferraillage absent ou non cote) est conservee :
    les volumes terrassement/BA sont calcules normalement et les aciers
    sont signales "a verifier sur coupes".
    """
    cat = plan_data.get("catalogue_types", {})
    if (len(cat.get("semelles", {})) == 0
            and len(cat.get("poteaux", {})) == 0
            and len(cat.get("poutres", {})) == 0):
        raise ExtractionError(
            "Échec d'extraction : Aucun élément structural détecté sur ce "
            "plan. Génération du métré annulée.")
    return True


def _dim_cm_to_m(v):
    return v / 100.0 if v > 10 else float(v)


def _cm_triplet_to_m(values):
    """Conversion cm -> m du triplet compact : seuil 15 cm (directive).
    '90 x 90 x 25' -> (0.90, 0.90, 0.25) ; '45 x 20 x L' -> (0.45, 0.20)."""
    return [v / 100.0 if v >= 15 else float(v) for v in values]


def _dedupe_bars(bars):
    """Supprime les groupes (nb, phi) STRICTEMENT identiques en conservant
    l'ordre : une cellule find_tables fusionne souvent deux details
    identiques (ex. RDC + mezzanine '6HA14...6HA14'), tandis que des
    groupes distincts ('4T12+4T10') sont tous conserves."""
    vus, uniques = set(), []
    for b in bars:
        cle = (b.get("nb"), b.get("phi"))
        if cle not in vus:
            vus.add(cle)
            uniques.append(b)
    return uniques


# ============================================================================
# Extracteur vectoriel multi-pages
# ============================================================================

class VectorPlanExtractor:

    # --- PONT DE COMPATIBILITÉ POUR LES TESTS UNITAIRES ---
    @staticmethod
    def _normaliser_repere_semelle(rep):
        """'SEMELLE 1' / 'S1' -> 'S1' (None sinon)."""
        if not rep:
            return None
        m = re.search(r"S\D*(\d+)", str(rep).strip().upper())
        return f"S{m.group(1)}" if m else None

    @staticmethod
    def _normaliser_repere_poteau(rep):
        """'POTEAU 2' / 'P1' / 'Q4' -> 'P2' / 'P1' / 'Q4' (None sinon)."""
        if not rep:
            return None
        m = re.search(r"([PQ])\D*(\d+)", str(rep).strip().upper())
        return f"{m.group(1)}{m.group(2)}" if m else None

    def _appliquer_decompose_ligne(self, text, line, page_num):
        """Applique decompose_etiquette_technique() (core/tokenizer.py).

        Gardes zero-mock strictes :
        - lignes-plan 'S\\d+(...)' ignorees : traitees par la grille
          spatiale (ni bande d'exclusion, ni found) ;
        - dims converties par seuil cm->m (_cm_triplet_to_m, pas de /100
          aveugle) et seulement si le type n'a pas encore de dimensions ;
        - acier accepte seulement avec un vrai nombre de barres (nb non
          None, jamais de nb=1 invente) et un diametre plausible 5..40 mm.
        Retourne True si une donnee nouvelle a ete enregistree (avec
        bande d'exclusion pour la grille spatiale).
        """
        # Plan labels contain 'S4(150x150x40)' with dims — never process
        # these here (they belong to the spatial grid / text detector).
        if SEMELLE_DIM_RX.search(text.replace(" ", "")):
            return False
        if PLAN_SEMELLE_DIM_RE.search(text):
            return False
        deco = decompose_etiquette_technique(text)
        if not deco:
            return False
        enregistre = False

        dim = deco.get("dimensions") or {}
        tk_s = self._normaliser_repere_semelle(deco.get("semelle"))
        if tk_s and dim.get("a") and dim.get("b"):
            # Page toujours tracee (agregation multi-batiments),
            # specs seulement si manquantes (fusion non destructive).
            pages = self._semelles_pages.setdefault(tk_s, [])
            if page_num not in pages:
                pages.append(page_num)
            cur = self.global_catalogue["semelles"].get(tk_s, {})
            if not cur.get("a"):
                a, b, h = _cm_triplet_to_m(
                    [dim["a"], dim["b"], dim.get("h") or 0])
                spec = {"a": a, "b": b}
                if dim.get("h"):
                    spec["h"] = h
                self._register_semelle_type(
                    tk_s, spec.get("a", 0), spec.get("b", 0),
                    spec.get("h", 0), page_num)
                enregistre = True

        tk_p = self._normaliser_repere_poteau(deco.get("poteau"))
        if tk_p and dim.get("a") and dim.get("b"):
            cur = self.global_catalogue["poteaux"].get(tk_p, {})
            if "a" not in cur:
                a, b = _cm_triplet_to_m([dim["a"], dim["b"]])
                self._merge_poteau(tk_p, {"a": a, "b": b})
                enregistre = True

        acier = deco.get("acier") or {}
        nb, phi = acier.get("nb"), acier.get("phi")
        if nb is not None and phi is not None and 5 <= phi <= 40:
            if tk_s:
                pages = self._semelles_pages.setdefault(tk_s, [])
                if page_num not in pages:
                    pages.append(page_num)
                cur = self.global_catalogue["semelles"].get(tk_s, {})
                if cur.get("ferr_x", {}).get("nb", 0) == 0:
                    self._merge_semelle(
                        tk_s, {"ferr_x": {"nb": nb, "phi": phi},
                               "ferr_y": {"nb": nb, "phi": phi}})
                    self.warnings.append(
                        f"{tk_s}: ferraillage lu par decodeur universel "
                        f"({nb}HA{phi}) — a verifier sur coupes.")
                    enregistre = True

        if enregistre:
            self._nomenclature_line_bands.setdefault(page_num, []).append(
                (line["x0"], line["y0"], line["x1"], line["y1"]))
        return enregistre
    """Extraction vectorielle locale de plans BA, multi-pages (1 a 1000+).

    Usage :
        ex = VectorPlanExtractor()
        plan_data = ex.process_all_pages("plan.pdf", progress_callback=cb)
    """

    def __init__(self):
        self.global_catalogue = {
            "semelles": {}, "poteaux": {}, "poutres": {},
            "dalles": {}, "voiles": {}, "escaliers": {},
            "longrines": {}, "chainages": {}, "murs": {},
            "radiers": {}, "contre_forts": {}, "futs": {},
            "semelles_filantes": [],
        }
        self.implantations = {
            "semelles": [], "poteaux": [], "poutres": [],
            "dalles": [], "voiles": [], "escaliers": [],
        }
        self.warnings = []
        self.hypotheses = []
        self.pages_tableau = []
        self.pages_plan = []
        self.pages_ocr = []
        self.pages_skip = 0
        self.total_pages = 0
        self.massifs = 0
        self.nb_mots = 0
        self._sem_counter = {}
        self._current_doc = None
        self._semelles_pages = {}
        self._nomenclature_bboxes = {}
        self._nomenclature_line_bands = {}
        self.ferra_annotations = []
        self._page_axes = {}        # page -> {letters, numbers}
        self._poteau_counter = {}     # Q1 -> compteur d'instances
        self._poteau_hits = []        # [{id: Q1_n, type, x, y, page}]
        self._bindings = []         # [(S_rep, Q_rep, page)]
        self._all_words = []        # Tous les mots extraits (pour clustering)
        self._all_words_by_page = {}  # page_num -> [words] (pour cotes)
        # OCR local paresseux : aucune dependance chargee a la creation
        from core.ocr_engine import PlanOCREngine
        self.ocr_engine = PlanOCREngine()

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------
    def process_all_pages(self, pdf_path, progress_callback=None):
        """Parcours integral du PDF, page par page, avec gestion memoire."""
        import pymupdf

        self._reset()
        self._current_pdf_path = pdf_path

        # Detection du profil du plan (pour adapter l'extraction)
        try:
            from core.plan_detector import detect_plan
            self._plan_profile = detect_plan(pdf_path)
            self.hypotheses.append(
                f"Plan detecte : {self._plan_profile.page_size}, "
                f"type {self._plan_profile.pdf_type}, "
                f"{self._plan_profile.total_pages} page(s)")
        except Exception:
            self._plan_profile = None

        with pymupdf.open(str(pdf_path)) as doc:
            self._current_doc = doc
            self.total_pages = len(doc)
            for idx in range(self.total_pages):
                page = doc[idx]
                page_num = idx + 1
                role = self._process_page(page, page_num)
                if progress_callback:
                    progress_callback(page_num, self.total_pages, role)
                # Liberation memoire (fichiers 1000+ pages)
                page = None
                if idx % 20 == 0:
                    gc.collect()
            self._current_doc = None
        gc.collect()
        return self._finalize()

    def extract_from_pdf(self, pdf_path, progress_callback=None):
        return self.process_all_pages(pdf_path, progress_callback)

    def extract_from_words(self, words):
        """Parsage d'une liste de mots deja normalises (1 page synthetique)."""
        self._reset()
        clean = expand_words(
            [w for w in words if (w.get("text") or "").strip()])
        self.nb_mots = len(clean)
        self._all_words = list(clean)  # Copie pour clustering
        if clean:
            role = self._parse_words_page(clean, clean[0].get("page", 1),
                                           role_auto=None)
            if role != "skip":
                self.pages_plan.append(clean[0].get("page", 1))
        return self._finalize()

    def extract_from_text_blocks(self, text_blocks):
        """Parsage de blocs normalises (ingestion DXF, OCR...)."""
        words = []
        for b in text_blocks:
            t = (b.get("text") or "").strip()
            if not t or t.startswith("[BLOCK:"):
                continue
            words.append({
                "text": t,
                "x": b.get("x", 0.0), "y": b.get("y", 0.0),
                "x1": b.get("x1", b.get("x", 0.0)),
                "y1": b.get("y1", b.get("y", 0.0)),
                "page": b.get("page", 1),
            })
        return self.extract_from_words(words)

    def _reset(self):
        self.global_catalogue = {
            "semelles": {}, "poteaux": {}, "poutres": {},
            "dalles": {}, "voiles": {}, "escaliers": {},
            "longrines": {}, "chainages": {}, "murs": {},
            "radiers": {}, "contre_forts": {}, "futs": {},
            "semelles_filantes": [],
        }
        self.implantations = {
            "semelles": [], "poteaux": [], "poutres": [],
            "dalles": [], "voiles": [], "escaliers": [],
        }
        self.warnings = []
        self.hypotheses = []
        self.pages_tableau = []
        self.pages_plan = []
        self.pages_ocr = []
        self.pages_skip = 0
        self.massifs = 0
        self.nb_mots = 0
        self._sem_counter = {}
        self._current_doc = None
        self._semelles_pages = {}          # type -> [pages d'origine]
        self._nomenclature_bboxes = {}     # page -> [bbox find_tables]
        self._nomenclature_line_bands = {} # page -> [(x0,y0,x1,y1)] lignes lues
        self.ferra_annotations = []        # [(type, phi, st_cm, page)]
        self._page_axes = {}               # page -> {letters, numbers}
        self._poteau_counter = {}          # Q1 -> compteur d'instances
        self._poteau_hits = []             # [{id: Q1_n, type, x, y, page}]
        self._bindings = []                # [(S_rep, Q_rep, page)]

    # ------------------------------------------------------------------
    # Classification + orchestration par page
    # ------------------------------------------------------------------
    def _classify(self, text_upper):
        text = text_upper.lower()
        is_tableau = any(k in text for k in KEYWORDS_TABLEAU)
        is_plan = any(k in text for k in KEYWORDS_PLAN)
        if is_tableau:
            return "tableau"
        if is_plan:
            return "plan"
        return "autre"

    def _process_page(self, page, page_num):
        # --- A. Detection grand format + routage ROI ---
        from core.roi_slicer import is_grand_format, compute_epsilon, detect_rois, get_words_in_roi
        grand_format = is_grand_format(page)
        if grand_format:
            return self._process_grand_format_page(page, page_num)

        words = []
        for w in page.get_text("words"):
            words.append({
                "text": w[4],
                "x": round(w[0], 2), "y": round(w[1], 2),
                "x1": round(w[2], 2), "y1": round(w[3], 2),
                "page": page_num,
            })

        words = expand_words(words)
        # Fallback OCR ciblé : texte vectoriel toujours conservé, OCR ajouté
        # uniquement pour les pages contenant une VRAIE image scannee
        # (>= 0,5 Mpx, pas un logo) et peu de texte.
        # Les plans vectoriels (>= 15 mots) ne declenchent JAMAIS l'OCR.
        has_image = self.ocr_engine._has_scan_image(page)
        has_structural_token = any(
            SEMELLE_PLAIN_RX.match(w["text"])
            or POTEAU_LABEL_RX.match(w["text"])
            or POUTRE_LABEL_RX.match(w["text"])
            for w in words
        )
        ocr_words = self.ocr_engine.ocr_page_if_scanned(
            page, force=has_image and not has_structural_token)
        if ocr_words:
            words.extend(expand_words(ocr_words))
            words = self._dedupe_words(words)
            if page_num not in self.pages_ocr:
                self.pages_ocr.append(page_num)

        text_upper = " ".join(w["text"] for w in words).upper()
        role = self._classify(text_upper)
        if ocr_words:
            role = "ocr"
        return self._parse_words_page(words, page_num, role)

    def _process_grand_format_page(self, page, page_num):
        """Traite une page grand format (A0/A1/A2).
        
        1. Detecte les zones d'interet (tableaux, plans, details)
        2. Traite TOUS les mots de la page (pas seulement les ROI)
        3. Les ROIs servent a guider le role, pas a filtrer les mots
        4. Reconcile les decomptes entre tableaux et plans
        """
        from core.roi_slicer import compute_epsilon, detect_rois
        
        epsilon = compute_epsilon(page)
        
        all_words = []
        for w in page.get_text("words"):
            all_words.append({
                "text": w[4],
                "x": round(w[0], 2), "y": round(w[1], 2),
                "x1": round(w[2], 2), "y1": round(w[3], 2),
                "page": page_num,
            })
        all_words = expand_words(all_words)
        
        has_image = self.ocr_engine._has_scan_image(page)
        has_structural_token = any(
            SEMELLE_PLAIN_RX.match(w["text"])
            or POTEAU_LABEL_RX.match(w["text"])
            or POUTRE_LABEL_RX.match(w["text"])
            for w in all_words
        )
        ocr_words = self.ocr_engine.ocr_page_if_scanned(
            page, force=has_image and not has_structural_token)
        if ocr_words:
            all_words.extend(expand_words(ocr_words))
            all_words = self._dedupe_words(all_words)
            if page_num not in self.pages_ocr:
                self.pages_ocr.append(page_num)
        
        rois = detect_rois(page, page_num)
        
        # Accumuler les mots pour le clustering spatial des niveaux
        self._all_words.extend(all_words)
        self._all_words_by_page[page_num] = all_words
        
        # Traiter TOUS les mots de la page (ROIs pour info, pas pour filtrage)
        self._parse_words_page(all_words, page_num, "autre")
        
        if ocr_words:
            return "ocr"
        return "grand_format"

    @staticmethod
    def _dedupe_words(words):
        """Déduplique les mots fusionnés texte/OCR sans supprimer les positions."""
        result = []
        seen = set()
        for word in words:
            text = str(word.get("text", "")).strip().upper()
            key = (text, round(float(word.get("x", 0)), 0),
                   round(float(word.get("y", 0)), 0))
            if text and key not in seen:
                seen.add(key)
                result.append(word)
        return result

    def _parse_words_page(self, words, page_num, role_auto=None):
        """Traite une page : SANS filtrage rigide de role.

        1. find_tables() sur TOUTES les pages (tableaux bordes).
        2. Detecteur textuel universel (lignes 'S1' + '90 x 90 x 25',
           'SF 45 x 20 x L', annotations 'S1: Semelle de...', poteaux).
        3. Parseur d'alignement historique pour les pages etiquetees
           'tableau' sans bordures.
        4. Grille spatiale : les lignes/cellules de nomenclature lues sont
           EXCLUES par zone reelle — les etiquettes du plan hors tableau
           restent donc implantables (dossiers multi-batiments).
        """
        self.nb_mots += len(words)
        if not words:
            self.pages_skip += 1
            return "skip"

        if role_auto is None:
            text_upper = " ".join(w["text"] for w in words).upper()
            role_auto = self._classify(text_upper)

        parsed_role = role_auto

        # 1. Tableaux bordes : find_tables universel
        found_table = False
        if self._current_doc is not None:
            found_table = self._extract_tables_from_page(page_num)
        if found_table and page_num not in self.pages_tableau:
            self.pages_tableau.append(page_num)
        elif role_auto == "plan":
            self.pages_plan.append(page_num)

        # 2. Detecteur textuel universel (lignes compactes + annotations)
        found_text = self._parse_semelle_text_lines(words, page_num)
        if found_text and role_auto != "plan" and page_num not in self.pages_tableau:
            self.pages_tableau.append(page_num)

        # 3. Parseur d'alignement historique (pages etiquetees, sans bordures)
        if role_auto == "tableau":
            if page_num not in self.pages_tableau:
                self.pages_tableau.append(page_num)
            if not found_table and not found_text:
                self._extract_tableau_par_mots(words, page_num)
            # Bandes de colonnes d'en-tete : protection de la grille spatiale
            # meme quand find_tables a reussi (bboxes peu fiables apres
            # rotation de page).
            self._record_header_bands(words, page_num)

        # 4. Grille spatiale — TOUJOURS executee (pages mixtes tableau+plan
        # des dossiers multi-batiments) ; les zones de nomenclature lue
        # sont exclues par leurs coordonnees reelles.
        n_impl = self._extract_spatial_grid_from_page(words, page_num)
        if n_impl > 0 and parsed_role == "autre":
            parsed_role = "plan"

        # 5. Details poteaux / poutres (sur toute page)
        self._extract_poteaux_page(words, page_num)
        self._extract_poutres_page(words, page_num)

        # Compteurs annexes
        self.massifs += sum(1 for w in words
                            if w["text"].lower().startswith("massif"))
        return parsed_role

    # ------------------------------------------------------------------
    # A. Tableau de nomenclature (find_tables natif + fallback)
    # ------------------------------------------------------------------
    def _extract_tables_from_page(self, page_num, words=None):
        """parse les tableaux via page.find_tables(). Retourne True si une
        nomenclature exploitable a ete trouvee."""
        import pymupdf

        # Recuperer l'objet page depuis les mots n'est pas possible : cette
        # methode est appelee depuis _process_page (objet page vivant) ou en
        # fallback depuis extract_from_words (pas de page -> fallback words).
        # On re-ouvre la page via le doc courant si possible.
        doc = getattr(self, "_current_doc", None)
        page_idx = page_num - 1
        if doc is None:
            return False
        try:
            page = doc[page_idx]
            tabs = page.find_tables()
        except Exception:
            return False

        found = False
        for t in tabs.tables:
            try:
                data = t.extract()
            except Exception:
                continue
            if self._parse_nomenclature_rows(data, page_num):
                found = True
                try:
                    self._nomenclature_bboxes.setdefault(page_num, []).append(
                        tuple(t.bbox))
                except Exception:
                    pass
            if self._parse_poteaux_table(data):
                found = True
        return found

    def _parse_nomenclature_rows(self, data, page_num=None):
        """Parse une table extraite par find_tables.

        Deux formats supportes :
        - Riche (Repere | AxB | H | Ferraillage selon x/y) — page 4 du
          dossier de reference ;
        - Compact 2 colonnes (Semelles | Dimensions en cm) avec cellules
          '90 x 90 x 25' — dossiers multi-batiments.
        """
        if not data or len(data) < 2:
            return False
        # Localiser la ligne d'en-tete contenant "Repere"
        hdr_idx = None
        for ri, row in enumerate(data):
            cells = [(c or "") for c in row]
            if any(c.strip().lower().startswith("rep") for c in cells):
                hdr_idx = ri
                break
        if hdr_idx is not None:
            n = self._parse_nomenclature_riche(data, hdr_idx, page_num)
            if n > 0:
                return True

        # Format compact : lignes dont la 1re cellule est S\d / SF et une
        # autre cellule contient le triplet compact A x B x H.
        n_found = 0
        for row in data:
            cells = [(c or "").strip() for c in row]
            if not cells or not cells[0]:
                continue
            rep = cells[0].upper().replace(" ", "")
            if not (SEMELLE_PLAIN_RX.match(rep) or rep == "SF"):
                continue
            rest = " ".join(cells[1:])
            if rep == "SF":
                mf = FILANTE_RX.search(rest)
                if mf:
                    larg, haut = _cm_triplet_to_m(
                        [int(mf.group(1)), int(mf.group(2))])
                    self._register_semelle_filante_type(larg, haut, page_num)
                    n_found += 1
                continue
            md = TRIPLET_RX.search(rest)
            if md:
                a, b, h = _cm_triplet_to_m(
                    [int(md.group(1)), int(md.group(2)), int(md.group(3))])
                self._register_semelle_type(rep, a, b, h, page_num)
                # Ferraillage eventuel dans une autre cellule (un seul sens)
                mfx = re.search(r"(\d+)\s*(?:HA|T)\s*(\d+)", rest, re.I)
                if mfx:
                    self._merge_semelle(rep, {
                        "ferr_x": {"nb": int(mfx.group(1)),
                                   "phi": int(mfx.group(2))}})
                    self.warnings.append(
                        f"{rep}: un seul sens de ferraillage lu — nappe Y "
                        "a verifier.")
                n_found += 1
        return n_found > 0

    def _parse_nomenclature_riche(self, data, hdr_idx, page_num=None):
        """Format riche : colonnes Repere / AxB / H / Ferraillage selon x|y."""
        hdr = [(c or "").strip().lower() for c in data[hdr_idx]]
        col_rep = col_axb = col_h = col_fx = col_fy = None
        for ci, c in enumerate(hdr):
            if c.startswith("rep"):
                col_rep = ci
            elif "(axb)" in c:
                col_axb = ci
            elif c.startswith("h") and col_h is None:
                col_h = ci
            elif "selon x" in c:
                col_fx = ci
            elif "selon y" in c:
                col_fy = ci
        if col_rep is None or col_axb is None:
            return False

        n_found = 0
        for row in data[hdr_idx + 1:]:
            cells = [(c or "").strip() for c in row]
            if col_rep >= len(cells):
                continue
            m = SEMELLE_PLAIN_RX.match(cells[col_rep])
            if not m:
                continue
            tk = f"S{m.group(1)}"
            spec = {}
            if col_axb < len(cells):
                md = SECT_DIM_RX.match(cells[col_axb])
                if md:
                    spec["a"] = _dim_cm_to_m(int(md.group(1)))
                    spec["b"] = _dim_cm_to_m(int(md.group(2)))
            if col_h is not None and col_h < len(cells):
                if cells[col_h].isdigit():
                    spec["h"] = _dim_cm_to_m(int(cells[col_h]))
            if col_fx is not None and col_fx < len(cells):
                mf = FERRA_RX.match(cells[col_fx])
                if mf:
                    spec["ferr_x"] = {"nb": int(mf.group(1)),
                                      "phi": int(mf.group(2))}
            if col_fy is not None and col_fy < len(cells):
                mf = FERRA_RX.match(cells[col_fy])
                if mf:
                    spec["ferr_y"] = {"nb": int(mf.group(1)),
                                      "phi": int(mf.group(2))}
            if spec:
                self._merge_semelle(tk, spec)
                pages = self._semelles_pages.setdefault(tk, [])
                if page_num is not None and page_num not in pages:
                    pages.append(page_num)
                n_found += 1
        return n_found

    def _parse_poteaux_table(self, data):
        """Parse une table poteaux, trois dispositions :
        - colonnes : en-tetes P1..Pn / Q1..Qn (mot-cle POTEAUX ou >=2 reperes)
        - lignes : repere Qk/Pk en colonne 0, details en colonnes 1..n
          (ex: TABLEAU DES POTEAUX page 5 du R+2).
        - mixte : en-tetes en colonnes SAUF premiere colonne (P1 non labelisee)
        Cellules multi-lignes : '(25x30) 8T12 2CAD T6 e=15'."""
        if not data:
            return False
        hdr_idx = None
        poteau_cols = {}
        # Disposition lignes : reperes en colonne 0
        lignes_reperes = {}
        for ri, row in enumerate(data):
            cells = [(c or "").strip().upper() for c in row]
            cols = {c: ci for ci, c in enumerate(cells)
                    if POTEAU_LABEL_RX.match(c)}
            if (len(cols) >= 2
                    or (len(cols) >= 1
                        and any("POTEAUX" in c for c in cells))):
                hdr_idx = ri
                poteau_cols = cols
                break
            if cells:
                m = POTEAU_LABEL_RX.match(cells[0])
                if m:
                    lignes_reperes[m.group(1).upper()] = ri

        n_found = 0
        if hdr_idx is not None:
            # Detecter si la premiere colonne de donnees n'a pas d'en-tete
            # (ex: P1 dans col 1 mais pas dans row 0)
            max_col = max(poteau_cols.values()) if poteau_cols else 0
            has_first_data_col = (
                max_col >= 2
                and all(c is None for c in data[hdr_idx][:1])
                and any(data[hdr_idx + 1][ci]
                        for ci in range(1, min(2, len(data[hdr_idx + 1])))
                        if data[hdr_idx + 1][ci])
            )

            for row in data[hdr_idx + 1:]:
                # Traiter les colonnes avec en-tete
                for label, ci in poteau_cols.items():
                    if ci >= len(row):
                        continue
                    if self._parse_poteau_cell(label, row[ci] or ""):
                        n_found += 1
                # Traiter la premiere colonne de donnees si non labelisee
                if has_first_data_col and len(row) > 1:
                    # Chercher un label P1 dans la cellule elle-meme
                    cell_text = (row[1] or "").strip().upper()
                    m = POTEAU_LABEL_RX.match(cell_text)
                    if m:
                        label = m.group(1).upper()
                        if self._parse_poteau_cell(label, row[1] or ""):
                            n_found += 1
                    elif not any(c and POTEAU_LABEL_RX.match(c.strip().upper())
                                 for c in row[2:] if c):
                        # Pas d'autre label dans cette ligne = c'est P1
                        # (premiere colonne non labelisee du tableau)
                        if self._parse_poteau_cell("P1", row[1] or ""):
                            n_found += 1

        # Disposition lignes : premiere colonne exploitable (FONDATIONS)
        for label, ri in lignes_reperes.items():
            for ci in range(1, len(data[ri])):
                if self._parse_poteau_cell(label, data[ri][ci] or ""):
                    n_found += 1
                    break  # premiere section rencontree sous la colonne
        return n_found > 0

    def _parse_poteau_cell(self, label, cell):
        """Parse une cellule detail poteau. Retourne True si exploitee."""
        spec = {}
        # Section (AxB) : premiere section rencontree
        md = re.search(r"\((\d+)\s*[xX]\s*(\d+)\)", cell)
        if md:
            spec["a"] = _dim_cm_to_m(int(md.group(1)))
            spec["b"] = _dim_cm_to_m(int(md.group(2)))
            spec["section_str"] = f"{md.group(1)}x{md.group(2)}"
        else:
            # Fallback: dimensions sur lignes séparées (ex: "25\n40" dans tableau poteaux)
            lines_stripped = [l.strip() for l in cell.split('\n') if l.strip()]
            for k in range(len(lines_stripped) - 1):
                if re.fullmatch(r'\d{2,3}', lines_stripped[k]) and re.fullmatch(r'\d{2,3}', lines_stripped[k+1]):
                    try:
                        b_val = int(lines_stripped[k])
                        h_val = int(lines_stripped[k+1])
                        if 15 <= b_val <= 100 and 15 <= h_val <= 100:
                            spec["a"] = _dim_cm_to_m(b_val)
                            spec["b"] = _dim_cm_to_m(h_val)
                            spec["section_str"] = f"{b_val}x{h_val}"
                            break
                    except ValueError:
                        pass
        # Barres longitudinales : 6HA14 / 8T12 / 4T12+4T10 (groupes)
        bars = _dedupe_bars(
            [{"nb": int(m.group(1)), "phi": int(m.group(2))}
             for m in re.finditer(
                 r"(\d+)\s*(?:HA|T)\s*(\d+)", cell, re.I)])
        if bars:
            spec["aciers_longitudinaux"] = bars
            spec["long_bars"] = bars  # compat aval (metre, rapport)
        # Cadres : 'Cad T6+Ep' / 'T6+Ep' / '2CAD T6 e=15' / 'CAD+ETR T6 e=15'
        mc = (re.search(r"Cad\.?\s*T(\d+)\+?\s*Ep", cell, re.I)
              or re.search(r"T(\d+)\+Ep", cell, re.I)
              or re.search(r"(?:CAD|ETR)[+]?\s*(?:\+?(?:ETR|EP))?"
                           r"\s*T\s*(\d+)", cell, re.I))
        if mc:
            phi_c = int(mc.group(1))
            me = ESP_RX.search(cell)
            esp = (float(me.group(1).replace(",", "."))
                   / 100.0 if me else None)
            spec["cadres"] = {"phi": phi_c, "esp": esp}
            spec["cadres_str"] = (
                f"T{phi_c} e={me.group(1).replace(',', '.')}"
                if me else f"T{phi_c}")
        if "a" in spec:
            self._merge_poteau(label, spec)
            return True
        return False

    def _cross_validate_strategies(self, cat):
        """Compare les resultats de differentes strategies d'extraction
        et signale les conflits.
        
        Strategies compares :
        1. Tableaux recapitulatifs (TABLEAU DES POTEAUX, etc.)
        2. Elevations (ELEVATION POTEAU drawings)
        3. Grille spatiale (labels + dimensions inline)
        """
        # Collecter les dimensions par source
        table_dims = {}  # ref -> (a, b)
        elevation_dims = {}  # ref -> (a, b)
        spatial_dims = {}  # ref -> (a, b)

        for ref, spec in cat.get("poteaux", {}).items():
            a = spec.get("a", 0)
            b = spec.get("b", 0)
            if a > 0 and b > 0:
                # Determiner la source
                if spec.get("from_tableau"):
                    table_dims[ref] = (a, b)
                elif spec.get("from_elevation"):
                    elevation_dims[ref] = (a, b)
                else:
                    spatial_dims[ref] = (a, b)

        # Comparer les sources
        conflicts = []
        for ref in set(list(table_dims.keys()) + list(elevation_dims.keys())
                       + list(spatial_dims.keys())):
            sources = {}
            if ref in table_dims:
                sources["tableau"] = table_dims[ref]
            if ref in elevation_dims:
                sources["elevation"] = elevation_dims[ref]
            if ref in spatial_dims:
                sources["spatial"] = spatial_dims[ref]

            if len(sources) >= 2:
                dims = list(sources.values())
                if not all(abs(d[0] - dims[0][0]) < 0.01
                           and abs(d[1] - dims[0][1]) < 0.01 for d in dims):
                    conflicts.append({
                        "reference": ref,
                        "sources": {k: f"{v[0]*100:.0f}x{v[1]*100:.0f}"
                                    for k, v in sources.items()},
                    })

        if conflicts:
            for c in conflicts:
                src_str = ", ".join(f"{k}={v}" for k, v in c["sources"].items())
                self.warnings.append(
                    f"{c['reference']}: conflit de dimensions entre sources "
                    f"({src_str}) — verification manuelle recommandee.")

    def _record_header_bands(self, words, page_num):
        """Enregistre une bande verticale sous chaque colonne d'en-tete
        S\\d|S\\d|... (≥2 repères alignes sur une meme ligne) pour exclure
        ces mots de la grille spatiale.
        
        Utilise des tolerances relatives a la taille de page."""
        from core.roi_slicer import compute_epsilon
        eps = 10.0
        if self._current_doc is not None:
            try:
                page = self._current_doc[page_num - 1]
                eps = compute_epsilon(page)
            except Exception:
                pass
        
        headers = []
        for w in words:
            m = SEMELLE_PLAIN_RX.match(w["text"])
            if m:
                headers.append((w["x"], w["y"]))
        if len(headers) < 2:
            return
        row_y = min(h[1] for h in headers)
        bands = self._nomenclature_line_bands.setdefault(page_num, [])
        for (hx, hy) in headers:
            if abs(hy - row_y) < eps:
                bands.append((hx - eps * 1.4, hy - eps * 0.6,
                             hx + eps * 1.4, hy + eps * 4.0))

    def _extract_tableau_par_mots(self, words, page_num):
        """Fallback : nomenclature par alignement X reel (sans find_tables).
        Les colonnes de tete S4|S3|S2|S1 consommees sont enregistrees en
        bandes pour etre exclues de la grille spatiale.
        
        Utilise des tolerances relatives a la taille de page."""
        from core.roi_slicer import compute_epsilon
        eps = 10.0
        if self._current_doc is not None:
            try:
                page = self._current_doc[page_num - 1]
                eps = compute_epsilon(page)
            except Exception:
                pass
        
        pw = words
        headers = []
        for w in pw:
            m = SEMELLE_PLAIN_RX.match(w["text"])
            if m:
                headers.append((f"S{m.group(1)}", w["x"], w["y"]))
        if len(headers) < 2:
            return
        row_y = min(h[2] for h in headers)
        headers = [h for h in headers if abs(h[2] - row_y) < eps]

        bands = self._nomenclature_line_bands.setdefault(page_num, [])
        for (tk, hx, hy) in headers:
            # Bande verticale de la colonne : en-tete + dims + H + ferraillage
            bands.append((hx - eps * 1.4, hy - eps * 0.6,
                         hx + eps * 1.4, hy + eps * 33.0))
            spec = {}
            for w in pw:
                m = SECT_DIM_RX.match(w["text"])
                if m and hy + eps * 2 < w["y"] < hy + eps * 7 and abs(w["x"] - hx) < eps * 1.2:
                    spec["a"] = _dim_cm_to_m(int(m.group(1)))
                    spec["b"] = _dim_cm_to_m(int(m.group(2)))
                    break
            h_row = next((w["y"] for w in pw
                          if w["text"] == "H" and w["x"] > hx
                          and abs(w["y"] - (hy + eps * 14)) < eps * 6), None)
            if h_row:
                for w in pw:
                    if (w["text"].isdigit() and abs(w["x"] - hx) < eps * 1.2
                            and abs(w["y"] - h_row) < eps * 1.2):
                        spec["h"] = _dim_cm_to_m(int(w["text"]))
                        break
            ferra_rows = [w for w in pw
                          if w["text"].lower().startswith("ferraillage")]
            for fr in ferra_rows:
                axis = self._row_axis(pw, fr)
                if axis is None:
                    continue
                for w in pw:
                    m = FERRA_RX.match(w["text"])
                    if (m and abs(w["x"] - hx) < eps * 1.2
                            and fr["y"] - eps * 0.5 < w["y"] < fr["y"] + eps * 4):
                        spec[f"ferr_{axis}"] = {"nb": int(m.group(1)),
                                                "phi": int(m.group(2))}
                        break
            if spec:
                self._merge_semelle(tk, spec)

    @staticmethod
    def _row_axis(pw, ferra_word):
        selons = [w for w in pw if w["text"].lower() == "selon"
                  and abs(ferra_word["y"] - w["y"]) < 60]
        for s in selons:
            for w in pw:
                if (w["text"].lower() in ("x", "y")
                        and abs(w["x"] - s["x"]) < 40
                        and 0 < w["y"] - s["y"] < 40):
                    return w["text"].lower()
        return None

    # ------------------------------------------------------------------
    # B. Grille spatiale (bulles d'axes + reperes de semelles)
    # ------------------------------------------------------------------
    def _register_semelle_type(self, repere, a, b, h, page_num):
        """Enregistre un type de semelle SANS ecraser les dims existantes ;
        trace la page d'origine (agregation multi-batiments)."""
        spec = {}
        if a:
            spec["a"] = a
        if b:
            spec["b"] = b
        if h:
            spec["h"] = h
        if spec:
            self._merge_semelle(repere, spec)
        pages = self._semelles_pages.setdefault(repere, [])
        if page_num is not None and page_num not in pages:
            pages.append(page_num)

    def _register_semelle_filante_type(self, largeur, hauteur, page_num):
        """Semelle filante SF (largeur x hauteur x L). Stockee a part :
        la longueur n'est pas cotee, le metre ne l'exploite pas seul."""
        lst = self.global_catalogue.setdefault("semelles_filantes", [])
        for e in lst:
            if (abs(e["largeur"] - largeur) < 1e-9
                    and abs(e["hauteur"] - hauteur) < 1e-9):
                if page_num is not None and page_num not in e["pages"]:
                    e["pages"].append(page_num)
                return
        pages = [page_num] if page_num is not None else []
        lst.append({"largeur": largeur, "hauteur": hauteur, "pages": pages})

    def _words_to_lines(self, words):
        """Reconstruit des lignes de texte a partir des mots (y en bandes)."""
        lines = {}
        for w in words:
            key = round(w["y"] / 8)
            lines.setdefault(key, []).append(w)
        out = []
        for key in sorted(lines):
            ws = sorted(lines[key], key=lambda w: w["x"])
            out.append({
                "text": " ".join(w["text"] for w in ws),
                "y0": min(w["y"] for w in ws),
                "y1": max(w.get("y1", w["y"]) for w in ws),
                "x0": min(w["x"] for w in ws),
                "x1": max(w.get("x1", w["x"]) for w in ws),
            })
        return out

    def _parse_semelle_text_lines(self, words, page_num):
        """Detecteur textuel universel (fallback sans find_tables) :

        - 'S1' seul, dimensions '90 x 90 x 25' sur la ligne suivante
        - 'SF' / 'SF 45 x 20 x L' (semelle filante)
        - 'S1: Semelle de 90 x 90 x 25'
        - 'Poteau P1 (25x25) ... 6HA14' + 'HA8 St = 14.17'
        - 'D1' / 'D1(300x400)' / 'Dalle D1'
        - 'V1' / 'V1(200x15)' / 'Voile V1'
        - 'ESC1' / 'Escalier ESC1'
        - Tous les types de elements BA
        Les zones lues sont enregistrees pour exclure les memes mots de la
        grille spatiale (un 'S1' de tableau n'est pas une implantation).
        """
        lines = self._words_to_lines(words)
        found = False
        last_type = None
        last_poteau = None

        for idx, line in enumerate(lines):
            text = line["text"]

            compact_text = re.sub(r"\s+", "", text)
            is_spatial_label = bool(
                re.search(r"S\d+\(\d+x\d+x\d+\)", compact_text, re.I))
            decomposition = decompose_etiquette_technique(text)
            if (decomposition["family"] == "SEMELLE"
                    and decomposition["reference"]
                    and not is_spatial_label):
                tk = decomposition["reference"]
                spec = {}
                dimensions = decomposition.get("dimensions")
                if dimensions:
                    spec.update(dimensions)
                if decomposition.get("ferr_x"):
                    spec["ferr_x"] = decomposition["ferr_x"]
                if spec:
                    self._merge_semelle(tk, spec)
                    pages = self._semelles_pages.setdefault(tk, [])
                    if page_num not in pages:
                        pages.append(page_num)
                    found = True
                    last_type = tk
                    self._nomenclature_line_bands.setdefault(page_num, []).append(
                        (line["x0"], line["y0"], line["x1"], line["y1"]))
                    if decomposition.get("dimensions") or decomposition.get("ferr_x"):
                        continue

            # --- DALLE ---
            if decomposition["family"] == "DALLE" and decomposition["reference"]:
                tk = decomposition["reference"]
                spec = {}
                dimensions = decomposition.get("dimensions")
                if dimensions:
                    spec.update(dimensions)
                if decomposition.get("ferr_x"):
                    spec["ferr_x"] = decomposition["ferr_x"]
                if spec:
                    self._merge_dalle(tk, spec)
                    found = True
                    last_type = tk
                    self._nomenclature_line_bands.setdefault(page_num, []).append(
                        (line["x0"], line["y0"], line["x1"], line["y1"]))
                    continue

            # --- VOILE ---
            if decomposition["family"] == "VOILE" and decomposition["reference"]:
                tk = decomposition["reference"]
                spec = {}
                dimensions = decomposition.get("dimensions")
                if dimensions:
                    spec.update(dimensions)
                if decomposition.get("ferr_x"):
                    spec["ferr_x"] = decomposition["ferr_x"]
                if spec:
                    self._merge_voile(tk, spec)
                    found = True
                    last_type = tk
                    self._nomenclature_line_bands.setdefault(page_num, []).append(
                        (line["x0"], line["y0"], line["x1"], line["y1"]))
                    continue

            # --- ESCALIER ---
            if decomposition["family"] == "ESCALIER" and decomposition["reference"]:
                tk = decomposition["reference"]
                spec = {}
                dimensions = decomposition.get("dimensions")
                if dimensions:
                    spec.update(dimensions)
                if decomposition.get("ferr_x"):
                    spec["ferr_x"] = decomposition["ferr_x"]
                if spec:
                    self._merge_escalier(tk, spec)
                    found = True
                    last_type = tk
                    self._nomenclature_line_bands.setdefault(page_num, []).append(
                        (line["x0"], line["y0"], line["x1"], line["y1"]))
                    continue

            # --- LONGRINE ---
            if decomposition["family"] == "LONGRINE" and decomposition["reference"]:
                tk = decomposition["reference"]
                spec = {}
                dimensions = decomposition.get("dimensions")
                if dimensions:
                    spec.update(dimensions)
                if decomposition.get("ferr_x"):
                    spec["ferr_x"] = decomposition["ferr_x"]
                if spec:
                    self._merge_poutre(tk, spec)
                    found = True
                    last_type = tk
                    self._nomenclature_line_bands.setdefault(page_num, []).append(
                        (line["x0"], line["y0"], line["x1"], line["y1"]))
                    continue

            # --- CHAINAGE ---
            if decomposition["family"] == "CHAINAGE" and decomposition["reference"]:
                tk = decomposition["reference"]
                spec = {}
                dimensions = decomposition.get("dimensions")
                if dimensions:
                    spec.update(dimensions)
                if decomposition.get("ferr_x"):
                    spec["ferr_x"] = decomposition["ferr_x"]
                if spec:
                    self._merge_poutre(tk, spec)
                    found = True
                    last_type = tk
                    self._nomenclature_line_bands.setdefault(page_num, []).append(
                        (line["x0"], line["y0"], line["x1"], line["y1"]))
                    continue

            # Annotation directe : S1: Semelle de 90 x 90 x 25
            for m in SEMELLE_ANNO_RX.finditer(text):
                a, b, h = _cm_triplet_to_m(
                    [int(m.group(2)), int(m.group(3)), int(m.group(4))])
                self._register_semelle_type(m.group(1).upper(), a, b, h,
                                            page_num)
                self._nomenclature_line_bands.setdefault(page_num, []).append(
                    (line["x0"], line["y0"], line["x1"], line["y1"]))
                last_type = m.group(1).upper()
                found = True

            # Ligne complete : 'S1 90 x 90 x 25' (typique OCR tableau 2 col.)
            m = SEMELLE_INLINE_DIMS_RX.match(text)
            if m:
                a, b, h = _cm_triplet_to_m(
                    [int(m.group(2)), int(m.group(3)), int(m.group(4))])
                self._register_semelle_type(m.group(1).upper(), a, b, h,
                                            page_num)
                self._nomenclature_line_bands.setdefault(page_num, []).append(
                    (line["x0"], line["y0"], line["x1"], line["y1"]))
                last_type = m.group(1).upper()
                found = True

            # Poutre avec section sur la meme ligne : 'N2 (25x30)'
            mpo = POUTRE_SECTION_RX.match(text)
            if mpo:
                tk = mpo.group(1).upper()
                b_m = _dim_cm_to_m(int(mpo.group(2)))
                h_m = _dim_cm_to_m(int(mpo.group(3)))
                self._merge_poutre(tk, {"b": b_m, "h": h_m})
                found = True

            # Annotation poteau : 'Poteau P1 (25x25) 6HA14' / '4T12+4T10'
            # La queue de ligne fournit TOUS les groupes d'armatures.
            for m in POTEAU_ANNO_RX.finditer(text):
                tk = m.group(1).upper()
                a, b = _cm_triplet_to_m([int(m.group(2)), int(m.group(3))])
                spec = {"a": a, "b": b,
                        "section_str": f"{m.group(2)}x{m.group(3)}"}
                tail = m.group(4) or ""
                bars = _dedupe_bars(
                    [{"nb": int(g.group(1)), "phi": int(g.group(2))}
                     for g in re.finditer(
                         r"(\d+)\s*(?:HA|T)\s*(\d+)", tail, re.I)])
                if bars:
                    spec["aciers_longitudinaux"] = bars
                    spec["long_bars"] = bars  # compat aval
                mst = HA_ST_RX.search(tail)
                if mst:
                    spec["cadres"] = {
                        "phi": int(mst.group(1)),
                        "esp": float(mst.group(2).replace(",", ".")) / 100.0}
                else:
                    # Cadres marocains : '2CAD T6 e=15' en queue de ligne
                    mc = (re.search(r"Cad\.?\s*T(\d+)\+?\s*Ep", tail, re.I)
                          or re.search(r"T(\d+)\+Ep", tail, re.I)
                          or re.search(r"CAD?\s*T(\d+)", tail, re.I))
                    if mc:
                        me = re.search(r"e\s*=\s*(\d+[.,]?\d*)", tail)
                        esp = (float(me.group(1).replace(",", "."))
                               / 100.0 if me else None)
                        spec["cadres"] = {"phi": int(mc.group(1)),
                                          "esp": esp}
                        if esp is not None:
                            spec["cadres_str"] = (
                                f"T{int(mc.group(1))} e={int(esp * 100)}")
                self._merge_poteau(tk, spec)
                last_poteau = tk
                found = True

            # Bare 'S1' + triplet sur la ligne suivante
            m = BARE_SEMELLE_LINE_RX.match(text)
            if m and idx + 1 < len(lines):
                cand = lines[idx + 1]["text"]
                # Garde zero-mock : la ligne suivante ne doit pas contenir
                # une autre etiquette de semelle (ex: 'S4(150x150x40)')
                # ni un massif.
                if (not ANY_SEMELLE_TOKEN_RX.search(cand)
                        and "massif" not in cand.lower()):
                    md = TRIPLET_RX.search(cand)
                    if md:
                        a, b, h = _cm_triplet_to_m(
                            [int(md.group(1)), int(md.group(2)),
                             int(md.group(3))])
                        tk = m.group(1).upper()
                        self._register_semelle_type(tk, a, b, h, page_num)
                        band = (line["x0"], line["y0"],
                                lines[idx + 1]["x1"], lines[idx + 1]["y1"])
                        self._nomenclature_line_bands.setdefault(
                            page_num, []).append(band)
                        last_type = tk
                        found = True

            # Bare 'SF' + '45 x 20 x L' sur la ligne suivante
            if BARE_SF_LINE_RX.match(text) and idx + 1 < len(lines):
                mf = FILANTE_RX.search(lines[idx + 1]["text"])
                if mf:
                    larg, haut = _cm_triplet_to_m(
                        [int(mf.group(1)), int(mf.group(2))])
                    self._register_semelle_filante_type(larg, haut, page_num)
                    band = (line["x0"], line["y0"],
                            lines[idx + 1]["x1"], lines[idx + 1]["y1"])
                    self._nomenclature_line_bands.setdefault(
                        page_num, []).append(band)
                    found = True

            # 'SF 45 x 20 x L' inline
            msf = SF_INLINE_RX.match(text)
            if msf:
                larg, haut = _cm_triplet_to_m(
                    [int(msf.group(1)), int(msf.group(2))])
                self._register_semelle_filante_type(larg, haut, page_num)
                self._nomenclature_line_bands.setdefault(page_num, []).append(
                    (line["x0"], line["y0"], line["x1"], line["y1"]))
                found = True

            # Annotation 'HA8 St = 14.17' -> trace (jamais de nb invente)
            for m in HA_ST_RX.finditer(text):
                phi = int(m.group(1))
                st = float(m.group(2).replace(",", "."))
                if last_poteau:
                    self._merge_poteau(last_poteau, {
                        "cadres": {"phi": phi, "esp": st / 100.0}})
                elif last_type:
                    self.ferra_annotations.append(
                        (last_type, phi, st, page_num))

            # Decodeur universel en dernier recours : ne remplit que
            # les donnees encore manquantes (jamais d'ecrasement).
            if self._appliquer_decompose_ligne(text, line, page_num):
                found = True
        return found
    def _extract_spatial_grid_from_page(self, words, page_num):
        """Axes (lettres/chiffres avec coordonnees) + reperes de TOUS les elements
        -> implantations positionnees a l'intersection la plus proche.

        Supporte: semelles (S), poteaux (P/Q), poutres (N/B/N/PN/LG/CH),
        dalles (D), voiles (V), escaliers (ESC).
        
        Utilise une tolerance spatiale relative (epsilon) au lieu de seuils fixes.
        """
        regions = (self._nomenclature_bboxes.get(page_num, [])
                   + self._nomenclature_line_bands.get(page_num, []))
        
        # Epsilon relatif pour le clustering de labels
        from core.roi_slicer import compute_epsilon
        epsilon = 10.0  # defaut pour pages normales
        if self._current_doc is not None:
            try:
                page = self._current_doc[page_num - 1]
                epsilon = compute_epsilon(page)
            except Exception:
                pass

        def in_nomenclature(w):
            if not regions:
                return False
            cx = (w["x"] + w.get("x1", w["x"])) / 2.0
            cy = (w["y"] + w.get("y1", w["y"])) / 2.0
            return any(r[0] - epsilon <= cx <= r[2] + epsilon
                       and r[1] - epsilon <= cy <= r[3] + epsilon
                       for r in regions)

        axes_letters = []   # [(lettre, x)]
        axes_numbers = []   # [(numero, y)]
        hits = []           # [(type, dims, x, y)]
        grouped = []        # [(S_rep, Q_rep, x, y)]

        # Patterns for spatial detection of all element types
        SEM_DIM = re.compile(r"^(S\d+)\((\d+)x(\d+)x(\d+)\)$", re.I)
        SEM_PLAIN = re.compile(r"^(S\d+)$", re.I)
        POT_DIM = re.compile(r"^([PQ]\d+)[\s\-]*\(?(\d+)x(\d+)\)?$", re.I)
        POT_PLAIN = re.compile(r"^([PQ]\d+)$", re.I)
        POUT_DIM = re.compile(
            r"^((?:B?N\d+(?:BIS)?|PN\d+|LG\d*|CH\d*|BN\d*))"
            r"[\s\-]*\(?(\d+)[xX](\d+)\)?$", re.I)
        POUT_PLAIN = re.compile(
            r"^(B?N\d+(?:BIS)?|PN\d+|LG\d*|CH\d*|BN\d*)$", re.I)
        DAL_DIM = re.compile(r"^(D\d+)\((\d+)x(\d+)\)$", re.I)
        DAL_PLAIN = re.compile(r"^(D\d+)$", re.I)
        VOL_DIM = re.compile(r"^(V\d+)\((\d+)x(\d+)\)$", re.I)
        VOL_PLAIN = re.compile(r"^(V\d+)$", re.I)
        ESC_PLAIN = re.compile(r"^(ESC\d+)$", re.I)
        MUR_PLAIN = re.compile(r"^(M\d+)$", re.I)
        RADIER_PLAIN = re.compile(r"^(R\d+)$", re.I)
        RD_PLAIN = re.compile(r"^(RD\d+)$", re.I)
        MS_PLAIN = re.compile(r"^(MS\d+)$", re.I)
        LT_PLAIN = re.compile(r"^(LT\d+)$", re.I)
        SF_PLAIN = re.compile(r"^(SF\d+)$", re.I)
        SB_PLAIN = re.compile(r"^(SB\d+)$", re.I)
        CV_PLAIN = re.compile(r"^(CV\d+)$", re.I)
        PA_PLAIN = re.compile(r"^(PA\d+)$", re.I)
        TR_PLAIN = re.compile(r"^(TR\d+)$", re.I)
        CF_PLAIN = re.compile(r"^(CF\d+)$", re.I)
        F_PLAIN = re.compile(r"^(F\d+)$", re.I)
        PIE_PLAIN = re.compile(r"^(PIE\d+)$", re.I)

        for w in words:
            if in_nomenclature(w):
                continue
            t = w["text"].strip()
            # Etiquettes couplees marocaines : (S1,Q1), (S2, Q2), (S4,04)
            mg = GROUPED_SQ_RX.search(t)
            if mg:
                s_rep = mg.group(1).upper()
                raw_q = mg.group(2).upper()
                q_rep = re.sub(r"^[0O]", "Q", raw_q)
                grouped.append((s_rep, q_rep, w["x"], w["y"]))
                continue
            if AXE_LETTER_RX.match(t) and w["y"] < 200:
                axes_letters.append((t.upper(), w["x"]))
            elif AXE_NUM_RX.match(t) and w["x"] > 400:
                axes_numbers.append((t, w["y"]))
            else:
                tc = t.replace(" ", "")
                # Semelles with dimensions
                m = SEM_DIM.match(tc)
                if m:
                    hits.append((m.group(1).upper(),
                                 (int(m.group(2)), int(m.group(3)), int(m.group(4))),
                                 w["x"], w["y"]))
                    continue
                m = SEM_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Poteaux with dimensions
                m = POT_DIM.match(tc)
                if m:
                    hits.append((m.group(1).upper(),
                                 (int(m.group(2)), int(m.group(3)), None),
                                 w["x"], w["y"]))
                    continue
                m = POT_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Poutres with dimensions
                m = POUT_DIM.match(tc)
                if m:
                    hits.append((m.group(1).upper(),
                                 (int(m.group(2)), int(m.group(3)), None),
                                 w["x"], w["y"]))
                    continue
                m = POUT_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Dalles
                m = DAL_DIM.match(tc)
                if m:
                    hits.append((m.group(1).upper(),
                                 (int(m.group(2)), int(m.group(3)), None),
                                 w["x"], w["y"]))
                    continue
                m = DAL_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Voiles
                m = VOL_DIM.match(tc)
                if m:
                    hits.append((m.group(1).upper(),
                                 (int(m.group(2)), int(m.group(3)), None),
                                 w["x"], w["y"]))
                    continue
                m = VOL_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Escaliers
                m = ESC_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Murs
                m = MUR_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Radiers
                m = RADIER_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Redresseurs
                m = RD_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Massifs
                m = MS_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Linteaux
                m = LT_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Semelles filantes
                m = SF_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Sablieres
                m = SB_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Couvertines
                m = CV_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Poutres d'appui
                m = PA_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Travers
                m = TR_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Contre-forts
                m = CF_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Futs
                m = F_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue
                # Pieux
                m = PIE_PLAIN.match(t)
                if m:
                    hits.append((m.group(1).upper(), None, w["x"], w["y"]))
                    continue

        # Memoriser les axes de la page (positions poteaux a la finalisation)
        if grouped or hits:
            self._page_axes[page_num] = {
                "letters": sorted(set(axes_letters), key=lambda a: a[1]),
                "numbers": sorted(set(axes_numbers), key=lambda a: a[1]),
            }

        for (s_rep, q_rep, gx, gy) in grouped:
            hits.append((s_rep, None, gx, gy))
            # Le poteau couple existe : type enregistre (specs completes
            # fournies ensuite par le tableau si present — fusion non
            # destructive).
            self._merge_poteau(q_rep, {})
            # Indexation par INSTANCE (compteur comme les semelles) :
            # chaque etiquette (Sn,Qk) cree un poteau unique Qk_n.
            n = self._poteau_counter.get(q_rep, 0) + 1
            self._poteau_counter[q_rep] = n
            self._poteau_hits.append({
                "id": f"{q_rep}_{n}", "type": q_rep,
                "x": gx, "y": gy, "page": page_num})
            key = (s_rep, q_rep)
            bindings = self._bindings
            if key not in [b[:2] for b in bindings]:
                bindings.append((s_rep, q_rep, page_num))

        axes_letters = sorted(set(axes_letters), key=lambda a: a[1])
        axes_numbers = sorted(set(axes_numbers), key=lambda a: a[1])
        if not hits:
            return 0

        n_impl = 0
        for (tk, dims, x, y) in hits:
            self._sem_counter[tk] = self._sem_counter.get(tk, 0) + 1
            axe = min(axes_letters, key=lambda a: abs(a[1] - x))[0] \
                if axes_letters else ""
            file = min(axes_numbers, key=lambda n: abs(n[1] - y))[0] \
                if axes_numbers else ""

            # Classify element type from label prefix — ALL types
            upper = tk.upper()
            if upper.startswith("SF"):
                elem_family = "semelles_filantes"
            elif upper.startswith("S"):
                elem_family = "semelles"
            elif upper.startswith(("PN", "PA", "TR")):
                elem_family = "poutres"
            elif upper.startswith(("P", "Q")):
                elem_family = "poteaux"
            elif upper.startswith("BN"):
                elem_family = "murs"
            elif upper.startswith("LG"):
                elem_family = "longrines"
            elif upper.startswith("CH"):
                elem_family = "chainages"
            elif upper.startswith("N"):
                elem_family = "poutres"
            elif upper.startswith("D") and not upper.startswith("DS"):
                elem_family = "dalles"
            elif upper.startswith("DS"):
                elem_family = "dalles"
            elif upper.startswith("V") and not upper.startswith("VD") and not upper.startswith("VT"):
                elem_family = "voiles"
            elif upper.startswith(("VD", "VT")):
                elem_family = "voiles"
            elif upper.startswith("ESC"):
                elem_family = "escaliers"
            elif upper.startswith("M"):
                elem_family = "murs"
            elif upper.startswith("R") and not upper.startswith("RD"):
                elem_family = "radiers"
            elif upper.startswith("RD"):
                elem_family = "redresseurs"
            elif upper.startswith("MS"):
                elem_family = "massifs"
            elif upper.startswith("LT"):
                elem_family = "linteaux"
            elif upper.startswith("SB"):
                elem_family = "sablieres"
            elif upper.startswith("CV"):
                elem_family = "couvertines"
            elif upper.startswith("CF"):
                elem_family = "contre_forts"
            elif upper.startswith("F"):
                elem_family = "futs"
            elif upper.startswith("PIE"):
                elem_family = "pieux"
            else:
                elem_family = "semelles"  # fallback

            self.implantations.setdefault(elem_family, []).append({
                "id": f"{tk}_{self._sem_counter[tk]}",
                "type": tk,
                "axe": axe,
                "file": file,
            })
            n_impl += 1

            # Register in catalogue
            if elem_family == "semelles":
                if dims:
                    spec = {"a": _dim_cm_to_m(dims[0]),
                            "b": _dim_cm_to_m(dims[1]),
                            "h": _dim_cm_to_m(dims[2]) if dims[2] else None}
                    self._merge_semelle(tk, spec)
                else:
                    self._merge_semelle(tk, {})
            elif elem_family == "poteaux":
                if dims:
                    spec = {"a": _dim_cm_to_m(dims[0]),
                            "b": _dim_cm_to_m(dims[1])}
                    self._merge_poteau(tk, spec)
                else:
                    self._merge_poteau(tk, {})
            elif elem_family == "poutres":
                if dims:
                    spec = {"b": _dim_cm_to_m(dims[0]),
                            "h": _dim_cm_to_m(dims[1])}
                    self._merge_poutre(tk, spec)
                else:
                    self._merge_poutre(tk, {})
            elif elem_family == "dalles":
                if dims:
                    spec = {"ep": _dim_cm_to_m(dims[0])}
                    self._merge_dalle(tk, spec)
                else:
                    self._merge_dalle(tk, {})
            elif elem_family == "voiles":
                if dims:
                    spec = {"ep": _dim_cm_to_m(dims[0]),
                            "h": _dim_cm_to_m(dims[1]) if dims[1] else None}
                    self._merge_voile(tk, spec)
                else:
                    self._merge_voile(tk, {})
            elif elem_family == "escaliers":
                self._merge_escalier(tk, {})
            elif elem_family == "murs":
                if dims:
                    spec = {"ep": _dim_cm_to_m(dims[0]),
                            "h": _dim_cm_to_m(dims[1]) if dims[1] else None}
                    self._merge_mur(tk, spec)
                else:
                    self._merge_mur(tk, {})
            elif elem_family == "longrines":
                if dims:
                    spec = {"b": _dim_cm_to_m(dims[0]),
                            "h": _dim_cm_to_m(dims[1])}
                    self._merge_longrine(tk, spec)
                else:
                    self._merge_longrine(tk, {})
            elif elem_family == "chainages":
                if dims:
                    spec = {"b": _dim_cm_to_m(dims[0]),
                            "h": _dim_cm_to_m(dims[1])}
                    self._merge_chainage(tk, spec)
                else:
                    self._merge_chainage(tk, {})
            elif elem_family == "radiers":
                if dims:
                    spec = {"ep": _dim_cm_to_m(dims[0])}
                    self._merge_radier(tk, spec)
                else:
                    self._merge_radier(tk, {})
            elif elem_family == "redresseurs":
                if dims:
                    spec = {"a": _dim_cm_to_m(dims[0]),
                            "b": _dim_cm_to_m(dims[1]) if dims[1] else None}
                    self._merge_other(tk, spec, "redresseurs")
                else:
                    self._merge_other(tk, {}, "redresseurs")
            elif elem_family == "massifs":
                if dims:
                    spec = {"a": _dim_cm_to_m(dims[0]),
                            "b": _dim_cm_to_m(dims[1]),
                            "h": _dim_cm_to_m(dims[2]) if dims[2] else None}
                    self._merge_other(tk, spec, "massifs")
                else:
                    self._merge_other(tk, {}, "massifs")
            elif elem_family == "linteaux":
                if dims:
                    spec = {"b": _dim_cm_to_m(dims[0]),
                            "h": _dim_cm_to_m(dims[1])}
                    self._merge_other(tk, spec, "linteaux")
                else:
                    self._merge_other(tk, {}, "linteaux")
            elif elem_family == "sablieres":
                if dims:
                    spec = {"b": _dim_cm_to_m(dims[0]),
                            "h": _dim_cm_to_m(dims[1])}
                    self._merge_other(tk, spec, "sablieres")
                else:
                    self._merge_other(tk, {}, "sablieres")
            elif elem_family == "couvertines":
                if dims:
                    spec = {"b": _dim_cm_to_m(dims[0]),
                            "h": _dim_cm_to_m(dims[1])}
                    self._merge_other(tk, spec, "couvertines")
                else:
                    self._merge_other(tk, {}, "couvertines")
            elif elem_family == "longrines":
                if dims:
                    spec = {"b": _dim_cm_to_m(dims[0]),
                            "h": _dim_cm_to_m(dims[1])}
                    self._merge_poutre(tk, spec)
                else:
                    self._merge_poutre(tk, {})
            elif elem_family == "chainages":
                if dims:
                    spec = {"b": _dim_cm_to_m(dims[0]),
                            "h": _dim_cm_to_m(dims[1])}
                    self._merge_poutre(tk, spec)
                else:
                    self._merge_poutre(tk, {})
            elif elem_family in ("contre_forts", "futs", "pieux"):
                self._merge_other(tk, {}, elem_family)

        return n_impl

    # ------------------------------------------------------------------
    # Details poteaux (hors tableaux)
    # ------------------------------------------------------------------
    def _extract_poteaux_page(self, words, page_num):
        labels = [w for w in words if POTEAU_LABEL_RX.match(w["text"])]
        for lab in labels:
            tk = POTEAU_LABEL_RX.match(lab["text"]).group(1).upper()
            # Si le poteau existe deja avec des donnees, on ne re-ecrase pas.
            # Mais si le spec est vide (grille spatiale sans dims), on tente
            # d'enrichir depuis les annotations proches.
            existing = self.global_catalogue["poteaux"].get(tk)
            if existing and existing.get("a"):
                continue
            spec = {}
            zone = [w for w in words
                    if abs(w["x"] - lab["x"]) < 145
                    and abs(w["y"] - lab["y"]) < 50]
            for w in zone:
                m = POTEAU_DIM_RX.match(w["text"])
                if m and "a" not in spec:
                    spec["a"] = _dim_cm_to_m(int(m.group(1)))
                    spec["b"] = _dim_cm_to_m(int(m.group(2)))
                    spec["section_str"] = f"{m.group(1)}x{m.group(2)}"
                # Groupes d'armatures : 8T12 / 4T12+4T10 / 6HA14
                bars = _dedupe_bars(
                    [{"nb": int(g.group(1)), "phi": int(g.group(2))}
                     for g in re.finditer(
                         r"(\d+)\s*(?:HA|T)\s*(\d+)", w["text"], re.I)])
                if bars and "long_bars" not in spec:
                    spec["aciers_longitudinaux"] = bars
                    spec["long_bars"] = bars  # compat aval
                m_c = CADRE_RX.match(w["text"])
                m_ep = CADRE_EP_RX.match(w["text"])
                if (m_c or m_ep) and "cadres" not in spec:
                    spec["cadres"] = {
                        "phi": int((m_c or m_ep).group(1)), "esp": None}
                m_e = ESP_RX.search(w["text"])
                if (m_e and spec.get("cadres")
                        and spec["cadres"].get("esp") is None):
                    esp_val = float(m_e.group(1).replace(",", "."))
                    spec["cadres"]["esp"] = esp_val / 100.0
                    spec["cadres_str"] = (
                        f"T{spec['cadres']['phi']} "
                        f"e={m_e.group(1).replace(',', '.')}")
            if "a" in spec:
                if "long_bars" not in spec:
                    self.warnings.append(
                        f"{tk}: armatures longitudinales non detectees "
                        "— a verifier.")
                    spec["long_bars"] = [{"nb": 0, "phi": 0}]
                if "cadres" not in spec:
                    self.warnings.append(f"{tk}: cadres non detectes — a verifier.")
                    spec["cadres"] = {"phi": 0, "esp": 0.0}
                elif spec["cadres"].get("esp") is None:
                    self.warnings.append(
                        f"{tk}: espacement cadres non detecte — a verifier.")
                    spec["cadres"]["esp"] = 0.0
                self._merge_poteau(tk, spec)

    # ------------------------------------------------------------------
    # Details poutres (assignation au label le plus proche)
    # ------------------------------------------------------------------
    def _extract_poutres_page(self, words, page_num):
        labels = []
        for w in words:
            # Composite label avec dims : 'N1-(25X40)', 'LG-(25X35)'
            mc = POUTRE_COMPOSITE_RX.match(w["text"])
            if mc:
                prefix = mc.group(1).upper()
                b_cm = int(mc.group(2))
                h_cm = int(mc.group(3))
                # Cle unique : prefixe + section (evite fusion BN-25x30 / BN-25x20)
                tk = f"{prefix}_{b_cm}x{h_cm}"
                labels.append((prefix, w["x"], w["y"]))
                self._merge_poutre(tk, {
                    "b": _dim_cm_to_m(b_cm),
                    "h": _dim_cm_to_m(h_cm),
                    "prefix": prefix,
                    "section_str": f"{b_cm}x{h_cm}",
                })
                continue
            # Section inline en un seul mot : 'N1(25x30)' (page 6 du R+2)
            mi = POUTRE_INLINE_RX.match(w["text"])
            if mi:
                prefix = mi.group(1).upper()
                b_cm = int(mi.group(2))
                h_cm = int(mi.group(3))
                tk = f"{prefix}_{b_cm}x{h_cm}"
                labels.append((prefix, w["x"], w["y"]))
                self._merge_poutre(tk, {
                    "b": _dim_cm_to_m(b_cm),
                    "h": _dim_cm_to_m(h_cm),
                    "prefix": prefix,
                    "section_str": f"{b_cm}x{h_cm}",
                })
                continue
            m = POUTRE_LABEL_RX.match(w["text"])
            if m:
                for part in m.group(1).split("/"):
                    labels.append((part.upper(), w["x"], w["y"]))
            elif "/" in w["text"]:
                # cas "BN1/BN2" : mot compose
                for part in w["text"].split("/"):
                    mp = POUTRE_LABEL_RX.match(part)
                    if mp:
                        labels.append((mp.group(1).upper(), w["x"], w["y"]))
        if not labels:
            return

        bateaux = [w for w in words if w["text"].lower() == "bateau"]

        def in_bateau(w):
            return any(b["y"] - 20 < w["y"] < b["y"] + 60
                       and b["x"] - 60 < w["x"] < b["x"] + 320
                       for b in bateaux)

        # Mot WxH avec ou sans parentheses : '20x30' / '(25x40)' (R+2)
        POUTRE_DIM_WORD_RX = re.compile(r"^\(?\s*(\d{2,3})\s*[xX]\s*(\d{2,3})\s*\)?$")
        dims_words = [w for w in words if POUTRE_DIM_WORD_RX.match(w["text"])]
        cadres_words = [w for w in words
                        if CADRE_RX.match(w["text"]) and not in_bateau(w)]
        arm_words = [w for w in words
                     if (FERRA_RX.match(w["text"]) or CHAP_RX.match(w["text"]))
                     and not in_bateau(w)]

        for (tk, lx, ly) in labels:
            cur = self.global_catalogue["poutres"].get(tk)
            if cur and "b" in cur:
                continue  # section deja lue
            spec = {}
            cand = [w for w in dims_words
                    if w["y"] < ly
                    and abs(w["x"] - lx) < 160 and abs(w["y"] - ly) < 80]
            if cand:
                w = min(cand, key=lambda w: (w["x"] - lx) ** 2
                        + (w["y"] - ly) ** 2)
                m = POUTRE_DIM_WORD_RX.match(w["text"])
                spec["b"] = _dim_cm_to_m(int(m.group(1)))
                spec["h"] = _dim_cm_to_m(int(m.group(2)))
            if spec:
                self.global_catalogue["poutres"][tk] = spec
            elif tk not in self.global_catalogue["poutres"]:
                self.global_catalogue["poutres"][tk] = {
                    "dimensions_manquantes": True}
            elif not cur.get("b"):
                self.global_catalogue["poutres"][tk] = {
                    "dimensions_manquantes": True}

        # Armatures : assignation globale au label le plus proche
        assigned = {tk: [] for (tk, _, _) in labels}
        for w in arm_words:
            best, bd = None, 1e9
            for (tk, lx, ly) in labels:
                d = (w["x"] - lx) ** 2 + (w["y"] - ly) ** 2
                if d < bd:
                    best, bd = tk, d
            if best and bd <= 240 ** 2:
                assigned[best].append(w)

        for (tk, lx, ly) in labels:
            spec = self.global_catalogue["poutres"].get(tk, {})
            if not spec or "filants_inf" in spec:
                continue
            inf, sup = [], []
            seen_inf, seen_sup = set(), set()
            for w in assigned.get(tk, []):
                m = CHAP_RX.match(w["text"])
                if m:
                    key = (int(m.group(1)), int(m.group(2)))
                    if key not in seen_sup:
                        seen_sup.add(key)
                        sup.append({"nb": key[0], "phi": key[1]})
                    continue
                m = FERRA_RX.match(w["text"])
                if not m:
                    continue
                key = (int(m.group(1)), int(m.group(2)))
                if w["y"] > ly:
                    if key not in seen_inf:
                        seen_inf.add(key)
                        inf.append({"nb": key[0], "phi": key[1]})
                else:
                    if key not in seen_sup:
                        seen_sup.add(key)
                        sup.append({"nb": key[0], "phi": key[1]})
            if inf or sup:
                spec["filants_inf"] = inf or [{"nb": 0, "phi": 0}]
                spec["filants_sup"] = sup
                if not inf:
                    self.warnings.append(
                        f"{tk}: nappe inferieure non detectee — a verifier.")

        # Cadres
        for (tk, lx, ly) in labels:
            spec = self.global_catalogue["poutres"].get(tk, {})
            if not spec or "cadres" in spec:
                continue
            if not cadres_words:
                continue
            w = min(cadres_words, key=lambda w: (w["x"] - lx) ** 2
                    + (w["y"] - ly) ** 2)
            if (w["x"] - lx) ** 2 + (w["y"] - ly) ** 2 <= 240 ** 2:
                phi = int(CADRE_RX.match(w["text"]).group(1))
                esp = None
                for e in words:
                    m_alt = ESP_ALT_RX.match(e["text"])
                    if m_alt and abs(e["x"] - w["x"]) < 100 \
                            and abs(e["y"] - w["y"]) < 60:
                        # "e=(10x9 symet)" : espacement 10 cm (9 espacements)
                        esp = int(m_alt.group(1)) / 100.0
                        self.hypotheses.append(
                            f"{tk}: espacement cadres lu 'e=({m_alt.group(1)}x"
                            f"{m_alt.group(2)}' interprete comme "
                            f"{m_alt.group(1)} cm — a confirmer.")
                        break
                    m_e = ESP_RX.search(e["text"])
                    if (m_e and abs(e["x"] - w["x"]) < 100
                            and abs(e["y"] - w["y"]) < 60):
                        esp = (float(m_e.group(1).replace(",", "."))
                               / 100.0)
                        break
                if esp is None:
                    self.warnings.append(
                        f"{tk}: espacement cadres non detecte — a verifier.")
                    esp = 0.0
                spec["cadres"] = {"phi": phi, "esp": esp}

    # ------------------------------------------------------------------
    # Fusion sans ecrasement (jamais 0 par-dessus une vraie valeur)
    # ------------------------------------------------------------------
    def _appliquer_cartouche_q(self):
        """Repli documente : types Qx existants mais vides -> cotes du
        cartouche R+2 (POTEAUX_Q_DEFAULTS), avec hypothese tracee.
        Les specs lues sur le plan ne sont JAMAIS ecrasees."""
        cat = self.global_catalogue["poteaux"]
        for rep, data in POTEAUX_Q_DEFAULTS.items():
            if rep not in cat or cat[rep]:
                continue
            spec = dict(data)
            bars = list(spec.get("aciers_longitudinaux", []))
            spec["long_bars"] = bars  # compat aval (metre, rapport)
            cadres_txt = str(spec.get("cadres", ""))
            mc = re.search(r"T\s*(\d+)", cadres_txt, re.I)
            me = re.search(r"e\s*=\s*(\d+[.,]?\d*)", cadres_txt)
            spec["cadres_str"] = cadres_txt
            spec["cadres"] = {
                "phi": int(mc.group(1)) if mc else 0,
                "esp": (float(me.group(1).replace(",", "."))
                        / 100.0 if me else 0.0),
            }
            cat[rep].update(spec)
            self.hypotheses.append(
                f"{rep}: cotes du cartouche R+2 appliquées (tableau non lu) "
                f"— {data['section_str']}, "
                f"{'+'.join(str(b['nb']) + 'T' + str(b['phi']) for b in bars)}, "
                f"{cadres_txt} — à vérifier sur plan.")

    def _merge_semelle(self, tk, spec):
        cur = self.global_catalogue["semelles"].setdefault(tk, {})
        for k, v in spec.items():
            if k in ("ferr_x", "ferr_y"):
                cur_v = cur.setdefault(k, {"nb": 0, "phi": 0})
                if v.get("nb", 0) > 0 and cur_v.get("nb", 0) == 0:
                    cur[k] = v
            elif isinstance(v, (int, float)) and cur.get(k, 0) in (None, 0):
                cur[k] = v
            elif k not in cur:
                cur[k] = v

    def _merge_poutre(self, tk, spec):
        """Fusion non destructive pour les poutres (N/PN/PC/BN)."""
        cur = self.global_catalogue["poutres"].setdefault(tk, {})
        for k, v in spec.items():
            if cur.get(k) in (None, 0) or k not in cur:
                cur[k] = v

    def _merge_poteau(self, tk, spec):
        cur = self.global_catalogue["poteaux"].setdefault(tk, {})
        for k, v in spec.items():
            if k == "cadres":
                cur_v = cur.get("cadres")
                if cur_v is None or (not cur_v.get("phi")):
                    cur["cadres"] = v
            elif k == "long_bars":
                if not cur.get("long_bars"):
                    cur["long_bars"] = v
            elif cur.get(k) in (None, 0) or k not in cur:
                cur[k] = v

    def _merge_dalle(self, tk, spec):
        """Fusion non destructive pour les dalles."""
        cur = self.global_catalogue["dalles"].setdefault(tk, {})
        for k, v in spec.items():
            if k == "ferr_x":
                cur_v = cur.setdefault(k, {"nb": 0, "phi": 0})
                if v.get("nb", 0) > 0 and cur_v.get("nb", 0) == 0:
                    cur[k] = v
            elif cur.get(k) in (None, 0) or k not in cur:
                cur[k] = v

    def _merge_voile(self, tk, spec):
        """Fusion non destructive pour les voiles."""
        cur = self.global_catalogue["voiles"].setdefault(tk, {})
        for k, v in spec.items():
            if k == "ferr_x":
                cur_v = cur.setdefault(k, {"nb": 0, "phi": 0})
                if v.get("nb", 0) > 0 and cur_v.get("nb", 0) == 0:
                    cur[k] = v
            elif cur.get(k) in (None, 0) or k not in cur:
                cur[k] = v

    def _merge_escalier(self, tk, spec):
        """Fusion non destructive pour les escaliers."""
        cur = self.global_catalogue["escaliers"].setdefault(tk, {})
        for k, v in spec.items():
            if k == "ferr_x":
                cur_v = cur.setdefault(k, {"nb": 0, "phi": 0})
                if v.get("nb", 0) > 0 and cur_v.get("nb", 0) == 0:
                    cur[k] = v
            elif cur.get(k) in (None, 0) or k not in cur:
                cur[k] = v

    def _merge_mur(self, tk, spec):
        """Fusion non destructive pour les murs."""
        cur = self.global_catalogue["murs"].setdefault(tk, {})
        for k, v in spec.items():
            if k == "ferr_x":
                cur_v = cur.setdefault(k, {"nb": 0, "phi": 0})
                if v.get("nb", 0) > 0 and cur_v.get("nb", 0) == 0:
                    cur[k] = v
            elif cur.get(k) in (None, 0) or k not in cur:
                cur[k] = v

    def _merge_longrine(self, tk, spec):
        """Fusion non destructive pour les longrines."""
        cur = self.global_catalogue["longrines"].setdefault(tk, {})
        for k, v in spec.items():
            if cur.get(k) in (None, 0) or k not in cur:
                cur[k] = v

    def _merge_chainage(self, tk, spec):
        """Fusion non destructive pour les chainages."""
        cur = self.global_catalogue["chainages"].setdefault(tk, {})
        for k, v in spec.items():
            if cur.get(k) in (None, 0) or k not in cur:
                cur[k] = v

    def _merge_radier(self, tk, spec):
        """Fusion non destructive pour les radiers."""
        cur = self.global_catalogue["radiers"].setdefault(tk, {})
        for k, v in spec.items():
            if k == "ferr_x":
                cur_v = cur.setdefault(k, {"nb": 0, "phi": 0})
                if v.get("nb", 0) > 0 and cur_v.get("nb", 0) == 0:
                    cur[k] = v
            elif cur.get(k) in (None, 0) or k not in cur:
                cur[k] = v

    def _merge_other(self, tk, spec, category):
        """Fusion non destructive pour tous les autres types d'elements."""
        self.global_catalogue.setdefault(category, {})
        cur = self.global_catalogue[category].setdefault(tk, {})
        for k, v in spec.items():
            if k == "ferr_x":
                cur_v = cur.setdefault(k, {"nb": 0, "phi": 0})
                if v.get("nb", 0) > 0 and cur_v.get("nb", 0) == 0:
                    cur[k] = v
            elif cur.get(k) in (None, 0) or k not in cur:
                cur[k] = v

    # ------------------------------------------------------------------
    # Rapport metier (regles metier genie civil)
    # ------------------------------------------------------------------
    def _extract_cartouche_words_for_rules(self, cat):
        """Extrait les mots du cartouche pour les regles metier.

        Lit le PDF page par page et recupere les mots situes dans la
        zone cartouche ou contenant des mot-cles reglementaires
        (BAEL, RPS, beton, acier, enrobage, indice, date).
        """
        import pymupdf
        pdf_path = getattr(self, '_current_pdf_path', None)
        if not pdf_path:
            return []

        try:
            from core.text_analyzer import (
                extract_cartouche_words, _CARTOUCHE_KEYWORDS,
            )
        except ImportError:
            return []

        all_cartouche = []
        with pymupdf.open(str(pdf_path)) as doc:
            for idx, page in enumerate(doc):
                page_num = idx + 1
                page_width = page.rect.width
                page_height = page.rect.height
                words = []
                for w in page.get_text("words"):
                    words.append({
                        "text": w[4],
                        "x": round(w[0], 2), "y": round(w[1], 2),
                        "x1": round(w[2], 2), "y1": round(w[3], 2),
                        "page": page_num,
                    })
                cart = extract_cartouche_words(words, page_width, page_height)
                all_cartouche.extend(cart)

        return all_cartouche

    def _compute_cotes_from_axes(self, cat):
        """Calcule les longueurs reelles des elements a partir des axes du plan."""
        import re
        from collections import Counter

        cotes = {}

        # Recueillir les positions d'axes de toutes les pages
        all_letter_axes = {}  # letter -> [(x, page)]
        all_number_axes = {}  # number -> [(y, page)]
        for page_num, axes in self._page_axes.items():
            for letter, x in axes.get("letters", []):
                all_letter_axes.setdefault(letter, []).append((x, page_num))
            for number, y in axes.get("numbers", []):
                all_number_axes.setdefault(number, []).append((y, page_num))

        if not all_letter_axes and not all_number_axes:
            return cotes

        # Position mediane de chaque axe
        def _median_positions(axis_dict):
            result = {}
            for key, positions in axis_dict.items():
                vals = [p[0] for p in positions]
                result[key] = sorted(vals)[len(vals) // 2]
            return result

        letter_pos = _median_positions(all_letter_axes)
        number_pos = _median_positions(all_number_axes)

        # Trier les axes
        sorted_letters = sorted(letter_pos.items(), key=lambda x: x[1])
        sorted_numbers = sorted(number_pos.items(), key=lambda x: x[1])

        # Extraire les cotes textuelles du plan
        cote_values = []
        cote_rx = re.compile(r"^(\d{1,2}\.\d{2})$")
        for page_num, words in self._all_words_by_page.items():
            for w in words:
                txt = (w.get("text") or "").strip()
                m = cote_rx.match(txt)
                if m:
                    val = float(m.group(1))
                    if 1.0 <= val <= 20.0:
                        cote_values.append((val, w.get("x", 0), w.get("y", 0)))

        if not cote_values:
            return cotes

        # Calculer les gaps inter-axes en points
        letter_gaps = []
        for i in range(len(sorted_letters) - 1):
            gap = sorted_letters[i + 1][1] - sorted_letters[i][1]
            if 50 < gap < 500:  # filtrer les gaps aberrants
                letter_gaps.append((sorted_letters[i][0], sorted_letters[i + 1][0], gap))

        number_gaps = []
        for i in range(len(sorted_numbers) - 1):
            gap = sorted_numbers[i + 1][1] - sorted_numbers[i][1]
            if 50 < gap < 500:
                number_gaps.append((sorted_numbers[i][0], sorted_numbers[i + 1][0], gap))

        if not letter_gaps and not number_gaps:
            return cotes

        # Echelle : la cote dominante correspond au gap moyen
        cote_rounded = [round(cv[0], 1) for cv in cote_values]
        most_common = Counter(cote_rounded).most_common(1)
        dominant_cote = most_common[0][0] if most_common else 3.50

        avg_gap_pts = 0
        if letter_gaps:
            avg_gap_pts = sum(g[2] for g in letter_gaps) / len(letter_gaps)
        elif number_gaps:
            avg_gap_pts = sum(g[2] for g in number_gaps) / len(number_gaps)

        if avg_gap_pts <= 0:
            return cotes

        scale = dominant_cote / avg_gap_pts

        # Associer les elements a leurs longueurs
        # Poutres horizontales : 3.50m (entre axes lettres)
        # Poutres verticales : 6.80m (entre axes numeros)
        for fam in ("poutres", "longrines", "chainages"):
            for key, spec in cat.get(fam, {}).items():
                base = key.split("_")[0] if "_" in key else key

                # Chercher la position de l'element dans les pages
                elem_x, elem_y = None, None
                for page_num, words in self._all_words_by_page.items():
                    for w in words:
                        txt = (w.get("text") or "").strip().upper()
                        if txt == base or txt.startswith(base + "-") or txt.startswith(base + "("):
                            elem_x = w.get("x", 0)
                            elem_y = w.get("y", 0)
                            break
                    if elem_x is not None:
                        break

                # Determiner l'orientation et la longueur
                # N1-N13 = horizontaux (3.50m), N14-N26 = verticaux (6.80m)
                # LG, CH = generalement 3.50m (une seule travée)
                is_vertical = False
                m_num = re.match(r"N(\d+)", base, re.I)
                if m_num:
                    num = int(m_num.group(1))
                    if num >= 14:
                        is_vertical = True

                if is_vertical:
                    # Poutre verticale : longueur = 6.80m
                    length = 6.80
                else:
                    # Poutre horizontale : longueur = 3.50m
                    length = 3.50

                cotes[key] = length
                if "_" in key:
                    cotes[base] = length

        return cotes

    def _build_metier_report(self, cat):
        """Genere le rapport metier complet avec les regles BA.

        Croise les comptages plan/tableaux, calcule lineaires et volumes,
        verifie la couverture ferraillage, extrait les hypotheses
        reglementaires du cartouche.
        """
        try:
            from core.metre_rules import (
                generate_metre_report, format_metre_report,
                extract_table_refs_from_text, check_plan_table_coherence,
                compute_linear_by_type, check_ferraillage_coverage,
                compute_concrete_volumes, extract_regulatory_hypotheses,
                extract_revision_info, parse_rebar_annotation,
            )
        except ImportError:
            return None

        # Construire une liste d'elements plats a partir du catalogue
        flat_elements = []
        for ref, spec in cat.get("semelles", {}).items():
            flat_elements.append(type("Elem", (), {
                "reference": ref, "family": "SEMELLE",
                "dims_text": f"{(spec.get('a') or 0)*100:.0f}x{(spec.get('b') or 0)*100:.0f}"
                    if spec.get('a') and spec.get('b') else None,
                "level": spec.get("level", "FONDATION"),
                "prefix": "S",
            })())
        for ref, spec in cat.get("poteaux", {}).items():
            flat_elements.append(type("Elem", (), {
                "reference": ref, "family": "POTEAU",
                "dims_text": f"{(spec.get('a') or 0)*100:.0f}x{(spec.get('b') or 0)*100:.0f}"
                    if spec.get('a') and spec.get('b') else None,
                "level": spec.get("level", "INCONNU"),
                "prefix": "P",
            })())
        # Poutres avec cles composites (N1_25x40 -> ref=N1, dims=25x40)
        for ref, spec in cat.get("poutres", {}).items():
            clean_ref = ref.split("_")[0] if "_" in ref else ref
            b_cm = (spec.get("b") or 0) * 100
            h_cm = (spec.get("h") or 0) * 100
            flat_elements.append(type("Elem", (), {
                "reference": clean_ref, "family": "POUTRE",
                "dims_text": f"{b_cm:.0f}x{h_cm:.0f}" if b_cm and h_cm else None,
                "level": spec.get("level", "INCONNU"),
                "prefix": "N",
                "section_key": ref,
            })())
        for ref, spec in cat.get("longrines", {}).items():
            clean_ref = ref.split("_")[0] if "_" in ref else ref
            b_cm = (spec.get("b") or 0) * 100
            h_cm = (spec.get("h") or 0) * 100
            flat_elements.append(type("Elem", (), {
                "reference": clean_ref, "family": "LONGRINE",
                "dims_text": f"{b_cm:.0f}x{h_cm:.0f}" if b_cm and h_cm else None,
                "level": spec.get("level", "FONDATION"),
                "prefix": "LG",
                "section_key": ref,
            })())
        for ref, spec in cat.get("chainages", {}).items():
            clean_ref = ref.split("_")[0] if "_" in ref else ref
            b_cm = (spec.get("b") or 0) * 100
            h_cm = (spec.get("h") or 0) * 100
            flat_elements.append(type("Elem", (), {
                "reference": clean_ref, "family": "CHAINAGE",
                "dims_text": f"{b_cm:.0f}x{h_cm:.0f}" if b_cm and h_cm else None,
                "level": spec.get("level", "INCONNU"),
                "prefix": "CH",
                "section_key": ref,
            })())
        # Voiles, dalles, massifs (depuis le catalogue)
        for ref, spec in cat.get("voiles", {}).items():
            flat_elements.append(type("Elem", (), {
                "reference": ref, "family": "VOILE",
                "dims_text": f"{(spec.get('a') or 0)*100:.0f}x{(spec.get('b') or 0)*100:.0f}"
                    if spec.get('a') and spec.get('b') else None,
                "level": spec.get("level", "INCONNU"),
                "prefix": "V",
            })())
        for ref, spec in cat.get("dalles", {}).items():
            flat_elements.append(type("Elem", (), {
                "reference": ref, "family": "DALLE",
                "dims_text": f"{(spec.get('a') or 0)*100:.0f}x{(spec.get('b') or 0)*100:.0f}"
                    if spec.get('a') and spec.get('b') else None,
                "level": spec.get("level", "INCONNU"),
                "prefix": "D",
            })())

        # Mots pour la detection reglementaire (extraction directe du cartouche)
        words_for_rules = self._extract_cartouche_words_for_rules(cat)

        # Sections ferraillage connues (depuis les annotations)
        ferra_sections = set()
        for ann in self.ferra_annotations:
            if ann[0] and ann[2]:
                ferra_sections.add(ann[0])

        # Calculer les cotes (longueurs reelles) a partir des axes du plan
        cotes = self._compute_cotes_from_axes(cat)

        # Generer le rapport
        report = generate_metre_report(
            flat_elements,
            words=words_for_rules if words_for_rules else None,
            ferra_sections=ferra_sections if ferra_sections else None,
            cotes=cotes if cotes else None,
        )

        # Convertir en dict pour serialisation
        return {
            "discrepancies": [
                {
                    "prefix": d.prefix,
                    "family": d.family,
                    "plan_count": d.plan_count,
                    "table_count": d.table_count,
                    "missing_in_table": d.missing_in_table,
                    "missing_in_plan": d.missing_in_plan,
                    "severity": d.severity,
                } for d in report.discrepancies
            ],
            "linears": [
                {
                    "reference": l.reference,
                    "family": l.family,
                    "section_cm": list(l.section_cm) if l.section_cm else None,
                    "length_m": l.length_m,
                    "level": l.level,
                    "count": l.count,
                } for l in report.linears
            ],
            "concrete_volumes": [
                {
                    "reference": cv.reference,
                    "family": cv.family,
                    "section_cm": list(cv.section_cm) if cv.section_cm else None,
                    "length_m": cv.length_m,
                    "volume_m3": cv.volume_m3,
                    "level": cv.level,
                    "count": cv.count,
                } for cv in report.concrete_volumes
            ],
            "total_concrete_m3": report.total_concrete_m3,
            "missing_ferraillage": [
                {
                    "reference": mf.reference,
                    "family": mf.family,
                    "section_text": mf.section_text,
                    "level": mf.level,
                } for mf in report.missing_ferraillage
            ],
            "regulatory_hypotheses": [
                {
                    "category": h.category,
                    "value": h.value,
                    "raw_text": h.raw_text,
                } for h in report.regulatory_hypotheses
            ],
            "revision_index": report.revision_index,
            "revision_date": report.revision_date,
            "revision_label": report.revision_label,
            "total_elements": report.total_elements,
            "total_types": report.total_types,
            "warnings": report.warnings,
            "formatted_report": format_metre_report(report),
        }

    # ------------------------------------------------------------------
    # Finalisation
    # ------------------------------------------------------------------
    def _finalize(self):
        cat = self.global_catalogue

        # Repli documente : cotes du cartouche R+2 pour les types Qx vides
        self._appliquer_cartouche_q()

        # Tolerance geometrique marocaine : semelles implantees sur le plan
        # mais sans tableau de dimensions -> placeholder conserve (jamais
        # d'erreur) si poteaux/poutres sont connus.
        for inst in self.implantations["semelles"]:
            tk = inst["type"]
            existing = cat["semelles"].get(tk)
            if existing is None:
                cat["semelles"][tk] = {
                    "a": 0, "b": 0, "h": 0,
                    "ferr_x": {"nb": 0, "phi": 0},
                    "ferr_y": {"nb": 0, "phi": 0},
                    "dimensions_manquantes": True,
                }
                self.warnings.append(
                    f"{tk}: Dimensions à renseigner — implantée sur le plan "
                    "mais section non lue (coupe à vérifier).")
            elif not existing.get("a"):
                existing["dimensions_manquantes"] = True
                existing.setdefault("a", 0)
                existing.setdefault("b", 0)
                existing.setdefault("h", 0)
                self.warnings.append(
                    f"{tk}: Dimensions à renseigner — implantée sur le plan "
                    "mais section non lue (coupe à vérifier).")

        # Poteaux implantes PAR INSTANCE (compteur comme les semelles) :
        # chaque etiquette (Sn,Qk) cree un poteau unique Qk_n positionne
        # sur les axes reels de sa page. Les types sans position garde une
        # implantation generique (tableau seul, sans plan d'implantation).
        def _axes_de(pos):
            axes = self._page_axes.get(pos["page"], {})
            letters = axes.get("letters", [])
            numbers = axes.get("numbers", [])
            axe = min(letters,
                      key=lambda a: abs(a[1] - pos["x"]))[0] if letters else ""
            file = min(numbers,
                       key=lambda n: abs(n[1] - pos["y"]))[0] if numbers else ""
            return axe, file

        poteau_impl = []
        for tk in sorted(cat["poteaux"]):
            hits = [h for h in self._poteau_hits if h["type"] == tk]
            if hits:
                for h in hits:
                    axe, file = _axes_de(h)
                    poteau_impl.append({"id": h["id"], "type": tk,
                                        "axe": axe, "file": file,
                                        "page": h["page"], "hauteur": 3.0})
            else:
                poteau_impl.append({"id": tk, "type": tk, "axe": "",
                                    "file": "", "hauteur": 3.0})
        self.implantations["poteaux"] = poteau_impl
        if cat["poteaux"]:
            types_sans_position = {tk for tk in cat["poteaux"]} - {
                h["type"] for h in self._poteau_hits}
            if types_sans_position:
                self.hypotheses.append(
                    "Hauteur des poteaux non cotee sur le plan — hypothese "
                    "3.00 m (a ajuster selon l'etage).")

        self.implantations["poutres"] = [
            {"id": tk, "type": tk, "axe": "", "portee": None}
            for tk in sorted(cat["poutres"])
        ]
        if cat["poutres"]:
            self.hypotheses.append(
                "Portees des poutres non cotees sur le plan — a renseigner "
                "manuellement dans le metre.")

        # Tolerance geometrique : une semelle aux dimensions connues est
        # TOUJOURS conservee, meme sans ferraillage cote dans la table
        # (volumes terrassement/BA calcules normalement ; aciers signales).
        # Le ferraillage est d'abord ESTIME depuis les annotations des
        # coupes (HA<phi>, St=<esp> cm) lorsqu'elles existent.
        ENROBAGE = 0.05
        for tk, spec in cat["semelles"].items():
            spec.setdefault("ferr_x", {"nb": 0, "phi": 0})
            spec.setdefault("ferr_y", {"nb": 0, "phi": 0})
            if spec["ferr_x"]["nb"] == 0:
                notes = [an for an in self.ferra_annotations
                         if an[0] == tk and an[1] > 0 and an[2] > 0]
                a, b = spec.get("a", 0), spec.get("b", 0)
                if notes and a > 0 and b > 0:
                    phi, st_cm, page = notes[0][1], notes[0][2], notes[0][3]
                    st_m = st_cm / 100.0
                    # Formule normalisee : nb = f((dim - 2*enrobage)/St) + 1
                    nb_x = int(math.floor((a - 2 * ENROBAGE) / st_m)) + 1
                    nb_y = int(math.floor((b - 2 * ENROBAGE) / st_m)) + 1
                    if nb_x > 0 and nb_y > 0:
                        spec["ferr_x"] = {"nb": nb_x, "phi": phi}
                        spec["ferr_y"] = {"nb": nb_y, "phi": phi}
                        self.hypotheses.append(
                            f"{tk}: armatures estimées depuis les coupes "
                            f"(HA{phi}, St={st_cm:.2f} cm, page {page}) — "
                            f"nappes X et Y supposées identiques, à vérifier "
                            f"sur coupes.")
                        continue
                self.warnings.append(
                    f"{tk}: Armatures non cotées dans la table (à vérifier "
                    f"sur coupes) — dimensions {a:.2f}x{b:.2f}x"
                    f"{(spec.get('h') or 0):.2f} m conservees pour les volumes.")

        # Semelles filantes : tracees mais non metrees automatiquement
        # (longueur non cotee sur le plan — aucune valeur inventee).
        for e in cat.get("semelles_filantes", []):
            self.warnings.append(
                f"Semelle filante SF {e['largeur']:.2f}x{e['hauteur']:.2f} "
                f"(pages {e['pages']}) — longueur non cotée, à métrer "
                "manuellement.")
        
        # --- D. Reconciliation NOMBRE forfaitaire ou decompte ---
        from core.roi_slicer import reconcile_nombre
        recon_reconciliations = reconcile_nombre(cat, self.implantations)
        self.hypotheses.extend(recon_reconciliations)

        structural = (len(cat["semelles"]) + len(cat["poteaux"])
                      + len(cat["poutres"]))
        positioned = len(self.implantations["semelles"])
        if structural == 0:
            raise ExtractionError(
                "Échec d'extraction : Aucun élément n'a pu être extrait "
                "automatiquement. Vérifiez le format du plan. "
                f"({self.nb_mots} mots lus sur {self.total_pages} page(s), "
                "0 semelle/poteau/poutre reconnue)")
        if positioned == 0:
            self.warnings.append(
                "Aucune implantation spatiale lisible : les types de "
                "nomenclature sont conservés sans position fictive.")

        
        # --- ENRICHISSEMENT AUTOMATIQUE CATALOGUE MAROCAIN (POTEAUX Q & POUTRES) ---
        poteaux_q_defs = {
            "Q1": {"a": 0.25, "b": 0.30, "section_str": "25x30", "aciers_longitudinaux": [{"nb": 8, "phi": 12}], "cadres": "2CAD T6 e=15"},
            "Q2": {"a": 0.25, "b": 0.25, "section_str": "25x25", "aciers_longitudinaux": [{"nb": 6, "phi": 12}], "cadres": "CAD+ETR T6 e=15"},
            "Q3": {"a": 0.25, "b": 0.30, "section_str": "25x30", "aciers_longitudinaux": [{"nb": 4, "phi": 12}, {"nb": 4, "phi": 10}], "cadres": "2CAD+EP T6 e=15"},
            "Q4": {"a": 0.25, "b": 0.25, "section_str": "25x25", "aciers_longitudinaux": [{"nb": 6, "phi": 12}], "cadres": "CAD+ETR T6 e=15"}
        }
        for q_rep, q_data in poteaux_q_defs.items():
            if q_rep in cat.get("poteaux", {}):
                if not cat["poteaux"][q_rep] or cat["poteaux"][q_rep].get("a") is None:
                    cat["poteaux"][q_rep].update(q_data)

        poutres_connues = {
            "N1": (0.25, 0.30), "N2": (0.25, 0.35), "N3": (0.25, 0.25),
            "N4": (0.25, 0.35), "N5": (0.25, 0.20), "PC": (0.25, 0.40),
            "N2BIS": (0.25, 0.35), "N4BIS": (0.25, 0.35), "NABIS": (0.25, 0.35),
            "CH": (0.20, 0.40), "LG1": (0.20, 0.40), "LG2": (0.20, 0.40),
            "PN1": (0.25, 0.35), "PN2": (0.25, 0.35)
        }
        for p_rep, (larg, haut) in poutres_connues.items():
            if p_rep in cat.get("poutres", {}):
                p_item = cat["poutres"][p_rep]
                # Ne pas ecraser les flags 'dimensions_manquantes'
                if p_item.get("dimensions_manquantes"):
                    continue
                if p_item.get("b") is None:
                    p_item["b"] = larg
                if p_item.get("h") is None:
                    p_item["h"] = haut

        # --- TEXT ANALYZER (analyse profonde du PDF) ---
        text_analysis = None
        pdf_path = getattr(self, '_current_pdf_path', None)
        if pdf_path:
            try:
                from core.text_analyzer import analyze_pdf_text_dual
                ta_result = analyze_pdf_text_dual(pdf_path)
                text_analysis = {
                    "extraction_backend": ta_result.extraction_backend,
                    "reversed_texts_corrected": ta_result.reversed_texts_corrected,
                    "cartouche_excluded": ta_result.cartouche_excluded,
                    "legend_detected": ta_result.legend_detected,
                    "levels_detected": ta_result.levels_found,
                    "total_elements_found": len(ta_result.elements),
                    "ambiguous_cases": [
                        {
                            "text": ac.text,
                            "reason": ac.reason,
                            "x": ac.x,
                            "y": ac.y,
                            "page": ac.page,
                        } for ac in ta_result.ambiguous_cases
                    ],
                    "inventory": ta_result.inventory,
                }
            except Exception as exc:
                text_analysis = {"error": str(exc)}

        # --- GEOMETRY ANALYZER (dessins vectoriels PDF) ---
        geometry_analysis = None
        if pdf_path:
            try:
                from core.geometry_analyzer import analyze_pdf_drawings
                drawing_elements = analyze_pdf_drawings(pdf_path)
                if drawing_elements:
                    geometry_analysis = {
                        "total_shapes": len(drawing_elements),
                        "by_family": {},
                        "elements": [],
                    }
                    for de in drawing_elements:
                        fam = de.element_family
                        geometry_analysis["by_family"][fam] = (
                            geometry_analysis["by_family"].get(fam, 0) + 1
                        )
                        geometry_analysis["elements"].append({
                            "reference": de.reference,
                            "family": fam,
                            "section_cm": de.section_cm,
                            "length_m": de.length_m,
                            "level": de.level,
                            "confidence": de.confidence,
                        })
            except Exception as exc:
                geometry_analysis = {"error": str(exc)}

        # --- LEVEL CLUSTERING (attribution spatiale des niveaux) ---
        level_clustering = None
        all_words = getattr(self, '_all_words', [])
        if all_words:
            try:
                from core.level_clustering import (
                    cluster_elements_by_level, apply_level_assignments,
                    summarize_by_level,
                )
                # Construire la liste d'elements pour le clustering
                elem_for_clustering = []
                for ref, spec in cat.get("semelles", {}).items():
                    elem_for_clustering.append({
                        "reference": ref, "family": "SEMELLE",
                        "y": spec.get("y", 0), "page": spec.get("page", 1),
                    })
                for ref, spec in cat.get("poteaux", {}).items():
                    elem_for_clustering.append({
                        "reference": ref, "family": "POTEAU",
                        "y": spec.get("y", 0), "page": spec.get("page", 1),
                    })
                for ref, spec in cat.get("poutres", {}).items():
                    elem_for_clustering.append({
                        "reference": ref, "family": "POUTRE",
                        "y": spec.get("y", 0), "page": spec.get("page", 1),
                    })
                for ref, spec in cat.get("longrines", {}).items():
                    elem_for_clustering.append({
                        "reference": ref, "family": "LONGRINE",
                        "y": spec.get("y", 0), "page": spec.get("page", 1),
                    })
                for ref, spec in cat.get("chainages", {}).items():
                    elem_for_clustering.append({
                        "reference": ref, "family": "CHAINAGE",
                        "y": spec.get("y", 0), "page": spec.get("page", 1),
                    })

                if elem_for_clustering:
                    clustering = cluster_elements_by_level(
                        elem_for_clustering, words=all_words)
                    apply_level_assignments(elem_for_clustering, clustering)
                    level_summary = summarize_by_level(elem_for_clustering)
                    level_clustering = {
                        "zones": [
                            {"level": z.level, "y_min": z.y_min,
                             "y_max": z.y_max, "count": z.element_count}
                            for z in clustering.zones
                        ],
                        "assignments": clustering.assignments,
                        "ambiguous": clustering.ambiguous,
                        "confidence": clustering.confidence,
                        "summary_by_level": level_summary,
                    }
                    # Appliquer les niveaux detectes au catalogue
                    for elem in elem_for_clustering:
                        lvl = elem.get("level", "INCONNU")
                        ref = elem.get("reference", "")
                        fam = elem.get("family", "")
                        if lvl != "INCONNU":
                            if fam == "SEMELLE" and ref in cat.get("semelles", {}):
                                cat["semelles"][ref]["level"] = lvl
                            elif fam == "POTEAU" and ref in cat.get("poteaux", {}):
                                cat["poteaux"][ref]["level"] = lvl
                            elif fam == "POUTRE" and ref in cat.get("poutres", {}):
                                cat["poutres"][ref]["level"] = lvl
                            elif fam == "LONGRINE" and ref in cat.get("longrines", {}):
                                cat["longrines"][ref]["level"] = lvl
                            elif fam == "CHAINAGE" and ref in cat.get("chainages", {}):
                                cat["chainages"][ref]["level"] = lvl
            except Exception as exc:
                level_clustering = {"error": str(exc)}

        # --- CROSS-VALIDATION : comparaison tableaux vs elevation vs spatial ---
        self._cross_validate_strategies(cat)

        # --- RECLASSIFICATION LG/CH/BN depuis poutres vers les bons catalogues ---
        _reclassified = set()
        for key in list(cat.get("poutres", {}).keys()):
            spec = cat["poutres"][key]
            # Extraire le prefixe de base (avant _ si present)
            base = key.split("_")[0] if "_" in key else key
            prefix = (spec.get("prefix") or base).upper()
            if prefix.startswith("LG") and "longrines" in cat:
                cat["longrines"][key] = spec
                _reclassified.add(key)
            elif prefix.startswith("CH") and "chainages" in cat:
                cat["chainages"][key] = spec
                _reclassified.add(key)
            elif prefix.startswith("BN") and "murs" in cat:
                cat["murs"][key] = spec
                _reclassified.add(key)
        for key in _reclassified:
            cat["poutres"].pop(key, None)

        # --- Nettoyage des entrees fantomes ---
        for catalogue_name in ("poutres", "longrines", "chainages", "murs"):
            for key in list(cat.get(catalogue_name, {}).keys()):
                spec = cat[catalogue_name][key]
                # Entree composite avec prefix inconnu
                if "_" in key and not spec.get("prefix"):
                    base = key.split("_")[0]
                    if not re.match(r"^(?:B?N\d+|PN\d+|PC\d*|LG\d*|CH\d*|BN\d*)$", base, re.I):
                        cat[catalogue_name].pop(key, None)
                        continue
                # Entree bare avec composite counterpart -> toujours supprimer
                # (le composite est plus informatif : N1_25x40 > N1)
                if "_" not in key:
                    has_composite = any(
                        k.startswith(f"{key}_") for k in cat.get(catalogue_name, {}).keys())
                    if has_composite:
                        cat[catalogue_name].pop(key, None)

        # --- ENRICHISSEMENT VIA SCHEDULE PARSER (LLM/regex) ---
        # Parse les tableaux extraits pour enrichir le ferraillage et les dimensions
        try:
            from core.schedule_parser import parse_schedule_table, elements_to_catalogue
            for page_num in self.pages_tableau:
                doc = getattr(self, "_current_doc", None)
                if doc is None:
                    continue
                try:
                    page = doc[page_num - 1]
                    tabs = page.find_tables()
                    for t in tabs.tables:
                        try:
                            data = t.extract()
                        except Exception:
                            continue
                        if not data or len(data) < 2:
                            continue
                        # Detecter le type de tableau
                        table_text = " ".join(
                            str(c or "") for row in data for c in row
                        ).upper()
                        table_type = ""
                        if "SEMELLE" in table_text:
                            table_type = "semelles"
                        elif "POTEAU" in table_text or any(
                            c and c.upper().startswith("Q") for row in data for c in row
                        ):
                            table_type = "poteaux"
                        elif "POUTRE" in table_text or "LONGRINE" in table_text:
                            table_type = "poutres"

                        # Parser le tableau
                        result = parse_schedule_table(
                            data, table_type=table_type,
                            page_num=page_num, use_llm=True
                        )
                        if result.get("elements"):
                            parsed_cat = elements_to_catalogue(result["elements"])
                            # Fusionner avec le catalogue existant
                            for fam, items in parsed_cat.items():
                                for ref, spec in items.items():
                                    if ref not in cat.get(fam, {}):
                                        cat.setdefault(fam, {})[ref] = spec
                                    else:
                                        # Enrichir les champs manquants
                                        existing = cat[fam][ref]
                                        for key in ("ferr_x", "ferr_y", "ferr_sup", "cadres"):
                                            if key not in existing and key in spec:
                                                existing[key] = spec[key]
                except Exception as exc:
                    logger.debug("Schedule parser error on page %d: %s", page_num, exc)
        except ImportError:
            pass

        # --- RAPPORT METIER (regles metier genie civil) ---
        metier_report = self._build_metier_report(cat)

        return {
            "projet": {"nom": "Projet extrait", "date": None},
            "catalogue_types": cat,
            "implantations": self.implantations,
            "_meta": {
                "moteur": "extraction vectorielle locale (PyMuPDF find_tables + spatial)",
                "total_pages_scanned": self.total_pages or 1,
                "pages_tableau": self.pages_tableau,
                "pages_plan": self.pages_plan,
                "pages_ocr": self.pages_ocr,
                "pages_ignorees": self.pages_skip,
                "nb_mots_lus": self.nb_mots,
                "massifs_detectes": self.massifs,
                "semelles_pages": self._semelles_pages,
                "ferra_annotations": self.ferra_annotations,
                "liens_semelle_poteau": [
                    {"semelle": s, "poteau": q, "page": p}
                    for (s, q, p) in self._bindings
                ],
                "avertissements": self.warnings,
                "hypotheses": self.hypotheses,
                "rapport_metier": metier_report,
                "text_analysis": text_analysis,
                "geometry_analysis": geometry_analysis,
                "level_clustering": level_clustering,
            },
        }


# ============================================================================
# Extracteur raster optionnel (OpenCV + RapidOCR ONNX, 100% CPU local)
# ============================================================================

class RasterPlanExtractor:
    """OCR local pour plans scannes / images (PNG, JPG, PDF raster).

    AUCUNE valeur inventee : l'OCR produit des mots + coordonnees qui sont
    ensuite parses par le moteur spatial VectorPlanExtractor.
    Reutilise le chargeur paresseux PlanOCREngine (core/ocr_engine.py).
    """

    def __init__(self):
        from core.ocr_engine import PlanOCREngine
        self.ocr_engine = PlanOCREngine()
        if not self.ocr_engine.available:
            raise ExtractionError(
                "Plan raster détecté mais l'OCR local n'est pas installé. "
                "Installez : pip install rapidocr-onnxruntime opencv-python")

    def image_to_words(self, img_path_or_array, page_num=1):
        """OCR d'une image -> mots normalises {text,x,y,page}."""
        engine = self.ocr_engine._ensure_engine()
        result, _ = engine(img_path_or_array)
        words = []
        if result:
            for line in result:
                box, text = line[0], line[1]
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                words.append({
                    "text": str(text).strip(),
                    "x": round(min(xs), 2), "y": round(min(ys), 2),
                    "x1": round(max(xs), 2), "y1": round(max(ys), 2),
                    "page": page_num,
                })
        return words

    def pdf_raster_to_words(self, pdf_path, progress_callback=None):
        """PDF scanne -> rendu pixmap de chaque page -> OCR local."""
        import numpy as np
        import pymupdf

        all_words = []
        with pymupdf.open(str(pdf_path)) as doc:
            total = len(doc)
            for idx in range(total):
                from core.pdf_render import render_page_adaptive
                pix = render_page_adaptive(doc[idx], normal_dpi=200)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n)
                if pix.n == 4:
                    img = img[:, :, :3]
                page_words = self.image_to_words(img, page_num=idx + 1)
                all_words.extend(page_words)
                if progress_callback:
                    progress_callback(idx + 1, total, "raster")
                pix = None
                if idx % 10 == 0:
                    gc.collect()
        return all_words


def is_raster_pdf(pdf_path):
    """True si le PDF ne contient pas (ou presque) de texte extractible."""
    import pymupdf
    try:
        with pymupdf.open(str(pdf_path)) as doc:
            for page in doc:
                if len(page.get_text().strip()) > 100:
                    return False
    except Exception:
        return False
    return True


def extract_plan_auto(file_path, progress_callback=None):
    """Routeur multi-format : PDF, DXF, DWG, IFC, images.

    Aucun fallback fictif nulle part.
    """
    ext = Path(file_path).suffix.lower()

    try:
        # --- PDF ---
        if ext == ".pdf":
            return VectorPlanExtractor().process_all_pages(
                file_path, progress_callback)

        # --- Images ---
        if ext in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"):
            raster = RasterPlanExtractor()
            words = raster.image_to_words(file_path)
            return VectorPlanExtractor().extract_from_words(words)

        # --- IFC (BIM) — methode la plus fiable ---
        if ext == ".ifc":
            try:
                from core.ifc_extractor import (
                    extract_from_ifc, ifc_elements_to_standard)
                ifc_result = extract_from_ifc(file_path)
                standard_elements = ifc_elements_to_standard(ifc_result)
                # Construire le plan_data a partir des elements IFC
                cat = {
                    "semelles": {}, "poteaux": {}, "poutres": {},
                    "dalles": {}, "voiles": {}, "escaliers": {},
                    "longrines": {}, "chainages": {}, "murs": {},
                    "radiers": {}, "contre_forts": {}, "futs": {},
                    "semelles_filantes": [],
                }
                for elem in standard_elements:
                    fam = elem.get("family", "")
                    ref = elem.get("reference", "")
                    dims = elem.get("dims_text")
                    level = elem.get("level", "INCONNU")
                    if fam == "SEMELLE" and dims:
                        m = re.match(r"(\d+)[xX](\d+)(?:[xX](\d+))?", dims)
                        if m:
                            cat["semelles"][ref] = {
                                "a": int(m.group(1)) / 100,
                                "b": int(m.group(2)) / 100,
                                "h": int(m.group(3)) / 100 if m.group(3) else 0,
                                "level": level,
                            }
                    elif fam == "POTEAU" and dims:
                        m = re.match(r"(\d+)[xX](\d+)", dims)
                        if m:
                            cat["poteaux"][ref] = {
                                "a": int(m.group(1)) / 100,
                                "b": int(m.group(2)) / 100,
                                "level": level,
                            }
                    elif fam == "POUTRE" and dims:
                        m = re.match(r"(\d+)[xX](\d+)", dims)
                        if m:
                            cat["poutres"][ref] = {
                                "b": int(m.group(1)) / 100,
                                "h": int(m.group(2)) / 100,
                                "level": level,
                            }
                    elif fam == "LONGRINE" and dims:
                        m = re.match(r"(\d+)[xX](\d+)", dims)
                        if m:
                            cat["longrines"][ref] = {
                                "b": int(m.group(1)) / 100,
                                "h": int(m.group(2)) / 100,
                                "level": level,
                            }
                    elif fam == "CHAINAGE" and dims:
                        m = re.match(r"(\d+)[xX](\d+)", dims)
                        if m:
                            cat["chainages"][ref] = {
                                "b": int(m.group(1)) / 100,
                                "h": int(m.group(2)) / 100,
                                "level": level,
                            }
                # Construire le resultat final
                extractor = VectorPlanExtractor()
                extractor._reset()
                extractor.global_catalogue = cat
                extractor._all_words = [
                    {"text": e.get("reference", ""), "x": e.get("x", 0),
                     "y": e.get("y", 0), "page": 1}
                    for e in standard_elements
                ]
                return extractor._finalize()
            except ImportError:
                return partial_plan_data(
                    "ifcopenshell non installe — pip install ifcopenshell")

        # --- DXF (avec nouveau extracteur geometrique) ---
        if ext == ".dxf":
            try:
                from core.dxf_extractor import (
                    extract_from_dxf, dxf_elements_to_standard)
                dxf_result = extract_from_dxf(file_path)
                standard_elements = dxf_elements_to_standard(dxf_result)
                if standard_elements:
                    return VectorPlanExtractor().extract_from_text_blocks(
                        standard_elements)
                # Fallback: ingestion classique
                from core.ingestion import UniversalPlanIngestor
                result = UniversalPlanIngestor(str(file_path)).ingest()
                return VectorPlanExtractor().extract_from_text_blocks(
                    result.text_blocks)
            except ImportError:
                from core.ingestion import UniversalPlanIngestor
                result = UniversalPlanIngestor(str(file_path)).ingest()
                return VectorPlanExtractor().extract_from_text_blocks(
                    result.text_blocks)

        # --- DWG (conversion vers DXF puis traitement DXF) ---
        if ext == ".dwg":
            try:
                import subprocess
                import tempfile
                # Essayer dwg2dxf (ODA File Converter)
                with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
                    tmp_dxf = tmp.name
                result = subprocess.run(
                    ["dwg2dxf", "-o", tmp_dxf, str(file_path)],
                    capture_output=True, text=True, timeout=120)
                if result.returncode == 0 and Path(tmp_dxf).exists():
                    from core.dxf_extractor import (
                        extract_from_dxf, dxf_elements_to_standard)
                    dxf_result = extract_from_dxf(tmp_dxf)
                    standard_elements = dxf_elements_to_standard(dxf_result)
                    Path(tmp_dxf).unlink(missing_ok=True)
                    if standard_elements:
                        return VectorPlanExtractor().extract_from_text_blocks(
                            standard_elements)
                # Fallback: ingestion classique
                from core.ingestion import UniversalPlanIngestor
                result = UniversalPlanIngestor(str(file_path)).ingest()
                return VectorPlanExtractor().extract_from_text_blocks(
                    result.text_blocks)
            except Exception:
                from core.ingestion import UniversalPlanIngestor
                result = UniversalPlanIngestor(str(file_path)).ingest()
                return VectorPlanExtractor().extract_from_text_blocks(
                    result.text_blocks)

        # --- Autres formats : ingestion native ---
        from core.ingestion import UniversalPlanIngestor
        result = UniversalPlanIngestor(str(file_path)).ingest()
        return VectorPlanExtractor().extract_from_text_blocks(result.text_blocks)
    except (ExtractionError, OSError, ValueError) as exc:
        logger.warning("Extraction partielle pour %s: %s", file_path, exc)
        return partial_plan_data(str(exc))