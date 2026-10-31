#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/build_fast_wall.py — Compile le noyau natif des murs (optionnel).

Détecte un compilateur C++ (g++/clang++/cl), compile
``extractors/fast_wall_core.cpp`` en bibliothèque partagée et dépose le
résultat dans ``extractors/``. La chaîne Python détecte ensuite la DLL/le .so
via ``extractors.fast_wall_bridge.load_fast_wall_or_none``.

Sans compilateur, ce script ne fait rien et le pipeline continue sur le
chemin Python pur (aucune régression).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CPP = ROOT / "extractors" / "fast_wall_core.cpp"


def _find_compiler() -> tuple[str, str]:
    for name in ("g++", "clang++", "cl"):
        path = shutil.which(name)
        if path:
            return name, path
    return "", ""


def main() -> int:
    comp, path = _find_compiler()
    if not comp:
        print("[fast_wall] Aucun compilateur C++ (g++/clang++/cl) dans le PATH.")
        print("[fast_wall] Le pipeline reste en Python pur. Installez MSVC ou "
              "MinGW-w64 puis relancez ce script pour activer le natif.")
        return 0

    print(f"[fast_wall] Compilateur : {comp} ({path})")
    out_dir = ROOT / "extractors"
    if sys.platform.startswith("win"):
        if comp == "cl":
            out = out_dir / "fastwall.dll"
            cmd = [path, "/O2", "/fp:fast", "/std:c++17", "/LD",
                   str(CPP), f"/Fe:{out}"]
        else:
            out = out_dir / "fastwall.dll"
            cmd = [path, "-O3", "-std=c++17", "-static", "-static-libgcc",
                   "-static-libstdc++", "-shared", "-o", str(out), str(CPP)]
    else:
        out = out_dir / "libfastwall.so"
        cmd = [path, "-O3", "-march=native", "-ffast-math", "-std=c++17",
               "-static-libgcc", "-static-libstdc++", "-shared", "-fPIC",
               "-o", str(out), str(CPP)]

    print("[fast_wall] ", " ".join(str(c) for c in cmd))
    res = subprocess.run(cmd, cwd=str(ROOT))
    if res.returncode != 0:
        print(f"[fast_wall] Compilation échouée (code {res.returncode}).")
        return res.returncode

    print(f"[fast_wall] OK -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
