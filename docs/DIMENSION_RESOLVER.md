# `dimension_resolver` — Résolution des dimensions non écrites

> Module : `core/dimension_resolver.py` · Pont : `core.vision_bridge.run_dimension_resolution`
> Overlay : `extractors/vision_extractor.py::render_audit_overlay(metre_annotations=...)`
> Démo : `scripts/demo_dimension_resolver.py` · Tests : `tests/test_dimension_resolver.py` (41 tests)

---

## 1. Plan d'implémentation (10 lignes)

1. Contrats typés : `Cote`, `Axis`, `SpanResolution`, `ResolvedElement`, `ResolverConfig`.
2. Graphe 1D par direction (X / Y) : nœuds = extrémités snappées, arêtes = cotes.
3. Résolution déterministe : système déterminé → exact ; surdéterminé → moindres carrés ; sous-déterminé → repli M4.
4. Cascade M1 DIRECT → M2 SOMME → M3 SOUS (manquante / net) → M4 ÉCHELLE → M5 POLYGONE.
5. Deuxième méthode systématique dès qu'elle existe ; écart > tolérance → `A_VERIFIER` avec les deux valeurs.
6. Contrôles de fermeture par chaîne + global, avec écart et verdict.
7. Modes **brut (axe à axe)** / **net (entre nus)** configurables par type, écrits dans la sortie.
8. Overlay : label `S=4,25 m² [SOM OK 1.00]`, mêmes couleurs vert / orange / rouge.
9. Excel 5 feuilles à **formules vivantes** (`Cotations!$C$n`, `ROUND(...)`) + couleurs de statut.
10. Journal de décision pour chaque élément (méthode, cotes, écart, unité).

---

## 2. API

```python
from core.dimension_resolver import (
    Cote, DimensionResolver, ElementSpec, ResolverConfig,
    cotations_from_dxf, cotations_from_number_centers,
)

resolver = DimensionResolver(ResolverConfig(unit="cm"), axis_names={"X": ["1","2","4","5","6","7","8"]})
resolver.add_cote(Cote(value=248, start=0, end=200, direction="X", label="1-2", row=780))
resolver.calibrate()                       # M4 : régression sur les couples (px, cm)

plan = resolver.resolve_plan([
    ElementSpec(label="CH 6-7", category="chainage", declared_text="CH(20x30)",
                axis_start="6", axis_end="7", pos_start=1689, pos_end=1954,
                width_start=120, width_end=120),
])

plan.to_workbook("output/metre_dimensions.xlsx")
plan.annotations()                         # labels prêts pour l'overlay
```

| Élément | Rôle |
|---|---|
| `Cote(value, start, end, direction, row, …)` | une cotation : valeur + 2 extrémités dessin + ligne de cotation |
| `AxisGraph` | graphe 1D : snap, moindres carrés, résidus, chaînes contiguës |
| `DimensionResolver` | orchestration M1→M5, arbitrage, contrôles, journal |
| `ElementSpec` | entrée : texte, axes, positions, largeurs d'appui, bbox, polygone |
| `ResolvedElement` | sortie : L/l/h, S, V, méthode, formule, confiance, statut |
| `PlanResolution` | plan complet : éléments + axes + cotations + contrôles + Excel |

---

## 3. Méthodes

### M1 — DIRECT
Regex tolérante (`x`, `X`, `×`, `*`, virgule décimale, suffixes `mm/cm/m`) :
`S4(150x150x40)` → `150 × 150 × 40`, `b=0.5 h=0.4 L=3.2` → section + longueur.
L'interprétation dépend de la catégorie : semelle `AxBxH`, longrine `bxh` + L cotée.

### M2 — SOMME (chaîne de cotations)
Le graphe cherche une **chaîne contiguë** de cotes couvrant exactement `[axe_a, axe_b]`
(même ligne de cotation privilégiée). Exemple : axes 2→8 = `725+238+478+265+460 = 2166`.
La formule est conservée et écrite telle quelle dans Excel.

### M3 — SOUSTRACTION
- **(a) cote manquante** = cote totale − partielles connues. Un seul trou autorisé,
  sinon `NON_RESOLU` (manque non unique). Ex. axe 3 : `725 − 238 = 487`.
- **(b) longueur nette** = entre-axes − demi-largeurs d'appui :
  `265 − 120/2 − 120/2 = 145` · `238 − 150/2 − 110/2 = 108`.
  Si les largeurs d'appui manquent, le mode **brut** est retenu et signalé (jamais de net inventé).

### M4 — ÉCHELLE
Régression passant par l'origine sur les couples `(longueur dessin, valeur cotée)`,
rejet itératif des outliers (médiane + MAD, 3 passes). Résultat : facteur, `r²`,
nombre de cotes utilisées / rejetées. Sert à **estimer** (`ESTIME`) et à **recouper**.

### M5 — POLYGONE
Formule du lacet sur HATCH / LWPOLYLINE fermée, **trous retirés**, arcs et bulges
discrétisés (16 segments par défaut), facteur d'échelle appliqué **au carré**.

---

## 4. Arbitrage, confiance, statut

```
confiance = min( base_méthode , ratio d'accord entre méthodes )
ratio     = min(valeur_1, valeur_2) / max(valeur_1, valeur_2)      # 1.00 = accord
```

| Méthode | Base |
|---|---|
| M1 DIRECT | 1.00 |
| M2 SOMME | 1.00 |
| M3 SOUS | 0.95 |
| M5 POLY | 0.90 |
| M4 ÉCHELLE | 0.85 |

Seuil vert = **0.90** (identique à l'overlay historique).

| Statut | Condition |
|---|---|
| `OK` | au moins une dimension, accord entre méthodes, fermeture respectée |
| `A_VERIFIER` | écart > `max(2 cm, 2 %)`, fermeture cassée, ou confiance < 0.90 |
| `ESTIME` | uniquement M4 (aucune cote exploitable) |
| `NON_RESOLU` | aucune méthode n'aboutit → raison écrite, aucune valeur inventée |

**Jamais d'écrasement** : en cas de désaccord, la valeur primaire reste en place et
la seconde est conservée dans `secondary` (méthode, valeur, écart, écart %).

---

## 5. Overlay

`PlanResolution.annotations()` renvoie une liste alignée sur les éléments :

```python
OverlayAnnotation("S=4,25 m² [SOM OK 1.00]", "OK", (0, 180, 0))
OverlayAnnotation("L=1,45 m [SOUS A VERIFIER 0.87]", "A_VERIFIER", (0, 140, 255))
```

```python
extractor.render_audit_overlay(
    img, elements, page_num=n,
    metre_annotations=plan.annotations())      # paramètre optionnel, non cassant
```

Le label principal (existant) est conservé ; le label de métré est dessiné **sous**
la bbox, dans la même charte couleur.

---

## 6. Excel — 5 feuilles, formules vivantes

| Feuille | Contenu |
|---|---|
| `Elements` | id, type, libellé, axes, **L** (formule), l, h, **S** (`=ROUND(F3*G3*0.0001,2)`), **V**, méthode, formule lisible, confiance, statut, mode, note |
| `Cotations` | id, direction, valeur, longueur dessin, échelle, axes, source, écart graphe, statut |
| `Axes` | direction, axe, position cumulée, statut, nb cotes |
| `Controles` | chaîne, total, somme, écart, tolérance, verdict, détail |
| `A_verifier` | unité, mode brut/net, tolérances, seuil, échelle M4, cas ambigus |

Exemples de cellules produites (aucune valeur calculée en dur) :

```
F9  = Cotations!$C$4+Cotations!$C$5+Cotations!$C$6+Cotations!$C$7+Cotations!$C$8
F10 = Cotations!$C$7-120/2-120/2
I3  = ROUND(F3*G3*0.0001,2)          (surface m² à partir de L et l)
J9  = ROUND(F9*G9*H9*0.000001,3)     (volume m³ d'une longrine)
L7  = "=725+238+478+265+460"         (texte lisible, jamais évalué)
```

Couleurs : vert `C6F6D5` (OK), orange `FEEBC8` (A_VERIFIER), gris `E2E8F0` (ESTIMÉ),
rouge `FED7D7` (NON_RESOLU).

---

## 7. Exemple d'exécution (plan de référence)

```
python scripts/demo_dimension_resolver.py
```

```
=== AXES X (cumul) ===
  1=0,00  2=248,00  4=973,00  5=1211,00  6=1689,00  7=1954,00  8=2414,00
=== AXES Y (cumul) ===
  A=0,00  B=382,00  C=422,00  D=504,00  E=612,00  F=627,00  G=1009,00
=== ECHELLE M4 : 1,2500 cm/px (r2=1.0000, 12 cotes) ===
```

| ID | Type | L (cm) | l (cm) | h (cm) | S (m²) | V (m³) | Méthode | Conf. | Statut |
|---|---|---:|---:|---:|---:|---:|---|---:|---|
| S1-1 | semelle | 120 | 120 | 30 | 1,44 | 0,432 | DIR | 1,00 | OK |
| S2-1 | semelle | 110 | 110 | 25 | 1,21 | 0,302 | DIR | 1,00 | OK |
| S3-1 | semelle | 120 | 120 | 30 | 1,44 | 0,432 | DIR | 1,00 | OK |
| S4-1 | semelle | 150 | 150 | 40 | 2,25 | 0,900 | DIR | 1,00 | OK |
| S5-1 | semelle | 150 | 150 | 40 | 2,25 | 0,900 | DIR | 1,00 | OK |
| MG-1 | massif | 50 | 50 | 50 | 0,25 | 0,125 | DIR | 1,00 | OK |
| CH-A | chaînage | 2166 | 20 | 30 | — | 1,300 | SOM | 1,00 | OK |
| CH-6-7 | chaînage | 145 | 20 | 30 | — | 0,087 | SOUS | 0,95 | OK |
| LG-4-5 | longrine | 108 | 30 | 40 | — | 0,130 | SOUS | 0,95 | OK |
| EMP | emprise | 2414 | 1009 | — | 243,57 | — | SOM | 1,00 | OK |
| Z-T8 | dalle | — | — | 15 | 14,16 | 2,124 | POLY | 0,90 | OK |

Contrôles : `FERMETURE X` écart 0,00 (tol. 14,50) · `FERMETURE Y` écart 0,00 (tol. 7,64).

Cas `A_VERIFIER` vérifié en test unitaire : ajout d'une cote `1-8 = 2500` sur une
chaîne qui vaut `2414` → écart 86 cm, chaîne et élément passent en `A_VERIFIER`
avec les deux valeurs conservées.

---

## 8. Tests (41)

`python -m pytest tests/test_dimension_resolver.py -q`

| Couverture | Assertions |
|---|---|
| Axes X / Y cumulés | `0, 248, 973, 1211, 1689, 1954, 2414` · `0, 382, 422, 504, 612, 627, 1009` |
| Axe 3 non coté | `725 − 238 = 487` et `487 + 238 = 725` |
| Longueurs nettes | `6→7 = 145` · `4→5 = 108` |
| Emprise | `2414 × 1009 = 243,57 m²` |
| M1 | `S4(150x150x40)`, `150 x 150 x 40`, décimales à virgule, `b/h/L` |
| M4 | facteur ≈ 1,24 cm/px, rejet d'outlier, statut `ESTIME` |
| M5 | lacet 10×10 − trou 2×2 = 96 · 4 bulges ≈ πr² |
| Chaîne fausse | `A_VERIFIER` + les deux valeurs |
| Overlay | label exact `S=4,25 m² [SOM OK 1.00]` + pixels verts dessinés |
| Excel | 5 feuilles, formules `Cotations!`, formule lisible en texte, couleurs |
| Règle zéro invention | `#T8 e=15cm` → aucune surface |

---

## 9. Limites connues

1. **Le plan de référence ne contient pas de cotes texte exploitables** : les
   chiffres sont des tracés vectoriels, `get_text()` ne renvoie que les repères.
   La démo utilise donc les cotes saisies ; l'extraction automatique passe par
   `cotations_from_dxf` (DIMENSION) ou `cotations_from_number_centers` (OCR).
2. **`cotations_from_number_centers` est une heuristique** : les traits de cote
   sont estimés aux milieux des nombres voisins. Fiable sur une chaîne homogène,
   à vérifier sur des lignes mixtes (cotes + indices d'axes + étiquettes).
3. **Un seul axe de portée par élément** : une semelle isolée est supposée carrée
   (`l = L`) sauf dimension écrite ; les semelles filantes rectangulaires doivent
   recevoir `width` explicitement.
4. **Moindres carrés non pondérés** : une cote aberrante est diluée sur toute la
   chaîne ; elle est signalée par le résidu et le contrôle de fermeture, pas corrigée.
5. **Le graphe est 1D par direction** : aucune gestion des axes obliques ni des
   cotations alignées non orthogonales.
6. **Bulges** : discrétisation à 16 segments (erreur < 0.1 % sur un demi-cercle) ;
   les splines ne sont pas converties.
7. **Unités DXF** : `dxf_unit` doit être fourni ; une unité erronée décale toutes
   les valeurs (le facteur d'échelle reste, lui, cohérent).
8. **Pas de recalcul Excel côté Python** : les formules sont écrites pour Excel /
   LibreOffice ; `openpyxl` ne les évalue pas (les tests vérifient les formules,
   pas leur évaluation).
9. **Intégration UI** : le pont `run_dimension_resolution` est prêt mais n'est pas
   encore branché dans le worker de `ui/desktop_app.py` — il faut lui fournir le
   flux de cotations (DXF ou OCR), inexistant pour ce PDF.
