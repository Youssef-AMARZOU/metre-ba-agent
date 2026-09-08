#!/usr/bin/env python3
"""
Comprehensive performance evaluation — LABEL-LEVEL comparison.
Compares exact extracted labels vs ground truth labels.
"""
import sys, os, json, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.local_extractor import VectorPlanExtractor

SYNTHETIC_DIR = "dataset/synthetic_ba_plans"
GROUND_TRUTH_FILE = os.path.join(SYNTHETIC_DIR, "ground_truth.json")

REAL_PLANS = {
    "reference/PLAN_BA_final.pdf": {
        "semelles": ["S1", "S2", "S3", "S4", "S5"],
        "poteaux": ["P1", "P2", "P3", "P4"],
        "poutres": ["N1", "N2", "N3", "N4", "N5", "N6", "N7"],
    },
    "reference/plan_extension.pdf": {
        "semelles": ["S1", "S2"],
        "poteaux": ["P1"],
        "poutres": ["LG2", "N2", "N3", "N4", "N5", "N6", "N7", "PC"],
    },
    "reference/ilide.info-plan-ba-r-2.pdf": {
        "semelles": ["S1", "S2", "S3", "S4"],
        "poteaux": ["Q1", "Q2", "Q3", "Q4"],
        "poutres": ["N1", "N2", "N3", "N4", "N5"],
    },
}


def safe_extract(path):
    if not os.path.exists(path):
        return None
    try:
        ex = VectorPlanExtractor()
        return ex.process_all_pages(path)
    except Exception:
        return None


def get_all_extracted_labels(data):
    """Get ALL extracted labels across all families."""
    cat = data["catalogue_types"]
    labels = set()
    for family in ["semelles", "poteaux", "poutres", "dalles", "voiles", "escaliers"]:
        labels.update(cat.get(family, {}).keys())
    return labels


def get_ground_truth_labels(gt_data):
    """Get all ground truth labels."""
    return set(e["label"] for e in gt_data["elements"])


def main():
    print("=" * 70)
    print("  METRE BA AGENT — LABEL-LEVEL PERFORMANCE EVALUATION")
    print("=" * 70)

    with open(GROUND_TRUTH_FILE, "r", encoding="utf-8") as f:
        ground_truth = json.load(f)

    total_tp = 0
    total_fp = 0
    total_fn = 0
    plan_f1_scores = []
    successful = 0
    failed = 0

    # Per-family stats
    family_stats = {}

    # Test synthetic plans
    print(f"\nTesting {len(ground_truth)} synthetic plans...")
    for plan_path, gt_data in ground_truth.items():
        if not os.path.exists(plan_path):
            continue

        data = safe_extract(plan_path)
        if data is None:
            failed += 1
            continue

        successful += 1
        ext_labels = get_all_extracted_labels(data)
        gt_labels = get_ground_truth_labels(gt_data)

        tp = len(ext_labels & gt_labels)
        fp = len(ext_labels - gt_labels)
        fn = len(gt_labels - ext_labels)

        total_tp += tp
        total_fp += fp
        total_fn += fn

        # Per-family
        for elem in gt_data["elements"]:
            fam = elem["family"]
            if fam not in family_stats:
                family_stats[fam] = {"tp": 0, "fp": 0, "fn": 0}
            if elem["label"] in ext_labels:
                family_stats[fam]["tp"] += 1
            else:
                family_stats[fam]["fn"] += 1

        # Check false positives per family
        for label in ext_labels - gt_labels:
            # Determine family from label prefix
            if label.startswith("S"):
                fam = "semelle"
            elif label.startswith(("P", "Q")):
                fam = "poteau"
            elif label.startswith(("N", "BN", "PN", "LG", "CH")):
                fam = "poutre"
            elif label.startswith("D"):
                fam = "dalle"
            elif label.startswith("V"):
                fam = "voile"
            elif label.startswith("ESC"):
                fam = "escalier"
            else:
                fam = "other"
            if fam not in family_stats:
                family_stats[fam] = {"tp": 0, "fp": 0, "fn": 0}
            family_stats[fam]["fp"] += 1

        # Plan F1
        plan_f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) > 0 else 1.0
        plan_f1_scores.append(plan_f1)

    # Test real plans
    print(f"\nTesting {len(REAL_PLANS)} real reference plans...")
    for plan_path, gt_labels in REAL_PLANS.items():
        if not os.path.exists(plan_path):
            continue

        data = safe_extract(plan_path)
        if data is None:
            failed += 1
            continue

        successful += 1
        ext_labels = get_all_extracted_labels(data)
        gt_set = set(gt_labels)

        tp = len(ext_labels & gt_set)
        fp = len(ext_labels - gt_set)
        fn = len(gt_set - ext_labels)

        total_tp += tp
        total_fp += fp
        total_fn += fn

        plan_f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) > 0 else 1.0
        plan_f1_scores.append(plan_f1)

    # ============================================================================
    # METRICS
    # ============================================================================
    print(f"\n{'=' * 70}")
    print(f"  RESULTS: {successful} plans tested, {failed} failed")
    print(f"{'=' * 70}")

    sensitivity = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
    f1 = 2 * precision * sensitivity / (precision + sensitivity) if (precision + sensitivity) > 0 else 0
    overfitting = total_fp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0

    avg_plan_f1 = sum(plan_f1_scores) / len(plan_f1_scores) if plan_f1_scores else 0
    min_plan_f1 = min(plan_f1_scores) if plan_f1_scores else 0
    max_plan_f1 = max(plan_f1_scores) if plan_f1_scores else 0
    std_plan_f1 = math.sqrt(sum((x - avg_plan_f1)**2 for x in plan_f1_scores) / len(plan_f1_scores)) if plan_f1_scores else 0

    print(f"\n  Per-family breakdown:")
    for fam, s in sorted(family_stats.items()):
        fam_sens = s["tp"] / (s["tp"] + s["fn"]) if (s["tp"] + s["fn"]) > 0 else 0
        fam_prec = s["tp"] / (s["tp"] + s["fp"]) if (s["tp"] + s["fp"]) > 0 else 0
        fam_f1 = 2 * fam_prec * fam_sens / (fam_prec + fam_sens) if (fam_prec + fam_sens) > 0 else 0
        print(f"    {fam:12s}: TP={s['tp']:3d} FP={s['fp']:3d} FN={s['fn']:3d} "
              f"Recall={fam_sens:.0%} Prec={fam_prec:.0%} F1={fam_f1:.0%}")

    print(f"\n  OVERALL:")
    print(f"    Total: TP={total_tp}  FP={total_fp}  FN={total_fn}")
    print(f"    Sensitivity (Recall): {sensitivity:.1%}")
    print(f"    Precision:            {precision:.1%}")
    print(f"    F1 Score:             {f1:.1%}")
    print(f"    Overfitting Rate:     {overfitting:.1%}")
    print(f"")
    print(f"  Plan-level F1:")
    print(f"    Average: {avg_plan_f1:.1%}")
    print(f"    Min:     {min_plan_f1:.1%}")
    print(f"    Max:     {max_plan_f1:.1%}")
    print(f"    Std Dev: {std_plan_f1:.1%}")
    print(f"")
    print(f"  AUC (proxy): {avg_plan_f1:.3f}")
    print(f"  R-squared:   {1 - overfitting:.3f}")

    print(f"\n{'=' * 70}")
    if f1 >= 0.90:
        print(f"  VERDICT: [5/5] EXCELLENT -- F1={f1:.1%}")
    elif f1 >= 0.75:
        print(f"  VERDICT: [4/5] BON -- F1={f1:.1%}")
    elif f1 >= 0.60:
        print(f"  VERDICT: [3/5] MOYEN -- F1={f1:.1%}")
    elif f1 >= 0.40:
        print(f"  VERDICT: [2/5] FAIBLE -- F1={f1:.1%}")
    else:
        print(f"  VERDICT: [1/5] INSUFFISANT -- F1={f1:.1%}")
    print(f"{'=' * 70}")

    results = {
        "plans_tested": successful,
        "plans_failed": failed,
        "total": {"tp": total_tp, "fp": total_fp, "fn": total_fn},
        "sensitivity": sensitivity,
        "precision": precision,
        "f1": f1,
        "overfitting_rate": overfitting,
        "plan_f1": {"avg": avg_plan_f1, "min": min_plan_f1, "max": max_plan_f1, "std": std_plan_f1},
    }
    with open("eval_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to eval_results.json")


if __name__ == "__main__":
    main()
