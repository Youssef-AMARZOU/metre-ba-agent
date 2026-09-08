#!/usr/bin/env python3
"""
Generate 100 comprehensive BA plans with ALL civil engineering element types.
Each plan contains: semelles, poteaux, poutres, dalles, voiles, escaliers,
longrines, chainages, radiers, murs, contre-forts, futs.
"""
import os, sys, json, random, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymupdf

# ============================================================================
# ELEMENT FAMILIES
# ============================================================================

ELEMENT_TYPES = {
    "semelle": {"prefix": "S", "count": (3, 8), "dims": {"a": (0.6, 2.0), "b": (0.6, 2.0), "h": (0.15, 0.50)}},
    "poteau": {"prefix": "P", "count": (4, 10), "dims": {"a": (0.20, 0.40), "b": (0.20, 0.40)}},
    "poutre": {"prefix": "N", "count": (5, 12), "dims": {"b": (0.15, 0.40), "h": (0.25, 0.60)}},
    "dalle": {"prefix": "D", "count": (3, 8), "dims": {"ep": (0.12, 0.25)}},
    "voile": {"prefix": "V", "count": (2, 6), "dims": {"ep": (0.15, 0.30), "h": (2.0, 4.0)}},
    "escalier": {"prefix": "ESC", "count": (1, 3), "dims": {"nb_marches": (4, 15), "giron": (25, 35)}},
    "longrine": {"prefix": "LG", "count": (2, 5), "dims": {"b": (0.20, 0.40), "h": (0.30, 0.50)}},
    "chainage": {"prefix": "CH", "count": (2, 5), "dims": {"b": (0.15, 0.25), "h": (0.20, 0.30)}},
    "radier": {"prefix": "R", "count": (1, 3), "dims": {"ep": (0.20, 0.40)}},
    "mur": {"prefix": "M", "count": (1, 4), "dims": {"ep": (0.15, 0.30), "h": (2.0, 3.5)}},
}

# Rebar patterns
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
    """Draw a realistic BA plan with table format (like real plans)."""
    page = doc.new_page(width=842, height=595)  # A4 landscape
    tw = pymupdf.TextWriter(page.rect)

    # Title block
    tw.append((30, 30), f"PLAN DE REINFORCEMENT - BATIMENT {plan_idx + 1}", fontsize=14, font=pymupdf.Font("helv"))
    tw.append((30, 50), "Echelle 1:100  |  Unite: cm", fontsize=9, font=pymupdf.Font("helv"))

    elements = []
    global_idx = 1
    y_start = 80

    # Draw each family as a table section
    for family, cfg in ELEMENT_TYPES.items():
        count = random.randint(cfg["count"][0], cfg["count"][1])

        # Section header
        tw.append((30, y_start), f"--- {family.upper()}S ---", fontsize=11, font=pymupdf.Font("helv"))
        y_start += 18

        # Table header
        tw.append((30, y_start), "Repere", fontsize=8, font=pymupdf.Font("helv"))
        tw.append((120, y_start), "Dimensions (cm)", fontsize=8, font=pymupdf.Font("helv"))
        tw.append((280, y_start), "Armatures", fontsize=8, font=pymupdf.Font("helv"))
        y_start += 14

        # Separator line
        tw.append((30, y_start), "-" * 60, fontsize=7, font=pymupdf.Font("helv"))
        y_start += 12

        for i in range(count):
            elem = gen_element(family, global_idx)
            elements.append(elem)

            # Table row
            tw.append((30, y_start), elem["label"], fontsize=9, font=pymupdf.Font("helv"))

            # Dimensions
            if family == "semelle":
                dim_str = f"{int(elem['dims']['a']*100)}x{int(elem['dims']['b']*100)}x{int(elem['dims']['h']*100)}"
            elif family == "poteau":
                dim_str = f"{int(elem['dims']['a']*100)}x{int(elem['dims']['b']*100)}"
            elif family == "poutre":
                dim_str = f"{int(elem['dims']['b']*100)}x{int(elem['dims']['h']*100)}"
            elif family == "dalle":
                dim_str = f"e={int(elem['dims']['ep']*100)}"
            elif family == "voile":
                dim_str = f"e={int(elem['dims']['ep']*100)} h={int(elem['dims']['h']*100)}"
            elif family == "escalier":
                dim_str = f"{elem['dims']['nb_marches']}marches g={elem['dims']['giron']}cm"
            elif family == "longrine":
                dim_str = f"{int(elem['dims']['b']*100)}x{int(elem['dims']['h']*100)}"
            elif family == "chainage":
                dim_str = f"{int(elem['dims']['b']*100)}x{int(elem['dims']['h']*100)}"
            elif family == "radier":
                dim_str = f"e={int(elem['dims']['ep']*100)}"
            elif family == "mur":
                dim_str = f"e={int(elem['dims']['ep']*100)} h={int(elem['dims']['h']*100)}"
            else:
                dim_str = ""

            tw.append((120, y_start), dim_str, fontsize=8, font=pymupdf.Font("helv"))
            tw.append((280, y_start), elem["rebar"], fontsize=8, font=pymupdf.Font("helv"))

            y_start += 13

            # New column if too many rows
            if y_start > 550:
                y_start = 80
                tw.append((450, 30), f"(suite)", fontsize=8, font=pymupdf.Font("helv"))

        y_start += 15
        global_idx += 1

    tw.write_text(page)
    return elements


def main():
    output_dir = "dataset/ba_plans_100"
    os.makedirs(output_dir, exist_ok=True)

    ground_truth = {}
    num_plans = 100

    print(f"Generating {num_plans} comprehensive BA plans...")
    for i in range(num_plans):
        filepath = os.path.join(output_dir, f"plan_{i+1:03d}.pdf")
        doc = pymupdf.open()
        elements = draw_plan(doc, i)
        doc.save(filepath)
        doc.close()

        ground_truth[filepath] = {"elements": elements}

        if (i + 1) % 20 == 0:
            print(f"  Generated {i + 1}/{num_plans}")

    # Save ground truth
    gt_path = os.path.join(output_dir, "ground_truth.json")
    with open(gt_path, "w", encoding="utf-8") as f:
        json.dump(ground_truth, f, indent=2, ensure_ascii=False)

    print(f"\nDone! {num_plans} plans in {output_dir}/")
    print(f"Ground truth: {gt_path}")

    # Summary
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
