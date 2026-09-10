#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/schedule_parser.py — Parseur intelligent de tableaux BA via Groq API.

Analyse les tableaux structuratres (semelles, poteaux, poutres, longrines,
chainages, murs, voiles) extraits d'un plan BA et produit un JSON strict
conforme au schema defini.

Utilise Groq API (LLM rapide et gratuit) pour l'analyse semantique.
100% local sinon : fallback sur regex si Groq indisponible.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# ============================================================================
# Schema de sortie — TOUTES les categories genie civil
# ============================================================================

OUTPUT_SCHEMA = {
    "elements": [
        {
            "family": (
                "SEMELLE | POTEAU | POUTRE | LONGRINE | CHAINAGE | MUR | VOILE | "
                "DALLE | ESCALIER | LINTEAU | RADIER | TERRASSEMENT | MACONNERIE | "
                "COFFRAGE | BETON | FERRAILLAGE | HOURDIS | ETANCHEITE | ENDUIT | "
                "PEINTURE | MENUISERIE | CHARPENTE | PLOMBERIE | ELECTRICITE"
            ),
            "reference": "String (e.g., 'S1', 'Q2', 'LG-25x40', 'Fouille S1')",
            "unite": "String: M2 | M3 | ML | KG | UTE | L (unite de mesure)",
            "quantite_tableau": "Integer or null",
            "dimensions": {
                "a": "Float or null — longueur (m)",
                "b": "Float or null — largeur/epaisseur (m)",
                "h": "Float or null — hauteur/profondeur (m)",
                "l": "Float or null — longueur supplementaire (m)",
            },
            "armatures_longitudinales": {
                "nappe_inf_x": {"nb": "Integer", "phi": "Integer"} or None,
                "nappe_inf_y": {"nb": "Integer", "phi": "Integer"} or None,
                "nappe_sup": {"nb": "Integer", "phi": "Integer"} or None,
            },
            "armatures_transversales": {
                "phi": "Integer or null",
                "espacement_m": "Float (meters) or null",
            },
            "notes": "String or null — designation texte libre",
        }
    ]
}

# ============================================================================
# System prompt pour Groq
# ============================================================================

SYSTEM_PROMPT = """You are an expert Structural Engineering Data Parser specialized in French and Moroccan Béton Armé (BA) and Génie Civil conventions.

Your task is to analyze ANY structural/civil engineering schedule table extracted from construction plans and extract specifications into strict JSON.

## SUPPORTED ELEMENT FAMILIES

### Structure BA (Béton Armé)
- SEMELLE: foundation footings (S1, S2, SF=semelle filante)
- POTEAU: columns (P1, Q1, Q2, Q3, Q4)
- POUTRE: beams (N1, N2, PN1, PC, etc.)
- LONGRINE: grade beams (LG, LG-25x40)
- CHAINAGE: tie beams (CH, CH-40x20)
- MUR: shear walls / bandes noyées (BN, Mur-01)
- VOILE: shear walls (V1, V2)
- DALLE: slabs (Dalle RDC, Dalle Etage)
- ESCALIER: stairs (Esc-01, Marche)
- LINTEAU: lintels (Linteau-01)
- RADIER: raft foundations

### Travaux d'aménagement
- TERRASSEMENT: earthworks (fouille, déblai, remblai, décapage, rigole)
- MACONNERIE: masonry (mur pierre, mur brique, voile béton)
- COFFRAGE: formwork (horizontal, vertical, cintré, poteau, poutre)
- BETON: concrete (dosage, type, confiné, autoplaçant)
- FERRAILLAGE: reinforcement steel (fourniture, pose, acier rond, treillis)
- HOURDIS: hollow core slabs (élément préfabriqué)
- ETANCHEITE: waterproofing (membrane, étanchéité toiture, cuvette)
- ENDUIT: plaster/render (enduit mur, ragréage)
- PEINTURE: paint (peinture mur, peinture plafond)
- MENUISERIE: joinery (porte, fenêtre, volet)
- CHARPENTE: framework (bois, métal)
- PLOMBERIE: plumbing (canalisation, évacuation)
- ELECTRICITE: electrical (gaine, câble)

## EXTRACTION RULES

### Column Mapping
Identify columns regardless of their exact headers. Look for:
- Reference (Repère/Type/Désignation): S1, P1, N1, Fouille S1, etc.
- Dimensions (A, B, H, L, b, h, l, ép, prof, dim): in cm OR meters
- Quantity (Nombre/Nb/Qté): may be blank, "--", "suiv plan", or "var"
- Unit (Unité/Un): M2, M3, ML, KG, UTE
- Reinforcement (Ferraillage/Aciers/Arma): e.g., "8T12", "2CAD T8 e=15"
- Description/Notes: free text designation

### UNIT DETECTION — CRITICAL
Plans use EITHER centimeters (cm) OR meters (m). You MUST detect which:
1. If dimensions are like "25", "30", "90", "120" → these are CENTIMETERS → divide by 100
2. If dimensions are like "0.25", "0.30", "0.90", "1.20" → these are already METERS
3. If header says "cm" → values are in cm
4. If header says "m" → values are in meters
5. Typical BA sections: 20-60 → cm (e.g., 25x40 = 0.25m x 0.40m)
6. Typical earthworks: 0.5-3.0 → meters (e.g., fouille 1.50m deep)

### Dimension Normalization
Convert ALL final dimension values to METERS (m) as floats:
- "25" (cm) → 0.25, "90" (cm) → 0.90, "120" (cm) → 1.20
- "0.25" (m) → 0.25, "1.50" (m) → 1.50
- For compact "AxBxH": "90x90x25" → a=0.90, b=0.90, h=0.25

### Unit Detection per Element
- SEMELLE/POTEAU/POUTRE/LONGRINE/DALLE → typically M3 (volume)
- COFFRAGE → M2 (surface de contact)
- FERRAILLAGE → KG (poids)
- TERRASSEMENT → M3 (volume cube)
- MACONNERIE → M2 (surface mur) or M3 (volume)
- ETANCHEITE → M2 (surface)
- PEINTURE/ENDUIT → M2 (surface)
- MENUISERIE → M2 or UTE (unité d'ouvrage)
- HOURDIS → M2 (surface)

### The Quantity Rule ("Nombre")
- If blank, "--", "suiv plan", "var", "s/p", or any non-numeric → set to null
- Do NOT guess or infer quantity. Only use explicit integers.

### Reinforcement Parsing (Aciers/Ferraillage)
- "8T12" or "8HA12" or "8 TOR 12" → {"nb": 8, "phi": 12}
- "4T10" → {"nb": 4, "phi": 10}
- "2CAD T8 e=15" → cadres phi: 8, espacement: 0.15m
- "ETRIERS T6 @20" → etriers phi: 6, espacement: 0.20m
- "5HA12 / 5HA12" → nappe_inf_x: {nb:5, phi:12}, nappe_inf_y: {nb:5, phi:12}

### Family Detection Patterns
- S\\d+ or S\\d+\\.\\d+ or SF → SEMELLE
- Q\\d+ or P\\d+ → POTEAU
- N\\d+ or PN\\d+ or PC\\d+ → POUTRE
- LG\\d* → LONGRINE
- CH\\d* → CHAINAGE
- BN\\d* → MUR (Bande Noyée)
- V\\d+ → VOILE
- Dalle\\w* → DALLE
- Fouille\\w* or Déblai or Remblai → TERRASSEMENT
- Coffrage\\w* → COFFRAGE
- Escalier\\w* or Marche → ESCALIER
- Linteau\\w* → LINTEAU

### Edge Cases
- Merged cells: apply value to all corresponding references
- Multiple variations (Q1, Q1_bis): treat as distinct elements
- Text notes like "B35" or "dosage 350 kg" → extract as notes
- Empty reinforcement → set all armatures fields to null

## OUTPUT FORMAT
Return ONLY valid JSON matching this exact structure. No markdown, no explanation.

{
  "elements": [
    {
      "family": "SEMELLE",
      "reference": "S1",
      "unite": "M3",
      "quantite_tableau": null,
      "dimensions": {"a": 0.90, "b": 0.90, "h": 0.25},
      "armatures_longitudinales": {
        "nappe_inf_x": {"nb": 5, "phi": 12},
        "nappe_inf_y": {"nb": 5, "phi": 12},
        "nappe_sup": null
      },
      "armatures_transversales": {"phi": null, "espacement_m": null},
      "notes": null
    }
  ]
}"""


# ============================================================================
# Extraction regex (fallback sans LLM)
# ============================================================================

# Patterns pour detecter les references
_REF_PATTERNS = {
    "SEMELLE": re.compile(r"^S\d+(?:\.\d+)?$|^SF$|^Sem", re.I),
    "POTEAU": re.compile(r"^[QP]\d+$", re.I),
    "POUTRE": re.compile(r"^(?:B?N\d+|PN\d+|PC\d*)$", re.I),
    "LONGRINE": re.compile(r"^LG\d*", re.I),
    "CHAINAGE": re.compile(r"^CH\d*", re.I),
    "MUR": re.compile(r"^BN\d*$|^Mur", re.I),
    "VOILE": re.compile(r"^V\d+$", re.I),
    "DALLE": re.compile(r"^Dalle|^Dal", re.I),
    "ESCALIER": re.compile(r"^Esc|^Escal|^Marche", re.I),
    "LINTEAU": re.compile(r"^Lint|^Lin", re.I),
    "RADIER": re.compile(r"^Rad", re.I),
    "TERRASSEMENT": re.compile(r"^Fouill|^Débla|^Rembla|^Décap|^Rigo|^TERR", re.I),
    "MACONNERIE": re.compile(r"^Mur\s|^Voile\s|^MAC", re.I),
    "COFFRAGE": re.compile(r"^Coff|^COF", re.I),
    "BETON": re.compile(r"^Béton|^Bet|^B\d{2,3}", re.I),
    "FERRAILLAGE": re.compile(r"^Ferr|^Acier|^FER", re.I),
    "HOURDIS": re.compile(r"^Hour|^HOU", re.I),
    "ETANCHEITE": re.compile(r"^Étanch|^Etan|^ETA", re.I),
    "ENDUIT": re.compile(r"^Endu|^Ragr|^END", re.I),
    "PEINTURE": re.compile(r"^Pein|^PEI", re.I),
    "MENUISERIE": re.compile(r"^Porte|^Fenêtr|^Volet|^MENU", re.I),
    "CHARPENTE": re.compile(r"^Charp|^CHAR", re.I),
    "PLOMBERIE": re.compile(r"^Plomb|^Canal|^Evac|^PLO", re.I),
    "ELECTRICITE": re.compile(r"^Gaine|^Câble|^ELEC", re.I),
}

# ============================================================================
# Detection d'unite (cm vs m) — CRITIQUE pour la conversion
# ============================================================================

def detect_unit_from_header(header_cells: list[str]) -> str:
    """Detecte l'unite depuis les en-tetes de colonnes.
    Retourne 'cm' ou 'm'."""
    for cell in header_cells:
        c = (cell or "").lower().strip()
        if "cm" in c:
            return "cm"
        if c.startswith("m ") or c.endswith(" m") or c == "m":
            return "m"
        if any(kw in c for kw in ["longueur", "largeur", "hauteur", "epaisseur",
                                    "profondeur", "dimension", "section"]):
            # Si le header ne specifie pas l'unite, deviner par les valeurs
            pass
    return "auto"


def detect_unit_from_values(values: list[str]) -> str:
    """Detecte l'unite en analysant les valeurs numeriques.
    Retourne 'cm' ou 'm'."""
    nums = []
    for v in values:
        v = v.strip().replace(",", ".")
        try:
            nums.append(float(v))
        except ValueError:
            continue
    if not nums:
        return "cm"  # defaut BA

    avg = sum(nums) / len(nums)
    # Si la moyenne est > 5, c'est probablement en cm
    # Si la moyenne est < 5, c'est probablement en m
    if avg > 5:
        return "cm"
    return "m"


def normalize_to_meters(value: float, unit: str) -> float:
    """Convertit une valeur en metres."""
    if unit == "cm":
        return round(value / 100.0, 3)
    return round(value, 3)


# Pattern pour detecter les dimensions (AxBxH ou A x B x H)
_DIM_PATTERN = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*[xX×]\s*(\d+(?:[.,]\d+)?)"
    r"(?:\s*[xX×]\s*(\d+(?:[.,]\d+)?))?"
)

# Pattern pour detecter le ferraillage
_FERRA_PATTERN = re.compile(
    r"(\d+)\s*(?:T|HA|TOR)\s*(\d+)", re.I
)

# Pattern pour cadres/etriers
_CADRE_PATTERN = re.compile(
    r"(?:CAD|ETR|cadre|etrier)\s*(?:T)?(\d+)\s*(?:e[@=\s]+)(\d+(?:[.,]\d+)?)",
    re.I
)


def _detect_family(ref: str) -> str:
    """Detecte la famille d'un element depuis sa reference."""
    ref_upper = ref.upper().strip()
    for family, pattern in _REF_PATTERNS.items():
        if pattern.search(ref_upper):
            return family
    return "POUTRE"  # fallback


def _parse_dimensions_from_text(text: str, unit_hint: str = "auto") -> dict:
    """Extrait les dimensions (a, b, h) en metres depuis un texte.
    
    Args:
        text: Texte contenant les dimensions (ex: "25x30x25" ou "0.25x0.30x0.25")
        unit_hint: "cm", "m", ou "auto" (detecter automatiquement)
    """
    match = _DIM_PATTERN.search(text)
    if not match:
        return {"a": None, "b": None, "h": None}

    groups = match.groups()
    a = float(groups[0].replace(",", "."))
    b = float(groups[1].replace(",", "."))
    h = float(groups[2].replace(",", ".")) if groups[2] else None

    # Detecter l'unite
    if unit_hint == "auto":
        vals = [a, b]
        if h:
            vals.append(h)
        unit = detect_unit_from_values([str(v) for v in vals])
    else:
        unit = unit_hint

    # Normaliser en metres
    a = normalize_to_meters(a, unit)
    b = normalize_to_meters(b, unit)
    if h is not None:
        h = normalize_to_meters(h, unit)

    return {"a": a, "b": b, "h": h}


def _parse_ferraillage_from_text(text: str) -> dict:
    """Extrait le ferraillage depuis un texte."""
    result = {
        "nappe_inf_x": None,
        "nappe_inf_y": None,
        "nappe_sup": None,
    }

    # Chercher les barres longitudinales
    ferras = _FERRA_PATTERN.findall(text)
    if ferras:
        # Premier ferraillage → nappe_inf_x
        nb, phi = int(ferras[0][0]), int(ferras[0][1])
        result["nappe_inf_x"] = {"nb": nb, "phi": phi}
        # Deuxième → nappe_inf_y
        if len(ferras) > 1:
            nb, phi = int(ferras[1][0]), int(ferras[1][1])
            result["nappe_inf_y"] = {"nb": nb, "phi": phi}
        # Troisième → nappe_sup
        if len(ferras) > 2:
            nb, phi = int(ferras[2][0]), int(ferras[2][1])
            result["nappe_sup"] = {"nb": nb, "phi": phi}

    return result


def _parse_cadres_from_text(text: str) -> dict:
    """Extrait les cadres/étriers depuis un texte."""
    match = _CADRE_PATTERN.search(text)
    if match:
        phi = int(match.group(1))
        esp = float(match.group(2).replace(",", "."))
        if esp > 5:  # Probablement en cm
            esp /= 100.0
        return {"phi": phi, "espacement_m": round(esp, 3)}
    return {"phi": None, "espacement_m": None}


def _parse_quantity(text: str) -> int | None:
    """Extrait la quantité depuis un texte."""
    text = text.strip()
    if not text or text in ("--", "-", "var", "suiv plan", "s/p", "sp"):
        return None
    try:
        return int(text)
    except ValueError:
        return None


# ============================================================================
# Groq API Client
# ============================================================================

def _call_groq_api(table_text: str, table_type: str = "") -> dict | None:
    """Appelle Groq API pour analyser un tableau BA."""
    import urllib.request
    import urllib.error

    if not GROQ_API_KEY:
        logger.warning("Groq API key not configured")
        return None

    user_prompt = f"""Analyze this structural schedule table from a Béton Armé (BA) plan and extract all elements into JSON.

Table type hint: {table_type or "unknown"}

Table content (raw text from PDF extraction):
---
{table_text}
---

Extract ALL elements. Convert dimensions from cm to meters. Set quantity to null if not explicitly specified.
Return ONLY the JSON object, no markdown formatting."""

    payload = json.dumps({
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    req = urllib.request.Request(GROQ_API_URL, data=payload, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            # Nettoyer le contenu (parfois entoure de markdown)
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)
                content = re.sub(r"\s*```$", "", content)
            return json.loads(content)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error("Groq API HTTP %d: %s", e.code, body[:500])
        return None
    except Exception as e:
        logger.error("Groq API error: %s", e)
        return None


# ============================================================================
# Fallback regex parser
# ============================================================================

def _parse_table_regex(table_data: list[list[str]], page_num: int = 0) -> dict:
    """Parse un tableau via regex (fallback sans LLM)."""
    elements = []

    if not table_data or len(table_data) < 2:
        return {"elements": []}

    # Detecter l'en-tête et les indices de colonnes
    header_idx = 0
    col_map = {}  # role -> index
    for ri, row in enumerate(table_data):
        cells = [(c or "").strip().lower() for c in row]
        if any("rep" in c or "designation" in c or "type" in c for c in cells):
            header_idx = ri
            for ci, c in enumerate(cells):
                if "rep" in c or "designation" in c:
                    col_map["ref"] = ci
                elif c in ("a", "a (cm)", "a(cm)", "longueur"):
                    col_map["a"] = ci
                elif c in ("b", "b (cm)", "b(cm)", "largeur"):
                    col_map["b"] = ci
                elif c in ("h", "h (cm)", "h(cm)", "hauteur"):
                    col_map["h"] = ci
                elif "nombre" in c or "nb" in c or "qty" in c:
                    col_map["qty"] = ci
                elif "ferr" in c or "acier" in c or "armat" in c:
                    col_map["ferra"] = ci
                elif "cad" in c or "etr" in c or "travers" in c:
                    col_map["cadres"] = ci
            break

    # Parser chaque ligne
    for row in table_data[header_idx + 1:]:
        cells = [(c or "").strip() for c in row]
        if not cells:
            continue

        # Reference
        ref_idx = col_map.get("ref", 0)
        ref = cells[ref_idx].strip() if ref_idx < len(cells) else ""
        if not ref:
            continue

        family = _detect_family(ref)

        # Dimensions depuis colonnes specifiques
        dims = {"a": None, "b": None, "h": None}
        for key in ("a", "b", "h"):
            ci = col_map.get(key)
            if ci is not None and ci < len(cells):
                val = cells[ci].strip()
                if val:
                    try:
                        v = float(val.replace(",", "."))
                        if v > 5:  # cm → m
                            v /= 100.0
                        dims[key] = round(v, 3)
                    except ValueError:
                        # Essayer d'extraire depuis un format compact "25x30"
                        parsed = _parse_dimensions_from_text(val)
                        if parsed["a"] is not None:
                            dims = parsed
                            break

        # Si pas de dimensions par colonnes, chercher dans toutes les cellules
        if dims["a"] is None and dims["b"] is None:
            for cell in cells[1:]:
                parsed = _parse_dimensions_from_text(cell)
                if parsed["a"] is not None:
                    dims = parsed
                    break

        # Quantité
        qty = None
        qty_idx = col_map.get("qty")
        if qty_idx is not None and qty_idx < len(cells):
            qty = _parse_quantity(cells[qty_idx])
        # Si pas de colonne qty explicite, ne pas deviner
        # (les valeurs 25, 30 etc. sont des dimensions, pas des quantités)

        # Ferraillage
        ferra = {"nappe_inf_x": None, "nappe_inf_y": None, "nappe_sup": None}
        cadres = {"phi": None, "espacement_m": None}
        ferra_idx = col_map.get("ferra")
        if ferra_idx is not None and ferra_idx < len(cells):
            ferra = _parse_ferraillage_from_text(cells[ferra_idx])
            cadres = _parse_cadres_from_text(cells[ferra_idx])
        else:
            for cell in cells[1:]:
                if _FERRA_PATTERN.search(cell):
                    ferra = _parse_ferraillage_from_text(cell)
                if _CADRE_PATTERN.search(cell):
                    cadres = _parse_cadres_from_text(cell)

        elements.append({
            "family": family,
            "reference": ref,
            "quantite_tableau": qty,
            "dimensions": dims,
            "armatures_longitudinales": ferra,
            "armatures_transversales": cadres,
        })

    return {"elements": elements}


# ============================================================================
# API publique
# ============================================================================

def parse_schedule_table(
    table_data: list[list[str]] | str,
    table_type: str = "",
    page_num: int = 0,
    use_llm: bool = True,
) -> dict:
    """Parse un tableau BA et retourne le JSON strict.

    Args:
        table_data: Données du tableau (liste de lignes ou texte brut)
        table_type: Type de tableau ("semelles", "poteaux", "poutres", etc.)
        page_num: Numéro de page pour les logs
        use_llm: Utiliser Groq API (True) ou fallback regex (False)

    Returns:
        Dict avec clé "elements" contenant la liste des éléments extraits
    """
    # Convertir en texte si nécessaire
    if isinstance(table_data, list):
        lines = []
        for row in table_data:
            cells = [str(c or "") for c in row]
            lines.append(" | ".join(cells))
        table_text = "\n".join(lines)
    else:
        table_text = str(table_data)

    if not table_text.strip():
        return {"elements": []}

    # Essayer Groq API d'abord
    if use_llm and GROQ_API_KEY:
        result = _call_groq_api(table_text, table_type)
        if result and "elements" in result:
            # Valider et normaliser chaque élément
            validated = []
            for elem in result["elements"]:
                validated.append(_validate_element(elem))
            logger.info(
                "Groq parsed %d elements from page %d (type: %s)",
                len(validated), page_num, table_type
            )
            return {"elements": validated}

    # Fallback regex
    if isinstance(table_data, list):
        result = _parse_table_regex(table_data, page_num)
    else:
        # Parser le texte ligne par ligne
        lines = table_text.split("\n")
        rows = [line.split("|") for line in lines if line.strip()]
        result = _parse_table_regex(rows, page_num)

    logger.info(
        "Regex parsed %d elements from page %d (type: %s)",
        len(result.get("elements", [])), page_num, table_type
    )
    return result


def _validate_element(elem: dict) -> dict:
    """Valide et normalise un élément extrait."""
    # Family — toutes les categories genie civil
    family = (elem.get("family") or "POUTRE").upper()
    valid_families = {
        "SEMELLE", "POTEAU", "POUTRE", "LONGRINE", "CHAINAGE", "MUR", "VOILE",
        "DALLE", "ESCALIER", "LINTEAU", "RADIER", "TERRASSEMENT", "MACONNERIE",
        "COFFRAGE", "BETON", "FERRAILLAGE", "HOURDIS", "ETANCHEITE", "ENDUIT",
        "PEINTURE", "MENUISERIE", "CHARPENTE", "PLOMBERIE", "ELECTRICITE",
    }
    if family not in valid_families:
        family = _detect_family(elem.get("reference", ""))
    elem["family"] = family

    # Reference
    elem["reference"] = str(elem.get("reference", "")).strip()

    # Unite (M2, M3, ML, KG, UTE)
    unite = elem.get("unite")
    if not unite:
        unite = _infer_unit(family)
    elem["unite"] = unite

    # Quantity
    qty = elem.get("quantite_tableau")
    if qty is not None:
        try:
            elem["quantite_tableau"] = int(qty)
        except (ValueError, TypeError):
            elem["quantite_tableau"] = None

    # Dimensions (normaliser en mètres)
    dims = elem.get("dimensions") or {}
    for key in ("a", "b", "h", "l"):
        val = dims.get(key)
        if val is not None:
            try:
                val = float(val)
                # Si > 5, probablement en cm → convertir
                if val > 5:
                    val /= 100.0
                dims[key] = round(val, 3)
            except (ValueError, TypeError):
                dims[key] = None
        else:
            dims[key] = None
    elem["dimensions"] = dims

    # Armatures longitudinales
    arm_long = elem.get("armatures_longitudinales") or {}
    for key in ("nappe_inf_x", "nappe_inf_y", "nappe_sup"):
        val = arm_long.get(key)
        if val and isinstance(val, dict):
            arm_long[key] = {
                "nb": int(val.get("nb") or 0),
                "phi": int(val.get("phi") or 0),
            }
        else:
            arm_long[key] = None
    elem["armatures_longitudinales"] = arm_long

    # Armatures transversales
    arm_trans = elem.get("armatures_transversales") or {}
    phi = arm_trans.get("phi")
    esp = arm_trans.get("espacement_m")
    if phi is not None:
        try:
            phi = int(phi)
        except (ValueError, TypeError):
            phi = None
    if esp is not None:
        try:
            esp = float(esp)
            if esp > 5:
                esp /= 100.0
            esp = round(esp, 3)
        except (ValueError, TypeError):
            esp = None
    elem["armatures_transversales"] = {"phi": phi, "espacement_m": esp}

    # Notes (texte libre)
    elem["notes"] = str(elem.get("notes") or "").strip() or None

    return elem


def _infer_unit(family: str) -> str:
    """Infere l'unite de mesure standard pour une famille d'element."""
    unit_map = {
        "SEMELLE": "M3",
        "POTEAU": "M3",
        "POUTRE": "M3",
        "LONGRINE": "M3",
        "CHAINAGE": "M3",
        "MUR": "M2",
        "VOILE": "M2",
        "DALLE": "M3",
        "ESCALIER": "M2",
        "LINTEAU": "M3",
        "RADIER": "M3",
        "TERRASSEMENT": "M3",
        "MACONNERIE": "M2",
        "COFFRAGE": "M2",
        "BETON": "M3",
        "FERRAILLAGE": "KG",
        "HOURDIS": "M2",
        "ETANCHEITE": "M2",
        "ENDUIT": "M2",
        "PEINTURE": "M2",
        "MENUISERIE": "M2",
        "CHARPENTE": "M3",
        "PLOMBERIE": "ML",
        "ELECTRICITE": "ML",
    }
    return unit_map.get(family.upper(), "M3")


def parse_multiple_tables(
    tables: list[dict],
    use_llm: bool = True,
) -> dict:
    """Parse plusieurs tableaux et fusionne les résultats.

    Args:
        tables: Liste de {"data": list[list], "type": str, "page": int}
        use_llm: Utiliser Groq API

    Returns:
        Dict avec "elements" fusionné
    """
    all_elements = []
    for table in tables:
        result = parse_schedule_table(
            table_data=table.get("data", []),
            table_type=table.get("type", ""),
            page_num=table.get("page", 0),
            use_llm=use_llm,
        )
        all_elements.extend(result.get("elements", []))

    return {"elements": all_elements}


def elements_to_catalogue(elements: list[dict]) -> dict:
    """Convertit la liste d'elements extraits en catalogue format extracteur.

    Transforme le format JSON strict en format catalogue utilise par
    VectorPlanExtractor. Supporte TOUTES les categories genie civil.
    """
    catalogue = {
        # Structure BA
        "semelles": {},
        "poteaux": {},
        "poutres": {},
        "longrines": {},
        "chainages": {},
        "murs": {},
        "voiles": {},
        "dalles": {},
        "escaliers": {},
        "linteaux": {},
        "radier": {},
        # Travaux d'amenagement
        "terrassement": {},
        "maconnerie": {},
        "coffrage": {},
        "beton": {},
        "ferraillage": {},
        "hourdis": {},
        "etancheite": {},
        "enduit": {},
        "peinture": {},
        "menuiserie": {},
        "charpente": {},
        "plomberie": {},
        "electricite": {},
    }

    for elem in elements:
        family = elem.get("family", "").upper()
        ref = elem.get("reference", "")
        dims = elem.get("dimensions", {})
        arm_long = elem.get("armatures_longitudinales", {})
        arm_trans = elem.get("armatures_transversales", {})
        unite = elem.get("unite", "M3")
        notes = elem.get("notes")

        spec = {
            "a": dims.get("a"),
            "b": dims.get("b"),
            "h": dims.get("h"),
            "l": dims.get("l"),
            "unite": unite,
        }

        # Ajouter le ferraillage si présent
        if arm_long.get("nappe_inf_x"):
            spec["ferr_x"] = arm_long["nappe_inf_x"]
        if arm_long.get("nappe_inf_y"):
            spec["ferr_y"] = arm_long["nappe_inf_y"]
        if arm_long.get("nappe_sup"):
            spec["ferr_sup"] = arm_long["nappe_sup"]
        if arm_trans.get("phi"):
            spec["cadres"] = {
                "phi": arm_trans["phi"],
                "esp": arm_trans.get("espacement_m", 0.15),
            }

        # Ajouter les notes si presentes
        if notes:
            spec["notes"] = notes

        # Mapper vers le bon catalogue — TOUTES les categories
        family_map = {
            # Structure BA
            "SEMELLE": "semelles",
            "POTEAU": "poteaux",
            "POUTRE": "poutres",
            "LONGRINE": "longrines",
            "CHAINAGE": "chainages",
            "MUR": "murs",
            "VOILE": "voiles",
            "DALLE": "dalles",
            "ESCALIER": "escaliers",
            "LINTEAU": "linteaux",
            "RADIER": "radier",
            # Travaux d'amenagement
            "TERRASSEMENT": "terrassement",
            "MACONNERIE": "maconnerie",
            "COFFRAGE": "coffrage",
            "BETON": "beton",
            "FERRAILLAGE": "ferraillage",
            "HOURDIS": "hourdis",
            "ETANCHEITE": "etancheite",
            "ENDUIT": "enduit",
            "PEINTURE": "peinture",
            "MENUISERIE": "menuiserie",
            "CHARPENTE": "charpente",
            "PLOMBERIE": "plomberie",
            "ELECTRICITE": "electricite",
        }
        cat_name = family_map.get(family, "poutres")
        catalogue[cat_name][ref] = spec

    return catalogue
