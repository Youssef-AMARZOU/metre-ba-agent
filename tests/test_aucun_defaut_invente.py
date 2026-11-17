# -*- coding: utf-8 -*-
"""RG-6 : aucune dimension par défaut dans les générateurs de métré.

Analyse statique (AST) : interdit ``x.get("<dimension>", <nombre non nul>)``
et ``x.get("<dimension>") or <nombre>`` dans les modules qui écrivent
l'Excel. Une dimension non lue doit devenir « A RENSEIGNER » (voir
core/guardrails.py), jamais 3,0 m ou 0,25 m.
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODULES = ["build_metre.py", "core/populate_modele.py",
           "core/populate_modele_bet.py"]

DIMENSIONS = {"a", "b", "h", "A", "B", "H", "L", "e", "ep", "hauteur",
              "portee", "longueur", "esp", "h_patin", "a_fut", "b_fut"}


def _num_non_nul(node):
    return (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool) and node.value != 0)


def _est_get_dimension(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in DIMENSIONS)


def _violations(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    out = []
    for node in ast.walk(tree):
        # x.get("hauteur", 3.0)
        if _est_get_dimension(node) and len(node.args) > 1 and _num_non_nul(node.args[1]):
            out.append((node.lineno, ast.unparse(node)))
        # x.get("portee") or 3.5
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            if any(_est_get_dimension(v) for v in node.values) and \
                    any(_num_non_nul(v) for v in node.values):
                out.append((node.lineno, ast.unparse(node)))
    return out


def test_aucune_dimension_par_defaut_dans_les_generateurs():
    trouvees = []
    for rel in MODULES:
        for ligne, code in _violations(os.path.join(ROOT, rel)):
            trouvees.append(f"{rel}:{ligne}  {code}")
    assert not trouvees, (
        "Dimensions inventées (RG-6) — utiliser core.guardrails.valeur_lue :\n  "
        + "\n  ".join(trouvees))
