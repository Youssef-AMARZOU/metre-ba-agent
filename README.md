# PlanBA — Métré Extracteur (Béton Armé)

Plan PDF de fondations en béton armé → métré Excel, rapport PDF d'audit et optimisation de découpe — 100 % en local, sans API. Chaque quantité est sourcée (texte natif, vecteurs, vision) et chaque écart est documenté : aucune valeur inventée.

Ce logiciel lit un plan de fondations en béton armé et calcule automatiquement les quantités (métré) : volumes de béton, poids d'acier, terrassements. Il produit des fichiers Excel et un rapport PDF, sans envoyer vos plans sur internet : tout se passe sur votre ordinateur.

Il est destiné aux métreurs, conducteurs de travaux, bureaux d'études, étudiants en génie civil, et à toute personne qui doit chiffrer des fondations à partir d'un plan.

---

## Sommaire

1. [Comprendre ce que fait le logiciel](#1-comprendre-ce-que-fait-le-logiciel)
2. [Ce qu'il vous faut avant de commencer](#2-ce-quil-vous-faut-avant-de-commencer)
3. [Installation pas à pas](#3-installation-pas-à-pas)
4. [Première utilisation pas à pas](#4-première-utilisation-pas-à-pas)
5. [Les fichiers produits, expliqués simplement](#5-les-fichiers-produits-expliqués-simplement)
6. [Utilisation avancée avec la ligne de commande](#6-utilisation-avancée-avec-la-ligne-de-commande)
7. [Le modèle Excel vierge et ses macros](#7-le-modèle-excel-vierge-et-ses-macros)
8. [Comment le logiciel lit votre plan](#8-comment-le-logiciel-lit-votre-plan)
9. [Limites honnêtes : ce que le logiciel ne fait pas](#9-limites-honnêtes--ce-que-le-logiciel-ne-fait-pas)
10. [Problèmes fréquents et solutions](#10-problèmes-fréquents-et-solutions)
11. [Pour les développeurs](#11-pour-les-développeurs)
12. [Licence](#12-licence)

---

## 1. Comprendre ce que fait le logiciel

### Le problème de départ

Quand on doit chiffrer des fondations, on part d'un plan papier ou PDF : un dessin avec des semelles (les plots de béton sous les poteaux), des poteaux, des poutres, et des tableaux qui décrivent les dimensions et les armatures (les barres d'acier à l'intérieur du béton). Faire le métré à la main — c'est-à-dire compter chaque élément, calculer chaque volume de béton et chaque poids d'acier — prend des heures et expose aux erreurs de calcul ou d'oubli.

### Ce que fait PlanBA à votre place

Vous lui donnez le plan (fichier PDF le plus souvent). Le logiciel :

1. **Lit le plan** : il repère les semelles et leur position (par exemple « la semelle S2 se trouve à l'intersection de l'axe A et de la file 5 »), les dimensions, les armatures décrites dans les tableaux du plan.
2. **Calcule les quantités** : pour chaque semelle, le volume de terrassement (la fouille à creuser), le volume de béton de propreté (la fine couche sous la semelle), le volume de béton armé, puis le poids de chaque barre d'acier.
3. **Remplit des tableaux Excel** avec les résultats — avec des formules actives, donc si vous modifiez une dimension, les totaux se recalculent tout seuls quand vous ouvrez le fichier dans Excel.
4. **Optimise la découpe des aciers** : il regroupe les barres à couper dans des barres standard de 12 mètres pour limiter les chutes (les morceaux perdus).
5. **Rédige un rapport PDF** qui résume : volume total de béton, poids total d'acier, ratio acier/béton, et les points à vérifier.

### Un exemple concret

Sur un plan de fondation de 6 pages, le logiciel trouve par exemple 23 semelles (types S1 à S5), 4 types de poteaux et 7 types de poutres. Il calcule environ 630 kg d'acier au total. Tout cela en moins d'une minute, fichiers Excel et PDF à l'appui.

### Une règle importante : rien n'est inventé

Si le plan ne donne pas une information (par exemple le ferraillage d'une semelle), le logiciel ne l'invente pas : il laisse la case à zéro, écrit un avertissement bien visible (« à vérifier sur coupes ») et l'indique dans le rapport. Si le plan est complètement inexploitable (mauvais fichier, photo floue), le logiciel affiche une erreur claire au lieu de produire des tableaux vides. C'est volontaire : un faux chiffre dans un métré coûte beaucoup plus cher qu'un avertissement.

---

## 2. Ce qu'il vous faut avant de commencer

- **Un ordinateur sous Windows 10 ou Windows 11.** Le logiciel fonctionne aussi sur Mac et Linux depuis le code source, mais le glisser-déposer de fichiers et l'exécutable prêt à l'emploi sont prévus pour Windows.
- **Python 3.12**, uniquement si vous installez depuis le code source (voir option B ci-dessous). Python est un langage de programmation gratuit. Pendant son installation, cochez bien la case « Add python.exe to PATH » (ajouter Python au chemin système) : sans cela, Windows ne trouvera pas la commande `python` et rien ne marchera.
- **Environ 500 Mo d'espace disque libre** pour l'environnement et les bibliothèques nécessaires.
- **Vos plans** : fichiers PDF de préférence (plans vectoriels, c'est-à-dire exportés depuis un logiciel de dessin, pas photographiés). Les photos/scans et fichiers AutoCAD (DXF/DWG) sont aussi acceptés, avec une lecture un peu moins précise pour les scans.

**Vocabulaire utile pour la suite :**
- *Invite de commandes / terminal* : la fenêtre noire de Windows où l'on tape des commandes texte (touche Windows, taper `cmd` ou `PowerShell`, Entrée).
- *Environnement virtuel* (`venv`) : un dossier qui isole les bibliothèques du projet pour ne pas perturber le reste de l'ordinateur. Vous n'avez pas besoin de comprendre son fonctionnement, juste de suivre les commandes.
- *Classeur* : un fichier Excel (qui contient une ou plusieurs feuilles).

---

## 3. Installation pas à pas

### Option A — Depuis le code source (conseillée pour commencer)

**Étape 1 : récupérer le projet.**
Ouvrez l'invite de commandes (touche Windows, tapez `cmd`, Entrée) puis tapez :

```
git clone https://github.com/Youssef-AMARZOU/metre-ba-agent.git
cd metre-ba-agent
```

La première ligne télécharge le projet depuis internet (il faut avoir installé Git au préalable : [git-scm.com](https://git-scm.com)). La seconde entre dans le dossier du projet. Toutes les commandes suivantes se tapent depuis ce dossier.

**Étape 2 : créer l'environnement isolé et installer les bibliothèques.**

```
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
pip install customtkinter openpyxl reportlab PyMuPDF Pillow pydantic tkinterdnd2
```

Explications ligne par ligne :
- `python -m venv .venv` : crée un dossier `.venv` qui contiendra une copie privée de Python pour ce projet.
- `.\.venv\Scripts\activate` : active cet environnement (l'invite affiche alors `(.venv)` au début de la ligne).
- Les deux lignes `pip install` téléchargent et installent les bibliothèques dont le logiciel a besoin (lecture PDF, écriture Excel, interface graphique, etc.). Cela prend quelques minutes la première fois.
- `ezdxf`, `pdfplumber`, OpenCV et PaddleOCR sont des adaptateurs **optionnels**. Leur absence ne bloque pas le PDF vectoriel/raster de base : l'application détecte l'adaptateur manquant, conserve un livrable partiel et affiche l'avertissement correspondant. Pour le support DXF, ajoutez `pip install ezdxf`; pour un fallback PDF texte léger, ajoutez `pip install pdfplumber`.

**Étape 3 : lancer l'application.**

```
python main.py
```

La fenêtre du logiciel s'ouvre. Passez à la section 4.

> Remarque : il existe un fichier `start.bat` (double-clic) censé automatiser ces étapes s'il est à jour sur votre poste.

### Option B — Exécutable Windows autonome (sans Python, pour un usage régulier)

Si vous utilisez le logiciel souvent et ne voulez plus taper de commandes, fabriquez l'exécutable une seule fois :

```
.\.venv\Scripts\python.exe build_executable.py
```

La compilation dure 5 à 10 minutes. À la fin, lancez :

```
dist\PlanBA_Metre_Extractor\PlanBA_Metre_Extractor.exe
```

(Conservez tout le dossier `PlanBA_Metre_Extractor`, pas seulement le fichier `.exe` : le programme a besoin des fichiers à côté de lui.)

Points à savoir sur l'exécutable :
- Une petite fenêtre « Chargement… » apparaît au démarrage puis disparaît toute seule quand l'interface est prête.
- Le dossier `dist/` pèse environ 300 Mo (il contient le moteur de lecture de plans scannés) et n'est **pas** fourni avec le dépôt : chaque poste construit son propre exécutable.
- La première ouverture peut être ralentie par l'antivirus Windows qui analyse le programme — c'est normal, patientez.

---

## Adaptateurs optionnels, licences et mode dégradé

Le chemin de base utilise PyMuPDF, Pillow, openpyxl et reportlab. Les modules
optionnels sont chargés à la demande afin de ne pas alourdir l'exécutable
PyInstaller : `ezdxf` (DXF), `pdfplumber` (fallback texte), OpenCV/PaddleOCR
(raster/OCR). Vérifiez leurs licences respectives avant redistribution
commerciale ; elles sont référencées dans leurs distributions et ne sont pas
vendues avec PlanBA. La commande `python -c "from core.ingestion import
optional_dependencies; print(optional_dependencies())"` permet de contrôler
les adaptateurs présents.

Si un adaptateur manque, le pipeline ne fabrique pas de géométrie : il écrit
la provenance, le niveau de confiance et l'avertissement dans `plan_data.json`
et produit quand c'est possible un Excel partiel vérifiable.

## 4. Première utilisation pas à pas

### 4.1. Charger un plan

Dans la fenêtre principale, vous avez deux façons de donner votre plan au logiciel :

- **Bouton « Parcourir… »** : cliquez, choisissez votre fichier PDF (ou DXF, DWG, PNG, JPG) dans la fenêtre qui s'ouvre, validez.
- **Glisser-déposer** : faites glisser le fichier depuis l'explorateur Windows et déposez-le n'importe où sur la fenêtre du logiciel.

Juste après le chargement, le logiciel examine rapidement le document et affiche son verdict sous la zone de dépôt :
- **Texte vert** : c'est bien un plan technique, le bouton d'analyse s'active. Vous pouvez continuer.
- **Texte orange « accepté avec réserve »** : le document ressemble peu à un plan (score faible), mais il n'est pas bloqué : l'analyse réelle vérifiera son contenu à l'étape suivante.

Les noms de fichiers avec espaces, accents ou parenthèses ne posent aucun problème : le logiciel nettoie le chemin tout seul.

### 4.2. Lancer l'analyse

Cliquez sur le bouton **« Lancer l'Analyse & Générer le Métré »**. Le traitement démarre dans l'ordre suivant, avec une barre de progression qui indique la page en cours (`Page 3/6 : plan`, `Page 4/6 : tableau`, etc.) :

1. Lecture du plan, page par page.
2. Calcul des métrés et ferraillages.
3. Remplissage du classeur Excel.
4. Optimisation de la découpe des barres.
5. Rédaction du rapport PDF.

Pendant l'analyse, des messages s'affichent dans le journal en bas de fenêtre : éléments trouvés, avertissements (donnée manquante sur le plan), hypothèses appliquées (voir section 9). Lisez-les : ils vous disent exactement ce qui mérite une vérification de votre part.

### 4.3. Ouvrir les résultats

Quand le message « Terminé » apparaît, deux boutons permettent d'ouvrir directement le fichier Excel et le dossier de sortie. Tous les fichiers produits se trouvent dans le dossier `output/` (situé à côté du programme).

### 4.4. Si quelque chose se passe mal

- **Message « Veuillez sélectionner un fichier d'abord »** alors que le fichier est affiché : rechargez le fichier (le chemin enregistré avait été perdu). Si cela persiste, signalez-le avec le nom exact du fichier.
- **Message « Échec d'extraction : Aucun élément structural détecté »** : le document n'est pas un plan de structure exploitable (mauvais fichier, plan vide, photo illisible). Vérifiez que vous avez chargé le bon fichier.
- **Aucun fichier Excel produit mais pas d'erreur** : regardez le journal — un avertissement explique toujours ce qui manque.

---

## 5. Les fichiers produits, expliqués simplement

Tous les fichiers se créent dans le dossier `output/`.

### `plan_data.json` — les données brutes extraites

C'est un fichier texte structuré (format JSON, lisible avec le Bloc-notes) qui contient tout ce que le logiciel a compris du plan : la liste des types de semelles/poteaux/poutres avec leurs dimensions et armatures, la position de chaque semelle (axe + file), et une section `_meta` qui retrace les pages lues, les avertissements et les hypothèses. Vous n'avez normalement pas besoin de le modifier : c'est la matière première des trois fichiers suivants.

### `metre_genere.xlsx` — le métré (le fichier principal)

C'est un classeur Excel à 2 feuilles, avec des **formules actives** (les totaux se recalculent si vous changez une valeur) :

- **Feuille 1 « Detail quontitafif fondation »** : pour chaque semelle implantée, trois lignes de calcul —
  - *Terrassement* : volume de terre à creuser, calculé sur une fouille élargie de 40 cm de chaque côté et profonde de 1,50 m ;
  - *Béton de propreté* : fine couche de 10 cm sous la semelle, élargie de 20 cm ;
  - *Béton armé* : volume réel de la semelle (longueur × largeur × hauteur).
  Puis les fûts de poteaux. Chaque bloc se termine par un sous-total, et un total général clôt la feuille.
- **Feuille 2 « Armatures »** : pour chaque élément, une ligne principale (repère, axe, file, dimensions) suivie des lignes d'armatures — par exemple « Armature INF X » (nappe inférieure dans le sens X) avec le nombre de barres, le diamètre et la longueur développée calculée par `LONG = (dimension − 0,05) + 34 × diamètre / 1000`. Les colonnes T6 à T32 ventilent automatiquement les longueurs par diamètre (`=SI($I=diamètre, ...)`), et le bas de feuille totalise : longueur totale par diamètre, poids au mètre (diamètre²/162), poids partiels, poids total.

### `optimisation_chantiers.xlsx` — la découpe des aciers

Pour chaque diamètre, le logiciel regroupe les barres à couper dans des barres standard de 12 mètres (celles qu'on achète chez le fournisseur) de façon à gaspiller le moins possible. Le tableau indique : nombre de barres nécessaires, longueur totale, chute (morceau perdu) par barre, taux de chute global et poids total. Les poutres dont la portée n'est pas cotée sur le plan sont exclues du calcul avec un avertissement explicite (le logiciel refuse d'inventer une longueur).

### `rapport_metre.pdf` — le rapport d'audit

Un document PDF de synthèse : volume total de béton, poids total d'acier, ratio acier/béton (kg par m³ — un indicateur classique pour juger si un ferraillage est normal, léger ou lourd), ventilation par famille (semelles/poteaux/poutres) et par diamètre, puis les observations et avertissements. Point important : si le volume ou le poids calculé vaut zéro, le rapport affiche **« ALERTE : Données manquantes »** en toutes lettres — jamais un ratio flatteur sans objet.

---

## 6. Utilisation avancée avec la ligne de commande

La ligne de commande permet d'automatiser le traitement (plusieurs plans à la chaîne, scripts) sans ouvrir la fenêtre. Toutes les commandes se tapent depuis le dossier du projet, environnement activé.

**Pipeline complet** (extraction + métré + optimisation + rapport) :

```
.\.venv\Scripts\python.exe main.py --cli --input "reference/PLAN_BA_final.pdf"
```

Avec options (dossier de sortie et nom de projet personnalisés) :

```
.\.venv\Scripts\python.exe main.py --cli --input "mon_plan.pdf" --out "C:\Chantiers\ProjetX" --projet-nom "Villa R+2"
```

**Étapes séparées** (utile pour diagnostiquer ou réutiliser des données déjà extraites) :

```
# Extraction seule — affiche aussi la liste brute des blocs lus (preuve de lecture)
.\.venv\Scripts\python.exe extract_plan.py --input "plan.pdf" --out output/plan_data.json

# Métré seul, à partir d'un JSON existant
.\.venv\Scripts\python.exe build_metre.py --input output/plan_data.json --out output/metre_genere.xlsx

# Modèle vierge standardisé (voir section 7)
.\.venv\Scripts\python.exe generators/modele_metre.py --out output/modele_metre_BA.xlsx
```

**Lancer les tests** (vérifie que tout fonctionne après une modification du code) :

```
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Le projet compte environ 190 tests automatiques : calculs, extraction sur plans réels, interface, OCR, modèle Excel. Ils doivent tous passer avant toute diffusion d'une nouvelle version.

---

## 7. Le modèle Excel vierge et ses macros

En plus des métrés calculés à partir de vos plans, le projet fournit un **classeur modèle vide**, conforme aux habitudes des bureaux d'études et bureaux de contrôle, réutilisable pour n'importe quel chantier : `output/modele_metre_BA.xlsx` (généré par `generators/modele_metre.py`). Il ne contient aucune donnée de projet, uniquement la mise en page, les intitulés et les formules.

### Les 4 feuilles du modèle

1. **01_Detail_Quantitatif** : cartouche à remplir (N° de marché, projet, bâtiment, date, établi/vérifié par) puis 3 blocs vierges prêts à saisir — Fouilles, Béton de propreté, Béton armé. Chaque ligne calcule sa quantité partielle toute seule (`=SI(NBVAL(...);...)`), chaque bloc a son sous-total, et un total général clôt la feuille.
2. **02_Armatures** : tableau de décorticage des barres (ouvrage, axe, file, dimensions, nombre, diamètre, longueur) avec ventilation automatique par diamètre T6 à T32, longueurs totales, poids unitaires réglementaires (0,222 à 6,313 kg/ml), poids partiels, **poids total en kg et en tonnes**, et taux de ferraillage (kg d'acier par m³ de béton).
3. **03_Attachement_Ferraillage** : synthèse regroupée par type d'ouvrage (semelle S1, poteau P1…), destinée au visa du bureau de contrôle, avec poids de synthèse.
4. **04_GO_Attachement** : bordereau de situation — quantités du marché (à saisir) face aux quantités réalisées (reprises automatiquement des feuilles 1 et 2), reste à réaliser et pourcentage d'avancement calculés seuls.

Présentation : en-têtes bleu nuit, sections bleu acier, totaux vert émeraude soulignés doubles, zones de saisie en blanc, zones calculées en bleu givré, formats de nombres stricts, aucune fusion de cellules hors cartouche (pour ne pas casser le tri ni les formules).

### Les macros VBA (`vba/Module1.bas`)

Le fichier `vba/Module1.bas` contient trois macros prêtes à l'emploi. Pour les utiliser : ouvrez le modèle dans Excel, onglet **Développeur → Visual Basic → Fichier → Importer un fichier**, choisissez `Module1.bas`, puis enregistrez le classeur au format `.xlsm` (classeur avec macros). Les macros fournissent :

- **Navigation** : `NaviguerVers` (et raccourcis `AllerDetail`, `AllerArmatures`, `AllerSynthese`, `AllerBordereau`) pour sauter d'une feuille à l'autre via des boutons ;
- **Audit** : `VerifierCoherenceMetre` contrôle que le total acier de la feuille 2 correspond au bordereau (tolérance 1 kg), détecte les textes et valeurs négatives dans les dimensions, puis affiche une boîte de dialogue avec le bilan (béton m³, acier kg/tonnes, statut) ;
- **Réinitialisation** : `ReinitialiserDonneesTemplate` vide toutes les saisies pour réutiliser le modèle sur un autre chantier, sans toucher aux formules ni à la mise en page (confirmation demandée avant).

---

## 8. Comment le logiciel lit votre plan

Cette section explique le fonctionnement interne en termes simples — utile pour comprendre les messages du journal et les avertissements.

1. **Contrôle d'entrée.** Avant tout calcul, le logiciel vérifie que le fichier ressemble à un plan de structure (mots comme « semelle », « poteau », « ferraillage », grand format, dessins vectoriels). Un PDF, DXF ou DWG n'est jamais bloqué à cette étape : si le contenu s'avère inexploitable, c'est l'étape d'extraction qui le dira clairement.
2. **Lecture page par page.** Chaque page est classée (tableau de nomenclature, plan d'implantation, détail, ou page ignorée comme une page de garde), puis lue avec ses coordonnées réelles : chaque mot du PDF est connu avec sa position en x/y.
3. **Tableaux de nomenclature.** Les tableaux bordés sont détectés automatiquement (`find_tables`) : repère, dimensions, ferraillage par colonne. Sinon, le logiciel lit les lignes de texte ordonnées (ex. `S1` suivi de `90 x 90 x 25`, avec conversion centimètres→mètres).
4. **Plan d'implantation.** Les lettres (A, B, C…) et chiffres (1, 2, 3…) des bulles d'axes sont repérés avec leurs positions ; chaque étiquette de semelle (`S4(150x150x40)`, `(S1,Q1)`) est rattachée à l'intersection d'axes la plus proche.
5. **Détails poteaux et poutres.** Les caractéristiques sont cherchées autour de chaque repère : section `(25x30)`, barres `8T12` ou `4T12+4T10`, cadres `2CAD T6 e=15`.
6. **Plans scannés.** Si une page contient moins de 15 mots de texte mais une image (cas d'un scan), un moteur de lecture optique local (RapidOCR, 100 % hors-ligne) lit l'image et ses résultats suivent exactement le même circuit que le texte normal. Les plans vectoriels ne déclenchent jamais cette étape, donc jamais de ralentissement inutile.
7. **Calculs et livrables.** Les données vérifiées alimentent les formules Excel, le calpinage et le rapport.

**Conventions reconnues** (françaises et marocaines) : semelles `S`, `SF` (filantes) ; poteaux `P` et `Q` ; étiquettes couplées `(S1,Q1)` ; poutres `N`, `BN`, `PN`, `PC`, `LG`, `CH` (et variantes `BIS`) ; armatures `HA` ou `T` (`6HA14`, `8T12`), espacements `e=15`, `Esp=15`, `e=(10x9`.

**Dossiers testés en conditions réelles** (et couverts par des tests automatiques) : plan 6 pages (23 semelles, ~630 kg d'acier), dossier 70 pages (67 semelles), dossier R+2 (17 semelles, 17 poteaux Q1–Q4, 15 poutres).

---

## 9. Limites honnêtes : ce que le logiciel ne fait pas

Un bon outil dit ce qu'il ne sait pas faire. Voici les cas où le logiciel applique une hypothèse (toujours affichée dans le journal et le rapport) ou s'abstient :

| Situation | Comportement du logiciel |
|---|---|
| Hauteur des poteaux non cotée sur le plan | Hypothèse 3,00 m appliquée et signalée (à ajuster selon l'étage réel) |
| Portées des poutres non cotées | Poutres exclues du calpinage et du bilan, avec avertissement explicite |
| Ferraillage donné en espacement (`HA8, St=14,17 cm`) | Nombre de barres estimé par `⌊(dimension − 0,10) / espacement⌋ + 1`, nappes X et Y supposées identiques, marqué « à vérifier sur coupes » |
| Semelle implantée mais dimensions introuvables | Conservée avec la mention « Dimensions à renseigner », volumes à zéro signalés |
| Espacement noté `e=(10x9` (notation ambiguë) | Lu 0,10 m, marqué « à confirmer » |
| Page scannée illisible ou floue | L'OCR peut mal lire : les résultats suspects restent vérifiables dans `plan_data.json` |

Ce que le logiciel ne fait pas du tout : dimensionner les structures (choisir les sections et armatures — c'est le travail de l'ingénieur), lire les plans papier non scannés, ni garantir un résultat sans relecture humaine. **Le métré produit doit toujours être contrôlé par une personne compétente avant usage contractuel.**

---

## 10. Problèmes fréquents et solutions

**« Veuillez sélectionner un fichier d'abord » alors que le fichier est affiché.**
Rechargez le fichier avec « Parcourir… ». Si le problème persiste, vérifiez que le chemin ne contient pas de caractère exotique et signalez le nom exact du fichier.

**Le curseur affiche « interdit » quand je glisse un fichier.**
Le glisser-déposer nécessite la version avec le module tactile embarqué : utilisez l'exécutable construit par `build_executable.py`, ou installez `tkinterdnd2` (`pip install tkinterdnd2`) en mode sources. Le bouton « Parcourir… » fonctionne dans tous les cas.

**« Échec d'extraction : Aucun élément structural détecté ».**
Le document n'est pas un plan de structure exploitable : mauvais fichier, page de garde seule, photo floue ou plan sans aucun repère lisible. Essayez le plan de fondation (pas la page de garde) ou une meilleure qualité de scan.

**Le programme semble figé au démarrage (« Chargement… » reste affiché).**
Problème connu et corrigé : mettez à jour vers la dernière version compilée. En mode sources, ce cas ne se produit pas.

**Premier lancement très lent (plusieurs dizaines de secondes).**
Si vos fichiers sont synchronisés par OneDrive, Windows télécharge les fichiers à la première ouverture. Ce n'est qu'au premier passage ; ensuite le démarrage prend environ 1 à 2 secondes. De même, l'antivirus analyse le programme à sa première exécution — patientez.

**Erreur `ModuleNotFoundError: No module named 'numpy'` (anciennes versions).**
Corrigée : la bibliothèque de lecture AutoCAD ne se charge plus qu'à l'ouverture d'un vrai fichier DXF, et `numpy` est embarqué dans l'exécutable. Recompilez avec le `build_executable.py` actuel.

**Des dossiers `_MEI*` s'accumulent dans le dossier temporaire Windows.**
Restes d'anciennes versions compilées en mode « un seul fichier ». Supprimables sans risque. La version actuelle compile en mode dossier, qui ne crée plus ces répertoires.

**Le taux de chute de l'optimisation me paraît élevé.**
Vérifiez d'abord les avertissements : des poutres exclues (portées non cotées) ou des longueurs inhabituelles faussent le calcul. Le taux affiché ne porte que sur les barres effectivement calculées.

**Les totaux Excel affichent 0.**
Soit les données d'entrée sont vides (voir journal d'extraction), soit vous avez ouvert le fichier avec un visualiseur qui n'évalue pas les formules : ouvrez-le dans Microsoft Excel ou LibreOffice Calc et activez le calcul automatique.

---

## 11. Pour les développeurs

### Tests

```
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Environ 190 tests : mathématiques du béton armé, extraction sur plans réels (référence 6 pages, dossier 70 pages, R+2 marocain), garde-fous anti-données-fictives, interface (fichier, glisser-déposer, splash), OCR, validateur, modèle Excel et macros VBA. Ils doivent tous passer avant toute diffusion.

### Arborescence du projet

```
metre-ba-agent/
├── main.py                   # Entrée : interface graphique + ligne de commande (4 étapes)
├── ui/desktop_app.py         # Interface (thème sombre, DnD, validation, progression)
├── core/
│   ├── local_extractor.py    # Moteur vectoriel multi-pages (find_tables + spatial)
│   ├── validator.py          # Contrôle pré-import (score multi-pages, sans blocage dur)
│   ├── ocr_engine.py         # Secours OCR paresseux (RapidOCR/ONNX, pages scannées)
│   ├── ingestion.py          # Ingestion DXF / images / PDF raster
│   ├── calculator.py         # Mathématiques béton armé pures
│   ├── schemas.py            # Modèles de données
│   └── paths.py              # Chemins compatibles .exe (jamais de dossier temporaire)
├── build_metre.py            # Classeur métré (formules actives)
├── optimisation_chantiers.py  # Calpinage barres 12 m (+ alias generators/excel_optimisation.py)
├── rapport_metre.py          # Rapport PDF (ReportLab)
├── generators/modele_metre.py # Modèle vierge 4 feuilles
├── vba/Module1.bas           # Macros : navigation, audit, réinitialisation
├── build_executable.py       # Packaging PyInstaller (mode dossier + splash + dépendances)
├── assets/splash.png         # Image de chargement (générée si absente)
├── tests/                    # ~190 tests automatiques
├── reference/                # Plans + métré de référence (petits fichiers uniquement)
├── config/                   # Réglages (postes, options)
└── output/                   # Livrables générés (régénérables, non versionnés en détail)
```

### Construire l'exécutable

```
.\.venv\Scripts\python.exe build_executable.py
```

Le script nettoie `dist/` et `build/`, régénère le splash si besoin, compile en mode dossier avec les dépendances embarquées (interface, PDF, Excel, AutoCAD, glisser-déposer, OCR) et vérifie le résultat. Temps constaté : 5 à 10 minutes. Le dossier `dist/` (~300 Mo, modèles OCR inclus) n'est volontairement pas versionné : chaque poste construit le sien.

### Contribuer

Les corrections passent par des tests automatiques : toute nouvelle convention de plan lue doit être accompagnée d'un test qui la prouve sur un exemple (voir `tests/test_moroccan_conventions.py` comme modèle), et la suite complète doit rester verte.

---

## 12. Licence

MIT — © 2025 Youssef-AMARZOU. Voir le fichier [LICENSE](LICENSE).
