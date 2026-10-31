#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/install_compiler.py — Installe un compilateur C++ pour le noyau natif.

Le noyau `extractors/fast_wall_core.cpp` est OPTIONNEL : sans compilateur,
le pipeline reste en Python pur. Ce script installe MinGW-w64 (g++) pour
pouvoir compiler `fastwall.dll` et activer l'accélération native.

Ordre de résolution :
  1. un compilateur est déjà présent (g++ / clang++ / cl) -> rien à faire ;
  2. sinon `winget` (Windows Package Manager, préinstallé sur Win10/11)
     installe WinLibs (distribution GCC MinGW-w64 autonome, sans admin) ;
  3. sinon `choco` (Chocolatey) ;
  4. sinon instructions manuelles (lien de téléchargement direct).

Après installation, le compilateur est ajouté au PATH utilisateur : ouvrez un
NOUVEAU terminal (ou rechargez l'environnement) pour que `g++` soit visible.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

WINGET_PKG = "BrechtSanders.WinLibs.POSIX.UCRT"   # GCC MinGW-w64 (pthreads + UCRT)
#: Emplacements usuels où winget dépose WinLibs (pour re-localiser g++ sans
#: redémarrer le shell).
WINLIBS_CANDIDATES = (
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.POSIX.UCRT_*\mingw64\bin\g++.exe",
    r"C:\Program Files\WinLibs\mingw64\bin\g++.exe",
)


def _compiler() -> str | None:
    for name in ("g++", "clang++", "cl"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _gpp_after_install() -> str | None:
    """Re-localise g++ fraîchement installé (PATH du processus non rafraîchi)."""
    direct = shutil.which("g++")
    if direct:
        return direct
    import glob
    for pat in WINLIBS_CANDIDATES:
        for p in glob.glob(os.path.expandvars(pat)):
            if os.path.isfile(p):
                return p
    return None


def main() -> int:
    existing = _compiler()
    if existing:
        print(f"[compilateur] déjà présent : {existing}")
        return 0

    print("[compilateur] Aucun g++/clang++/cl détecté.")

    winget = shutil.which("winget")
    if winget:
        print(f"[compilateur] Installation de MinGW-w64 via winget "
              f"({WINGET_PKG})…")
        try:
            subprocess.run(
                [winget, "install", "--id", WINGET_PKG, "-e",
                 "--accept-package-agreements", "--accept-source-agreements"],
                check=False)
        except OSError as exc:
            print(f"[compilateur] Échec winget : {exc}")
        found = _gpp_after_install()
        if found:
            print(f"[compilateur] g++ installé : {found}")
            print("[compilateur] Ouvrez un NOUVEAU terminal puis lancez "
                  "`python scripts\\verify_fast_wall.py`.")
            return 0
        print("[compilateur] Installation lancée mais g++ pas encore dans le "
              "PATH de ce processus : ouvrez un nouveau terminal et relancez "
              "`scripts\\verify_fast_wall.py`.")
        return 0

    choco = shutil.which("choco")
    if choco:
        print("[compilateur] winget absent — tentative via Chocolatey.")
        subprocess.run([choco, "install", "mingw", "-y"], check=False)
        if _compiler():
            print("[compilateur] g++ installé via Chocolatey. Ouvrez un "
                  "nouveau terminal.")
            return 0

    # Instructions manuelles
    print("""
[compilateur] Aucun gestionnaire de paquets trouvé (winget/choco).
Installation manuelle de MinGW-w64 :
  1. Téléchargez WinLibs GCC (zip autonome, sans admin) :
     https://winlibs.com/#download-release
  2. Décompressez-le (ex. C:\\mingw64).
  3. Ajoutez C:\\mingw64\\bin au PATH utilisateur (Paramètres > Variables
     d'environnement), ou pointez la variable d'environnement PLANBA_FASTWALL_LIB
     vers fastwall.dll une fois compilée.
  4. Relancez : python scripts\\verify_fast_wall.py
""")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
