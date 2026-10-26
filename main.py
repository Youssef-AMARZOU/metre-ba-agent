#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py -- Point d'entree principal de PlanBA Metre Extracteur.

Pipeline complet :
  1. extract_plan.py  -> output/plan_data.json
  2. build_metre.py   -> output/metre_genere.xlsx
  3. optimisation_chantiers.py -> output/optimisation_chantiers.xlsx
  4. rapport_metre.py -> output/rapport_metre.pdf

Usage :
  python main.py                          # Lance l'application GUI
  python main.py --cli --input plan.pdf   # Pipeline complet CLI
"""
import argparse
import json
import os
import sys
from pathlib import Path

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
else:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
else:
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from core.paths import get_output_dir


def close_splash():
    """Ferme le splash natif PyInstaller s'il est actif.

    En mode frozen avec --splash, le splash reste au premier plan et
    intercepte les entrees tant que pyi_splash.close() n'est pas appele.
    Obligatoire en mode CLI (aucune fenetre Tk n'est creee pour le fermer).
    """
    try:
        import pyi_splash
        if pyi_splash.is_alive():
            pyi_splash.close()
    except Exception:
        pass  # mode script ou splash deja ferme


def launch_gui():
    from ui.desktop_app import launch_app
    launch_app()


from core.vision_bridge import run_vision_hybride  # noqa: E402


def run_cli(input_path, output_dir=None, projet_nom=None, vision_mode="auto",
            largeur_hors_tout_m=None):
    """Pipeline complet CLI : extraction + metrique + optimisation + rapport."""
    from extract_plan import LocalPlanExtractor
    from build_metre import MetreGenerator
    from optimisation_chantiers import generer_optimisation
    from rapport_metre import generer_rapport

    close_splash()
    output_dir = str(output_dir or get_output_dir())

    plan_json = os.path.join(output_dir, "plan_data.json")
    metre_xlsx = os.path.join(output_dir, "metre_genere.xlsx")
    optim_xlsx = os.path.join(output_dir, "optimisation_chantiers.xlsx")
    rapport_pdf = os.path.join(output_dir, "rapport_metre.pdf")
    note_pdf = os.path.join(output_dir, "note_calculs_chantier.pdf")

    print("=" * 60)
    print("  PlanBA -- Pipeline Metre Beton Arme")
    print("=" * 60)

    input_ext = Path(input_path).suffix.lower()

    # --- Etape 1 : Extraction vectorielle multi-pages 100% locale ---
    print(f"\n[1/5] Extraction du plan : {input_path}")
    from core.local_extractor import (
        VectorPlanExtractor, extract_plan_auto, is_raster_pdf, ExtractionError)
    from extract_plan import dump_raw_blocks

    if input_ext == ".json":
        print("  Format JSON -- chargement direct")
        with open(input_path, encoding="utf-8") as f:
            plan_data = json.load(f)
    elif input_ext == ".pdf":
        print("  Parsage hybride texte + OCR cible via PyMuPDF")
        n_total_ref = {"n": 0}

        def cb(page_num, total, role):
            n_total_ref["n"] = total
            if total <= 30 or page_num % 20 == 0 or page_num == total:
                print(f"  Page {page_num}/{total} : {role}")

        plan_data = extract_plan_auto(input_path, progress_callback=cb)
    else:
        print(f"  Routeur automatique pour {input_ext}...")
        plan_data = extract_plan_auto(input_path)

    if projet_nom:
        plan_data.setdefault("projet", {})["nom"] = projet_nom

    # --- Couche Computer Vision hybride (pages scannees / SHX explosees) ---
    print(f"\n[1b/5] Computer Vision hybride (mode {vision_mode})...")
    vision = run_vision_hybride(input_path, output_dir, projet_nom,
                                mode=vision_mode)
    if vision["pages_vision"]:
        plan_data.setdefault("_meta", {})["vision"] = {
            "pages": vision["pages_vision"],
            "overlays": vision["overlays"],
            "xlsx_certifie": vision["xlsx"],
            "n_records": len(vision["records"]),
        }
        cat0 = plan_data.get("catalogue_types", {})
        vectoriel_vide = not any(len(cat0.get(k, {})) for k in
                                 ("semelles", "poteaux", "poutres"))
        if vectoriel_vide and vision["plan_data_fallback"]:
            print("  Extraction vectorielle vide -> bascule sur les "
                  "éléments Computer Vision")
            meta_old = plan_data.get("_meta", {})
            plan_data = vision["plan_data_fallback"]
            plan_data["_meta"]["vision"] = meta_old.get("vision", {})
            plan_data["_meta"]["avertissements"].append(
                "Éléments issus de la Computer Vision (OCR/VLM) : vérifier "
                "les vignettes d'audit avant utilisation chantier.")

    c = plan_data["catalogue_types"]
    impl = plan_data["implantations"]
    n_sem_types = len(c["semelles"])
    n_sem_pos = len(impl["semelles"])
    n_pot = len(c["poteaux"])
    n_pout = len(c["poutres"])
    meta = plan_data.get("_meta", {})
    print(f"  Pages scannees : {meta.get('total_pages_scanned', '?')} "
          f"(tableaux : {meta.get('pages_tableau', [])}, "
          f"plans : {meta.get('pages_plan', [])}, "
          f"ignorees : {meta.get('pages_ignorees', 0)})")
    print(f"  Semelles : {n_sem_types} types, {n_sem_pos} positionnees sur axes")
    print(f"  Poteaux  : {n_pot} types detailles | Poutres : {n_pout} types detaillees")
    for a in meta.get("avertissements", []):
        print(f"  âš  {a}")
    for h in meta.get("hypotheses", []):
        print(f"  â€¢ Hypothese : {h}")

    # --- Metre des surfaces (plan d'architecture : pieces / etages) ---
    print(f"\n[1c/5] Métré des surfaces (pièces / étages)...")
    from core.vision_bridge import run_surface_metre
    surfaces = run_surface_metre(input_path, output_dir, projet_nom,
                                 known_width_m=largeur_hors_tout_m)
    if surfaces["xlsx"]:
        plan_data.setdefault("_meta", {})["surfaces"] = {
            "xlsx": surfaces["xlsx"], "total_m2": surfaces["total_m2"],
            "summary": surfaces["summary"], "overlays": surfaces["overlays"],
            "rooms": [r.to_dict() for rep in surfaces["reports"] for r in rep.rooms],
        }

    # --- Garde-fou partage CLI/UI (source de verite unique) ---
    from core.local_extractor import verifier_livrables_ou_lever
    from core.vision_bridge import plan_data_is_placeholder
    try:
        if surfaces["xlsx"] and plan_data_is_placeholder(plan_data):
            raise ExtractionError("Aucun ouvrage BA réel (géométrie par défaut).")
        verifier_livrables_ou_lever(plan_data)
    except Exception as e:
        with open(plan_json, "w", encoding="utf-8") as f:
            json.dump(plan_data, f, ensure_ascii=False, indent=2)
        if surfaces["xlsx"]:
            # Plan d'architecture sans ouvrage BA : le metre des surfaces
            # EST le livrable (pas une erreur).
            print("\n  Aucun ouvrage BA (semelle/poteau/poutre) : plan "
                  "d'architecture -> livrable = métré des surfaces.")
            print("=" * 60)
            print(f"  metre_surfaces.xlsx         : {surfaces['xlsx']}")
            for ov in surfaces["overlays"]:
                print(f"  overlay surfaces            : {ov}")
            return {"plan_data": plan_json, "metre_surfaces": surfaces["xlsx"],
                    "surfaces_overlays": surfaces["overlays"], "mode": "surfaces"}
        raise

    # --- Pipeline modular : extraction zero-miss ---
    if input_ext == ".pdf":
        try:
            from core.pipeline.runner import PipelineRunner
            print("  [Pipeline] Extraction modular zero-miss...")
            runner = PipelineRunner(verbose=False)
            pipeline_report = runner.run(input_path)
            stats = pipeline_report.get("rapport", {}).get("statistiques", {})
            print(f"  [Pipeline] {stats.get('total_instances', 0)} instances, "
                  f"{stats.get('instances_completes', 0)} completes")
            print(f"  [Pipeline] Etat: {pipeline_report['validation']['etat']}")
            plan_data["_pipeline_report"] = pipeline_report
        except Exception as e:
            print(f"  [Pipeline] Erreur: {e}")
            # Fallback vers l'extracteur legacy
            try:
                from core.zero_miss_extractor import run_zero_miss_extraction
                print("  [Zero-Miss] Fallback extraction legacy...")
                zm_result = run_zero_miss_extraction(input_path)
                plan_data["_zero_miss"] = zm_result
            except Exception as e2:
                print(f"  [Zero-Miss] Erreur: {e2}")

    with open(plan_json, "w", encoding="utf-8") as f:
        json.dump(plan_data, f, ensure_ascii=False, indent=2)
    print(f"  -> {plan_json}")

    # --- Etape 2 : Generation Excel metrique ---
    print(f"\n[2/5] Generation du metre Excel...")
    gen = MetreGenerator(plan_data)
    gen.generer(metre_xlsx)
    print(f"  -> {metre_xlsx}")

    # --- Etape 3 : Optimisation decoupe ---
    print(f"\n[3/5] Optimisation de la decoupe des barres...")
    generer_optimisation(plan_data, optim_xlsx)
    print(f"  -> {optim_xlsx}")

    # --- Etape 4 : Rapport PDF ---
    print(f"\n[4/5] Generation du rapport d'audit PDF...")
    generer_rapport(plan_data, rapport_pdf)
    print(f"  -> {rapport_pdf}")

    # --- Etape 5 : Note de calculs chantier (meme donnees que l'Excel) ---
    print(f"\n[5/5] Generation de la note de calculs chantier PDF...")
    from build_metre import adapter_plan_vers_injecteur
    from generators.generate_pdf_note import generer_note_calcul_chantier
    donnees_note = adapter_plan_vers_injecteur(plan_data)
    if projet_nom:
        donnees_note["projet"] = projet_nom
    generer_note_calcul_chantier(donnees_note, note_pdf)
    print(f"  -> {note_pdf}")

    print("\n" + "=" * 60)
    print("  Pipeline termine avec succes !")
    print("=" * 60)
    print(f"  plan_data.json              : {plan_json}")
    print(f"  metre_genere.xlsx           : {metre_xlsx}")
    print(f"  optimisation_chantiers.xlsx : {optim_xlsx}")
    print(f"  rapport_metre.pdf           : {rapport_pdf}")
    print(f"  note_calculs_chantier.pdf   : {note_pdf}")
    if vision["xlsx"]:
        print(f"  metre_certifie_BET.xlsx     : {vision['xlsx']}")
        for ov in vision["overlays"]:
            print(f"  overlay d'audit             : {ov}")

    return {
        "plan_data": plan_json,
        "metre": metre_xlsx,
        "optimisation": optim_xlsx,
        "rapport": rapport_pdf,
        "note": note_pdf,
        "metre_certifie_bet": vision["xlsx"],
        "metre_surfaces": surfaces["xlsx"],
        "audit_overlays": vision["overlays"],
    }


def main():
    ap = argparse.ArgumentParser(
        description="PlanBA -- Metre Extracteur (GUI ou CLI)")
    ap.add_argument("--cli", action="store_true",
                    help="Mode ligne de commande")
    ap.add_argument("--input", "-i", default=None,
                    help="Chemin du fichier d'entree (mode CLI)")
    ap.add_argument("--out", "-o", default=None,
                    help="Repertoire de sortie (mode CLI)")
    ap.add_argument("--projet-nom", default=None,
                    help="Nom du projet (optionnel)")
    ap.add_argument("--vision", choices=("auto", "force", "off"),
                    default="auto",
                    help="Computer Vision : auto (pages scannees / SHX "
                         "uniquement), force (toutes les pages), off")
    ap.add_argument("--largeur-hors-tout", type=float, default=None,
                    help="Largeur hors-tout reelle du batiment (m) pour "
                         "calibrer l'echelle du metre des surfaces")
    args = ap.parse_args()

    if args.cli:
        if not args.input:
            print("ERREUR : --input requis en mode CLI")
            sys.exit(1)
        run_cli(args.input, args.out, args.projet_nom,
                vision_mode=args.vision,
                largeur_hors_tout_m=args.largeur_hors_tout)
    else:
        launch_gui()


if __name__ == "__main__":
    main()
