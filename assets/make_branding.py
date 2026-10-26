#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
assets/make_branding.py -- Genere l'identite visuelle "PlanBA Pro" :
  - assets/app_icon.png  (512x512)  + assets/app_icon.ico (16..256)
  - assets/splash.png    (ecran de chargement PyInstaller)

Theme genie civil : bleu structure, jaune securite chantier, poteau + poutre
BA stylises avec section d'armatures. Execute : python assets/make_branding.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parent
APP_NAME = "PlanBA Pro"
TAGLINE = "Métré Béton Armé & Surfaces"

NAVY = "#0F1B2D"
BLUE = "#1F5FA8"
BLUE_LIGHT = "#2F7BD1"
YELLOW = "#F5B700"
STEEL = "#E8772E"
WHITE = "#FFFFFF"
CONCRETE = "#C7D0DB"


def _font(size: int, bold: bool = True):
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"),
                 ("arialbd.ttf" if bold else "arial.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def draw_icon(size: int = 512) -> Image.Image:
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(s * 0.22)
    # Fond : carre arrondi bleu nuit avec degrade simule
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=NAVY)
    d.rounded_rectangle([int(s * 0.04), int(s * 0.04), int(s * 0.96), int(s * 0.96)],
                        radius=int(r * 0.85), fill=BLUE)
    # Bande jaune securite (diagonale en bas)
    band = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    bd.polygon([(0, int(s * 0.80)), (s, int(s * 0.64)), (s, int(s * 0.74)),
                (0, int(s * 0.90))], fill=YELLOW)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [int(s * 0.04), int(s * 0.04), int(s * 0.96), int(s * 0.96)],
        radius=int(r * 0.85), fill=255)
    img.paste(band, (0, 0), Image.composite(band.split()[3], Image.new("L", (s, s), 0), mask))
    d = ImageDraw.Draw(img)

    # Poutre (horizontale) + poteau (vertical) en beton clair
    bw = int(s * 0.13)
    d.rectangle([int(s * 0.20), int(s * 0.24), int(s * 0.80), int(s * 0.24) + bw], fill=CONCRETE)
    d.rectangle([int(s * 0.50) - bw // 2, int(s * 0.24), int(s * 0.50) + bw // 2, int(s * 0.62)],
                fill=CONCRETE)
    # Section d'armatures : cadre + 4 barres (cercles orange acier)
    cx, cy, half = int(s * 0.50), int(s * 0.24) + bw // 2, int(bw * 0.34)
    d.rectangle([cx - half, cy - half, cx + half, cy + half], outline=STEEL,
                width=max(2, s // 128))
    rr = max(2, s // 64)
    for dx in (-1, 1):
        for dy in (-1, 1):
            d.ellipse([cx + dx * half * 0.72 - rr, cy + dy * half * 0.72 - rr,
                       cx + dx * half * 0.72 + rr, cy + dy * half * 0.72 + rr], fill=STEEL)
    # Cotation (ligne + fleches) sous la poutre
    y = int(s * 0.50)
    d.line([(int(s * 0.20), y), (int(s * 0.80), y)], fill=WHITE, width=max(2, s // 110))
    for x in (int(s * 0.20), int(s * 0.80)):
        d.line([(x, y - int(s * 0.03)), (x, y + int(s * 0.03))], fill=WHITE, width=max(2, s // 110))
    # Lettre "P" en bas a droite, dans la bande
    f = _font(int(s * 0.22))
    d.text((int(s * 0.62), int(s * 0.60)), "P", font=f, fill=NAVY)
    return img


def build_icon() -> None:
    base = draw_icon(512)
    base.save(ASSETS / "app_icon.png", "PNG")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [base.resize((z, z), Image.LANCZOS) for z in sizes]
    frames[-1].save(ASSETS / "app_icon.ico", format="ICO",
                    sizes=[(z, z) for z in sizes], append_images=frames[:-1])
    print(f"   Icone : {ASSETS / 'app_icon.ico'} ({', '.join(map(str, sizes))})")


def build_splash() -> None:
    W, H = 460, 280
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 6], fill=YELLOW)
    icon = draw_icon(96).convert("RGBA")
    img.paste(icon, (28, 40), icon)
    d.text((140, 48), APP_NAME, font=_font(30), fill=WHITE)
    d.text((142, 92), TAGLINE, font=_font(14, bold=False), fill=CONCRETE)
    d.text((142, 114), "Vision locale · BAEL 91 · RPS 2000 v2011 · NM 01.4.097",
           font=_font(11, bold=False), fill="#9FB0C4")
    bar_w, bar_h, x0, y0 = 300, 10, (W - 300) // 2, 190
    d.rounded_rectangle([x0, y0, x0 + bar_w, y0 + bar_h], radius=5, outline=CONCRETE, width=2)
    d.rounded_rectangle([x0 + 2, y0 + 2, x0 + bar_w * 0.4, y0 + bar_h - 2], radius=4, fill=YELLOW)
    d.text((W // 2 - 48, 216), "Chargement...", font=_font(12, bold=False), fill="#9FB0C4")
    d.text((W - 170, H - 26), "100 % local · zéro valeur inventée",
           font=_font(10, bold=False), fill="#6F8197")
    img.save(ASSETS / "splash.png", "PNG")
    print(f"   Splash : {ASSETS / 'splash.png'}")


if __name__ == "__main__":
    build_icon()
    build_splash()
