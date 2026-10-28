# -*- coding: utf-8 -*-
"""core/label_scanner.py — Lecture GENERIQUE des etiquettes de plans BA.

Objectif : ne dependre ni d'un plan particulier, ni d'un format source
(PDF vectoriel, OCR, DXF, IFC), ni des conventions d'un seul bureau d'etudes.

Tout token de la forme suivante est reconnu ::

    <PREFIX><numero>[separateur]<A>[x<B>[x<H>]]

Exemples couverts (toutes variantes reelles rencontrees) ::

    S2(110x110x25)      S2 110x110x25       S2 : 110 x 110 x 25
    P1(25x35)           P1 25x35            POT3(30x30)     PD2 30x30
    N1(25x40)           N1-(25X40)          N1_25x40        N1-25x40
    BN_25x30            LG 25x35            CH1(20x15)
    M1(20x30)           V2(15x250)          D1 e=20

Regles de rigueur (aligne es sur les regles globales du projet) :
- une dimension n'est enregistree que si elle est REELLEMENT lue ;
- les tokens de ferraillage / espacement (``8T12``, ``e=15``, ``e=(10x9)``)
  ne sont jamais interpretes comme des sections ;
- unites : cm sur les plans, conversion explicite vers m en sortie ;
- la famille est deduite du prefixe, via une table configurable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

__all__ = [
    "SECTION_RULES", "PREFIX_FAMILIES", "LabelDims",
    "family_of_ref", "parse_section_values", "parse_label",
    "dims_to_metres", "section_text", "register_label",
]


# ---------------------------------------------------------------------------
# 1. Conventions : prefixe -> famille, et semantique des dimensions
# ---------------------------------------------------------------------------
#: Ordre = priorite (les prefixes les plus longs doivent passer en premier).
#: Table volontairement centrale : ajouter une convention = une ligne ici.
PREFIX_FAMILIES: tuple[tuple[str, str], ...] = (
    ("ESC", "escaliers"),
    ("SEM", "semelles"),
    ("SF", "semelles"),
    ("POT", "poteaux"),
    ("PIE", "pieux"),
    ("PD", "poteaux"),
    ("PO", "poteaux"),
    ("BN", "poutres"),
    ("PN", "poutres"),
    ("PC", "poutres"),
    ("PR", "poutres"),
    ("LG", "longrines"),
    ("CH", "chainages"),
    ("LT", "linteaux"),
    ("MS", "massifs"),
    ("RD", "redresseurs"),
    ("CF", "contre_forts"),
    ("S", "semelles"),
    ("P", "poteaux"),
    ("Q", "poteaux"),
    ("N", "poutres"),
    ("M", "murs"),
    ("V", "voiles"),
    ("D", "dalles"),
    ("R", "radiers"),
    ("F", "futs"),
)

#: Ordre des dimensions tel qu'ecrit sur le plan, par famille.
#:   semelle  : A x B x H   (a = B, b = L, h = epaisseur)
#:   poteau   : A x B
#:   poutre   : B x H       (largeur x hauteur ; la portee n'est pas cotee)
#:   mur/voile: ep x H
#:   dalle    : ep
SECTION_RULES: dict[str, tuple[str, ...]] = {
    "semelles": ("a", "b", "h"),
    "massifs": ("a", "b", "h"),
    "radiers": ("a", "b", "h"),
    "poteaux": ("a", "b"),
    "poutres": ("b", "h"),
    "longrines": ("b", "h"),
    "chainages": ("b", "h"),
    "linteaux": ("b", "h"),
    "consoles": ("b", "h"),
    "poutrelles": ("b", "h"),
    "murs": ("a", "h"),
    "voiles": ("a", "h"),
    "dalles": ("h",),
    "escaliers": ("a", "b", "h"),
    "contre_forts": ("a", "b"),
    "futs": ("a", "b"),
    "pieux": ("a",),
    "redresseurs": ("a", "b"),
}

#: Surcharges par reference : conventions particulieres d'un prefixe.
#: 'SF 45x20' = semelle filante -> 45 cm de largeur, 20 cm de hauteur.
REF_RULES: dict[str, tuple[str, ...]] = {"SF": ("a", "h")}

#: Bornes de plausibilite d'une section en cm (evite les faux positifs).
MIN_CM, MAX_CM = 8.0, 700.0

# ---------------------------------------------------------------------------
# 2. Parsing
# ---------------------------------------------------------------------------
_NUM = r"\d{1,4}(?:[.,]\d{1,3})?"
_DIMS_RX = re.compile(
    rf"^\(?\s*({_NUM})\s*[xX\u00d7*]\s*({_NUM})"
    rf"(?:\s*[xX\u00d7*]\s*({_NUM}))?\s*\)?\s*(cm|mm|m)?$")
_EP_RX = re.compile(
    rf"^(?:ep|ep\.|e|\u00e9p\.?)\s*[=:]?\s*({_NUM})\s*(cm|mm|m)?$", re.I)
_REF_RX = re.compile(r"^([A-Z]{1,4})\s*[-_ ]?\s*(\d{0,4})\s*(BIS)?$", re.I)


@dataclass(frozen=True)
class LabelDims:
    """Etiquette lue : famille, reference canonique, dimensions en cm."""

    family: str
    reference: str
    values_cm: tuple[float, ...]
    rule: tuple[str, ...] = ()
    raw: str = ""
    source: str = "label"
    extras: Mapping[str, object] = field(default_factory=dict)

    def as_metres(self) -> dict[str, float]:
        return dims_to_metres(self.values_cm, self.family, self.rule)


def family_of_ref(ref: str) -> str:
    """Famille deduite du prefixe d'une reference ('' si inconnue).

    Les prefixes sont testes du plus long au plus court : ``POT1`` -> poteaux
    (et non ``poutres``), ``BN_25x30`` -> poutres (convention existante du
    moteur, ou ``BN`` designe une bande/poutre).
    """
    txt = str(ref or "").upper().strip()
    for prefix, family in PREFIX_FAMILIES:
        if not txt.startswith(prefix):
            continue
        rest = txt[len(prefix):]
        # Le prefixe doit etre suivi d'un chiffre, d'un separateur ou de rien
        # ('S2' ok, 'SB1' ne doit pas tomber dans 'S').
        if rest and not (rest[0].isdigit() or rest[0] in "-_ .("):
            continue
        return family
    return ""


def parse_section_values(text: str) -> tuple[float, ...] | None:
    """Lit ``A x B [x H]`` (ou ``ép. 20``) et retourne les valeurs en cm.

    ``None`` si le texte n'est pas une section plausible. Les valeurs
    decimales inferieures a 3 sont considerees en metres (``0.25x0.30``),
    les autres en cm.
    """
    txt = str(text or "").strip().replace(",", ".")
    if not txt:
        return None
    # '(110x110x25)' ou '110x110x250mm)' -> retire les parentheses externes
    txt = txt.strip().strip("()").strip()
    if not txt:
        return None

    m = _EP_RX.match(txt)
    if m:
        vals = (_to_cm(m.group(1), m.group(2)),)
        return vals if MIN_CM <= vals[0] <= MAX_CM else None

    m = _DIMS_RX.match(txt)
    if not m:
        return None
    unit = m.group(4)
    raw = [m.group(i) for i in (1, 2, 3) if m.group(i) is not None]
    vals = tuple(_to_cm(v, unit) for v in raw)
    if any(v is None for v in vals):
        return None
    nums = tuple(float(v) for v in vals)  # type: ignore[arg-type]
    if any(not (MIN_CM <= v <= MAX_CM) for v in nums):
        return None
    return nums


def _to_cm(value: str, unit: str | None) -> float | None:
    try:
        f = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    u = (unit or "").lower()
    if u == "m":
        return f * 100.0
    if u == "mm":
        return f / 10.0
    if u == "cm":
        return f
    # Unite implicite : un plan cote en m ecrit 0.25 / 1.10 ; en cm 25 / 110.
    return f * 100.0 if f < 3 else f


def parse_label(text: str) -> LabelDims | None:
    """Reconnait ``REF + dimensions`` dans un token unique.

    Retourne ``None`` si le token n'est pas une etiquette cotee connue.
    """
    txt = str(text or "").strip()
    if not txt or len(txt) > 40:
        return None
    # Ecarte les tokens de ferraillage / espacement / cotes brutes
    if re.search(r"(?<![A-Za-z])\d+\s*(?:HA|T)\s*\d+", txt, re.I):
        return None
    if re.search(r"\be\s*=\s*\(", txt, re.I):
        return None

    # 1) Reference = lettres + numero optionnel (+ BIS)
    m = re.match(r"^([A-Za-z]{1,4})(\d{0,4})(BIS)?(.*)$", txt, re.I)
    if not m:
        return None
    prefix, num, bis = m.group(1).upper(), m.group(2), (m.group(3) or "").upper()
    rest = m.group(4).strip()
    # 'BN25x30' (sans separateur) : le nombre fait partie de la formule
    if re.match(r"^[xX\u00d7*]", rest):
        rest = num + rest
        num = ""
    reference = f"{prefix}{num}{bis}"
    family = family_of_ref(reference)
    if not family:
        return None

    # 2) Dimensions = reste du token, debarrasse des separateurs
    rest = re.sub(r"^[-_:=\s(]+", "", rest).strip()
    if not rest:
        return None

    rule = REF_RULES.get(prefix, SECTION_RULES.get(family, ()))
    values = parse_section_values(rest)
    if not values and len(rule) == 1:
        # Dalle / pieu / semelle filante : une seule valeur cotee
        single = re.match(r"^(\d{1,4}(?:[.,]\d{1,3})?)\)?$", rest)
        if single:
            v = _to_cm(single.group(1), None)
            if v is not None and MIN_CM <= v <= MAX_CM:
                values = (v,)
    if not values:
        return None

    # 3 valeurs lues sur une famille a 2 dimensions : la 3e est une
    # tolerance/hauteur parasite -> on garde les deux premieres.
    if len(values) > len(rule):
        values = values[:len(rule)]
    return LabelDims(family=family, reference=reference,
                     values_cm=tuple(float(v) for v in values), rule=rule,
                     raw=txt)


def parse_reference(text: str) -> tuple[str, str] | None:
    """Reconnait une reference SEULE (``S2``, ``BN1``, ``POT3``).

    Retourne ``(famille, reference)`` ou ``None``. Sert aux tableaux de
    nomenclature ou la reference et les dimensions occupent deux cellules.
    """
    txt = str(text or "").strip()
    if not txt or len(txt) > 12:
        return None
    m = re.match(r"^([A-Za-z]{1,4})\s*[-_ ]?\s*(\d{0,4})\s*(BIS)?$", txt, re.I)
    if not m:
        return None
    reference = (f"{m.group(1)}{m.group(2)}{(m.group(3) or '')}").upper()
    family = family_of_ref(reference)
    if not family:
        return None
    return family, reference


def rule_for(reference: str, family: str) -> tuple[str, ...]:
    """Ordre des dimensions pour une reference donnee (surcharges incluses)."""
    m = re.match(r"^([A-Za-z]{1,4})", str(reference or ""))
    if not m:
        return SECTION_RULES.get(family, ())
    return REF_RULES.get(m.group(1).upper(), SECTION_RULES.get(family, ()))


def dims_to_metres(values_cm: Iterable[float], family: str,
                   rule: Iterable[str] | None = None) -> dict[str, float]:
    """Associe les valeurs lues (cm) aux cles metier de la famille."""
    keys = tuple(rule) if rule else SECTION_RULES.get(family, ())
    out: dict[str, float] = {}
    for key, val in zip(keys, values_cm):
        out[key] = round(float(val) / 100.0, 4)
    return out


def section_text(values_cm: Iterable[float], family: str) -> str:
    """Section affichable en cm (``25x35``, ``e=20``)."""
    vals = [round(float(v)) for v in values_cm]
    if not vals:
        return ""
    if family == "dalles":
        return f"e={vals[0]}"
    return "x".join(str(v) for v in vals)


# ---------------------------------------------------------------------------
# 3. Enregistrement non destructif dans un catalogue_types
# ---------------------------------------------------------------------------
def register_label(catalogue: dict, label: LabelDims, *,
                   page: int | None = None) -> str:
    """Enregistre une etiquette dans ``catalogue`` sans ecraser l'existant.

    Retourne la cle catalogue utilisee. Une reference portant deja une
    section differente est eclatee en ``REF_AxB`` (jamais de fusion
    silencieuse de deux sections distinctes).
    """
    family = label.family
    bucket = catalogue.setdefault(family, {})
    dims = label.as_metres()
    # Section affichee : (a x b) pour les semelles, (b x h) pour les poutres,
    # (e=..) pour les dalles — la 3e valeur (epaisseur) reste dans 'h'.
    sec_values = label.values_cm[:2] if len(label.rule) == 3 else label.values_cm
    sec = section_text(sec_values, family)

    key = label.reference
    # Deja enregistre sous la forme composite historique ('BN_25x30')
    if sec and f"{key}_{sec}" in bucket:
        key = f"{key}_{sec}"
    existing = bucket.get(key)
    conflict = ""
    if existing:
        old = existing.get("section_str") or _section_from_spec(existing, label.rule)
        if old and sec and old != sec:
            # Une valeur deduite par proximite est remplacee par l'etiquette
            # explicite ; une valeur de tableau/etiquette n'est JAMAIS ecrasee
            # en silence -> cle distincte + conflit trace.
            if existing.get("section_source") == "heuristic":
                for k, v in dims.items():
                    existing[k] = v
                existing["section_str"] = sec
                existing["section_source"] = label.source
                return key
            conflict = f"{old} (autre source) vs {sec} ({label.source})"
            key = f"{label.reference}_{sec}"

    spec = bucket.setdefault(key, {})
    if conflict:
        spec.setdefault("section_conflit", conflict)
    for k, v in dims.items():
        if spec.get(k) in (None, 0, ""):
            spec[k] = v
    if sec and not spec.get("section_str"):
        spec["section_str"] = sec
    spec.setdefault("section_source", label.source)
    if page is not None:
        spec.setdefault("section_page", page)
    return key


def _section_from_spec(spec: Mapping[str, object],
                       rule: Iterable[str] | None) -> str:
    keys = tuple(rule or ())
    vals: list[float] = []
    for k in keys:
        try:
            v = float(spec.get(k) or 0)
        except (TypeError, ValueError):
            return ""
        if v <= 0:
            return ""
        vals.append(v * 100.0)
    if len(vals) == 3:          # semelle/massif : section affichee = a x b
        vals = vals[:2]
    if keys == ("h",):          # dalle : epaisseur
        return f"e={round(vals[0])}" if vals else ""
    return "x".join(str(round(v)) for v in vals)
