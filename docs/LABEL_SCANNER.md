# Lecture générique des étiquettes (`core/label_scanner.py`)

## Pourquoi

Un plan de coffrage n'écrit pas toujours ses sections de la même façon :
`S2(110x110x25)`, `S2 110 x 110 x 25`, `SEM1 : 120x120x30`, `P1(25x35)`,
`POT3 30x30`, `N1-(25X40)`, `BN_25x30`, `M1(20x250)`, `D1 e=20`…

Les anciens parseurs étaient spécialisés par famille (et donc dépendants du
plan de référence). `label_scanner` fournit une **couche unique** qui reconnaît
toute étiquette « repère + dimensions », quelle que soit :

- la famille (semelle, poteau, poutre, longrine, chaînage, mur, voile, dalle,
  escalier, massif, radier, pieu, fut, console, linteau…) ;
- l'écriture (parenthèses, tiret, underscore, deux-points, espaces) ;
- l'unité (cm par défaut, m si valeurs < 3, mm si suffixe `mm`) ;
- la source (PDF vectoriel, OCR, DXF, IFC).

## Chaîne de traitement

```
mots de la page
   ├─ parse_label(token)              REF + dims dans un seul mot
   ├─ _pair_labels(words)             REF puis (dims) dans deux mots voisins
   └─ _parse_generic_table(rows)      REF | dims dans deux cellules
                    ↓
        register_label(catalogue)     fusion non destructive
                    ↓
        _finalize_sections(cat)       section_str + statut NON_RESOLU
                    ↓
        ui/correction_view            colonnes et sections par famille
```

## Règles de dimension par famille

| Famille | Écriture | Clés |
|---|---|---|
| semelle, massif, radier, escalier | `A x B x H` | `a`, `b`, `h` |
| poteau, contre-fort, fut | `A x B` | `a`, `b` |
| poutre, longrine, chaînage, linteau | `B x H` | `b`, `h` |
| mur, voile | `ép x H` | `a`, `h` |
| dalle | `e=20` / `D1(20)` | `h` |
| semelle filante `SF` | `largeur x hauteur` | `a`, `h` |

Ces règles sont centralisées dans `SECTION_RULES` et `REF_RULES` : ajouter une
convention = **une ligne**.

## Priorité des sources (jamais d'écrasement silencieux)

1. Une **étiquette explicite** (`P1(25x35)`) prime sur une valeur déduite par
   proximité (marquée `section_source` absente).
2. Deux sections **différentes** pour la même référence ne sont jamais
   fusionnées : la seconde ouvre une clé `REF_25x40`.
3. Les doublons `N1` / `N1_25x40` (même section) sont fusionnés.
4. Les artefacts de découpe (`'1'` extrait de `SEM1`) sont fusionnés dans
   l'entrée nommée équivalente et tracés dans `hypotheses`.
5. Rien n'est inventé : sans dimension lue, l'entrée reste `null` et une
   hypothèse `NON_RESOLU` est écrite.

## Ajouter une convention

```python
# core/label_scanner.py
PREFIX_FAMILIES = (
    ("ESC", "escaliers"),
    ...
    ("MONPREFIXE", "poteaux"),   # 1 ligne
)
REF_RULES = {"SF": ("a", "h"), "MONPREFIXE": ("b", "h")}
```

## Vérification

```bash
python -m pytest tests/test_label_scanner.py tests/test_generic_extraction.py -q
```

Les tests couvrent 6 plans synthétiques aux conventions différentes
(parenthèses, espaces, deux-points, tableaux à deux cellules, blocs DXF,
étiquettes nues) sans dépendre d'un PDF de référence.
