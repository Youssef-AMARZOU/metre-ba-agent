#!/usr/bin/env python3
"""
Generate 100 certified BA plans with ALL civil engineering element types.
Covers: semelles, poteaux, poutres, dalles, voiles, escaliers, longrines,
chainages, radiers, murs, redresseurs, massifs, linteaux, semelles filantes,
sablieres, couvertines, poutres d'appui, travers, contre-forts, futs, pieux.
"""
import os, sys, json, random, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymupdf

# ALL civil engineering element types
ELEMENT_TYPES = {
    "semelle": {"prefix": "S", "count": (3, 6), "dims": {"a": (0.6, 2.0), "b": (0.6, 2.0), "h": (0.15, 0.50)}},
    "poteau": {"prefix": "P", "count": (4, 8), "dims": {"a": (0.20, 0.40), "b": (0.20, 0.40)}},
    "poutre": {"prefix": "N", "count": (5, 10), "dims": {"b": (0.15, 0.40), "h": (0.25, 0.60)}},
    "dalle": {"prefix": "D", "count": (3, 6), "dims": {"ep": (0.12, 0.25)}},
    "voile": {"prefix": "V", "count": (2, 5), "dims": {"ep": (0.15, 0.30), "h": (2.0, 4.0)}},
    "escalier": {"prefix": "ESC", "count": (1, 2), "dims": {"nb_marches": (4, 15), "giron": (25, 35)}},
    "longrine": {"prefix": "LG", "count": (2, 4), "dims": {"b": (0.20, 0.40), "h": (0.30, 0.50)}},
    "chainage": {"prefix": "CH", "count": (2, 4), "dims": {"b": (0.15, 0.25), "h": (0.20, 0.30)}},
    "radier": {"prefix": "R", "count": (1, 2), "dims": {"ep": (0.20, 0.40)}},
    "mur": {"prefix": "M", "count": (2, 4), "dims": {"ep": (0.15, 0.30), "h": (2.0, 3.5)}},
    "redresseur": {"prefix": "RD", "count": (1, 3), "dims": {"a": (0.30, 0.60), "b": (0.30, 0.60)}},
    "massif": {"prefix": "MS", "count": (1, 2), "dims": {"a": (1.0, 3.0), "b": (1.0, 3.0), "h": (0.5, 1.0)}},
    "linteau": {"prefix": "LT", "count": (2, 4), "dims": {"b": (0.15, 0.25), "h": (0.20, 0.35)}},
    "semelle_filante": {"prefix": "SF", "count": (1, 3), "dims": {"b": (0.40, 0.80), "h": (0.15, 0.30)}},
    "sabliere": {"prefix": "SB", "count": (1, 2), "dims": {"b": (0.15, 0.25), "h": (0.20, 0.30)}},
    "couvertine": {"prefix": "CV", "count": (1, 2), "dims": {"b": (0.15, 0.25), "h": (0.15, 0.25)}},
    "poutre_appui": {"prefix": "PA", "count": (1, 3), "dims": {"b": (0.15, 0.30), "h": (0.25, 0.45)}},
    "travers": {"prefix": "TR", "count": (1, 2), "dims": {"b": (0.15, 0.25), "h": (0.20, 0.35)}},
    "contre_fort": {"prefix": "CF", "count": (1, 2), "dims": {"a": (0.30, 0.60), "h": (2.0, 4.0)}},
    "fut": {"prefix": "F", "count": (1, 2), "dims": {"diam": (0.30, 0.80)}},
    "pieu": {"prefix": "PIE", "count": (2, 4), "dims": {"diam": (0.30, 0.60), "l": (5.0, 15.0)}},
}

REBAR_PATTERNS = ["{nb}HA{phi}", "{nb}T{phi}"]


def rand_range(lo, hi):
    return round(random.uniform(lo, hi), 2)


def gen_rebar():
    nb = random.choice([4, 6, 8, 10, 12])
    phi = random.choice([8, 10, 12, 14, 16, 20])
    fmt = random.choice(REBAR_PATTERNS)
    return fmt.format(nb=nb, phi=phi)


def gen_element(family, idx):
    cfg = ELEMENT_TYPES[family]
    label = f"{cfg['prefix']}{idx}"
    dims = {}
    for k, (lo, hi) in cfg["dims"].items():
        dims[k] = rand_range(lo, hi)
    rebar = gen_rebar()
    return {"label": label, "dims": dims, "rebar": rebar, "family": family}


def draw_plan(doc, plan_idx):
    """Draw a certified BA plan with table format."""
    page = doc.new_page(width=842, height=595)  # A4 landscape
    tw = pymupdf.TextWriter(page.rect)

    # Title block
    tw.append((30, 30), f"PLAN DE RENFORCEMENT - BATIMENT {plan_idx + 1}", fontsize=14, font=pymupdf.Font("helv"))
    tw.append((30, 50), "Echelle 1:100  |  Unite: cm  |  Norme: BAEL 91", fontsize=9, font=pymupdf.Font("helv"))

    elements = []
    global_idx = 1
    y_start = 80

    for family, cfg in ELEMENT_TYPES.items():
        count = random.randint(cfg["count"][0], cfg["count"][1])

        tw.append((30, y_start), f"--- {family.upper().replace('_', ' ')}S ---", fontsize=10, font=pymupdf.Font("helv"))
        y_start += 16

        tw.append((30, y_start), "Rep.", fontsize=7, font=pymupdf.Font("helv"))
        tw.append((80, y_start), "Dimensions (cm)", fontsize=7, font=pymupdf.Font("helv"))
        tw.append((220, y_start), "Armatures", fontsize=7, font=pymupdf.Font("helv"))
        y_start += 12

        tw.append((30, y_start), "-" * 50, fontsize=6, font=pymupdf.Font("helv"))
        y_start += 10

        for i in range(count):
            elem = gen_element(family, global_idx)
            elements.append(elem)

            tw.append((30, y_start), elem["label"], fontsize=8, font=pymupdf.Font("helv"))

            # Dimensions
            d = elem["dims"]
            if family in ("semelle", "massif"):
                dim_str = f"{int(d.get('a',0)*100)}x{int(d.get('b',0)*100)}"
                if "h" in d:
                    dim_str += f"x{int(d['h']*100)}"
            elif family in ("poteau", "redresseur"):
                dim_str = f"{int(d.get('a',0)*100)}x{int(d.get('b',0)*100)}"
            elif family in ("poutre", "longrine", "chainage", "linteau", "sabliere",
                           "couvertine", "poutre_appui", "travers"):
                dim_str = f"{int(d.get('b',0)*100)}x{int(d.get('h',0)*100)}"
            elif family in ("dalle", "radier"):
                dim_str = f"e={int(d.get('ep',0)*100)}"
            elif family in ("voile", "mur", "contre_fort"):
                dim_str = f"e={int(d.get('ep',0)*100)} h={int(d.get('h',0)*100)}"
            elif family == "escalier":
                dim_str = f"{int(d.get('nb_marches',8))}m g={int(d.get('giron',30))}"
            elif family == "semelle_filante":
                dim_str = f"{int(d.get('b',0)*100)}x{int(d.get('h',0)*100)}"
            elif family == "fut":
                dim_str = f"D={int(d.get('diam',0)*100)}"
            elif family == "pieu":
                dim_str = f"D={int(d.get('diam',0)*100)} L={int(d.get('l',0)*100)}"
            else:
                dim_str = ""

            tw.append((80, y_start), dim_str, fontsize=7, font=pymupdf.Font("helv"))
            tw.append((220, y_start), elem["rebar"], fontsize=7, font=pymupdf.Font("helv"))

            y_start += 12
            if y_start > 560:
                y_start = 80

        y_start += 10
        global_idx += 1

    tw.write_text(page)
    return elements


def main():
    output_dir = "dataset/ba_plans_100_v2"
    os.makedirs(output_dir, exist_ok=True)

    ground_truth = {}
    num_plans = 100

    print(f"Generating {num_plans} certified BA plans (21 element types)...")
    for i in range(num_plans):
        filepath = os.path.join(output_dir, f"plan_{i+1:03d}.pdf")
        doc = pymupdf.open()
        elements = draw_plan(doc, i)
        doc.save(filepath)
        doc.close()

        ground_truth[filepath] = {"elements": elements}

        if (i + 1) % 20 == 0:
            print(f"  Generated {i + 1}/{num_plans}")

    gt_path = os.path.join(output_dir, "ground_truth.json")
    with open(gt_path, "w", encoding="utf-8") as f:
        json.dump(ground_truth, f, indent=2, ensure_ascii=False)

    print(f"\nDone! {num_plans} plans in {output_dir}/")
    print(f"Ground truth: {gt_path}")

    total = sum(len(v["elements"]) for v in ground_truth.values())
    by_family = {}
    for v in ground_truth.values():
        for e in v["elements"]:
            by_family[e["family"]] = by_family.get(e["family"], 0) + 1
    print(f"\nTotal elements: {total}")
    for f, c in sorted(by_family.items()):
        print(f"  {f}: {c}")


if __name__ == "__main__":
    random.seed(42)
    main()
