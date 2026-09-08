#!/usr/bin/env python3
"""
Generate 50+ synthetic BA plan PDFs for performance testing.
Each plan has known elements (semelles, poteaux, poutres, dalles, voiles)
with ground truth dimensions for metric calculation.
"""
import os, sys, json, random, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymupdf

# ============================================================================
# ELEMENT FAMILIES WITH MULTILINGUAL LABELS
# ============================================================================

FAMILIES = {
    "semelle": {
        "prefixes": ["S"],  # Standard: S1, S2, S3...
        "dims_range": {"a": (0.6, 2.0), "b": (0.6, 2.0), "h": (0.15, 0.50)},
        "rebar_formats": ["{nb}HA{phi}", "{nb}T{phi}"],
    },
    "poteau": {
        "prefixes": ["P", "Q"],  # FR/MA: P1, Q1...
        "dims_range": {"a": (0.20, 0.40), "b": (0.20, 0.40)},
        "rebar_formats": ["{nb}HA{phi}", "{nb}T{phi}"],
    },
    "poutre": {
        "prefixes": ["N", "BN", "PN", "LG", "CH"],  # FR/MA: N1, BN1, PN1...
        "dims_range": {"b": (0.15, 0.40), "h": (0.25, 0.60)},
        "rebar_formats": ["{nb}HA{phi}", "{nb}T{phi}"],
    },
    "dalle": {
        "prefixes": ["D"],  # FR: D1, D2...
        "dims_range": {"ep": (0.12, 0.25)},
        "rebar_formats": ["{nb}HA{phi}"],
    },
    "voile": {
        "prefixes": ["V"],  # FR: V1, V2...
        "dims_range": {"ep": (0.15, 0.30), "h": (2.0, 4.0)},
        "rebar_formats": ["{nb}HA{phi}"],
    },
    "escalier": {
        "prefixes": ["ESC"],  # FR: ESC1, ESC2...
        "dims_range": {"nb_marches": (4, 15), "giron": (25, 35), "contremarche": (15, 20)},
        "rebar_formats": ["{nb}HA{phi}"],
    },
}

# Plan templates: which elements appear in each plan type
PLAN_TEMPLATES = [
    # Template 1: Foundation plan (semelles + poteaux)
    {"semelle": (4, 8), "poteau": (4, 8), "poutre": (0, 2)},
    # Template 2: Floor plan (poutres + dalles)
    {"poutre": (6, 12), "dalle": (3, 6), "poteau": (4, 8)},
    # Template 3: Full plan (all types)
    {"semelle": (3, 6), "poteau": (4, 8), "poutre": (6, 10), "dalle": (2, 4), "voile": (1, 3)},
    # Template 4: Wall plan (voiles + poutres)
    {"voile": (4, 8), "poutre": (4, 8), "poteau": (2, 4)},
    # Template 5: Staircase plan
    {"escalier": (1, 3), "poutre": (2, 4), "dalle": (1, 2)},
    # Template 6: Moroccan foundation (Q-prefix poteaux)
    {"semelle": (5, 10), "poteau": (6, 12), "poutre": (2, 4)},
    # Template 7: English plan (COL/B prefix)
    {"semelle": (3, 5), "poteau": (4, 6), "poutre": (5, 8), "dalle": (2, 3)},
    # Template 8: Portuguese plan (L/ES prefix)
    {"semelle": (3, 5), "poteau": (3, 5), "poutre": (4, 6), "dalle": (2, 3), "escalier": (1, 2)},
]

# Languages for labels
LANGUAGES = ["FR", "MA", "EN", "PT"]


def rand_range(lo, hi):
    return round(random.uniform(lo, hi), 2)


def gen_rebar(fmt, family):
    """Generate reinforcement string."""
    nb = random.choice([4, 6, 8, 10, 12])
    phi = random.choice([8, 10, 12, 14, 16, 20])
    return fmt.format(nb=nb, phi=phi)


def gen_element(family, idx, lang):
    """Generate one element with label, dimensions, rebar."""
    fam = FAMILIES[family]
    prefix = random.choice(fam["prefixes"])
    label = f"{prefix}{idx}"

    dims = {}
    for k, (lo, hi) in fam["dims_range"].items():
        dims[k] = rand_range(lo, hi)

    fmt = random.choice(fam["rebar_formats"])
    rebar = gen_rebar(fmt, family)

    return {"label": label, "dims": dims, "rebar": rebar, "family": family}


def draw_plan_page(doc, plan_idx, template, lang):
    """Draw a single page of a BA plan with elements."""
    page = doc.new_page(width=595, height=842)  # A4
    tp = pymupdf.TextWriter(page.rect)

    # Title
    tp.append((50, 50), f"PLAN BA - BATIMENT {plan_idx + 1}", fontsize=14, font=pymupdf.Font("helv"))
    tp.append((50, 70), f"Convention: {lang}", fontsize=10, font=pymupdf.Font("helv"))
    tp.append((50, 85), f"Scale 1:100", fontsize=8, font=pymupdf.Font("helv"))

    y = 120
    elements = []
    global_idx = 1

    for family, (min_count, max_count) in template.items():
        count = random.randint(min_count, max_count)
        if count == 0:
            continue

        # Section header
        tp.append((50, y), f"--- {family.upper()}S ---", fontsize=11, font=pymupdf.Font("helv"))
        y += 20

        for i in range(count):
            elem = gen_element(family, global_idx, lang)
            elements.append(elem)

            # Draw element label on "plan"
            x_pos = 80 + (i % 4) * 120
            y_pos = y + (i // 4) * 40
            tp.append((x_pos, y_pos), elem["label"], fontsize=12, font=pymupdf.Font("helv"))

            # Draw dimensions
            if family == "semelle":
                dim_str = f"({int(elem['dims']['a']*100)}x{int(elem['dims']['b']*100)}x{int(elem['dims']['h']*100)})"
            elif family == "poteau":
                dim_str = f"({int(elem['dims']['a']*100)}x{int(elem['dims']['b']*100)})"
            elif family == "poutre":
                dim_str = f"({int(elem['dims']['b']*100)}x{int(elem['dims']['h']*100)})"
            elif family == "dalle":
                dim_str = f"e={int(elem['dims']['ep']*100)}"
            elif family == "voile":
                dim_str = f"({int(elem['dims']['ep']*100)}x{int(elem['dims']['h']*1000)})"
            elif family == "escalier":
                dim_str = f"{elem['dims']['nb_marches']}m x g={elem['dims']['giron']}"
            else:
                dim_str = ""

            tp.append((x_pos + 80, y_pos), dim_str, fontsize=8, font=pymupdf.Font("helv"))
            tp.append((x_pos + 80, y_pos + 12), elem["rebar"], fontsize=7, font=pymupdf.Font("helv"))

            global_idx += 1

        y += max(1, (count + 3) // 4) * 40 + 20

    tp.write_text(page)
    return elements


def generate_plan(plan_idx, output_dir):
    """Generate one complete BA plan PDF."""
    template = random.choice(PLAN_TEMPLATES)
    lang = random.choice(LANGUAGES)

    filename = f"plan_{plan_idx + 1:03d}_{lang}.pdf"
    filepath = os.path.join(output_dir, filename)

    doc = pymupdf.open()
    elements = draw_plan_page(doc, plan_idx, template, lang)
    doc.save(filepath)
    doc.close()

    return filepath, elements, lang


def main():
    output_dir = "dataset/synthetic_ba_plans"
    os.makedirs(output_dir, exist_ok=True)

    ground_truth = {}
    num_plans = 55

    print(f"Generating {num_plans} synthetic BA plans...")
    for i in range(num_plans):
        filepath, elements, lang = generate_plan(i, output_dir)
        ground_truth[filepath] = {
            "language": lang,
            "elements": elements,
        }
        if (i + 1) % 10 == 0:
            print(f"  Generated {i + 1}/{num_plans}")

    # Save ground truth
    gt_path = os.path.join(output_dir, "ground_truth.json")
    with open(gt_path, "w", encoding="utf-8") as f:
        json.dump(ground_truth, f, indent=2, ensure_ascii=False)

    print(f"\nDone! {num_plans} plans in {output_dir}/")
    print(f"Ground truth saved to {gt_path}")

    # Summary
    total_elements = sum(len(v["elements"]) for v in ground_truth.values())
    families_count = {}
    for v in ground_truth.values():
        for e in v["elements"]:
            families_count[e["family"]] = families_count.get(e["family"], 0) + 1
    print(f"\nTotal elements: {total_elements}")
    for f, c in sorted(families_count.items()):
        print(f"  {f}: {c}")


if __name__ == "__main__":
    random.seed(42)  # Reproducible
    main()
