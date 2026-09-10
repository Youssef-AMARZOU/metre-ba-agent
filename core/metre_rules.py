"""
Regles metier genie civil / beton arme (BA).

Implemente les regles professionnelles pour le metre de construction :
  1. Coherence plan <-> tableaux recapitulatifs
  2. Distinction TYPE vs OCCURRENCE PHYSIQUE vs LINEAIRE
  3. Rattachement aux plans de ferraillage
  4. Calcul de quantites (volume beton, ratio acier, DQE)
  5. Conformite reglementaire (BAEL, RPS, B25, HA500)
  6. Gestion des indices de revision
  7. Vocabulaire/units locaux (Maroc / francophone BA)
  8. Regles de metrologie (Livre Metre de Batement - Manteau)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ============================================================================
# Constantes metier Maroc / francophone BA
# ============================================================================

# Unites de longueur : cotes en cm sur sections, en m sur portees
_CM_THRESHOLD = 10  # Valeur > 10 → cm, sinon → m

# Diametres barres HA courants (mm)
_DIAMETERS_HA = [6, 8, 10, 12, 14, 16, 20, 25, 32]

# Masse volumique beton (kg/m3)
_MASSE_BETON = 2500.0

# Masse lineique acier (kg/m) par diametre (mm)
_MASSE_ACIER = {
    6: 0.222, 8: 0.395, 10: 0.617, 12: 0.888,
    14: 1.208, 16: 1.578, 20: 2.466, 25: 3.854, 32: 6.313,
}

# Enrobage minimal par element (cm) - BAEL 91
_ENROBAGE = {
    "SEMELLE": 5, "FONDATION": 5, "RADIER": 5,
    "POUTRE": 3, "LONGRINE": 3, "CHAINAGE": 3,
    "POTEAU": 3, "DALLE": 2, "VOILE": 2,
    "BANDE_NOYEE": 3, "LINTEAU": 2,
}

# ============================================================================
# Regles de metrologie (Livre Metre de Batement - Manteau, 1993)
# ============================================================================
# Reference : "Metre de Batement" par Michel Manteau, 7eme edition, 1993
# Editions Eyrolles - ISBN 2-212-02110-0

# Conventions de mesure
MESURE_CONVENTIONS = {
    "H.O.": "Hors Oeuvre - mesure prise hors des ouvrages (faces exterieures)",
    "D.O.": "Dans Oeuvre - mesure prise dans les ouvrages (faces interieures)",
    "REDUIT": "Reduit ou moyenne - ex: reduite d'un trapeze = moyenne de ses deux bases",
    "DEVE": "Developpe - longueur developpee d'une ligne courbe ou brisee",
    "EQUERRE": "Mesurage des trous par addition des deux cotes en plan, ou d'un cote et de la profondeur",
}

# Unites de mesure par type d'ouvrage (Serie Centrale Academie d'Architecture)
UNITES_METRE = {
    "TERRASSEMENT": "M3",        # Volume (cube)
    "MACONNERIE": "M2",          # Surface (mur)
    "COFFRAGE": "M2",            # Surface de contact beton/bois
    "BETON": "M3",               # Volume
    "FERRAILLAGE": "KG",         # Poids
    "ETANCHEITE": "M2",          # Surface
    "CARRELAGE": "M2",           # Surface
    "ENDUIT": "M2",              # Surface
    "PEINTURE": "M2",            # Surface
    "CHARPENTE": "M3" if True else "KG",  # Volume ou poids
    "MENUISERIE": "M2",          # Surface
    "ESCALIER": "M2" if True else "M3",   # Surface ou volume
    "PLAFOND": "M2",             # Surface
    "FAIENCE": "M2",             # Surface
    "SANITAIRE": "UTE",          # Unite
    "ELECTRICITE": "UTE",        # Unite
    "VOIRIE": "M2",              # Surface
    "ESPACES_VERTS": "M2",       # Surface
}

# Formules de calcul par element (Serie Centrale 1985, Academie d'Architecture)
FORMULES_ELEMENT = {
    "SEMELLE_ISOLEE": {
        "terrassement_fouille": "L × l × (h + 0.20) M3",  # encaissement
        "beton_proprete": "(L + 0.10) × (l + 0.10) × 0.10 M3",
        "beton_arme": "L × l × h M3",
        "coffrage_joues": "2 × (L + l) × h M2",
    },
    "SEMELLE_BANDAGE": {
        "terrassement_fouille": "L × l × (h + 0.20) M3",
        "beton_proprete": "(L + 0.10) × (l + 0.10) × 0.10 M3",
        "beton_arme": "L × l × h M3",
        "coffrage_joues": "2 × L × h M2",
    },
    "POTEAU": {
        "beton_arme": "b × h × Hauteur M3",
        "coffrage": "2 × (b + h) × Hauteur M2",
    },
    "POUTRE": {
        "beton_arme": "b × h × Longueur M3",
        "coffrage_joues": "2 × h × Longueur M2",
        "coffrage_sous_face": "b × Longueur M2",
    },
    "LONGRINE": {
        "beton_arme": "b × h × Longueur M3",
        "coffrage_joues": "2 × h × Longueur M2",
        "coffrage_sous_face": "b × Longueur M2",
    },
    "CHAINAGE": {
        "beton_arme": "b × h × Longueur M3",
        "coffrage_joues": "2 × h × Longueur M2",
    },
    "DALLE": {
        "beton_arme": "Surface × epaisseur M3",
        "coffrage_sous_face": "Surface M2",
    },
    "VOILE": {
        "beton_arme": "Surface × epaisseur M3",
        "coffrage": "Surface M2",
    },
    "ESCALIER_DROIT": {
        "beton_arme": "Volume marches + palier M3",
        "coffrage": "Surface paillasse + marches + sous-face palier M2",
    },
    "LINTEAU": {
        "beton_arme": "b × h × Longueur M3",
        "coffrage_joues": "2 × h × Longueur M2",
        "coffrage_sous_face": "b × Longueur M2",
    },
}

# Nomenclature des corps d'etat (Serie Centrale 1985)
CORPS_ETAT = {
    "01": "Terrassement",
    "02": "Travaux souterrains",
    "03": "Assainissement",
    "04": "Chaussees et trottoirs",
    "05": "Espaces verts",
    "06": "Clotures",
    "07": "Maconnerie",
    "08": "Beton arme",
    "09": "Etancheite",
    "10": "Materiaux naturels (pierres, marbres, ardoises)",
    "11": "Carrelage",
    "12": "Platrerie",
    "13": "Staff",
    "14": "Stuc",
    "20": "Charpente metallique",
    "21": "Métallerie",
    "22": "Menuiserie aluminium",
    "24": "Charpente bois",
    "25": "Menuiserie",
    "26": "Escaliers - mains courantes",
    "27": "Parquetage",
    "29": "Quincaillerie",
    "30": "Electricite",
    "31": "Paratonnerres",
    "40": "Couverture",
    "41": "Plomberie - sanitaire",
    "42": "Genie climatique",
    "50": "Peinture",
    "51": "Ravalement",
    "52": "Vitrerie - miroiterie",
    "53": "Revetement - tenture",
    "54": "Decor - filage - lettres",
    "55": "Dorure",
}

# Code des articles beton arme (Serie Centrale 1985)
ARTICLES_BA = {
    # Coffrage
    "COFFRAGE_SEMELLE_ISOLEE": "2/100-2/101",
    "COFFRAGE_SEMELLE_BANDAGE": "2/100-2/101",
    "COFFRAGE_MUR_EMBRONCHABLE": "2/110-2/111",
    "COFFRAGE_DALLE_SANS_REMBOURRAGE": "2/120-2/121",
    "COFFRAGE_DALLE_AVEC_POUTRES": "2/130-2/131",
    "COFFRAGE_POUTRE": "2/140",
    "COFFRAGE_POUTRE_ISOLEE": "2/141",
    "COFFRAGE_LINTEAU": "2/142",
    "COFFRAGE_JOUSS_CHAINAGE": "2/143",
    "COFFRAGE_VOILE": "2/150-2/151",
    "COFFRAGE_POTEAU": "2/152-2/153",
    "COFFRAGE_CONTREFORT": "2/154-2/155",
    "COFFRAGE_PETIT_OUVRAGE": "2/160-2/165",
    "COFFRAGE_ESCALIER_DROIT": "2/170",
    "COFFRAGE_ESCALIER_BALANCE": "2/171",
    "COFFRAGE_ESCALIER_PALIER": "2/172",
    "COFFRAGE_CINTE_PLAN": "2/180-2/183",
    "COFFRAGE_CINTE_ELEVATION": "2/184-2/187",
    "COFFRAGE_TOITURE": "2/190-2/194",
    "COFFRAGE_INCLINE": "2/200-2/202",
    "COFFRAGE_HAUTEUR": "2/210-2/213",
    "COFFRAGE_PAREMENT_PLANCHES": "2/220-2/221",
    "COFFRAGE_PAREMENT_PANNEAUX": "2/222-2/223",
    "COFFRAGE_CHANFREIN": "2/230",
    "COFFRAGE_MOULURE": "2/231-2/232",
    "COFFRAGE_TROUS_PATTES": "2/250",
    "COFFRAGE_BOIS_ABANDONNE": "2/260",
    # Ferraillage
    "FERRAILLAGE_FOURNITURE": "3/010-3/023",
    "FERRAILLAGE_FACON": "3/020-3/022",
    "FERRAILLAGE_MINORATION": "3/025-3/026",
    "TREILLIS_SOUDE": "3/050-3/051",
    # Béton
    "BETON_SEMELLE": "4/151",
    "BETON_POUTRE": "4/170-4/172",
    "BETON_LINTEAU": "4/172",
    "BETON_ESCALIER": "4/201",
    # Hourdis
    "HOURDIS_ELE_CERAMIQUE": "5/010-5/014",
    "HOURDIS_SURCHARGE": "6/010-6/011",
    "HOURDIS_JOINT": "6/070-6/073",
}

# Patterns pour detection hypotheses reglementaires dans le cartouche
_REGLEMENTARY_PATTERNS = [
    # Regles de calcul
    (re.compile(r"BAEL\s*(\d{2})\s*modifi[eé]s?\s*(\d{2})", re.I),
     "regles_calcul", "BAEL {0} modifiees {1}"),
    (re.compile(r"R\.?P\.?S\.?\s*(\d{4})", re.I),
     "regles_calcul", "RPS {0}"),
    (re.compile(r"REgles?\s+de\s+calcul", re.I),
     "regles_calcul", "Regles de calcul (non specifiees)"),

    # Beton
    (re.compile(r"[BÉE]ton\s+B(\d+)", re.I),
     "classe_beton", "B{0}"),
    (re.compile(r"fc\s*=\s*(\d+)\s*MPA", re.I),
     "resistance_beton", "fc={0} MPa"),

    # Acier
    (re.compile(r"Aciers?\s+HA\s*(\d+)", re.I),
     "classe_acier", "HA{0}"),
    (re.compile(r"HA\s*(\d+)", re.I),
     "classe_acier", "HA{0}"),

    # Enrobage
    (re.compile(r"[EÉ]nrobage\s+minimum\s*=\s*(\d+)\s*cm", re.I),
     "enrobage_min", "{0} cm"),

    # Charges
    (re.compile(r"(\d+)\s*daN/m[sup2²2]", re.I),
     "charge", "{0} daN/m2"),

    # Fissuration
    (re.compile(r"Fisuration\s+peu\s+pr[eé]judiciable", re.I),
     "fissuration", "Classe d'exposition: peu prejudiciable"),

    # Sol
    (re.compile(r"Sol\s*:\s*Contrainte\s*=\s*(\d+)\s*Bars?", re.I),
     "sol", "Contrainte sol: {0} Bars"),
]


@dataclass
class RegulatoryHypothesis:
    """Hypothese reglementaire extraite du cartouche."""
    category: str
    value: str
    raw_text: str
    x: float = 0.0
    y: float = 0.0


@dataclass
class LinearMeasure:
    """Mesure lineaire d'un element."""
    reference: str
    family: str
    section_cm: tuple[int, int] | None = None  # (b, h) en cm
    length_m: float = 0.0
    level: str = "INCONNU"
    count: int = 1


@dataclass
class ConcreteVolume:
    """Volume de beton pour un type d'element."""
    reference: str
    family: str
    section_cm: tuple[int, int] | None = None
    length_m: float = 0.0
    volume_m3: float = 0.0
    level: str = "INCONNU"
    count: int = 1


@dataclass
class TableDiscrepancy:
    """Ecart detecte entre le plan et un tableau recapitulatif."""
    prefix: str
    family: str
    plan_count: int
    table_count: int
    plan_refs: list[str] = field(default_factory=list)
    table_refs: list[str] = field(default_factory=list)
    missing_in_table: list[str] = field(default_factory=list)
    missing_in_plan: list[str] = field(default_factory=list)
    severity: str = "INFO"  # INFO, WARNING, ERROR


@dataclass
class MissingFerraillage:
    """Section sans detail de ferraillage associe."""
    reference: str
    family: str
    section_text: str
    level: str = "INCONNU"
    has_ferra_detail: bool = False


@dataclass
class MetreReport:
    """Rapport complet du metre avec toutes les regles metier."""
    # Coherence plan/tableaux
    discrepancies: list[TableDiscrepancy] = field(default_factory=list)

    # Lineaire
    linears: list[LinearMeasure] = field(default_factory=list)

    # Volumes beton
    concrete_volumes: list[ConcreteVolume] = field(default_factory=list)
    total_concrete_m3: float = 0.0

    # Ferraillage
    missing_ferraillage: list[MissingFerraillage] = field(default_factory=list)

    # Hypotheses reglementaires
    regulatory_hypotheses: list[RegulatoryHypothesis] = field(default_factory=list)

    # Revision
    revision_index: str = ""
    revision_date: str = ""
    revision_label: str = ""

    # Stats
    total_elements: int = 0
    total_types: int = 0
    warnings: list[str] = field(default_factory=list)


# ============================================================================
# 1. Coherence plan <-> tableaux recapitulatifs
# ============================================================================

def extract_table_refs_from_text(words: list[dict]) -> dict[str, list[str]]:
    """Extrait les repères listés dans les tableaux recapitulatifs.

    Cherche les zones "TABLEAU DES POTEAUX", "TABLEAU DES SEMELLES", etc.
    et collecte les repères qui y figurent.
    """
    table_refs: dict[str, list[str]] = {}
    in_tableau = False
    current_table = ""

    # Patterns pour detecter le debut/fin d'un tableau
    tableau_start = re.compile(
        r"TABLEAU\s+DES\s+(POTEAUX|SEMELLES|POUTRES|LONGRINES|CHAINAGES)",
        re.I
    )
    tableau_end = re.compile(
        r"(COUPE|DETAIL|FONDATION|PL\.HT|IMPLANTATION|PLAN\s+D)",
        re.I
    )

    for w in words:
        text = w.get("text", "").strip()
        if not text:
            continue

        # Detecter debut de tableau
        m = tableau_start.search(text)
        if m:
            in_tableau = True
            current_table = m.group(1).upper()
            table_refs.setdefault(current_table, [])
            continue

        # Detecter fin de tableau
        if in_tableau and tableau_end.search(text):
            in_tableau = False
            current_table = ""
            continue

        # Collecter les repères dans le tableau
        if in_tableau and current_table:
            # Pattern repere: P1, S1, N1, LG1, etc.
            m = re.match(r"^([A-Z]{1,3})(\d+)(?:BIS|ALL)?$", text, re.I)
            if m:
                ref = m.group(1).upper() + m.group(2)
                if ref not in table_refs[current_table]:
                    table_refs[current_table].append(ref)

    return table_refs


def check_plan_table_coherence(
    plan_elements: list[Any],
    table_refs: dict[str, list[str]],
) -> list[TableDiscrepancy]:
    """Croise le comptage geometrique (plan) avec les tableaux recapitulatifs.

    Retourne les ecarts detectes.
    """
    discrepancies: list[TableDiscrepancy] = []

    # Mapping prefixe -> nom de tableau
    prefix_to_table = {
        "P": "POTEAUX", "Q": "POTEAUX",
        "S": "SEMELLES",
        "N": "POUTRES", "BN": "POUTRES", "LG": "LONGRINES",
        "CH": "CHAINAGES",
    }

    # Compter les elements du plan par prefixe
    plan_by_prefix: dict[str, set[str]] = {}
    for elem in plan_elements:
        prefix = getattr(elem, "prefix", "")
        ref = getattr(elem, "reference", "")
        if prefix and ref:
            plan_by_prefix.setdefault(prefix, set()).add(ref)

    # Comparer avec les tableaux
    for prefix, table_name in prefix_to_table.items():
        plan_refs = plan_by_prefix.get(prefix, set())
        table_list = table_refs.get(table_name, [])
        table_set = set(table_list)

        if not table_list and not plan_refs:
            continue

        missing_in_table = sorted(plan_refs - table_set)
        missing_in_plan = sorted(table_set - plan_refs)

        if missing_in_table or missing_in_plan:
            severity = "ERROR" if (missing_in_table and missing_in_plan) else "WARNING"
            if len(missing_in_table) > 3 or len(missing_in_plan) > 3:
                severity = "ERROR"

            discrepancies.append(TableDiscrepancy(
                prefix=prefix,
                family=prefix_to_table.get(prefix, "INCONNU"),
                plan_count=len(plan_refs),
                table_count=len(table_list),
                plan_refs=sorted(plan_refs),
                table_refs=table_list,
                missing_in_table=missing_in_table,
                missing_in_plan=missing_in_plan,
                severity=severity,
            ))

    return discrepancies


# ============================================================================
# 2. Distinction TYPE vs OCCURRENCE vs LINEAIRE
# ============================================================================

def compute_linear_by_type(
    elements: list[Any],
    cotes: dict[str, float] | None = None,
) -> list[LinearMeasure]:
    """Calcule le lineaire total par type d'element.

    Si les cotes (longueurs) ne sont pas disponibles, estime un lineaire
    moyen par type (defaut conservateur).
    """
    # Lineaire moyen estime par type (m) si pas de cotes
    DEFAULT_LENGTHS = {
        "POUTRE": 6.0, "LONGRINE": 6.0, "CHAINAGE": 6.0,
        "POTEAU": 3.0, "SEMELLE": 2.0, "DALLE": 0.0,
        "VOILE": 3.0, "BANDE_NOYEE": 6.0, "LINTEAU": 2.0,
        "RADIER": 0.0, "MASSIF": 0.0, "REDRESSEUR": 0.0,
        "ESCALIER": 0.0, "POUTRE_REDOUBLANTE": 6.0,
    }

    # Grouper par (reference, section, niveau)
    groups: dict[tuple, list[Any]] = {}
    for elem in elements:
        ref = getattr(elem, "reference", "")
        dims = getattr(elem, "dims_text", None)
        level = getattr(elem, "level", "INCONNU")
        key = (ref, dims, level)
        groups.setdefault(key, []).append(elem)

    linears: list[LinearMeasure] = []
    for (ref, dims, level), occs in groups.items():
        family = getattr(occs[0], "family", "INCONNU") if occs else "INCONNU"
        count = len(occs)

        # Parser la section
        section_cm = None
        if dims:
            parts = re.split(r"[xX×]", dims)
            if len(parts) >= 2:
                try:
                    section_cm = (int(parts[0]), int(parts[1]))
                except ValueError:
                    pass

        # Longueur
        if cotes and ref in cotes:
            length = cotes[ref]
        else:
            length = DEFAULT_LENGTHS.get(family, 6.0)

        linears.append(LinearMeasure(
            reference=ref,
            family=family,
            section_cm=section_cm,
            length_m=length,
            level=level,
            count=count,
        ))

    return linears


# ============================================================================
# 3. Rattachement aux plans de ferraillage
# ============================================================================

def check_ferraillage_coverage(
    elements: list[Any],
    ferra_sections: set[str] | None = None,
) -> list[MissingFerraillage]:
    """Verifie que chaque section utilisee a un detail de ferraillage.

    Compare les sections trouvees dans le coffrage avec les sections
    pour lesquelles un detail de ferraillage est present.
    """
    if ferra_sections is None:
        ferra_sections = set()

    missing: list[MissingFerraillage] = []

    # Grouper les elements par section
    sections_used: dict[str, list[Any]] = {}
    for elem in elements:
        dims = getattr(elem, "dims_text", None)
        if dims:
            sections_used.setdefault(dims, []).append(elem)

    # Verifier chaque section
    for section, occs in sections_used.items():
        # Normaliser la section pour comparaison
        section_normalized = section.upper().replace("X", "x").replace("×", "x")
        if section_normalized not in ferra_sections:
            for occ in occs[:1]:  # Signaler une seule fois par section
                missing.append(MissingFerraillage(
                    reference=getattr(occ, "reference", ""),
                    family=getattr(occ, "family", "INCONNU"),
                    section_text=section,
                    level=getattr(occ, "level", "INCONNU"),
                    has_ferra_detail=False,
                ))

    return missing


# ============================================================================
# 4. Calcul de quantites
# ============================================================================

def compute_concrete_volumes(
    linears: list[LinearMeasure],
) -> tuple[list[ConcreteVolume], float]:
    """Calcule les volumes de beton par type d'element.

    Returns:
        (volumes_par_type, volume_total_m3)
    """
    volumes: list[ConcreteVolume] = []
    total = 0.0

    for lin in linears:
        vol = 0.0
        if lin.section_cm and lin.length_m > 0:
            b_m = lin.section_cm[0] / 100.0
            h_m = lin.section_cm[1] / 100.0
            vol = b_m * h_m * lin.length_m * lin.count

        cv = ConcreteVolume(
            reference=lin.reference,
            family=lin.family,
            section_cm=lin.section_cm,
            length_m=lin.length_m,
            volume_m3=round(vol, 4),
            level=lin.level,
            count=lin.count,
        )
        volumes.append(cv)
        total += vol

    return volumes, round(total, 2)


def compute_steel_ratio(
    elements: list[Any],
) -> dict[str, float]:
    """Calcule le poids d'acier approximatif par categorie.

    Utilise les armatures detectees (nb barres x diametre x longueur).
    """
    steel_by_category: dict[str, float] = {}

    for elem in elements:
        family = getattr(elem, "family", "INCONNU")

        # Chercher les armatures dans les attributs
        for attr_name in ["filants_inf", "filants_sup", "ferr_x", "ferr_y"]:
            arms = getattr(elem, attr_name, None)
            if not arms:
                # Essayer dans un dict
                if isinstance(elem, dict):
                    arms = elem.get(attr_name, [])
                else:
                    continue

            if isinstance(arms, dict):
                arms = [arms]

            for arm in arms:
                if not isinstance(arm, dict):
                    continue
                nb = arm.get("nb", 0)
                phi = arm.get("phi", 0)
                if nb and phi and phi in _MASSE_ACIER:
                    # Longueur estimee (sera precisee avec les cotes)
                    length_est = 6.0  # m par defaut
                    poids = nb * length_est * _MASSE_ACIER[phi]
                    steel_by_category[family] = steel_by_category.get(family, 0) + poids

    return {k: round(v, 2) for k, v in steel_by_category.items()}


# ============================================================================
# 5. Conformite reglementaire (extraction cartouche)
# ============================================================================

def extract_regulatory_hypotheses(
    words: list[dict],
    full_text: str | None = None,
) -> list[RegulatoryHypothesis]:
    """Extrait les hypotheses reglementaires du cartouche.

    Lit le texte et identifie les regles de calcul, classe de beton,
    type d'acier, enrobage, etc.

    Accepte une liste de mots (du cartouche) et en texte complet
    (optionnel) pour detecter les patterns dans n'importe quelle partie
    du cartouche.
    """
    hypotheses: list[RegulatoryHypothesis] = []

    # 1. Essayer d'abord sur le texte complet si fourni
    if full_text:
        text_upper = full_text.upper()
        for pattern, category, template in _REGLEMENTARY_PATTERNS:
            m = pattern.search(text_upper)
            if m:
                value = template.format(*m.groups())
                # Trouver le mot source
                for w in words:
                    if w.get("text", "").upper() in text_upper[:m.start()] or \
                       w.get("text", "").upper() in text_upper[m.end():]:
                        hypotheses.append(RegulatoryHypothesis(
                            category=category,
                            value=value,
                            raw_text=w.get("text", ""),
                            x=w.get("x", 0),
                            y=w.get("y", 0),
                        ))
                        break

    # 2. Mots-cles isolés dans le cartouche
    if not hypotheses:
        standalone = {
            "BAEL": "regles_calcul",
            "B25": "classe_beton", "B30": "classe_beton",
            "HA500": "classe_acier", "HA400": "classe_acier",
            "ENROBAGE": "enrobage_min",
        }
        word_map = {}
        for w in words:
            u = w.get("text", "").upper().strip()
            if u and u not in word_map:
                word_map[u] = w
        for kw, cat in standalone.items():
            if kw in word_map:
                w = word_map[kw]
                hypotheses.append(RegulatoryHypothesis(
                    category=cat, value=kw,
                    raw_text=w.get("text", ""),
                    x=w.get("x", 0), y=w.get("y", 0)))

    # 3. Patterns regex sur chaque mot
    if not hypotheses:
        for w in words:
            text = w.get("text", "").strip()
            upper = text.upper()
            for pattern, category, template in _REGLEMENTARY_PATTERNS:
                m = pattern.search(upper)
                if m:
                    hypotheses.append(RegulatoryHypothesis(
                        category=category,
                        value=template.format(*m.groups()),
                        raw_text=text,
                        x=w.get("x", 0), y=w.get("y", 0)))
                    break

    return hypotheses


# ============================================================================
# 6. Gestion des indices de revision
# ============================================================================

def extract_revision_info(
    words: list[dict],
    full_text: str | None = None,
) -> tuple[str, str, str]:
    """Extrait l'indice de revision, la date et le label du cartouche.

    Returns:
        (indice, date, label)

    Accepte une liste de mots (du cartouche) et en texte complet
    (optionnel) pour detecter les patterns dans n'importe quelle partie
    du cartouche.
    """
    indice = ""
    date = ""
    label = ""

    # 1. Essayer d'abord sur le texte complet si fourni
    if full_text:
        text_upper = full_text.upper()

        # Pattern: "Indice A" (avec ou sans deux-points)
        m = re.search(r"INDICE\s*[:.]?\s*([A-Z])\b", text_upper)
        if m:
            indice = m.group(1)

        # Pattern: date DD/MM/AAAA ou DD-MM-AA
        m = re.search(r"(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})", text_upper)
        if m:
            date = m.group(1)

        # Pattern: "EMISSION A" ou "1ere EMISSION"
        m = re.search(r"(\d+)\s*EMISSION", text_upper)
        if m:
            label = f"{m.group(1)}eme EMISSION"

    # 2. Si pas de correspondance dans le texte complet,
    #    chercher sur les mots individuels du cartouche
    if not indice or not date:
        for w in words:
            text = w.get("text", "")
            upper = text.upper().strip()

            if not indice and upper == "INDICE":
                # Chercher la lettre d'indice dans les mots suivants
                for w2 in words:
                    t2 = w2.get("text", "").strip()
                    if re.match(r"^[A-Z]$", t2):
                        indice = t2.upper()
                        break

            if not date and upper == "DATE":
                # Chercher la date dans les mots suivants
                for w2 in words:
                    t2 = w2.get("text", "").strip()
                    if re.match(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", t2):
                        date = t2
                        break

    # 3. Fallback: patterns originaux sur chaque mot
    if not indice:
        for w in words:
            m = re.search(r"Indice\s*:\s*([A-Z])", w.get("text", "").upper())
            if m:
                indice = m.group(1).upper()
                break
    if not date:
        for w in words:
            m = re.search(r"(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})", w.get("text", ""))
            if m:
                date = m.group(1)
                break

    return indice, date, label


# ============================================================================
# 7. Vocabulaire et unites locales
# ============================================================================

def normalize_section_cm(value: str | int | float) -> float:
    """Normalise une cote en metres.

    Regle metier : > 10 → cm, sinon → m.
    """
    v = float(value)
    return v / 100.0 if v > _CM_THRESHOLD else v


def parse_rebar_annotation(text: str) -> list[dict]:
    """Parse une annotation de ferraillage type "8T12" ou "4T10+2T12".

    Returns:
        [{"nb": 8, "phi": 12, "kind": "T"}, ...]
    """
    rebars: list[dict] = []
    # Pattern : 8T12, 4HA10, 10TOR16, etc.
    for m in re.finditer(
        r"(\d+)\s*(T|HA|TOR|Ø|PHI)\s*(\d{1,2})", text, re.I
    ):
        rebars.append({
            "nb": int(m.group(1)),
            "kind": m.group(2).upper(),
            "phi": int(m.group(3)),
        })
    return rebars


def parse_espacement(text: str) -> float | None:
    """Parse un espacement type "Esp=15" ou "e=15".

    Returns:
        Espacement en cm, ou None.
    """
    m = re.search(r"(?:Esp|e)\s*=\s*(\d+(?:[.,]\d+)?)", text, re.I)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def parse_charge(text: str) -> tuple[float, str] | None:
    """Parse une charge type "250 daN/m2" ou "350 kg/m2".

    Returns:
        (valeur, unite) ou None.
    """
    m = re.search(r"(\d+)\s*(daN/m[sup2²2]|kg/m[sup2²2]|kN/m2)", text, re.I)
    if m:
        return float(m.group(1)), m.group(2)
    return None


# ============================================================================
# Pipeline metier complet
# ============================================================================

def generate_metre_report(
    elements: list[Any],
    words: list[dict] | None = None,
    ferra_sections: set[str] | None = None,
    cotes: dict[str, float] | None = None,
) -> MetreReport:
    """Genere le rapport complet du metre avec toutes les regles metier.

    1. Coherence plan <-> tableaux
    2. Lineaire par type
    3. Verification ferraillage
    4. Volume beton
    5. Hypotheses reglementaires
    6. Revision
    """
    report = MetreReport()
    report.total_elements = len(elements)

    # 1. Coherence plan/tableaux
    if words:
        table_refs = extract_table_refs_from_text(words)
        report.discrepancies = check_plan_table_coherence(elements, table_refs)

    # 2. Lineaire par type
    report.linears = compute_linear_by_type(elements, cotes)

    # 3. Verification ferraillage
    report.missing_ferraillage = check_ferraillage_coverage(elements, ferra_sections)

    # 4. Volume beton
    report.concrete_volumes, report.total_concrete_m3 = compute_concrete_volumes(
        report.linears
    )

    # 5. Hypotheses reglementaires
    if words:
        report.regulatory_hypotheses = extract_regulatory_hypotheses(words)

    # 6. Revision
    if words:
        report.revision_index, report.revision_date, report.revision_label = \
            extract_revision_info(words)

    # 7. Stats
    refs = set()
    for elem in elements:
        refs.add(getattr(elem, "reference", ""))
    report.total_types = len(refs)

    # Avertissements
    if report.discrepancies:
        n_errors = sum(1 for d in report.discrepancies if d.severity == "ERROR")
        if n_errors:
            report.warnings.append(
                f"{n_errors} ecarts majeurs plan/tableaux detects."
            )

    if report.missing_ferraillage:
        report.warnings.append(
            f"{len(report.missing_ferraillage)} sections sans detail ferraillage."
        )

    return report


def format_metre_report(report: MetreReport) -> str:
    """Formate le rapport metre en texte lisible."""
    lines: list[str] = []

    lines.append("=" * 80)
    lines.append("RAPPORT METRE - GENIE CIVIL / BETON ARME")
    lines.append("=" * 80)

    # En-tete
    if report.revision_index:
        lines.append(f"Indice: {report.revision_index}")
    if report.revision_date:
        lines.append(f"Date: {report.revision_date}")
    if report.revision_label:
        lines.append(f"{report.revision_label}")
    lines.append(f"Elements totaux: {report.total_elements}")
    lines.append(f"Types distincts: {report.total_types}")
    lines.append("")

    # 1. Hypotheses reglementaires
    if report.regulatory_hypotheses:
        lines.append("--- HYPOTHESES REGLEMENTAIRES ---")
        seen = set()
        for h in report.regulatory_hypotheses:
            key = (h.category, h.value)
            if key not in seen:
                seen.add(key)
                lines.append(f"  {h.category}: {h.value}")
        lines.append("")

    # 2. Coherence plan/tableaux
    if report.discrepancies:
        lines.append("--- ECARTS PLAN / TABLEAUX RECAPITULATIFS ---")
        for d in report.discrepancies:
            marker = "!!!" if d.severity == "ERROR" else "! " if d.severity == "WARNING" else "  "
            lines.append(
                f"{marker}{d.family} ({d.prefix}): "
                f"plan={d.plan_count} rep., tableau={d.table_count} lignes"
            )
            if d.missing_in_table:
                lines.append(
                    f"    Absents du tableau: {', '.join(d.missing_in_table[:10])}")
            if d.missing_in_plan:
                lines.append(
                    f"    Absents du plan: {', '.join(d.missing_in_plan[:10])}")
        lines.append("")

    # 3. Lineaire par type
    lines.append("--- LINEAIRE PAR TYPE ---")
    header = f"  {'Ref':<10} {'Famille':<20} {'Section':<12} {'Niveau':<12} {'Nb':<5} {'Long.m':<8} {'Total.m':<8}"
    lines.append(header)
    lines.append("  " + "-" * 76)
    for lin in report.linears:
        sec = f"{lin.section_cm[0]}x{lin.section_cm[1]}" if lin.section_cm else "-"
        total = lin.length_m * lin.count
        lines.append(
            f"  {lin.reference:<10} {lin.family:<20} {sec:<12} {lin.level:<12} "
            f"{lin.count:<5} {lin.length_m:<8.2f} {total:<8.2f}"
        )
    lines.append("")

    # 4. Volume beton
    lines.append("--- VOLUME BETON ESTIME ---")
    total_vol = 0.0
    for cv in report.concrete_volumes:
        if cv.volume_m3 > 0:
            sec = f"{cv.section_cm[0]}x{cv.section_cm[1]}" if cv.section_cm else "-"
            lines.append(
                f"  {cv.reference:<10} {cv.family:<20} {sec:<12} "
                f"{cv.level:<12} {cv.count}x{cv.length_m:.1f}m = {cv.volume_m3:.3f} m3"
            )
            total_vol += cv.volume_m3
    lines.append(f"  {'TOTAL':.<54} {total_vol:.3f} m3")
    lines.append("")

    # 5. Ferraillage manquant
    if report.missing_ferraillage:
        lines.append("--- SECTIONS SANS DETAIL DE FERRAILLAGE ---")
        for mf in report.missing_ferraillage:
            lines.append(
                f"  ! {mf.reference} ({mf.family}) section {mf.section_text} "
                f"- niveau {mf.level}")
        lines.append("")

    # 6. Avertissements
    if report.warnings:
        lines.append("--- AVERTISSEMENTS ---")
        for w in report.warnings:
            lines.append(f"  [!] {w}")

    return "\n".join(lines)
