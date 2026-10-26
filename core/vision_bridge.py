#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/vision_bridge.py -- Pont entre la couche Computer Vision (extractors/)
et l'orchestrateur legacy (CLI `main.py` et UI `ui/desktop_app.py`).

Partage par CLI et UI : source de verite unique du routage hybride.
"""
from __future__ import annotations

from pathlib import Path

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")


def plan_data_is_placeholder(plan_data) -> bool:
    """True si le catalogue ne contient que des geometries par defaut
    (`partial_plan_data`) : aucun ouvrage BA reellement lu."""
    cat = (plan_data or {}).get("catalogue_types", {}) or {}
    real = 0
    for fam in ("semelles", "poteaux", "poutres", "longrines", "chainages", "voiles"):
        for ref, item in (cat.get(fam) or {}).items():
            if isinstance(item, dict) and item.get("dimensions_par_defaut"):
                continue
            if str(ref).upper().endswith("_DEFAULT"):
                continue
            real += 1
    return real == 0


def run_surface_metre(input_path, output_dir, projet_nom=None,
                      known_width_m=None, known_height_m=None,
                      hauteur_sous_plafond_m=2.80, log=print):
    """Metre des surfaces (plan d'architecture : pieces / etages / total).

    Image -> OCR local + segmentation ; PDF -> texte natif ou OCR par page.
    Retourne {'reports', 'xlsx', 'overlays', 'total_m2', 'summary'} ;
    ne leve jamais (filet de securite).
    """
    empty = {"reports": [], "xlsx": None, "overlays": [], "total_m2": 0.0,
             "summary": {}, "warnings": []}
    try:
        from extractors.surface_extractor import PlanSurfaceExtractor
        from core.surface_metre_excel import SurfaceMetreExcel

        ext = Path(input_path).suffix.lower()
        overlay_dir = str(Path(output_dir) / "audit_overlays")
        extractor = PlanSurfaceExtractor()
        if ext in IMAGE_EXTS:
            reports = [extractor.extract_image(
                input_path, overlay_dir=overlay_dir,
                known_width_m=known_width_m, known_height_m=known_height_m)]
        elif ext == ".pdf":
            reports = extractor.extract_pdf(
                input_path, overlay_dir=overlay_dir,
                progress_callback=lambda n, t, r: log(f"  Page {n}/{t} : surfaces"))
        else:
            return empty
        reports = [r for r in reports if r.rooms]
        if not reports:
            log("  Surfaces : aucune pièce identifiée")
            return empty

        xlsx = str(Path(output_dir) / "metre_surfaces.xlsx")
        SurfaceMetreExcel(projet_nom or "Projet (surfaces)",
                          hauteur_sous_plafond_m=hauteur_sous_plafond_m
                          ).export(reports, xlsx)
        total = round(sum(r.total_area_m2 for r in reports), 2)
        by_level = {r.level: r.total_area_m2 for r in reports}
        n_rooms = sum(len(r.rooms) for r in reports)
        warnings = [w for r in reports for w in r.warnings]
        log(f"  Surfaces : {n_rooms} pièce(s), {len(reports)} niveau(x), "
            f"total {total} m² -> {xlsx}")
        for w in warnings:
            log(f"  ⚠ {w}")
        return {"reports": reports, "xlsx": xlsx,
                "overlays": [r.overlay_path for r in reports if r.overlay_path],
                "total_m2": total, "warnings": warnings,
                "summary": {"n_rooms": n_rooms, "by_level": by_level,
                            "scale": reports[0].scale_px_per_m,
                            "scale_confidence": reports[0].scale_confidence}}
    except Exception as e:
        log(f"  Surfaces : erreur non bloquante ({e})")
        return empty


def run_vision_hybride(input_path, output_dir, projet_nom=None,
                       mode="auto", log=print):
    """Couche Computer Vision (freemium) : OpenCV + OCR local + VLM optionnel.

    - mode 'auto'  : uniquement les pages scannees / polices SHX explosees
    - mode 'force' : toutes les pages
    - mode 'off'   : desactive
    Retourne {'records', 'plan_data_fallback', 'xlsx', 'pages_vision',
    'overlays'} ; ne leve jamais (filet de securite).
    """
    empty = {"records": [], "plan_data_fallback": None, "xlsx": None,
             "pages_vision": [], "overlays": []}
    if mode == "off" or Path(input_path).suffix.lower() != ".pdf":
        return empty
    try:
        from extractors.vision_extractor import PlanBAVisionExtractor
        from core.bet_contracts import records_to_legacy_plan_data
        from core.populate_modele import PlanBAExcelPopulator

        extractor = PlanBAVisionExtractor()

        def cb(page_num, total, role):
            if role == "vision":
                log(f"  Page {page_num}/{total} : Computer Vision "
                    f"(scan / SHX explosé)")

        result = extractor.extract_pdf(
            input_path, force=(mode == "force"), progress_callback=cb)
        records = result["records"]
        pages_vision = result["pages_vision"]
        overlays = [p["overlay_path"] for p in result["pages"]
                    if p.get("overlay_path")]
        if not pages_vision:
            log("  Vision : aucune page scannée / SHX détectée (chemin "
                "vectoriel conservé)")
            return empty
        log(f"  Vision : {len(pages_vision)} page(s), {len(records)} "
            f"élément(s) structural(aux), OCR={result['pages'][0].get('ocr_engine') if result['pages'] else None}, "
            f"VLM={result['pages'][0].get('vlm_backend') if result['pages'] else None}")
        if not records:
            return {**empty, "pages_vision": pages_vision, "overlays": overlays}

        populator = PlanBAExcelPopulator(
            project_name=projet_nom or "Projet extrait (Vision)")
        xlsx = str(Path(output_dir) / "metre_certifie_BET.xlsx")
        populator.process_and_export(records, xlsx)
        n_bloq = sum(1 for a in populator.engine.anomalies
                     if a.severity == "BLOQUANT")
        log(f"  -> {xlsx} ({len(populator.avant_metre_lines)} lignes, "
            f"{n_bloq} anomalie(s) bloquante(s))")
        for a in populator.engine.anomalies[:10]:
            log(f"  [{a.severity}] {a.element_label} {a.rule_code} : {a.message}")

        plan_fallback = records_to_legacy_plan_data(
            populator.records, projet=projet_nom or "Projet extrait (Vision)",
            normalizer=populator.gatekeeper.normalize_dimensions_to_meters)
        return {"records": records, "plan_data_fallback": plan_fallback,
                "xlsx": xlsx, "pages_vision": pages_vision,
                "overlays": overlays}
    except Exception as e:
        log(f"  Vision : erreur non bloquante ({e})")
        return empty
