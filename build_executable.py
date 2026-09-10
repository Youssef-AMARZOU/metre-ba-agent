#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_executable.py — Script de packaging industriel pour PyInstaller.

Genere un executable Windows autonome (.exe) en un seul clic :
  python build_executable.py

Le resultat sera dans : dist/PlanBA_Metre_Extractor.exe
"""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"


def check_dependencies():
    """Verifie et installe les dependances necessaires."""
    deps = ["pyinstaller", "customtkinter", "openpyxl", "pydantic",
            "PyMuPDF", "Pillow", "reportlab"]

    print("[deps] Verification des dependances...")
    for dep in deps:
        try:
            __import__(dep.lower().replace("-", "_").replace("pymupdf", "fitz"))
        except ImportError:
            print(f"   Installation de {dep}...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", dep, "--quiet"])

    # Verification specifique de fitz
    try:
        import fitz
    except ImportError:
        print("   Installation de PyMuPDF...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "PyMuPDF", "--quiet"])

    print("[OK] Toutes les dependances sont installees.\n")


def build():
    """Lance la compilation PyInstaller."""
    print("[build] Debut de la compilation PyInstaller...\n")

    # Chemins des donnees a inclure
    config_dir = ROOT / "config"

    # Arguments PyInstaller
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",           # Pas de console noire
        "--onedir",             # Mode dossier : demarrage instantane
                                # (pas d'extraction 47 Mo dans %TEMP% a chaque
                                # lancement, pas de scan Defender systematique)
        "--name", "PlanBA_Metre_Extractor",
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
    ]

    # Ajouter les datas (config, etc.)
    if config_dir.exists():
        args.extend(["--add-data", f"{config_dir};config"])

    # Gabarit Excel 5 feuilles (pont build_metre -> populate_modele) :
    # prepare dans templates/ (regenere si absent) puis embarque pour
    # le chemin frozen sys._MEIPASS/templates/modele_metre_BA.xlsx.
    try:
        templates_dir = ROOT / "templates"
        tpl_cible = templates_dir / "modele_metre_BA.xlsx"
        if not tpl_cible.exists():
            templates_dir.mkdir(parents=True, exist_ok=True)
            from generators.modele_metre import generer_modele
            generer_modele(str(tpl_cible))
            from generators.add_catalogue_sheet import \
                injecter_catalogue_standard
            import openpyxl as _oxl
            _wb_tpl = _oxl.load_workbook(tpl_cible)
            injecter_catalogue_standard(_wb_tpl)
            _wb_tpl.save(tpl_cible)
            print(f"   Gabarit prepare : {tpl_cible}")
        args.extend(["--add-data", f"{templates_dir};templates"])
    except Exception as e:
        print(f"[WARN] Gabarit non embarque ({e}) — repli regeneration.")

    # Splash screen natif PyInstaller : image affichee par le bootloader
    # pendant l'initialisation de l'interpreteur (avant la fenetre Tk).
    splash_path = ensure_splash()
    if splash_path:
        args.extend(["--splash", str(splash_path)])

    # Collect-all : binaires Tcl tkdnd (Drag & Drop), donnees ezdxf
    # (resources, fonts) et OCR local embarque (modeles .onnx + runtime).
    try:
        import tkinterdnd2  # noqa: F401
        args.extend(["--collect-all", "tkinterdnd2"])
    except ImportError:
        print("[WARN] tkinterdnd2 absent — Drag & Drop desactive dans le build.")
    try:
        import ezdxf  # noqa: F401
        args.extend(["--collect-all", "ezdxf"])
    except ImportError:
        print("[WARN] ezdxf absent — support AutoCAD limite dans le build.")
    try:
        import rapidocr_onnxruntime  # noqa: F401
        args.extend(["--collect-all", "rapidocr_onnxruntime"])
        args.extend(["--collect-all", "onnxruntime"])
    except ImportError:
        print("[WARN] rapidocr absent — OCR desactive dans le build.")

    # Modules a exclure (lourds, pas utilises par le moteur deterministe)
    # NOTE -- numpy N'EST PAS exclu : ezdxf (moteur AutoCAD, embarque) en
    # depend a l'import. Il est charge uniquement a la demande (DXF).
    exclude_modules = [
        # Data science / ML (jamais appeles par le flux vectoriel local)
        "pandas", "matplotlib", "scipy", "statsmodels",
        "torch", "torchvision", "torchaudio",
        "tensorflow", "keras", "transformers", "datasets",
        "skimage", "sklearn", "xgboost", "lightgbm", "networkx", "sympy",
        # Toolkits GUI concurrents
        "PyQt5", "PyQt6", "PySide2", "PySide6", "shiboken2", "shiboken6",
        # Dashboards / notebooks
        "plotly", "altair", "dash",
        "IPython", "jupyter", "notebook", "pygments",
        # Formats / IO inutilises
        "lxml", "pyarrow", "duckdb", "numba", "llvmlite",
        "sqlalchemy", "psycopg2", "boto3", "botocore",
        "google.cloud", "google.api_core", "grpc",
        # OCR / audio — rapidocr/onnxruntime SONT embarques (fallback scanne) ;
        # les autres restent exclus.
        "sounddevice", "soundfile",
        "pydub", "librosa", "openvino",
        # Reseau / serveurs
        "httpx", "uvicorn", "websockets", "zmq", "tornado", "aiohttp",
        "opentelemetry", "mako",
    ]
    for mod in exclude_modules:
        args.extend(["--exclude-module", mod])

    # Modules a inclure explicitement
    hidden_imports = [
        "customtkinter",
        "openpyxl",
        "pydantic",
        "fitz",
        "ezdxf",
        "PIL",
        "reportlab",
        "numpy",
        "tkinterdnd2",
        "sklearn",
        "sklearn.cluster",
        "sklearn.neighbors",
        "shapely",
        "shapely.geometry",
        "ifcopenshell",
        "pdfplumber",
        "core.schemas",
        "core.calculator",
        "core.ingestion",
        "core.paths",
        "core.validator",
        "core.local_extractor",
        "core.roi_slicer",
        "core.tokenizer",
        "core.normalization",
        "core.ocr_engine",
        "core.populate_modele",
        "core.civil_engine",
        "core.text_analyzer",
        "core.metre_rules",
        "core.geometry_analyzer",
        "core.dxf_extractor",
        "core.ifc_extractor",
        "core.level_clustering",
        "ui.desktop_app",
        "build_metre",
        "optimisation_chantiers",
        "rapport_metre",
        "extract_plan",
    ]
    for hi in hidden_imports:
        args.extend(["--hidden-import", hi])

    # Point d'entree
    args.append(str(ROOT / "main.py"))

    print(f"Commande : {' '.join(args)}\n")

    result = subprocess.run(args, cwd=str(ROOT))

    if result.returncode == 0:
        # Mode --onedir : le lanceur est dans un sous-dossier du meme nom
        app_dir = DIST_DIR / "PlanBA_Metre_Extractor"
        exe_path = app_dir / "PlanBA_Metre_Extractor.exe"
        if exe_path.exists():
            total_mb = sum(
                f.stat().st_size for f in app_dir.rglob("*") if f.is_file()
            ) / (1024 * 1024)
            print(f"\n[OK] Compilation reussie !")
            print(f"   Dossier : {app_dir}")
            print(f"   Lanceur : {exe_path}")
            print(f"   Taille totale : {total_mb:.1f} Mo")
            print(f"\n   Double-cliquez sur {exe_path.name} dans ce dossier")
            print(f"   (demarrage instantane : aucune extraction %TEMP%).")
        else:
            print(f"\n[WARN] Compilation terminee mais .exe introuvable : {exe_path}")
    else:
        print(f"\n[ERROR] Erreur de compilation (code {result.returncode})")
        sys.exit(1)


def ensure_splash():
    """Genere assets/splash.png (image de chargement native PyInstaller)
    si elle n'existe pas deja. Renvoie le chemin ou None en cas d'echec."""
    assets_dir = ROOT / "assets"
    splash_path = assets_dir / "splash.png"

    if splash_path.exists():
        return splash_path

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("[WARN] PIL indisponible — splash ignore.")
        return None

    assets_dir.mkdir(parents=True, exist_ok=True)

    W, H = 420, 260
    img = Image.new("RGB", (W, H), "#2F5496")
    draw = ImageDraw.Draw(img)

    # Barre d'accent en haut
    draw.rectangle([0, 0, W, 6], fill="#28A745")

    def font(size, bold=True):
        try:
            return ImageFont.truetype("segoeuib.ttf" if bold else "segoeui.ttf",
                                      size)
        except Exception:
            return ImageFont.load_default()

    f_title = font(26)
    f_sub = font(13, bold=False)
    f_load = font(12, bold=False)

    def center(text, fnt, y, fill="#FFFFFF"):
        bbox = draw.textbbox((0, 0), text, font=fnt)
        w = bbox[2] - bbox[0]
        draw.text(((W - w) / 2, y), text, font=fnt, fill=fill)

    center("PlanBA", f_title, 70)
    center("Métré Extracteur", f_sub, 118, "#C8D9F0")
    center("Béton armé — extraction locale 100 % hors-ligne", f_sub, 148,
           "#9FB8DC")

    # Barre de progression statique
    bar_w, bar_h = 240, 10
    x0, y0 = (W - bar_w) / 2, 195
    draw.rounded_rectangle([x0, y0, x0 + bar_w, y0 + bar_h], radius=5,
                           outline="#C8D9F0", width=2)
    draw.rounded_rectangle([x0 + 2, y0 + 2, x0 + bar_w * 0.35, y0 + bar_h - 2],
                           radius=4, fill="#28A745")

    center("Chargement...", f_load, 220, "#9FB8DC")

    img.save(splash_path, "PNG")
    print(f"   Splash genere : {splash_path}")
    return splash_path


def clean():
    """Nettoie dist/, build/ et les caches avant toute nouvelle compilation.

    Evite la cohabitation d'anciens binaires (onefile vs onedir) et les
    conflits d'archives dans le dossier de distribution.
    """
    import shutil
    for d in [DIST_DIR, BUILD_DIR, ROOT / "__pycache__"]:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            print(f"   Supprime : {d}")


if __name__ == "__main__":
    print("=" * 60)
    print("  PlanBA -- Packaging Executable Windows")
    print("=" * 60 + "\n")

    check_dependencies()
    clean()
    build()
