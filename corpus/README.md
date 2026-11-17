# Corpus de généralisation (C1 / C5)

Le but : savoir si une modification améliore l'extraction **sur des plans
que le code n'a jamais vus**, au lieu de la régler sur un seul plan.

## Règles

1. Les PDF ne sont pas versionnés (documents clients). Ils restent sur la
   machine, dans `PLANBA_CORPUS_DIR` (par défaut `%USERPROFILE%\Downloads`).
   `manifest.yaml` les identifie par nom + SHA-256.
2. `role: calibrage` = plan ayant servi à régler le code. Ses scores ne
   prouvent rien. `role: evaluation` = seul juge d'une modification.
3. Une vérité terrain (`verite_terrain/*.json`) n'est utilisée que si son
   `statut` vaut `"valide"`, après relecture par l'ingénieur.
4. **Une valeur fausse est pire qu'une valeur manquante.** Le critère
   d'acceptation d'une modification : aucune valeur fausse nouvelle sur
   les plans `evaluation`, rappel ≥ 80 %.

## Commandes

```powershell
# Brouillon de vérité terrain depuis un métré manuel (gabarit « Détail Quantitatif Métré »)
python tools\corpus\gt_depuis_metre.py "<metre.xlsx>" corpus\verite_terrain\<id>.json --plan-id <id> --feuille "Detail quontitafif fondation"

# Benchmark (ajouter --brouillon pour inclure les vérités terrain non validées)
$env:PLANBA_CORPUS_DIR = "$env:USERPROFILE\Downloads"
python tools\corpus\bench.py --brouillon

# Porte de CI : échoue si une vérité terrain validée révèle une valeur fausse
python -m pytest tests\test_corpus_generalisation.py -rs
```

## Valider une vérité terrain

Ouvrir `verite_terrain/<id>.json`, comparer chaque type au plan et au métré
(sections, nombre, positions), corriger, puis passer `"statut": "valide"`.
Les champs `sections_variantes` signalent des lignes du métré manuel qui ne
concordent pas entre elles (ex. LG1 30×32 et 30×37) : à trancher sur le plan.

## État

| Plan | Rôle | Vérité terrain |
|---|---|---|
| STR El Jadida type 2 | calibrage | aucune |
| STR El Jadida type 4 | evaluation (même BET) | aucune |
| Extension Archive (AO 61948) | evaluation | **brouillon** à valider |
| PLAN BA final | evaluation | métré MZINDA probable, à confirmer |
| Plan BA R+2 (ilide) | evaluation | aucune |
| BOLBOL-GOUMANDEY (70 p.) | evaluation | aucune |

Il manque encore des plans **scannés** et des plans d'autres bureaux d'études
avec leur métré manuel : c'est la priorité n°1 pour que le benchmark juge.
