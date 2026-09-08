#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/validator.py -- Pre-flight validation des plans importes.

Bloque tout document qui n'est pas un plan d'ingenierie structurelle
(factures, CV, contrats, images quelconques) avant l'analyse.
"""
import os

import fitz  # PyMuPDF

# NOTE -- ezdxf est importe paresseusement dans _validate_cad() uniquement.
# ezdxf tire numpy a l'import : le charger au niveau module casserait
# l'analyse des PDF dans l'exe (numpy exclu/charge differemment) et
# penaliserait le demarrage pour un usage PDF. Un fichier PDF ne doit
# JAMAIS provoquer le chargement d'ezdxf.

# Dictionnaire de mots-cles specifiques au Genie Civil / Beton Arme
CIVIL_KEYWORDS = {
    # Elements structuraux
    "semelle", "poteau", "poutre", "longrine", "chainage", "chaînage",
    "voile", "radier", "dallage", "amorce", "fut", "fût",
    # Armatures et ferraillage
    "armature", "acier", "cadre", "epingle", "étrier", "etrier",
    "nappe", "ha", "tor", "recouvrement", "enrobage", "fe500", "fe400",
    # DAO et Cartouche
    "coffrage", "ferraillage", "fondation", "implantation", "ech", "echelle",
    "coupe", "detail", "détail", "axe", "file", "repartition",
}


class PlanSanityValidator:
    @classmethod
    def validate_file(cls, file_path: str) -> tuple[bool, str, int]:
        """
        Valide si le document importe est un plan d'ingenierie structurelle.
        Retourne : (est_valide: bool, message: str, score: int)
        """
        if not os.path.exists(file_path):
            return False, "Fichier introuvable.", 0

        ext = os.path.splitext(file_path)[1].lower()

        # 1. Cas des fichiers AutoCAD natifs
        if ext in [".dxf", ".dwg"]:
            return cls._validate_cad(file_path, ext)

        # 2. Cas des documents PDF
        elif ext == ".pdf":
            return cls._validate_pdf(file_path)

        # 3. Cas des images (scans / exports raster)
        elif ext in [".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"]:
            return cls._validate_image(file_path)

        return False, f"Extension non supportée : {ext}", 0

    @classmethod
    def _validate_cad(cls, path: str, ext: str) -> tuple[bool, str, int]:
        """CAD natif : JAMAIS de blocage dur (directive).
        Un fichier illisible/vide est accepte avec reserve — l'analyse
        reelle (extraction) declenchera une erreur explicite si besoin."""
        if ext == ".dwg":
            # DWG est par definition un dessin technique
            return True, "Fichier DWG AutoCAD reconnu comme dessin technique.", 90

        # Import paresseux : ezdxf (et son numpy) ne doit etre charge QUE
        # pour un vrai fichier DXF.
        try:
            import ezdxf
        except ImportError as e:
            return True, (
                f"⚠ DXF accepté mais le moteur AutoCAD est indisponible "
                f"({e}) — l'analyse vérifiera le contenu."), 50

        try:
            doc = ezdxf.readfile(path)
            msp = doc.modelspace()
            entity_count = len(msp)
            if entity_count < 10:
                return True, (
                    f"⚠ DXF accepté avec réserve ({entity_count} entités DAO) "
                    f"— contenu à vérifier à l'analyse."), 10
            return True, (
                f"Fichier DXF technique validé ({entity_count} entités DAO)."), 95
        except Exception as e:
            return True, (
                f"⚠ DXF illisible ({e}) — accepté pour analyse ; "
                f"l'extraction vérifiera le contenu réel."), 0

    @classmethod
    def _validate_pdf(cls, path: str) -> tuple[bool, str, int]:
        """PDF : scan MULTI-PAGES (jusqu'a 10 pages) + jamais de blocage dur.

        - Mots-cles et dessins vectors agreges sur toutes les pages scannees.
        - Plancher de score 50 si le PDF est vectoriel ou multi-pages.
        - Un PDF (meme textuel) est toujours accepte (is_valid=True) :
          l'extraction en aval leve une erreur explicite si aucun element
          structural n'est detecte (zero mock, mais pas de blocage pre-flight).
        """
        try:
            with fitz.open(str(path)) as doc:
                n_pages = len(doc)
                if n_pages == 0:
                    return True, (
                        "⚠ PDF sans page — accepté pour analyse "
                        "(contenu à vérifier)."), 0

                pages_to_scan = min(n_pages, 10)
                text_parts = []
                word_set = set()
                total_drawings = 0
                for pi in range(pages_to_scan):
                    page = doc[pi]
                    text_parts.append(page.get_text().lower())
                    word_set |= {
                        w[4].lower().strip(":,.;()")
                        for w in page.get_text("words")
                    }
                    try:
                        total_drawings += len(page.get_drawings())
                    except Exception:
                        pass
                text = "\n".join(text_parts)

                # Geometrie de la premiere page (format grand / paysage)
                rect = doc[0].rect
                score = 0
                if rect.width > rect.height:
                    score += 15
                if rect.width > 800 or rect.height > 800:
                    score += 10

                # Analyse semantique multi-pages
                matches = CIVIL_KEYWORDS.intersection(word_set)
                has_rebar_codes = any(
                    code in text
                    for code in ["ha", "t6", "t8", "t10", "t12", "t14", "t16", "t20"]
                )
                if has_rebar_codes:
                    score += 20
                score += min(len(matches) * 8, 45)

                # Densite vectorielle cumulee
                if total_drawings > 50:
                    score += 15

                # Plancher : PDF vectoriel OU multi-pages = document technique
                if total_drawings > 0 or n_pages > 1:
                    score = max(score, 50)

                if score >= 40:
                    details = (
                        f"Plan validé (Score : {score}/100 | "
                        f"Pages : {n_pages} | "
                        f"Termes clés : {', '.join(list(matches)[:5]) or '—'})"
                    )
                    return True, details, score

                return True, (
                    f"⚠ Accepté avec réserve (Score : {score}/100) — "
                    f"l'analyse vérifiera le contenu réel du document."
                ), score

        except Exception as e:
            return True, (
                f"⚠ Lecture PDF difficile ({e}) — accepté pour analyse ; "
                f"l'extraction vérifiera le contenu réel."), 0

    @classmethod
    def _validate_image(cls, path: str) -> tuple[bool, str, int]:
        from PIL import Image

        try:
            with Image.open(path) as img:
                w, h = img.size
                ratio = w / h if h > 0 else 1

                # Un plan scanne depasse generalement 1500 px et est paysage
                if w < 800 and h < 800:
                    return False, (
                        "Résolution d'image trop faible pour un plan technique (< 800px)."
                    ), 15

                score = 30
                if ratio > 1.2:
                    score += 15
                if w >= 2000 or h >= 2000:
                    score += 15

                return True, (
                    f"Image technique acceptée pour analyse visuelle "
                    f"(Résolution : {w}x{h})."
                ), score
        except Exception as e:
            return False, f"Image invalide : {e}", 0
