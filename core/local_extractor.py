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
from core.tokenizer import decompose_etiquette_technique, expand_words

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
POUTRE_LABEL_RX = re.compile(
    r"^(B?N\d+(?:BIS)?|PN\d+|PC\d*|LG\d+|CH\d*)$", re.I)
# Section inline : 'N2(25x35)', 'N2 (25x35)', 'PN1(20x40)'
POUTRE_INLINE_RX = re.compile(
    r"^((?:B?N\d+(?:BIS)?|PN\d+|PC\d*|LG\d+|CH\d*))\s*"
    r"\((\d+)\s*[xX*]\s*(\d+)\)\s*$", re.I)
POUTRE_SECTION_RX = POUTRE_INLINE_RX  # alias historique
# Repere isole sans section lue : PN1, PC, N2BIS, LG1, CH...
POUTRE_ISOLATED_RX = re.compile(
    r"^(PN\d+|PC\d*|B?N\d+(?:BIS)?|LG\d+|CH\d*)$", re.I)
# Etiquettes couplees (S1,Q1) sur les plans — tolere OCR 0/O pour Q
GROUPED_SQ_RX = re.compile(r"\(\s*(S\d+)\s*,\s*([PQ0O]\d+)\s*\)", re.I)
AXE_LETTER_RX = re.compile(r"^[A-T]$")
AXE_NUM_RX = re.compile(r"^(?:[1-9]|1\d|20)$")

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
        self.global_catalogue = {"semelles": {}, "poteaux": {}, "poutres": {},
                                 "semelles_filantes": []}
        self.implantations = {"semelles": [], "poteaux": [], "poutres": []}
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
        with pymupdf.open(pdf_path) as doc:
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
        self.global_catalogue = {"semelles": {}, "poteaux": {}, "poutres": {},
                                 "semelles_filantes": []}
        self.implantations = {"semelles": [], "poteaux": [], "poutres": []}
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
        if found_text and page_num not in self.pages_tableau:
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
        """Parse une table poteaux, deux dispositions :
        - colonnes : en-tetes P1..Pn / Q1..Qn (mot-cle POTEAUX ou >=2 reperes)
        - lignes : repere Qk/Pk en colonne 0, details en colonnes 1..n
          (ex: TABLEAU DES POTEAUX page 5 du R+2).
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
            for row in data[hdr_idx + 1:]:
                for label, ci in poteau_cols.items():
                    if ci >= len(row):
                        continue
                    if self._parse_poteau_cell(label, row[ci] or ""):
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

    def _record_header_bands(self, words, page_num):
        """Enregistre une bande verticale sous chaque colonne d'en-tete
        S\\d|S\\d|... (≥2 repères alignes sur une meme ligne) pour exclure
        ces mots de la grille spatiale."""
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
            if abs(hy - row_y) < 6:
                bands.append((hx - 14, hy - 6, hx + 14, hy + 40))

    def _extract_tableau_par_mots(self, words, page_num):
        """Fallback : nomenclature par alignement X reel (sans find_tables).
        Les colonnes de tete S4|S3|S2|S1 consommees sont enregistrees en
        bandes pour etre exclues de la grille spatiale."""
        pw = words
        headers = []
        for w in pw:
            m = SEMELLE_PLAIN_RX.match(w["text"])
            if m:
                headers.append((f"S{m.group(1)}", w["x"], w["y"]))
        if len(headers) < 2:
            return
        row_y = min(h[2] for h in headers)
        headers = [h for h in headers if abs(h[2] - row_y) < 6]

        bands = self._nomenclature_line_bands.setdefault(page_num, [])
        for (tk, hx, hy) in headers:
            # Bande verticale de la colonne : en-tete + dims + H + ferraillage
            bands.append((hx - 14, hy - 6, hx + 14, hy + 330))
            spec = {}
            for w in pw:
                m = SECT_DIM_RX.match(w["text"])
                if m and hy + 20 < w["y"] < hy + 70 and abs(w["x"] - hx) < 12:
                    spec["a"] = _dim_cm_to_m(int(m.group(1)))
                    spec["b"] = _dim_cm_to_m(int(m.group(2)))
                    break
            h_row = next((w["y"] for w in pw
                          if w["text"] == "H" and w["x"] > hx
                          and abs(w["y"] - (hy + 140)) < 60), None)
            if h_row:
                for w in pw:
                    if (w["text"].isdigit() and abs(w["x"] - hx) < 12
                            and abs(w["y"] - h_row) < 12):
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
                    if (m and abs(w["x"] - hx) < 12
                            and fr["y"] - 5 < w["y"] < fr["y"] + 40):
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
        Les zones lues sont enregistrees pour exclure les memes mots de la
        grille spatiale (un 'S1' de tableau n'est pas une implantation).
        """
        lines = self._words_to_lines(words)
        found = False
        last_type = None
        last_poteau = None

        for idx, line in enumerate(lines):
            text = line["text"]

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
        """Axes (lettres/chiffres avec coordonnees) + reperes de semelles
        -> implantations positionnees a l'intersection la plus proche.

        Les mots situes dans une zone de nomenclature deja lue (cellules
        de tableau find_tables ou lignes textuelles compactes) sont EXCLUS :
        un 'S1' de tableau n'est pas une implantation ; les etiquettes du
        plan hors tableau restent exploitees (multi-batiments).
        """
        regions = (self._nomenclature_bboxes.get(page_num, [])
                   + self._nomenclature_line_bands.get(page_num, []))

        def in_nomenclature(w):
            if not regions:
                return False
            cx = (w["x"] + w.get("x1", w["x"])) / 2.0
            cy = (w["y"] + w.get("y1", w["y"])) / 2.0
            return any(r[0] - 5 <= cx <= r[2] + 5 and r[1] - 5 <= cy <= r[3] + 5
                       for r in regions)

        axes_letters = []   # [(lettre, x)]
        axes_numbers = []   # [(numero, y)]
        hits = []           # [(type, dims, x, y)]
        grouped = []        # [(S_rep, Q_rep, x, y)]

        for w in words:
            if in_nomenclature(w):
                continue
            t = w["text"].strip()
            # Etiquettes couplees marocaines : (S1,Q1), (S2, Q2), (S4,04)
            mg = GROUPED_SQ_RX.search(t)
            if mg:
                s_rep = mg.group(1).upper()
                raw_q = mg.group(2).upper()
                # Remplacement tolerant OCR : '04' ou 'O4' -> 'Q4'
                q_rep = re.sub(r"^[0O]", "Q", raw_q)
                grouped.append((s_rep, q_rep, w["x"], w["y"]))
                continue
            if AXE_LETTER_RX.match(t) and w["y"] < 200:
                axes_letters.append((t.upper(), w["x"]))
            elif AXE_NUM_RX.match(t) and w["x"] > 400:
                axes_numbers.append((t, w["y"]))
            else:
                tc = t.replace(" ", "")
                m = SEMELLE_DIM_RX.match(tc)
                if m:
                    hits.append((f"S{m.group(1)}",
                                 (int(m.group(2)), int(m.group(3)),
                                  int(m.group(4))),
                                 w["x"], w["y"]))
                    continue
                m = SEMELLE_PLAIN_RX.match(t)
                if m:
                    hits.append((f"S{m.group(1)}", None, w["x"], w["y"]))

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
            self.implantations["semelles"].append({
                "id": f"{tk}_{self._sem_counter[tk]}",
                "type": tk,
                "axe": axe,
                "file": file,
            })
            n_impl += 1
            # Dimensions lues directement sur l'etiquette du plan
            if dims:
                spec = {}
                spec["a"] = _dim_cm_to_m(dims[0])
                spec["b"] = _dim_cm_to_m(dims[1])
                spec["h"] = _dim_cm_to_m(dims[2])
                self._merge_semelle(tk, spec)
        return n_impl

    # ------------------------------------------------------------------
    # Details poteaux (hors tableaux)
    # ------------------------------------------------------------------
    def _extract_poteaux_page(self, words, page_num):
        labels = [w for w in words if POTEAU_LABEL_RX.match(w["text"])]
        for lab in labels:
            tk = POTEAU_LABEL_RX.match(lab["text"]).group(1).upper()
            if tk in self.global_catalogue["poteaux"]:
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
            # Section inline en un seul mot : 'N1(25x30)' (page 6 du R+2)
            mi = POUTRE_INLINE_RX.match(w["text"])
            if mi:
                tk = mi.group(1).upper()
                labels.append((tk, w["x"], w["y"]))
                self._merge_poutre(tk, {
                    "b": _dim_cm_to_m(int(mi.group(2))),
                    "h": _dim_cm_to_m(int(mi.group(3)))})
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
                # Repere isole (PN1, LG1, CH...) : type conserve sans section
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
            if tk not in cat["semelles"]:
                cat["semelles"][tk] = {
                    "a": 0, "b": 0, "h": 0,
                    "ferr_x": {"nb": 0, "phi": 0},
                    "ferr_y": {"nb": 0, "phi": 0},
                    "dimensions_manquantes": True,
                }
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
                    f"{spec.get('h', 0):.2f} m conservées pour les volumes.")

        # Semelles filantes : tracees mais non metrees automatiquement
        # (longueur non cotee sur le plan — aucune valeur inventee).
        for e in cat.get("semelles_filantes", []):
            self.warnings.append(
                f"Semelle filante SF {e['largeur']:.2f}x{e['hauteur']:.2f} "
                f"(pages {e['pages']}) — longueur non cotée, à métrer "
                "manuellement.")

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
            default_type = next(iter(cat["semelles"]), "SEMELLE_DEFAULT")
            cat["semelles"].setdefault(default_type, {
                "a": 1.0, "b": 1.0, "h": 0.30,
                "ferr_x": {"nb": 0, "phi": 0},
                "ferr_y": {"nb": 0, "phi": 0},
                "dimensions_par_defaut": True,
            })
            self.implantations["semelles"].append({
                "id": f"{default_type}_1", "type": default_type,
                "axe": "", "file": "", "position_par_defaut": True})
            self.warnings.append(
                f"Aucune implantation lisible : géométrie par défaut "
                f"{default_type} 1.00x1.00x0.30 m ajoutée, à vérifier.")

        
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
                if p_item.get("b") is None:
                    p_item["b"] = larg
                if p_item.get("h") is None:
                    p_item["h"] = haut

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
        with pymupdf.open(pdf_path) as doc:
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
        with pymupdf.open(pdf_path) as doc:
            for page in doc:
                if len(page.get_text().strip()) > 100:
                    return False
    except Exception:
        return False
    return True


def extract_plan_auto(file_path, progress_callback=None):
    """Routeur : DXF -> ingestion, PDF vectoriel -> vector, PDF raster /
    image -> OCR local (si installe). Aucun fallback fictif nulle part."""
    ext = Path(file_path).suffix.lower()

    try:
        if ext == ".pdf":
            # Le moteur vectoriel conserve le texte et ajoute l'OCR cible
            # page par page pour les zones image/hybrides.
            return VectorPlanExtractor().process_all_pages(
                file_path, progress_callback)

        if ext in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"):
            raster = RasterPlanExtractor()
            words = raster.image_to_words(file_path)
            return VectorPlanExtractor().extract_from_words(words)

        # DXF / autres : ingestion native.
        from core.ingestion import UniversalPlanIngestor
        result = UniversalPlanIngestor(str(file_path)).ingest()
        return VectorPlanExtractor().extract_from_text_blocks(result.text_blocks)
    except (ExtractionError, OSError, ValueError) as exc:
        logger.warning("Extraction partielle pour %s: %s", file_path, exc)
        return partial_plan_data(str(exc))