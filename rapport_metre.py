#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rapport_metre.py -- Rapport d'audit PDF avec ratios acier kg/m3 et bilan matiere.
Genere un PDF via ReportLab.
"""
import json
import math
import os
import sys
from datetime import datetime
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


def _collect_data(plan_data):
    """Calcule le bilan materiau complet."""
    catalogue = plan_data.get("catalogue_types", {})
    implantations = plan_data.get("implantations", {})

    masse_lineaire = {
        6: 0.222, 8: 0.395, 10: 0.617, 12: 0.888,
        14: 1.210, 16: 1.580, 20: 2.470, 25: 3.850, 32: 6.310,
    }

    vol_beton = 0.0
    poids_acier = 0.0
    poids_par_diam = {}
    poids_par_famille = {"Semelles": 0, "Poteaux": 0, "Poutres": 0}
    nb_elements = {"Semelles": 0, "Poteaux": 0, "Poutres": 0}
    donnees_manquantes = []

    # Semelles
    sem_impl = implantations.get("semelles", [])
    for inst in sem_impl:
        tk = inst.get("type", "")
        dims = catalogue.get("semelles", {}).get(tk, {})
        a = dims.get("a", 0)
        b = dims.get("b", 0)
        h = dims.get("h", 0)
        vol_beton += (a or 0) * (b or 0) * (h or 0)
        nb_elements["Semelles"] += 1

        for key, ferr_key in [("ferr_x", "ferr_x"), ("ferr_y", "ferr_y")]:
            ferr = dims.get(ferr_key, {})
            nb = ferr.get("nb", 0)
            phi = ferr.get("phi", 12)
            if nb > 0:
                long = (a if key == "ferr_x" else b) + 0.20
                ml = masse_lineaire.get(phi, phi * phi / 162.0)
                p = ml * long * nb
                poids_acier += p
                poids_par_diam[phi] = poids_par_diam.get(phi, 0) + p
                poids_par_famille["Semelles"] += p

    # Poteaux
    pot_impl = implantations.get("poteaux", [])
    for inst in pot_impl:
        tk = inst.get("type", "")
        dims = catalogue.get("poteaux", {}).get(tk, {})
        a = dims.get("a", 0.25)
        b = dims.get("b", 0.35)
        hauteur = inst.get("hauteur", 3.0)
        vol_beton += a * b * hauteur
        nb_elements["Poteaux"] += 1

        for lb in dims.get("long_bars", []):
            phi = lb["phi"]
            nb = lb["nb"]
            if phi <= 0 or nb <= 0:
                continue
            long = hauteur + 0.50
            ml = masse_lineaire.get(phi, phi * phi / 162.0)
            p = ml * long * nb
            poids_acier += p
            poids_par_diam[phi] = poids_par_diam.get(phi, 0) + p
            poids_par_famille["Poteaux"] += p

        cadres = dims.get("cadres", {})
        phi_c = cadres.get("phi", 6)
        esp = cadres.get("esp", 0.15)
        if esp > 0:
            nb_cadres = math.ceil(hauteur / esp) + 2
            perimetre = 2 * (a + b) + 0.10
            ml = masse_lineaire.get(phi_c, phi_c * phi_c / 162.0)
            p = ml * perimetre * nb_cadres
            poids_acier += p
            poids_par_diam[phi_c] = poids_par_diam.get(phi_c, 0) + p
            poids_par_famille["Poteaux"] += p

    # Poutres
    pou_impl = implantations.get("poutres", [])
    for inst in pou_impl:
        tk = inst.get("type", "")
        dims = catalogue.get("poutres", {}).get(tk, {})
        b_sect = dims.get("b", 0.20)
        h_sect = dims.get("h", 0.30)
        portee = inst.get("portee")
        if portee is None:
            # Portee non cotee sur le plan : pas d'invention de valeur
            nb_elements["Poutres"] += 1
            donnees_manquantes.append(
                f"{tk} : portée non renseignée — poutre exclue du bilan.")
            continue
        vol_beton += b_sect * h_sect * portee
        nb_elements["Poutres"] += 1

        for fi in dims.get("filants_inf", []) + dims.get("filants_sup", []):
            phi = fi["phi"]
            nb = fi["nb"]
            if phi <= 0 or nb <= 0:
                continue
            long = portee + 0.50
            ml = masse_lineaire.get(phi, phi * phi / 162.0)
            p = ml * long * nb
            poids_acier += p
            poids_par_diam[phi] = poids_par_diam.get(phi, 0) + p
            poids_par_famille["Poutres"] += p

        cadres = dims.get("cadres", {})
        phi_c = cadres.get("phi", 6)
        esp = cadres.get("esp", 0.18)
        if esp > 0:
            nb_cadres = math.ceil(portee / esp) + 2
            perimetre = 2 * (b_sect + h_sect) + 0.10
            ml = masse_lineaire.get(phi_c, phi_c * phi_c / 162.0)
            p = ml * perimetre * nb_cadres
            poids_acier += p
            poids_par_diam[phi_c] = poids_par_diam.get(phi_c, 0) + p
            poids_par_famille["Poutres"] += p

    ratio_kg_m3 = poids_acier / vol_beton if vol_beton > 0 else 0

    return {
        "vol_beton_m3": vol_beton,
        "poids_acier_kg": poids_acier,
        "ratio_kg_m3": ratio_kg_m3,
        "poids_par_diam": poids_par_diam,
        "poids_par_famille": poids_par_famille,
        "nb_elements": nb_elements,
        "nb_total": sum(nb_elements.values()),
        "donnees_manquantes": donnees_manquantes,
    }


def generer_rapport(plan_data, output_path):
    """Genere le rapport PDF d'audit."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm, mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    bilan = _collect_data(plan_data)
    projet = plan_data.get("projet", {})

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    titre_style = ParagraphStyle(
        "Titre", parent=styles["Title"], fontSize=16, spaceAfter=20,
    )
    sous_titre_style = ParagraphStyle(
        "SousTitre", parent=styles["Heading2"], fontSize=13, spaceAfter=10,
    )
    normal_style = styles["Normal"]

    elements = []

    # Titre
    elements.append(Paragraph(
        f"Rapport d'Audit - Métré Béton Armé", titre_style))
    elements.append(Paragraph(
        f"Projet : {projet.get('nom', 'N/A')}", normal_style))
    elements.append(Paragraph(
        f"Date : {datetime.now().strftime('%d/%m/%Y %H:%M')}", normal_style))
    elements.append(Spacer(1, 20))

    # Bilan materiau
    elements.append(Paragraph("1. Bilan Matière", sous_titre_style))
    data_bilan = [
        ["Paramètre", "Valeur", "Unité"],
        ["Volume béton total", f"{bilan['vol_beton_m3']:.3f}", "m³"],
        ["Poids acier total", f"{bilan['poids_acier_kg']:.1f}", "kg"],
        ["Ratio acier", f"{bilan['ratio_kg_m3']:.1f}", "kg/m³"],
        ["Nombre d'éléments", str(bilan['nb_total']), ""],
    ]
    t = Table(data_bilan, colWidths=[5 * cm, 4 * cm, 3 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F5496")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 15))

    # Ventilation par famille
    elements.append(Paragraph("2. Ventilation par Famille", sous_titre_style))
    data_fam = [["Famille", "Poids (kg)", "Volume (m³)", "Nb éléments"]]
    for fam, poid in bilan["poids_par_famille"].items():
        data_fam.append([fam, f"{poid:.1f}", "", str(bilan["nb_elements"][fam])])
    t2 = Table(data_fam, colWidths=[4 * cm, 3 * cm, 3 * cm, 3 * cm])
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F5496")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
    ]))
    elements.append(t2)
    elements.append(Spacer(1, 15))

    # Ventilation par diametre
    elements.append(Paragraph("3. Ventilation par Diamètre", sous_titre_style))
    data_diam = [["Diamètre (mm)", "Poids (kg)", "% du total"]]
    for phi in sorted(bilan["poids_par_diam"].keys()):
        p = bilan["poids_par_diam"][phi]
        pct = (p / bilan["poids_acier_kg"] * 100) if bilan["poids_acier_kg"] > 0 else 0
        data_diam.append([f"T{phi}", f"{p:.1f}", f"{pct:.1f}%"])
    t3 = Table(data_diam, colWidths=[4 * cm, 3 * cm, 3 * cm])
    t3.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F5496")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
    ]))
    elements.append(t3)
    elements.append(Spacer(1, 15))

    # Observations
    elements.append(Paragraph("4. Observations", sous_titre_style))
    if (bilan["vol_beton_m3"] <= 0 or bilan["poids_acier_kg"] <= 0
            or bilan["nb_total"] <= 0):
        obs = ("⚠ ALERTE : Données manquantes — l'extraction n'a produit "
               "aucune quantité exploitable (volume béton ou poids acier nul). "
               "Le plan n'a pas pu être exploité : vérifier l'extraction ou "
               "la nomenclature du document source.")
    else:
        ratio = bilan["ratio_kg_m3"]
        if ratio < 30:
            obs = f"Ratio {ratio:.1f} kg/m³ — dans la norme (25-50 kg/m³ pour BA fondation)."
        elif ratio < 50:
            obs = f"Ratio {ratio:.1f} kg/m³ — acceptable pour structure mixte."
        else:
            obs = (f"Ratio {ratio:.1f} kg/m³ — élevé. Vérifier la conception "
                   "ou les hypothèses de chargement.")
    elements.append(Paragraph(obs, normal_style))

    # Avertissements d'extraction (donnees non lues sur le plan)
    meta = plan_data.get("_meta", {}) if isinstance(plan_data, dict) else {}
    for warn in bilan.get("donnees_manquantes", []):
        elements.append(Paragraph(f"⚠ {warn}", normal_style))
    for warn in meta.get("avertissements", []):
        elements.append(Paragraph(f"⚠ {warn}", normal_style))
    for hyp in meta.get("hypotheses", []):
        elements.append(Paragraph(f"• Hypothèse : {hyp}", normal_style))

    doc.build(elements)
    print(f"Rapport genere : {output_path}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="output/plan_data.json")
    ap.add_argument("--out", default="output/rapport_metre.pdf")
    args = ap.parse_args()
    with open(args.plan, encoding="utf-8") as f:
        plan_data = json.load(f)
    generer_rapport(plan_data, args.out)
