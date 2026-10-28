"""Contraste WCAG du theme PlanBA Pro (lisibilite sur ecran de chantier).

Verifie que les couples texte/fond reellement utilises par l'interface
respectent au moins 4.5:1 (texte normal) ou 3.0:1 (texte large/gras).
Aucune dependance Tk : le test tourne en CI sans ecran.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui.design import KIND_COLORS, T


def _srgb(channel: float) -> float:
    channel /= 255.0
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def contrast(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# (texte, fond, minimum) -- couples utilises par les widgets
PAIRS = [
    (T["text"], T["bg"], 7.0),
    (T["text"], T["bg_card"], 7.0),
    (T["text"], T["bg_panel"], 7.0),
    (T["text"], T["bg_input"], 7.0),
    (T["text_dim"], T["bg_card"], 4.5),
    (T["text_dim"], T["bg_input"], 4.5),
    (T["text_muted"], T["bg_card"], 4.5),
    (T["text_muted"], T["bg_panel"], 4.5),
    (T["text_muted"], T["bg_input"], 4.5),
    (T["accent"], T["bg_card"], 4.5),
    (T["accent"], T["accent_soft"], 4.5),
    (T["success"], T["success_soft"], 4.5),
    (T["warning"], T["warning_soft"], 4.5),
    (T["danger"], T["danger_soft"], 4.5),
    # Texte blanc sur bouton accent : la maquette impose ce bleu. Le libelle
    # est toujours en gras >= 11 pt (seuil WCAG "grand texte" : 3.0:1).
    ("#ffffff", T["accent"], 3.0),
]


@pytest.mark.parametrize("fg,bg,minimum", PAIRS)
def test_contrast(fg: str, bg: str, minimum: float):
    ratio = contrast(fg, bg)
    assert ratio >= minimum, (
        f"contraste insuffisant {fg} sur {bg} : {ratio:.2f}:1 "
        f"(minimum {minimum}:1)")


def test_kind_colors_readable():
    """Chaque couleur de statut doit rester lisible sur son fond associe."""
    for kind, (fg, bg) in KIND_COLORS.items():
        if bg is None or bg == "":
            continue
        ratio = contrast(fg, bg)
        assert ratio >= 4.5, f"{kind}: {fg} sur {bg} = {ratio:.2f}:1"
