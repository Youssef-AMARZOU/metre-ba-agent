"""Tokenisation tolerance des etiquettes CAO/OCR composites — civil engineering.

Supporte toutes les families d'elements BA :
  Semelles (S), Poteaux (P/Q), Poutres (N/BN/PN/LG/CH), Dalles (D),
  Voiles (V), Escaliers (ESC), Longrines (LG), Chainages (CH),
  Murs (M), Radiers (R), Contre-forts (CF), Futs (F),
  Pieux (PIE), Voiles deitenage (VD), Dalles sur.solives (DS).
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# ============================================================================
# PATTERNS — toutes les families de elements BA
# ============================================================================

# Semelles : S1, S2, S12, SEM-1, SEMELLE 1
_RE_SEM = r"S\d+(?:\w)*"
# Poteaux : P1, Q1, PQ1, POT-1, POTEAU 1, COL-1
_RE_POT = r"[PQ]\d+"
# Poutres : N1, BN1, PN1, N1BIS, POUTRE 1, BT1, DB1
_RE_POUT = r"(?:B?N\d+(?:BIS)?|PN\d+|LG\d+|CH\d*|BT\d+|DB\d+)"
# Dalles : D1, DAL-1, DALLE 1
_RE_DAL = r"D\d+"
# Voiles : V1, VOL-1, VOILE 1
_RE_VOL = r"V\d+"
# Escaliers : ESC1, ESC-1, ESCALIER 1
_RE_ESC = r"ESC\d+"
# Longrines : LG1, LONG-1
_RE_LG = r"LG\d+"
# Chainages : CH1, CHA-1
_RE_CH = r"CH\d+"
# Murs : M1, MUR-1
_RE_MUR = r"M\d+"
# Radiers : R1, RAD-1
_RE_RAD = r"R\d+"
# Contre-forts : CF1, COT-1
_RE_CF = r"CF\d+"
# Futs : F1, FUT-1
_RE_FUT = r"F\d+"
# Pieux : PIE1, PIEU-1
_RE_PIE = r"PIE\d+"
# Voiles deitenage : VD1
_RE_VD = r"VD\d+"
# Dalles sur solives : DS1
_RE_DS = r"DS\d+"

# Pattern global : tous les types combines
_RE_ALL_LABELS = (
    rf"(?:{_RE_SEM}|{_RE_POT}|{_RE_POUT}|{_RE_DAL}|{_RE_VOL}|"
    rf"{_RE_ESC}|{_RE_LG}|{_RE_CH}|{_RE_MUR}|{_RE_RAD}|"
    rf"{_RE_CF}|{_RE_FUT}|{_RE_PIE}|{_RE_VD}|{_RE_DS})"
)

# Labels composites (type + dims + armatures)
_LABEL = re.compile(
    rf"^(?:{_RE_SEM}|{_RE_POT}|{_RE_POUT}|{_RE_DAL}|{_RE_VOL}|"
    rf"{_RE_ESC}|{_RE_LG}|{_RE_CH}|{_RE_MUR}|{_RE_RAD}|"
    rf"{_RE_CF}|{_RE_FUT}|{_RE_PIE}|{_RE_VD}|{_RE_DS})$",
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
    """Classifie une etiquette en famille d'element."""
    upper = label.upper()
    if upper.startswith("S"):
        return "SEMELLE"
    if upper.startswith(("P", "Q")):
        return "POTEAU"
    if upper.startswith(("N", "BN", "PN", "BT", "DB")):
        return "POUTRE"
    if upper.startswith("D"):
        return "DALLE"
    if upper.startswith("V") and not upper.startswith("VD"):
        return "VOILE"
    if upper.startswith("VD"):
        return "VOILE_DENAGE"
    if upper.startswith("ESC"):
        return "ESCALIER"
    if upper.startswith("LG"):
        return "LONGRINE"
    if upper.startswith("CH"):
        return "CHAINAGE"
    if upper.startswith("M"):
        return "MUR"
    if upper.startswith("R"):
        return "RADIER"
    if upper.startswith("CF"):
        return "CONTRE_FORT"
    if upper.startswith("F"):
        return "FUT"
    if upper.startswith("PIE"):
        return "PIEU"
    if upper.startswith("DS"):
        return "DALLE_SOLIVE"
    return "INCONNU"


def decompose_etiquette_technique(texte: Any) -> dict:
    """Decode universellement toute notation de plan BA.

    Supporte toutes les conventions :
      FR: S1(90x90x25), P1(25x25), N2(25x30)
      MA: Q1, (S1,Q1), 2CAD T6 e=15
      EN: F1(90x90x25), C1(25x25), B2(25x30)
      PT: L1(90x90x25), P1(25x25), V1(25x30)
    """
    txt = str(texte or "").strip()
    if not txt:
        return {"family": None, "reference": None}

    elements: dict[str, Any] = {"family": None, "reference": None}

    # 1. Detection de famille et reference
    m_sem = re.search(r"(?:SEMELLE|SEML?)\s*(\d+)", txt, re.IGNORECASE)
    m_pot = re.search(r"(?:POTEAU|POT|COL(?:ONNE)?)\s*(\d+)", txt, re.IGNORECASE)
    m_pou = re.search(
        r"(?:POUTRE|POUT|BEAM)\s*(\d+)", txt, re.IGNORECASE
    )
    m_dal = re.search(r"(?:DALLE|SLAB)\s*(\d+)", txt, re.IGNORECASE)
    m_vol = re.search(r"(?:VOILE|WALL|SHEAR)\s*(\d+)", txt, re.IGNORECASE)
    m_esc = re.search(r"(?:ESCALIER|STAIR)\s*(\d+)", txt, re.IGNORECASE)

    # Labels courts : S1, P1, Q1, N1, D1, V1, etc.
    if not m_sem and not m_pot and not m_pou:
        m_sem = re.search(r"\b(S\d+)\b", txt, re.IGNORECASE)
    if not m_pot:
        m_pot = re.search(r"\b([PQ]\d+)\b", txt, re.IGNORECASE)
    if not m_pou:
        m_pou = re.search(
            r"\b(B?N\d+(?:BIS)?|PN\d+|LG\d+|CH\d*|BT\d+|DB\d*)\b",
            txt, re.IGNORECASE,
        )
    if not m_dal:
        m_dal = re.search(r"\b(D\d+)\b", txt, re.IGNORECASE)
    if not m_vol:
        m_vol = re.search(r"\b(V\d+)\b", txt, re.IGNORECASE)
    if not m_esc:
        m_esc = re.search(r"\b(ESC\d+)\b", txt, re.IGNORECASE)

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
