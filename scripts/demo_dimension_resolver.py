#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Demonstration du resolveur de dimensions sur le plan BA de reference.

Cotes du plan (cm) :
  X : 248 + 725 + 238 + 478 + 265 + 460 = 2414
  Y : 382 +  40 +  82 + 108 +  15 + 382 = 1009

Sorties :
  output/metre_dimensions.xlsx   (5 feuilles, formules vivantes)
  output/metre_dimensions.json   (audit / integration UI)

Usage :
  python scripts/demo_dimension_resolver.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.dimension_resolver import (  # noqa: E402
    Cote,
    DimensionResolver,
    ElementSpec,
    ResolverConfig,
    dump_plan,
    format_fr,
    overlay_label,
)

#: 0.80 px/cm : 248 cm ~ 200 px (mesure sur le rendu du plan).
PX_PER_CM = 0.80

#: (valeur cm, position cumulee cm, axes)
X_CHAIN = [(248, 0, "1-2"), (725, 248, "2-4"), (238, 973, "4-5"),
           (478, 1211, "5-6"), (265, 1689, "6-7"), (460, 1954, "7-8")]
Y_CHAIN = [(382, 0, "A-B"), (40, 382, "B-C"), (82, 422, "C-D"),
           (108, 504, "D-E"), (15, 612, "E-F"), (382, 627, "F-G")]


def build_resolver() -> DimensionResolver:
    resolver = DimensionResolver(
        ResolverConfig(unit="cm", length_mode="auto", tol_pct=0.02, tol_abs=2.0),
        axis_names={"X": ["1", "2", "4", "5", "6", "7", "8"],
                    "Y": ["A", "B", "C", "D", "E", "F", "G"]},
    )
    for value, pos, axes in X_CHAIN:
        resolver.add_cote(Cote(
            value=value, start=pos * PX_PER_CM, end=(pos + value) * PX_PER_CM,
            direction="X", label=axes,
            axis_start=axes.split("-")[0], axis_end=axes.split("-")[1],
            row=780.0))
    for value, pos, axes in Y_CHAIN:
        resolver.add_cote(Cote(
            value=value, start=pos * PX_PER_CM, end=(pos + value) * PX_PER_CM,
            direction="Y", label=axes,
            axis_start=axes.split("-")[0], axis_end=axes.split("-")[1],
            row=120.0))
    resolver.calibrate()
    return resolver


def px(pos_cm: float) -> float:
    return pos_cm * PX_PER_CM


def build_specs() -> list[ElementSpec]:
    specs: list[ElementSpec] = []
    # -- semelles : M1 DIRECT (dimensions ecrites) -------------------------
    for label, dims in (("S1", "120x120x30"), ("S2", "110x110x25"),
                        ("S3", "120x120x30"), ("S4", "150x150x40"),
                        ("S5", "150x150x40")):
        specs.append(ElementSpec(
            label=f"{label}({dims})", category="semelle",
            declared_text=f"{label}({dims})", element_id=f"{label}-1"))
    specs.append(ElementSpec(
        label="Massif en G.B (50x50x50)", category="massif",
        declared_text="Massif en G.B (50x50x50)", element_id="MG-1"))

    # -- chainage axe A de 2 a 8 : M2 SOMME (brut axe a axe) ---------------
    specs.append(ElementSpec(
        label="CH axe A 2-8", category="chainage", declared_text="CH(20x30)",
        element_id="CH-A", axis_start="2", axis_end="8", direction="X",
        pos_start=px(248), pos_end=px(2414), row=780.0))

    # -- chainage 6-7 entre deux S3(120) : M3b net = 265-60-60 = 145 --------
    specs.append(ElementSpec(
        label="CH 6-7", category="chainage", declared_text="CH(20x30)",
        element_id="CH-6-7", axis_start="6", axis_end="7", direction="X",
        pos_start=px(1689), pos_end=px(1954), row=780.0,
        width_start=120, width_end=120))

    # -- longrine 4-5 entre S4(150) et S2(110) : 238-75-55 = 108 -----------
    specs.append(ElementSpec(
        label="LG 4-5", category="longrine", declared_text="LG(30x40)",
        element_id="LG-4-5", axis_start="4", axis_end="5", direction="X",
        pos_start=px(973), pos_end=px(1211), row=780.0,
        width_start=150, width_end=110))

    # -- emprise globale : M2 X + largeur Y --------------------------------
    specs.append(ElementSpec(
        label="Emprise 2414x1009", category="emprise", element_id="EMP",
        axis_start="1", axis_end="8", direction="X",
        pos_start=0.0, pos_end=px(2414), row=780.0, width=1009))

    # -- zone hachuree : M5 POLYGONE --------------------------------------
    zone = [(0.0, 0.0), (px(480), 0.0), (px(480), px(260)),
            (px(240), px(330)), (0.0, px(260))]
    specs.append(ElementSpec(
        label="Zone #T8 e=15cm", category="dalle", element_id="Z-T8",
        declared_text="#T8 e=15cm", polygon=[zone]))
    return specs


def main() -> int:
    resolver = build_resolver()
    plan = resolver.resolve_plan(build_specs())

    out_dir = ROOT / "output"
    xlsx = plan.to_workbook(out_dir / "metre_dimensions.xlsx")
    js = dump_plan(plan, out_dir / "metre_dimensions.json")

    print("=== AXES X (cumul) ===")
    print("  " + "  ".join(f"{a.name}={format_fr(a.position, 2)}"
                           for a in plan.axes.get("X", [])))
    print("=== AXES Y (cumul) ===")
    print("  " + "  ".join(f"{a.name}={format_fr(a.position, 2)}"
                           for a in plan.axes.get("Y", [])))
    if plan.scale is not None:
        print(f"=== ECHELLE M4 : {format_fr(plan.scale.factor, 4)} cm/px "
              f"(r2={plan.scale.r2:.4f}, {plan.scale.n_used} cotes) ===")

    print("\n=== ELEMENTS ===")
    header = (f"{'ID':<8}{'TYPE':<10}{'L':>8}{'l':>8}{'h':>8}"
              f"{'S m2':>9}{'V m3':>9}  {'METHODE':<7}{'CONF':>6}  STATUT")
    print(header)
    print("-" * len(header))
    for el in plan.elements:
        print(f"{el.element_id:<8}{el.category:<10}"
              f"{format_fr(el.L, 2):>8}{format_fr(el.l, 2):>8}{format_fr(el.h, 2):>8}"
              f"{format_fr(el.surface_m2, 2):>9}{format_fr(el.volume_m3, 3):>9}  "
              f"{el.method.value:<7}{el.confidence:>6.2f}  {el.status.value}")

    print("\n=== LABELS OVERLAY ===")
    for el in plan.elements:
        print(f"  {el.element_id:<8} {overlay_label(el)}")

    print("\n=== CONTROLES ===")
    for ctrl in plan.controls:
        gap = format_fr(ctrl.gap, 2)
        print(f"  {ctrl.chain:<28} ecart={gap:>8}  "
              f"tol={format_fr(ctrl.tolerance, 2):>6}  "
              f"{'OK' if ctrl.ok else 'A VERIFIER'}")

    print(f"\nClasseur : {xlsx}")
    print(f"JSON     : {js}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
