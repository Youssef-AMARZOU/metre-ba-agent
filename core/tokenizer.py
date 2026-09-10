"""Tokenisation tolerance des etiquettes CAO/OCR composites — civil engineering.

Supporte TOUTES les families d'elements BA / genie civil :
  Semelles (S), Poteaux (P/Q), Poutres (N/BN/PN), Dalles (D),
  Voiles (V), Escaliers (ESC), Longrines (LG), Chainages (CH),
  Murs (M), Radiers (R), Contre-forts (CF), Futs (F),
  Pieux (PIE), Redresseurs (RD), Massifs (MS), Linteaux (LT),
  Semelles filantes (SF), Sablieres (SB), Couvertines (CV),
  Poutres d'appui (PA), Travers (TR), Voiles de terrain (VT),
  Dalles sur solives (DS), Voiles de rinage (VD).
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# ============================================================================
# PATTERNS — TOUTES les families de elements BA / genie civil
# ============================================================================

# Semelles : S1, S2, S12
_RE_SEM = r"S\d+"
# Poteaux : P1, Q1
_RE_POT = r"[PQ]\d+"
# Poutres : N1, BN1, PN1, N1BIS
_RE_POUT = r"(?:B?N\d+(?:BIS)?|PN\d+)"
# Dalles : D1, D2
_RE_DAL = r"D\d+"
# Voiles : V1, V2
_RE_VOL = r"V\d+"
# Escaliers : ESC1, ESC2
_RE_ESC = r"ESC\d+"
# Longrines : LG1, LG2
_RE_LG = r"LG\d+"
# Chainages : CH1, CH2
_RE_CH = r"CH\d+"
# Murs : M1, M2
_RE_MUR = r"M\d+"
# Radiers : R1, R2
_RE_RAD = r"R\d+"
# Contre-forts : CF1, CF2
_RE_CF = r"CF\d+"
# Futs : F1, F2
_RE_FUT = r"F\d+"
# Pieux : PIE1, PIE2
_RE_PIE = r"PIE\d+"
# Redresseurs : RD1, RD2
_RE_RD = r"RD\d+"
# Massifs : MS1, MS2
_RE_MS = r"MS\d+"
# Linteaux : LT1, LT2
_RE_LT = r"LT\d+"
# Semelles filantes : SF1, SF2
_RE_SF = r"SF\d+"
# Sablieres : SB1, SB2
_RE_SB = r"SB\d+"
# Couvertines : CV1, CV2
_RE_CV = r"CV\d+"
# Poutres d'appui : PA1, PA2
_RE_PA = r"PA\d+"
# Travers : TR1, TR2
_RE_TR = r"TR\d+"
# Voiles de terrain : VT1, VT2
_RE_VT = r"VT\d+"
# Dalles sur solives : DS1, DS2
_RE_DS = r"DS\d+"
# Voiles de rinage : VD1, VD2
_RE_VD = r"VD\d+"

# Pattern global : TOUS les types combines
_RE_ALL_LABELS = (
    rf"(?:{_RE_SEM}|{_RE_POT}|{_RE_POUT}|{_RE_DAL}|{_RE_VOL}|"
    rf"{_RE_ESC}|{_RE_LG}|{_RE_CH}|{_RE_MUR}|{_RE_RAD}|"
    rf"{_RE_CF}|{_RE_FUT}|{_RE_PIE}|{_RE_RD}|{_RE_MS}|"
    rf"{_RE_LT}|{_RE_SF}|{_RE_SB}|{_RE_CV}|{_RE_PA}|"
    rf"{_RE_TR}|{_RE_VT}|{_RE_DS}|{_RE_VD})"
)

# Labels composites (type + dims + armatures)
_LABEL = re.compile(
    rf"^(?:{_RE_SEM}|{_RE_POT}|{_RE_POUT}|{_RE_DAL}|{_RE_VOL}|"
    rf"{_RE_ESC}|{_RE_LG}|{_RE_CH}|{_RE_MUR}|{_RE_RAD}|"
    rf"{_RE_CF}|{_RE_FUT}|{_RE_PIE}|{_RE_RD}|{_RE_MS}|"
    rf"{_RE_LT}|{_RE_SF}|{_RE_SB}|{_RE_CV}|{_RE_PA}|"
    rf"{_RE_TR}|{_RE_VT}|{_RE_DS}|{_RE_VD})$",
    re.I,
)

_DIM = re.compile(r"^\(?[\d\.]{2,3}\s*[xX*]\s*[\d\.]{2,3}\)?$")
_REBAR = re.compile(
    r"^\d+\s*(?:HA|T|TOR|Ø|PHI)\s*\d+(?:\+\d+\s*(?:HA|T|TOR|Ø|PHI)\s*\d+)*$",
    re.I,
)
_SEMELLE_SPATIAL = re.compile(r"^S\d+\(\d+x\d+x\d+\)$", re.I)

# Deux labels separes par / ou - : S1/P1, D1-V1
_TWO_LABELS = re.compile(
    rf"^({_RE_ALL_LABELS})[/\-]({_RE_ALL_LABELS})$",
    re.I,
)

# Composite : label + (dims) + armatures
_COMPOSITE = re.compile(
    rf"^({_RE_ALL_LABELS})"
    rf"(\([\d\.]{{2,3}}\s*[xX*]\s*[\d\.]{{2,3}}(?:\s*[xX*]\s*[\d\.]{{2,3}})?\))?"
    r"((?:\d+\s*(?:HA|T|TOR|Ø|PHI)\s*\d+(?:\+\d+\s*(?:HA|T|TOR|Ø|PHI)\s*\d+)*)?)$",
    re.I,
)

# Reference pattern (pour decompose)
_REFERENCE = re.compile(
    rf"^(?P<reference>{_RE_ALL_LABELS})$",
    re.I,
)

# Dimensions pattern
_DIMENSIONS = re.compile(
    r"(?P<a>\d+(?:[.,]\d+)?)\s*[xX*]\s*"
    r"(?P<b>\d+(?:[.,]\d+)?)"
    r"(?:\s*[xX*]\s*(?P<h>\d+(?:[.,]\d+)?))?"
)

# Armatures pattern
_REINFORCEMENT = re.compile(
    r"(?P<nb>\d+)\s*(?P<kind>HA|T|TOR|Ø|PHI)\s*(?P<phi>\d{1,2})",
    re.I,
)


def _metres(value: str) -> float:
    """Convertit une valeur en metres (seuil 10 cm)."""
    number = float(value.replace(",", "."))
    return number / 100.0 if number > 10 else number


def tokenize_composite(text: object) -> list[str]:
    """Tokenise un mot OCR en sous-etiquettes."""
    value = str(text or "").strip()
    if not value:
        return []
    normalized = value.replace("\u00d7", "x").replace("*", "x")
    compact = normalized.replace(" ", "")

    two = _TWO_LABELS.match(compact)
    if two:
        return [two.group(1), two.group(2)]

    if _SEMELLE_SPATIAL.match(compact):
        return [compact]

    if _DIM.match(normalized) or _REBAR.match(normalized):
        return [normalized]

    composite = _COMPOSITE.match(compact)
    if composite and (composite.group(2) or composite.group(3)):
        res = [composite.group(1)]
        if composite.group(2):
            res.append(composite.group(2))
        if composite.group(3):
            res.append(composite.group(3))
        return res
    return [normalized]


def expand_words(words: Iterable[dict]) -> list[dict]:
    """Expand les mots composites pour l'extraction spatiale."""
    expanded = []
    for w in words:
        tokens = tokenize_composite(w.get("text", ""))
        if len(tokens) <= 1:
            expanded.append(w)
        else:
            for t in tokens:
                new_w = dict(w)
                new_w["text"] = t
                expanded.append(new_w)
    return expanded


def classify_element_family(label: str) -> str:
    """Classifie une etiquette en famille d'element — TOUTES les familles."""
    upper = label.upper()
    if upper.startswith("SF"):
        return "SEMELLE_FILANTE"
    if upper.startswith("S"):
        return "SEMELLE"
    if upper.startswith(("P", "Q")):
        return "POTEAU"
    if upper.startswith(("N", "BN", "PN")):
        return "POUTRE"
    if upper.startswith("D") and not upper.startswith("DS"):
        return "DALLE"
    if upper.startswith("DS"):
        return "DALLE_SOLIVE"
    if upper.startswith("V") and not upper.startswith("VD") and not upper.startswith("VT"):
        return "VOILE"
    if upper.startswith("VD"):
        return "VOILE_RINAGE"
    if upper.startswith("VT"):
        return "VOILE_TERRAIN"
    if upper.startswith("ESC"):
        return "ESCALIER"
    if upper.startswith("LG"):
        return "LONGRINE"
    if upper.startswith("CH"):
        return "CHAINAGE"
    if upper.startswith("M"):
        return "MUR"
    if upper.startswith("R") and not upper.startswith("RD"):
        return "RADIER"
    if upper.startswith("RD"):
        return "REDRESSEUR"
    if upper.startswith("MS"):
        return "MASSIF"
    if upper.startswith("LT"):
        return "LINTEAU"
    if upper.startswith("SB"):
        return "SABLIERE"
    if upper.startswith("CV"):
        return "COUVERTINE"
    if upper.startswith("PA"):
        return "POUTRE_APPUI"
    if upper.startswith("TR"):
        return "TRAVERS"
    if upper.startswith("CF"):
        return "CONTRE_FORT"
    if upper.startswith("F") and not upper.startswith("FS"):
        return "FUT"
    if upper.startswith("PIE"):
        return "PIEU"
    return "INCONNU"


def decompose_etiquette_technique(texte: Any) -> dict:
    """Decode universellement toute notation de plan BA / genie civil.

    Supporte TOUTES les conventions :
      FR: S1(90x90x25), P1(25x25), N2(25x30), RD1, MS1, LT1
      MA: Q1, (S1,Q1), 2CAD T6 e=15
      EN: F1(90x90x25), C1(25x25), B2(25x30)
      PT: L1(90x90x25), P1(25x25), V1(25x30)
    """
    txt = str(texte or "").strip()
    if not txt:
        return {"family": None, "reference": None}

    elements: dict[str, Any] = {"family": None, "reference": None}

    # 1. Detection de famille et reference — TOUTES les familles
    m_sem = re.search(r"(?:SEMELLE|SEML?)\s*(\d+)", txt, re.IGNORECASE)
    m_pot = re.search(r"(?:POTEAU|POT|COL(?:ONNE)?)\s*(\d+)", txt, re.IGNORECASE)
    m_pou = re.search(r"(?:POUTRE|POUT|BEAM)\s*(\d+)", txt, re.IGNORECASE)
    m_dal = re.search(r"(?:DALLE|SLAB)\s*(\d+)", txt, re.IGNORECASE)
    m_vol = re.search(r"(?:VOILE|WALL|SHEAR)\s*(\d+)", txt, re.IGNORECASE)
    m_esc = re.search(r"(?:ESCALIER|STAIR)\s*(\d+)", txt, re.IGNORECASE)
    m_rd = re.search(r"(?:REDRESSEUR|RELEVEL)\s*(\d+)", txt, re.IGNORECASE)
    m_ms = re.search(r"(?:MASSIF|MASS)\s*(\d+)", txt, re.IGNORECASE)
    m_lt = re.search(r"(?:LINTEAU|LINTEL)\s*(\d+)", txt, re.IGNORECASE)
    m_sf = re.search(r"(?:SEMELLE\s*FILANTE|SF)\s*(\d+)", txt, re.IGNORECASE)
    m_sb = re.search(r"(?:SABLIERE|SILL)\s*(\d+)", txt, re.IGNORECASE)
    m_cv = re.search(r"(?:COUVERTINE|COPING)\s*(\d+)", txt, re.IGNORECASE)
    m_pa = re.search(r"(?:POUTRE\s*APPUI|PA)\s*(\d+)", txt, re.IGNORECASE)
    m_tr = re.search(r"(?:TRAVERS|TR)\s*(\d+)", txt, re.IGNORECASE)
    m_cf = re.search(r"(?:CONTRE\s*FORT|CF)\s*(\d+)", txt, re.IGNORECASE)
    m_fut = re.search(r"(?:FUT|SHAFT)\s*(\d+)", txt, re.IGNORECASE)
    m_pie = re.search(r"(?:PIEU|PILE)\s*(\d+)", txt, re.IGNORECASE)

    # Labels courts : S1, P1, Q1, N1, D1, V1, RD1, MS1, LT1, etc.
    if not m_sem and not m_pot and not m_pou:
        m_sem = re.search(r"\b(SF?\d+)\b", txt, re.IGNORECASE)
    if not m_pot:
        m_pot = re.search(r"\b([PQ]\d+)\b", txt, re.IGNORECASE)
    if not m_pou:
        m_pou = re.search(r"\b(B?N\d+(?:BIS)?|PN\d+|PA\d+|TR\d*)\b", txt, re.IGNORECASE)
    if not m_dal:
        m_dal = re.search(r"\b(DS?\d+)\b", txt, re.IGNORECASE)
    if not m_vol:
        m_vol = re.search(r"\b(V[DT]?\d+)\b", txt, re.IGNORECASE)
    if not m_esc:
        m_esc = re.search(r"\b(ESC\d+)\b", txt, re.IGNORECASE)
    if not m_rd:
        m_rd = re.search(r"\b(RD\d+)\b", txt, re.IGNORECASE)
    if not m_ms:
        m_ms = re.search(r"\b(MS\d+)\b", txt, re.IGNORECASE)
    if not m_lt:
        m_lt = re.search(r"\b(LT\d+)\b", txt, re.IGNORECASE)
    if not m_sb:
        m_sb = re.search(r"\b(SB\d+)\b", txt, re.IGNORECASE)
    if not m_cv:
        m_cv = re.search(r"\b(CV\d+)\b", txt, re.IGNORECASE)
    if not m_cf:
        m_cf = re.search(r"\b(CF\d+)\b", txt, re.IGNORECASE)
    if not m_fut:
        m_fut = re.search(r"\b(F\d+)\b", txt, re.IGNORECASE)
    if not m_pie:
        m_pie = re.search(r"\b(PIE\d+)\b", txt, re.IGNORECASE)

    # Priorite : semelle > poteau > poutre > dalle > voile > escalier > autres
    if m_sem:
        ref = m_sem.group(1).upper() if m_sem.lastindex else m_sem.group(0).upper()
        elements["family"] = "SEMELLE_FILANTE" if ref.startswith("SF") else "SEMELLE"
        elements["reference"] = ref
        elements["semelle"] = ref
    elif m_pot:
        ref = m_pot.group(1).upper() if m_pot.lastindex else m_pot.group(0).upper()
        elements["family"] = "POTEAU"
        elements["reference"] = ref
        elements["poteau"] = ref
    elif m_pou:
        ref = m_pou.group(1).upper() if m_pou.lastindex else m_pou.group(0).upper()
        elements["family"] = "POUTRE_APPUI" if ref.startswith("PA") else (
            "TRAVERS" if ref.startswith("TR") else "POUTRE")
        elements["reference"] = ref
        elements["poutre"] = ref
    elif m_dal:
        ref = m_dal.group(1).upper() if m_dal.lastindex else m_dal.group(0).upper()
        elements["family"] = "DALLE_SOLIVE" if ref.startswith("DS") else "DALLE"
        elements["reference"] = ref
        elements["dalle"] = ref
    elif m_vol:
        ref = m_vol.group(1).upper() if m_vol.lastindex else m_vol.group(0).upper()
        elements["family"] = "VOILE_RINAGE" if ref.startswith("VD") else (
            "VOILE_TERRAIN" if ref.startswith("VT") else "VOILE")
        elements["reference"] = ref
        elements["voile"] = ref
    elif m_esc:
        ref = m_esc.group(1).upper() if m_esc.lastindex else m_esc.group(0).upper()
        elements["family"] = "ESCALIER"
        elements["reference"] = ref
        elements["escalier"] = ref
    elif m_rd:
        ref = m_rd.group(1).upper() if m_rd.lastindex else m_rd.group(0).upper()
        elements["family"] = "REDRESSEUR"
        elements["reference"] = ref
    elif m_ms:
        ref = m_ms.group(1).upper() if m_ms.lastindex else m_ms.group(0).upper()
        elements["family"] = "MASSIF"
        elements["reference"] = ref
    elif m_lt:
        ref = m_lt.group(1).upper() if m_lt.lastindex else m_lt.group(0).upper()
        elements["family"] = "LINTEAU"
        elements["reference"] = ref
    elif m_sb:
        ref = m_sb.group(1).upper() if m_sb.lastindex else m_sb.group(0).upper()
        elements["family"] = "SABLIERE"
        elements["reference"] = ref
    elif m_cv:
        ref = m_cv.group(1).upper() if m_cv.lastindex else m_cv.group(0).upper()
        elements["family"] = "COUVERTINE"
        elements["reference"] = ref
    elif m_cf:
        ref = m_cf.group(1).upper() if m_cf.lastindex else m_cf.group(0).upper()
        elements["family"] = "CONTRE_FORT"
        elements["reference"] = ref
    elif m_fut:
        ref = m_fut.group(1).upper() if m_fut.lastindex else m_fut.group(0).upper()
        elements["family"] = "FUT"
        elements["reference"] = ref
    elif m_pie:
        ref = m_pie.group(1).upper() if m_pie.lastindex else m_pie.group(0).upper()
        elements["family"] = "PIEU"
        elements["reference"] = ref

    # Labels courts : S1, P1, Q1, N1, D1, V1, etc.
    if not m_sem and not m_pot and not m_pou:
        m_sem = re.search(r"\b(S\d+)\b", txt, re.IGNORECASE)
    if not m_pot:
        m_pot = re.search(r"\b([PQ]\d+)\b", txt, re.IGNORECASE)
    if not m_pou:
        m_pou = re.search(
            r"\b(B?N\d+(?:BIS)?|PN\d+|LG\d+|CH\d+|BT\d+|DB\d*)\b",
            txt, re.IGNORECASE,
        )
    if not m_dal:
        m_dal = re.search(r"\b(D\d+)\b", txt, re.IGNORECASE)
    if not m_vol:
        m_vol = re.search(r"\b(V\d+)\b", txt, re.IGNORECASE)
    if not m_esc:
        m_esc = re.search(r"\b(ESC\d*)\b", txt, re.IGNORECASE)

    # Bare N-(dims): N-(25X45)ALL
    if not m_pou and not elements["family"]:
        m_bare_n = re.search(r"\b(N)\s*[-–]\s*\(", txt, re.IGNORECASE)
        if m_bare_n:
            elements["family"] = "POUTRE"
            elements["reference"] = "N"
            elements["poutre"] = "N"

    # R1, M1 in short labels
    m_rad = None if elements["family"] else re.search(r"\b(R\d+)\b", txt, re.IGNORECASE)
    m_mur = None if elements["family"] else re.search(r"\b(M\d+)\b", txt, re.IGNORECASE)

    # Priorite : semelle > poteau > poutre > dalle > voile > escalier
    if m_sem:
        ref = m_sem.group(1).upper() if m_sem.lastindex else m_sem.group(0).upper()
        elements["family"] = "SEMELLE"
        elements["reference"] = ref
        elements["semelle"] = ref
    elif m_pot:
        ref = m_pot.group(1).upper() if m_pot.lastindex else m_pot.group(0).upper()
        elements["family"] = "POTEAU"
        elements["reference"] = ref
        elements["poteau"] = ref
    elif m_pou:
        ref = m_pou.group(1).upper() if m_pou.lastindex else m_pou.group(0).upper()
        elements["family"] = "POUTRE"
        elements["reference"] = ref
        elements["poutre"] = ref
    elif m_dal:
        ref = m_dal.group(1).upper() if m_dal.lastindex else m_dal.group(0).upper()
        elements["family"] = "DALLE"
        elements["reference"] = ref
        elements["dalle"] = ref
    elif m_vol:
        ref = m_vol.group(1).upper() if m_vol.lastindex else m_vol.group(0).upper()
        elements["family"] = "VOILE"
        elements["reference"] = ref
        elements["voile"] = ref
    elif m_esc:
        ref = m_esc.group(1).upper() if m_esc.lastindex else m_esc.group(0).upper()
        elements["family"] = "ESCALIER"
        elements["reference"] = ref
        elements["escalier"] = ref
    elif m_rad:
        ref = m_rad.group(1).upper() if m_rad.lastindex else m_rad.group(0).upper()
        elements["family"] = "RADIER"
        elements["reference"] = ref
    elif m_mur:
        ref = m_mur.group(1).upper() if m_mur.lastindex else m_mur.group(0).upper()
        elements["family"] = "MUR"
        elements["reference"] = ref

    # Bare prefix + dims: LG-(25X35), BN-(25X30), CH-(40X20), PR-(25X40), N-(25X45)
    m_bare_pou = re.search(r"\b(BN|LG|CH|PR|LT|PA)\s*[-–]\s*\(", txt, re.IGNORECASE)
    if m_bare_pou and not elements["family"]:
        prefix = m_bare_pou.group(1).upper()
        if prefix == "LG":
            elements["family"] = "LONGRINE"
            elements["reference"] = prefix
        elif prefix == "BN":
            elements["family"] = "BANDE_NOYEE"
            elements["reference"] = prefix
        elif prefix == "CH":
            elements["family"] = "CHAINAGE"
            elements["reference"] = prefix
        elif prefix == "PR":
            elements["family"] = "POUTRE_REDOUBLANTE"
            elements["reference"] = prefix
        elif prefix == "LT":
            elements["family"] = "LINTEAU"
            elements["reference"] = prefix
        elif prefix == "PA":
            elements["family"] = "POUTRE_APPUI"
            elements["reference"] = prefix

    # Bare prefix + dims without parens: LG-25x35-, BN-25x20-
    m_bare_pou2 = re.search(r"\b(BN|LG|CH|PR|LT|PA)\s*[-–]\s*\d+\s*[xX*]\s*\d+", txt, re.IGNORECASE)
    if m_bare_pou2 and not elements["family"]:
        prefix = m_bare_pou2.group(1).upper()
        if prefix == "LG":
            elements["family"] = "LONGRINE"
            elements["reference"] = prefix
        elif prefix == "BN":
            elements["family"] = "BANDE_NOYEE"
            elements["reference"] = prefix
        elif prefix == "CH":
            elements["family"] = "CHAINAGE"
            elements["reference"] = prefix
        elif prefix == "PR":
            elements["family"] = "POUTRE_REDOUBLANTE"
            elements["reference"] = prefix

    # 2. Dimensions
    dim_match = _DIMENSIONS.search(txt)
    if dim_match:
        try:
            a = float(dim_match.group("a").replace(",", "."))
            b = float(dim_match.group("b").replace(",", "."))
            h_raw = dim_match.group("h")
            h = float(h_raw.replace(",", ".")) if h_raw else None
            elements["dimensions"] = {
                "a": _metres(dim_match.group("a")),
                "b": _metres(dim_match.group("b")),
                "h": _metres(h_raw) if h_raw else None,
            }
        except (ValueError, AttributeError):
            pass

    # 3. Armatures
    rebar_match = _REINFORCEMENT.search(txt)
    if rebar_match:
        try:
            nb = int(rebar_match.group("nb")) if rebar_match.group("nb") else 1
            phi = int(rebar_match.group("phi")) if rebar_match.group("phi") else None
            if phi and 5 <= phi <= 40:
                ferr = {"nb": nb, "phi": phi}
                elements["ferr_x"] = ferr
                elements["acier"] = ferr
        except (ValueError, TypeError):
            pass

    return elements
