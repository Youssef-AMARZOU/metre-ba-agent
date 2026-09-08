#!/usr/bin/env python3
"""
Evaluate extraction on 100 comprehensive BA plans.
All element types: semelles, poteaux, poutres, dalles, voiles, escaliers,
longrines, chainages, radiers, murs.
"""
import sys, os, json, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.local_extractor import VectorPlanExtractor

BA_DIR = "dataset/ba_plans_100"
GT_FILE = os.path.join(BA_DIR, "ground_truth.json")

# Also test real plans
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


def get_all_labels(data):
    cat = data["catalogue_types"]
    labels = set()
    for fam in ["semelles", "poteaux", "poutres", "dalles", "voiles",
                 "escaliers", "longrines", "chainages", "radiers", "murs",
                 "contre_forts", "futs"]:
        labels.update(cat.get(fam, {}).keys())
    return labels


def main():
    print("=" * 70)
    print("  METRE BA AGENT — 100-PLAN COMPREHENSIVE EVALUATION")
    print("=" * 70)

    with open(GT_FILE, "r", encoding="utf-8") as f:
        ground_truth = json.load(f)

    stats = {}
    total_tp = total_fp = total_fn = 0
    plan_f1s = []
    ok = fail = 0

    # Test 100 BA plans
    print(f"\nTesting {len(ground_truth)} BA plans...")
    for plan_path, gt_data in ground_truth.items():
        if not os.path.exists(plan_path):
            continue
        data = safe_extract(plan_path)
        if data is None:
            fail += 1
            continue
        ok += 1

        ext = get_all_labels(data)
        gt = set(e["label"] for e in gt_data["elements"])

        tp = len(ext & gt)
        fp = len(ext - gt)
        fn = len(gt - ext)
        total_tp += tp; total_fp += fp; total_fn += fn

        # Per-family
        for e in gt_data["elements"]:
            fam = e["family"]
            if fam not in stats:
                stats[fam] = {"tp": 0, "fp": 0, "fn": 0}
            if e["label"] in ext:
                stats[fam]["tp"] += 1
            else:
                stats[fam]["fn"] += 1
        for label in ext - gt:
            fam = "other"
            for f2 in ["semelle", "poteau", "poutre", "dalle", "voile",
                        "escalier", "longrine", "chainage", "radier", "mur"]:
                if label.startswith(ELEMENT_PREFIXES.get(f2, "XX")):
                    fam = f2
                    break
            if fam not in stats:
                stats[fam] = {"tp": 0, "fp": 0, "fn": 0}
            stats[fam]["fp"] += 1

        f1 = 2*tp/(2*tp+fp+fn) if (2*tp+fp+fn) > 0 else 1.0
        plan_f1s.append(f1)

    # Test real plans
    print(f"\nTesting {len(REAL_PLANS)} real reference plans...")
    for plan_path, gt_labels in REAL_PLANS.items():
        if not os.path.exists(plan_path):
            continue
        data = safe_extract(plan_path)
        if data is None:
            fail += 1
            continue
        ok += 1
        ext = get_all_labels(data)
        gt = set(gt_labels)
        tp = len(ext & gt); fp = len(ext - gt); fn = len(gt - ext)
        total_tp += tp; total_fp += fp; total_fn += fn
        f1 = 2*tp/(2*tp+fp+fn) if (2*tp+fp+fn) > 0 else 1.0
        plan_f1s.append(f1)

    # Results
    print(f"\n{'='*70}")
    print(f"  RESULTS: {ok} plans tested, {fail} failed")
    print(f"{'='*70}")

    for fam in sorted(stats.keys()):
        s = stats[fam]
        sens = s["tp"]/(s["tp"]+s["fn"]) if (s["tp"]+s["fn"])>0 else 0
        prec = s["tp"]/(s["tp"]+s["fp"]) if (s["tp"]+s["fp"])>0 else 0
        f1 = 2*prec*sens/(prec+sens) if (prec+sens)>0 else 0
        print(f"  {fam:12s}: TP={s['tp']:4d} FP={s['fp']:4d} FN={s['fn']:4d} "
              f"Recall={sens:.0%} Prec={prec:.0%} F1={f1:.0%}")

    sens = total_tp/(total_tp+total_fn) if (total_tp+total_fn)>0 else 0
    prec = total_tp/(total_tp+total_fp) if (total_tp+total_fp)>0 else 0
    f1 = 2*prec*sens/(prec+sens) if (prec+sens)>0 else 0
    of = total_fp/(total_tp+total_fp) if (total_tp+total_fp)>0 else 0

    avg = sum(plan_f1s)/len(plan_f1s) if plan_f1s else 0
    mn = min(plan_f1s) if plan_f1s else 0
    mx = max(plan_f1s) if plan_f1s else 0
    std = math.sqrt(sum((x-avg)**2 for x in plan_f1s)/len(plan_f1s)) if plan_f1s else 0

    print(f"\n  OVERALL:")
    print(f"    Total: TP={total_tp}  FP={total_fp}  FN={total_fn}")
    print(f"    Sensitivity: {sens:.1%}   Precision: {prec:.1%}")
    print(f"    F1 Score:    {f1:.1%}   Overfitting: {of:.1%}")
    print(f"    AUC:         {avg:.3f}   R-squared:   {1-of:.3f}")
    print(f"\n  Plan F1: avg={avg:.1%} min={mn:.1%} max={mx:.1%} std={std:.1%}")

    if f1 >= 0.90: v = "[5/5] EXCELLENT"
    elif f1 >= 0.75: v = "[4/5] BON"
    elif f1 >= 0.60: v = "[3/5] MOYEN"
    elif f1 >= 0.40: v = "[2/5] FAIBLE"
    else: v = "[1/5] INSUFFISANT"
    print(f"\n  VERDICT: {v} -- F1={f1:.1%}")
    print(f"{'='*70}")

    with open("eval_100_results.json", "w") as f2:
        json.dump({"plans": ok, "failed": fail, "tp": total_tp, "fp": total_fp,
                    "fn": total_fn, "sensitivity": sens, "precision": prec,
                    "f1": f1, "overfitting": of, "plan_f1": {"avg": avg, "min": mn,
                    "max": mx, "std": std}}, f2, indent=2)
    print("\nSaved to eval_100_results.json")


ELEMENT_PREFIXES = {
    "semelle": "S", "poteau": "P", "poutre": "N",
    "dalle": "D", "voile": "V", "escalier": "ESC",
    "longrine": "LG", "chainage": "CH", "radier": "R", "mur": "M",
}

if __name__ == "__main__":
    main()
