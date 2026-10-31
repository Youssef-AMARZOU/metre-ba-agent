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
* **Agrégation axe par axe** : les échelles X et Y sont calculées
  séparément puis comparées.
  * Écart ≤ 15 % → moyenne des deux axes (médiane des estimations
    concordantes), confiance 0,90–0,92.
  * Écart > 15 % → **aucune moyenne** (elle inventerait une échelle fausse) :
    l'axe le **mieux coté** est retenu, confiance 0,55, alerte
    `[ALERTE ECHELLE] axes X et Y incohérents`.
    Cas typique : un plan basse résolution où l'axe Y ne porte qu'une cote de
    chaîne (`15'-0"`) — la prendre pour un hors-tout donnait 60,6 px/m au lieu
    de 12,0, soit +204 % d'échelle et +89 % de surface.
  * Un seul axe coté → échelle acceptée, confiance 0,60, alerte
    « une seule cote hors-tout exploitable ».
* **Hors-tout = plus grande cote de son axe** : aucune cote de chaîne ne peut
  dépasser la dimension totale ; si la plus grande cote contredit la médiane
  des chaînes (> 25 %), la plus grande cote *cohérente* est retenue.

Toutes les estimations restent visibles dans `SurfaceReport.scale_estimates`
(méthode, valeur, cotes utilisées) — rien n'est silencieux.

## 1 bis. Préparation de l'image

Deux défauts de scan rendaient le calcul faux sans le dire ; ils sont
détectés et corrigés avant toute mesure, avec une alerte tracée :

| Défaut | Détection | Correction |
| :--- | :--- | :--- |
| **Scan inversé** (tirage bleu, négatif, photocopie foncée) | médiane de luminance < 110 | inversion de l'image (`bitwise_not`) — sinon tout le papier est pris pour du mur |
| **Bruit de scan** (sel et poivre, poussière) | part de pixels sombres isolés > 2 % | filtre médian 3×3 (deux passes au-delà de 15 %) — sinon l'OCR perd `192'-0"` et lit une cote de chaîne à sa place (échelle ×3) |

## 1 ter. Lecture des murs (hachures, double trait, pointillés)

Sur un plan réel, le mur n'est presque jamais un aplat franc. Il est hachuré,
dessiné en **double trait** (deux nus parallèles avec du blanc entre eux) ou en
**pointillés**. Une fermeture morphologique globale appliquée à l'image brute
fusionne alors les hachures obliques en blobs et coupe les pièces : la mesure
échoue silencieusement. Le masque de murs procède donc dans cet ordre :

| Étape | Traitement | Rôle |
| :--- | :--- | :--- |
| 1 | `_binarize_ink` : seuil adaptatif + garde-fou `gray > 205` | isole l'encre sans prendre le papier grisé pour du mur |
| 2 | **ouverture** `(k,k)` | ne garde que les traits épais (murs pleins) |
| 3 | **ouverture directionnelle** `(Lmin,1)` / `(1,Lmin)` | isole les **nus** horizontaux et verticaux **sans les fusionner** |
| 4 | `_link_dot_chains` | relie les points alignés et régulièrement espacés (mur pointillé) |
| 5 | fermeture **perpendiculaire** `(1,G)` / `(G,1)` | comble l'écart entre **deux nus parallèles** (≤ G ≈ 0,43 m) |
| 6 | nettoyage final | supprime ce qui n'est pas allongé dans un seul axe |

Points importants :

* Le tri se fait sur la **géométrie**, pas sur l'épaisseur : une hachure à
  45° a une boîte englobante quasi carrée, un mur est allongé dans un seul
  axe. C'est ce critère qui rejette le mobilier et les hachures de sol.
* Les bandes ne sont fermées **qu'entre deux nus parallèles**, jamais sur
  l'image brute : un meuble n'est donc pas refermé en fausse pièce.
* Le noyau de fermeture est **impair** : un noyau de taille paire épaissit un
  trait de 1 px (asymétrie d'ancre OpenCV), ce qui laissait survivre le
  mobilier filaire et découpait la pièce.
* Aucun mur n'est inventé : si les traits ne se referment pas, la pièce est
  signalée ouverte et sa surface n'est pas fabriquée.

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

### 4 bis. Matrice de robustesse (plan de référence 192'-0" × 75'-0")

Le plan de référence est dégradé de 19 façons réalistes ; on compare l'échelle
retrouvée et la surface de chacune des 10 pièces à la vérité terrain.

| Variante | Erreur d'échelle | Erreur surface moyenne | Pièces mesurées |
| :--- | ---: | ---: | ---: |
| référence | +0,4 % | 1,8 % | 10/10 |
| rotation 1° / 3° | +1,1 % / +1,5 % | 3,3 % / 4,0 % | 10/10 |
| cisaillement 2° | +1,1 % | 3,2 % | 10/10 |
| perspective 2,5 % / 5 % | −3,6 % / −7,4 % | 1,7 % / 2,2 % | 10/10 |
| JPEG q50 / q20 | +0,7 % / +0,5 % | 2,4 % / 1,9 % | 10/10 |
| 2000 / 1500 / 900 px de large | +0,8 % / +0,2 % / +0,8 % | 2,7 % / 1,8 % / 2,6 % | 10/10 |
| agrandissement 4000 px | +0,7 % | 2,3 % | 10/10 |
| bruit gaussien | +0,4 % | 1,9 % | 10/10 |
| sel et poivre 0,5 % | +0,9 % | 2,9 % | 10/10 |
| flou léger | +1,1 % | 3,1 % | 10/10 |
| scan inversé | +0,4 % | 1,8 % | 10/10 |
| papier bleu | +0,4 % | 1,8 % | 10/10 |
| cotes métriques | +0,1 % | 1,1 % | 10/10 |
| mobilier dense | +0,6 % | 3,5 % | **9/10** |
| **sans aucune cote** | non calibrable | — | **0/10 (refus explicite)** |

### 4 ter. Généralisation à une géométrie indépendante (plan B)

Le plan de référence est une seule disposition. Pour vérifier que le moteur ne
surajuste pas son rendu, une **géométrie différente** est mesurée : bâtiment
12,00 × 9,00 m métrique, libellés FR, pièce en **L**, couloir, 6 pièces
(vérité terrain par formule de Gauss sur le contour intérieur). Géométrie et
OCR sont jugés **séparément**.

| Style de mur | Échelle | Pièces appariées (géométrie) | Erreur moyenne | Libellés exacts |
| :--- | ---: | ---: | ---: | ---: |
| aplat noir | +2,3 % | 6/6 | 5,4 % | 6/6 |
| double trait | +2,3 % | 6/6 | 5,4 % | 6/6 |
| poche hachurée dense | +1,9 % | 6/6 | 4,8 % | 4/6 |
| poche hachurée | +1,9 % | 5/6 | 12 % | 3/4 |
| pointillé | +1,7 % | 5/6 | 7,3 % | 5/5 |

L'erreur homogène d'environ 5 % est un **biais d'échelle** (l'enveloppe du
bâtiment inclut le débord des murs), pas une erreur géométrique : en pixels,
les surfaces sont exactes à moins de 1 %. Les limites restantes :

* **Couloir en pointillé/poché** non refermé (les longues cloisons fines ne
  sont pas toutes reconstruites) → pièce absente, aucune surface inventée.
* **OCR** lit parfois `COULOIR` → `COULOI` et `SALON` → `SAON` sur les murs
  pochés : la géométrie est correcte, le libellé non (voir § 2, OCR renforcé).

Limites connues (mesurées, non corrigées) :

* **Mobilier qui traverse un libellé** : l'OCR peut perdre ce libellé (1 pièce
  sur 10 dans le test). Aucune surface n'est inventée pour les autres.
* **Plan sans cote ni échelle** : l'application refuse de mesurer et le dit ;
  utiliser la saisie manuelle largeur/hauteur hors-tout.
* **Croquis à main levée, photo en biais forte (> 5 %), plan en perspective
  marquée** : hors du domaine mesuré.
* **DXF/IFC** : non couverts par ce module.

Tests automatisés : `tests/test_surface_metre.py`,
`tests/test_surfaces_rooms.py` (16 tests).
