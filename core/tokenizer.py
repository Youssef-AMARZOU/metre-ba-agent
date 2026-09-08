"""Tokenisation tolérante des étiquettes CAO/OCR composites."""
from __future__ import annotations

import re
from typing import Iterable

_LABEL = re.compile(r"^(S\d+|[PQ]\d+|(?:B?N\d+(?:BIS)?|PN\d+|LG\d+|CH\d*))$", re.I)
_DIM = re.compile(r"^\(?\d{2,3}\s*[xX*]\s*\d{2,3}\)?$")
_REBAR = re.compile(r"^\d+\s*(?:HA|T)\s*\d+(?:\+\d+\s*(?:HA|T)\s*\d+)*$", re.I)
_COMPOSITE = re.compile(
    r"^((?:S\d+|[PQ]\d+|(?:B?N\d+(?:BIS)?|PN\d+|LG\d+|CH\d*)))"
    r"(\(\d{2,3}\s*[xX*]\s*\d{2,3}(?:\s*[xX*]\s*\d{2,3})?\))?"
    r"((?:\d+\s*(?:HA|T)\s*\d+(?:\+\d+\s*(?:HA|T)\s*\d+)*)?)$",
    re.I,
)


def tokenize_composite(text: object) -> list[str]:
    """Retourne les sous-étiquettes d'un mot OCR sans perdre sa forme utile."""
    value = str(text or "").strip()
    if not value:
        return []
    normalized = value.replace("×", "x").replace("*", "x")
    if _DIM.match(normalized) or _REBAR.match(normalized):
        return [normalized]
    compact = normalized.replace(" ", "")
    # Multi-labels "S1/P1", "S2-P1", "S1,Q1" : eclater uniquement si
    # TOUTES les parties sont des reperes (garde les notations
    # arithmetiques type "25-4x15-85" intactes).
    for sep_rx in (r"[/,;]+", r"(?<=\d)-(?=[A-Za-z])|(?<=[A-Za-z])-(?=\d)"):
        parts = [p for p in re.split(sep_rx, compact) if p]
        if len(parts) > 1 and all(_LABEL.match(p) for p in parts):
            return [p.upper() for p in parts]
    composite = _COMPOSITE.match(compact)
    if composite and (composite.group(2) or composite.group(3)):
        # Semelle + triplet (S1(90x90x25)) : garder entier, pas de split.
        dim_part = (composite.group(2) or "").lower()
        if composite.group(1).upper().startswith("S") and \
                dim_part.count("x") >= 2:
            return [normalized]
        res = []
        if composite.group(1):
            res.append(composite.group(1))
        if composite.group(2):
            res.append(composite.group(2))
        if composite.group(3):
            res.append(composite.group(3))
        return res
    return [normalized]


def expand_words(words: Iterable[dict]) -> list[dict]:
    """Expanse les mots composites pour l'extraction spatiale."""
    expanded = []
    for w in words:
        text = w.get("text", "")
        tokens = tokenize_composite(text)
        if len(tokens) <= 1:
            expanded.append(w)
        else:
            for t in tokens:
                new_w = dict(w)
                new_w["text"] = t
                expanded.append(new_w)
    return expanded


def decompose_etiquette_technique(texte: str) -> dict:
    """Décode universellement toute notation de plan :
    'S1/P1'           -> Semelle S1 + Poteau P1
    'LG2(30*50)'      -> Longrine LG2, section 30x50
    'S1 6 TOR 10'     -> Semelle S1 + 6 barres T10
    'Semelle S1 90x90x25' -> Semelle S1, 90x90x25 cm
    """
    txt = str(texte or "").strip()
    if not txt:
        return {}

    elements = {}

    # 1. Composite Repères
    m_sem = re.search(r"(?:SEMELLE\s+)?\b(S\d+)\b", txt, re.IGNORECASE)
    if m_sem:
        elements["semelle"] = m_sem.group(1).upper()

    m_pot = re.search(r"(?:POTEAU\s+)?\b([PQ]\d+)\b", txt, re.IGNORECASE)
    if m_pot:
        if not (m_sem and m_sem.group(1).upper() == m_pot.group(1).upper()):
            elements["poteau"] = m_pot.group(1).upper()

    m_ptr = re.search(r"\b((?:B?N\d+(?:BIS)?|PN\d+|LG\d+|CH\d*))\b", txt, re.IGNORECASE)
    if m_ptr and not m_sem and not m_pot:
        elements["poutre"] = m_ptr.group(1).upper()

    # 2. Dimensions
    dim_match = re.search(
        r"\(?\s*(\d{1,3}(?:[.,]\d+)?)\s*[xX*×]\s*(\d{1,3}(?:[.,]\d+)?)(?:\s*[xX*×]\s*(\d{1,3}(?:[.,]\d+)?))?\s*\)?",
        txt
    )
    if dim_match:
        try:
            a = float(dim_match.group(1).replace(",", "."))
            b = float(dim_match.group(2).replace(",", "."))
            h = float(dim_match.group(3).replace(",", ".")) if dim_match.group(3) else None
            elements["dimensions"] = {"a": a, "b": b, "h": h}
        except ValueError:
            pass

    # 3. Armatures
    rebar_match = re.search(
        r"(?:(\d+)\s*)?(?:HA|T|TOR|Ø|PHI)\s*(\d{1,2})(?:\s*(?:E|ESP|@)\s*[=:]?\s*(\d+))?",
        txt,
        re.IGNORECASE
    )
    if rebar_match:
        try:
            nb = int(rebar_match.group(1)) if rebar_match.group(1) else None
            phi = int(rebar_match.group(2)) if rebar_match.group(2) else None
            esp = float(rebar_match.group(3)) if rebar_match.group(3) else None
            if phi and 5 <= phi <= 40:
                elements["acier"] = {"nb": nb, "phi": phi, "esp": esp}
        except (ValueError, TypeError):
            pass

    return elements
