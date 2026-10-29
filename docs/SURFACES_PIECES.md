# Mesure des pièces sur un plan d'architecture

Ce module (`extractors/surface_extractor.py`) calcule la surface des pièces
d'un plan de distribution. Il est indépendant du module structure BA : un plan
résidentiel sans étiquette `S1` / `P1` / `N1` doit passer par le mode
**« Surfaces architecture uniquement »**.

## 1. Calibration de l'échelle (px/m)

Ordre de priorité :

1. **Calibration manuelle** : largeur / hauteur hors-tout saisie par
   l'ingénieur (`known_width_m` / `known_height_m`) → confiance 0,95.
2. **Cote hors-tout** : la cote extérieure (ex. `192'-0"`) confrontée à
   l'emprise des murs. C'est la mesure la plus fiable : elle relie directement
   le dessin à la dimension réelle.
3. **Chaînes de cotes** : paires consécutives de cotes (`57'-0"`, `60'-0"`…).
   Elles servent de **contrôle**, pas de référence : sur un plan en pieds,
   l'OCR les bruité facilement.

Règles de sécurité :

* Une cote hors-tout n'est retenue que si sa valeur est **cohérente à 25 %
  avec la médiane des chaînes**. Sinon une lecture OCR erronée (ex. `24.00`
  au lieu de `8.00`) fausserait toute la métré ; la cote est alors écartée,
  listée dans `scale_estimates` (`hors_tout_x_ignoree`) et une alerte est
  émise.
* Deux cotes hors-tout très proches mais différentes (ratio ≥ 0,55, écart
  > 3 %) sur la même ligne → `[ALERTE ECHELLE] cotes hors-tout
  contradictoires` : l'ingénieur doit saisir la dimension réelle.
* Les axes X et Y sont contrôlés séparément (scan déformé) ; l'axe le mieux
  soutenu par les chaînes est retenu et la confiance est abaissée.

Toutes les estimations restent visibles dans `SurfaceReport.scale_estimates`
(méthode, valeur, cotes utilisées) — rien n'est silencieux.

## 2. Libellés de pièces

* Les libellés sont filtrés par lexique (`KITCHEN`, `BEDROOM`…) et par forme.
* Les libellés **empilés** sont fusionnés (`MASTER` + `BEDROOM` →
  `MASTER BEDROOM`) ; les boîtes OCR qui se chevauchent de 1–2 px sont
  acceptées.
* Un **numéro isolé** rattaché au libellé est récupéré (`BEDROOM` + `2` →
  `BEDROOM 2`), sinon deux pièces différentes porteraient le même nom.

## 3. Mesure d'une pièce — ordre de priorité

| Priorité | Source | Condition |
| :--- | :--- | :--- |
| 1 | `cote_explicite` | cote `L x l` accolée au libellé (appariement mutuel) |
| 2 | `segmentation` (région) | région fermée **exclusive** au libellé : géométrie réelle, donc exacte même pour une pièce en **L** |
| 3 | `segmentation` (boîte ancrée) | région partagée : 5 rayons par direction depuis le libellé jusqu'aux murs |
| 4 | `segmentation` (enveloppe) | côté ouvert dans un espace partagé : la mesure est **bornée par l'enveloppe** du bâtiment |
| 5 | `zone_ouverte` | espace partagé non mesurable → ligne `ZONE OUVERTE` résiduelle |
| 6 | `aucune` | rien de mesurable → surface vide, aucune invention |

Points importants :

* **Pièce en L** : la région segmentée donne la surface exacte ; la boîte
  ancrée la surestimerait. Un écart boîte/région > 35 % est signalé.
* **Ouverture large / plan libre** : la surface de la région est répartie
  entre les pièces **au prorata** de leurs mesures individuelles, de sorte
  que la somme égale exactement la surface mesurée (aucun double comptage,
  aucun m² inventé). Le facteur appliqué est tracé dans les avertissements.
* **Anti double comptage** : deux boîtes qui se recouvrent de plus de 15 %
  → l'intersection est retirée de la plus petite, avec avertissement.
* Une ligne `ZONE OUVERTE` n'est créée que si un résidu réel subsiste ; elle
  disparaît dès que toutes les pièces sont mesurées.

## 4. Validation

Bancs de contrôle (plans synthétiques à géométrie exacte, cotes en
pieds/pouces `192'-0"` × `75'-0"`, échelle vraie 21,19 px/m) :

| Contrôle | Résultat |
| :--- | :--- |
| Échelle retrouvée | 21,30 px/m (+0,5 %) |
| 10 pièces rectangulaires | −1,2 % à −2,5 % (biais d'échelle connu) |
| Pièce en L | −2,4 % (la boîte ancrée aurait donné +14 %) |
| Deux pièces séparées par une porte | mesurées séparément, aucune `ZONE OUVERTE` |
| Plan sans cote | `NON TROUVEE`, aucune surface inventée |

Tests automatisés : `tests/test_surface_metre.py`,
`tests/test_surfaces_rooms.py`.
