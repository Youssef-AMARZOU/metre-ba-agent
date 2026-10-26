# PlanBA Pro — Documentation Ingénieur & Développeur

> Guide utilisateur (non technique) : [README.md](README.md).
> Ce document décrit **les mathématiques**, **l'architecture logicielle**, **les technologies** et **la procédure de build** de PlanBA Pro.

Sommaire
1. [Principes](#1-principes)
2. [Stack technologique](#2-stack-technologique)
3. [Architecture et flux de données](#3-architecture-et-flux-de-données)
4. [Mathématiques du métré BA (BAEL 91 / RPS 2000 v2011 / DGA)](#4-mathématiques-du-métré-ba)
5. [Les 4 portes de validation (Gatekeeper)](#5-les-4-portes-de-validation)
6. [Computer Vision hybride](#6-computer-vision-hybride)
7. [Métré des surfaces (plans d'architecture)](#7-métré-des-surfaces)
8. [Classeurs Excel : formules dynamiques](#8-classeurs-excel)
9. [Ligne de commande](#9-ligne-de-commande)
10. [Tests, build, déploiement](#10-tests-build-déploiement)
11. [Limites connues](#11-limites-connues)

---

## 1. Principes

| Principe | Mise en œuvre |
|---|---|
| **Zéro hallucination mathématique** | L'OCR et le VLM ne produisent **jamais** un volume ni un poids. Ils retournent des grandeurs *lues* (`DrawingElement`). Tous les calculs sont faits par `MoroccanBETEngine` (formules codées en dur) puis recalculés par des **formules Excel** (`ROUND`, `SUM`, `SUMIF`, `IF`, `AND`). |
| **Traçabilité** | Chaque élément porte `source_engine`, `page_num`, `bbox`, `confidence`, `warnings`. Les overlays PNG entourent chaque zone lue. |
| **Données manquantes = NON TROUVÉ** | Jamais d'estimation silencieuse : anomalie `AuditAnomaly` ou mention `NON TROUVÉE`. |
| **100 % local, 100 % freemium** | PyMuPDF, OpenCV, RapidOCR (ONNX CPU). VLM optionnel : Ollama local ou Groq Free Tier via HTTP OpenAI-compatible. Aucune API payante. |
| **Non-régression** | Le pipeline vectoriel historique (`core/local_extractor.py`) reste le chemin rapide ; la vision n'intervient que sur les pages qui l'exigent. |

---

## 2. Stack technologique

| Couche | Technologie | Rôle |
|---|---|---|
| Langage | **Python 3.10+** (3.12 en production) | Tout le moteur |
| Lecture PDF | **PyMuPDF (fitz)**, pdfplumber | Texte natif, vecteurs, rendu 300 DPI, `find_tables` |
| DAO / BIM | **ezdxf**, ifcopenshell | DXF/DWG (TEXT, MTEXT, INSERT), IFC (IfcColumn, IfcBeam…) |
| Vision | **OpenCV**, **NumPy** | Binarisation adaptative, morphologie (tableaux), Canny/approxPolyDP (contours), HoughCircles (bulles d'axes), fermeture (portes), flood-fill (pièces) |
| Graphe spatial | **scipy.spatial.cKDTree** (repli NumPy dans l'exe) | Liaison contour ↔ repère ↔ cote ↔ acier |
| OCR local | **RapidOCR** (onnxruntime) ; PaddleOCR / EasyOCR / Tesseract si installés | Scans, polices SHX explosées, images |
| VLM (optionnel) | **Ollama** `qwen2.5-vl:7b` ou **Groq** `llama-4-scout` (requests, JSON strict, `temperature=0`) | Tableaux de ferraillage complexes, sur crops ROI uniquement |
| Données | **pydantic** (`core/schemas.py`), `dataclasses` (`core/bet_contracts.py`) | Contrats de données |
| Tableurs | **openpyxl** | Classeurs à formules, mise en forme conditionnelle, vignettes |
| Rapports | **reportlab** | PDF d'audit et note de calculs |
| Interface | **customtkinter** (Tk), tkinterdnd2 | Application bureau, thème génie civil |
| Packaging | **PyInstaller** (`--onedir --windowed --icon`) | Exécutable Windows autonome (~410 Mo) |
| Tests | **pytest**, Excel COM (pywin32) | 280+ tests dont recalcul réel des formules par Excel |

---

## 3. Architecture et flux de données

```
            PDF / DXF / DWG / IFC / Image
                        │
        ┌───────────────┴────────────────┐
        │  core/local_extractor.py        │  chemin vectoriel historique (texte natif + find_tables)
        │  core/pipeline/*                │  pipeline zero-miss (zones, légende, grille, réconciliation)
        └───────────────┬────────────────┘
                        │ plan_data (catalogue_types / implantations)
   needs_vision_fallback(page) ?  ──oui──►  extractors/vision_extractor.py
                        │                   ├─ render 300 DPI (PyMuPDF)
                        │                   ├─ detect_table_and_schedule_rois (morphologie)
                        │                   ├─ detect_structural_elements_contours (Canny)
                        │                   ├─ detect_grid_axis_bubbles (HoughCircles + OCR)
                        │                   ├─ ocr_page (extractors/ocr_router.py, tuiles 2400 px)
                        │                   ├─ associate_ocr_tokens_spatially (cKDTree)
                        │                   ├─ geometric_sanity_check (anti-hallucination)
                        │                   ├─ extract_roi_with_vlm (extractors/vlm_router.py)
                        │                   └─ render_audit_overlay → output/audit_overlays/
                        │                               │ DrawingElement[]
                        ▼                               ▼
          core/bet_contracts.py  ◄──── ExtractedPlanRecord (contrat unifié)
                        │
          core/moroccan_bet_engine.py  ── PlanBAGatekeeper (4 portes) ── MoroccanBETEngine
                        │ AvantMetreLine[], RebarScheduleRow[], AuditAnomaly[]
                        ▼
          core/populate_modele_bet.py  ── PlanBAExcelPopulator → metre_certifie_BET.xlsx (5 feuilles)
          build_metre.py / core/populate_modele.py → metre_genere.xlsx (gabarit historique)

   Plan d'architecture ──► extractors/surface_extractor.py ──► core/surface_metre_excel.py → metre_surfaces.xlsx
   Orchestration partagée CLI / UI : core/vision_bridge.py (run_vision_hybride, run_surface_metre)
```

Arborescence :

```
metre-ba-agent/
├── main.py                        # CLI + lancement GUI (--cli, --vision, --largeur-hors-tout)
├── ui/desktop_app.py              # Interface PlanBA Pro (KPI, surfaces, overlay, paramètres ingénieur)
├── core/
│   ├── bet_contracts.py           # DrawingElement, ExtractedPlanRecord, AvantMetreLine, RebarScheduleRow, AuditAnomaly
│   ├── moroccan_bet_engine.py     # MoroccanBETEngine + PlanBAGatekeeper (formules et contrôles)
│   ├── populate_modele_bet.py     # Classeur certifié 5 feuilles (formules dynamiques) + FFD 12 m
│   ├── surface_metre_excel.py     # Classeur surfaces (pièces / étages / second œuvre / audit échelle)
│   ├── vision_bridge.py           # Routage hybride partagé CLI/UI, pont vers plan_data legacy
│   ├── local_extractor.py         # Moteur vectoriel historique
│   ├── pipeline/                  # Pipeline zero-miss modulaire
│   └── …                          # calculator, schemas, ocr_engine, validator, dxf/ifc…
├── extractors/
│   ├── vision_extractor.py        # Computer Vision hybride (PlanBAVisionExtractor)
│   ├── surface_extractor.py       # Métré des surfaces (PlanSurfaceExtractor)
│   ├── ocr_router.py              # Routeur OCR local
│   └── vlm_router.py              # Routeur VLM OpenAI-compatible (Ollama / Groq)
├── assets/                        # app_icon.ico/png, splash.png, make_branding.py
├── tests/                         # pytest (test_bet_cv_pipeline.py, test_surface_metre.py, …)
├── build_executable.py            # Packaging PyInstaller
└── docs/                          # ZERO_HALLUCINATION.md, SOURCES.md, README_DETAILLE_v1.md (archive)
```

---

## 4. Mathématiques du métré BA

Toutes les formules sont dans `core/moroccan_bet_engine.py` (déterministes, testées).

### 4.1 Béton (DGA / CPS Maroc — sans double comptage)

**Béton de propreté (prix B.01)** — débord $d_b = 0{,}10$ m par face, épaisseur $e_{BP} = 0{,}10$ m :

$$V_{BP} = (A + 2d_b)(B + 2d_b)\; e_{BP}$$

**Semelle isolée (prix B.02)** — patin $A \times B \times h_1$ + tronc de pyramide si glacis $h_2 > 0$ (fût $a \times b$) :

$$V_{BA} = A\,B\,h_1 + \frac{h_2}{6}\left[AB + ab + (A+a)(B+b)\right]$$

La hauteur équivalente écrite dans Excel est $h_{eq} = V_{BA}/(AB)$ afin que la formule `=ROUND(F*G*H*I,3)` reproduise exactement $V_{BA}$.

**Poutres (B.06), longrines & chaînages (B.04)** — portée **strictement entre nus** des appuis :

$$L_{nus} = L_{axe\text{-}axe} - \frac{a_g}{2} - \frac{a_d}{2}, \qquad
h_{calcul} = \begin{cases} h_{totale} & \text{plancher hourdis} \\ h_{totale} - e_{dalle} & \text{dalle pleine (retombée)} \end{cases}$$

$$V = n \cdot L_{nus} \cdot b \cdot h_{calcul}$$

**Poteaux (B.05) / voiles (B.07)** : $V = n \cdot H \cdot b \cdot h$.

### 4.2 Acier FeE500 (NM 01.4.097 / RPS 2000 v2011)

Masses linéiques (kg/m) : HA6 0,222 · HA8 0,395 · HA10 0,617 · HA12 0,888 · HA14 1,208 · HA16 1,578 · HA20 2,466 · HA25 3,853 · HA32 6,313 · HA40 9,864.
Un diamètre hors catalogue (HA15, HA18…) lève `INVALID_DIAMETER_OCR` et propose le diamètre voisin.

**Barres longitudinales** — ancrage d'extrémité $2 \times 15\phi$ (sauf longueur `L=` explicite) et recouvrement $50\phi$ par tranche de barre commerciale de 12 m :

$$L_{dev} = L + 30\phi + n_{rec}\,50\phi,\qquad n_{rec} = \max\!\left(0,\ \left\lceil \tfrac{L + 30\phi}{12} \right\rceil - 1\right)$$

$$P = n_{ouvrages} \cdot n_{barres} \cdot L_{dev} \cdot m_\phi$$

**Cadres / étriers** (crochets à 135°, enrobage $c = 2{,}5$ cm en élévation) :

$$L_{cadre} = 2(b - 2c) + 2(h - 2c) + 2\max(10\phi,\ 0{,}08), \qquad
n_{cadres} = \left\lceil \frac{L}{e} \right\rceil + 1$$

### 4.3 Ratios de contrôle (kg d'acier / m³ de béton)

| Famille | Plage BET Maroc (zone sismique) |
|---|---|
| semelle | 40 – 95 |
| longrine | 75 – 140 |
| chaînage | 65 – 125 |
| poteau | 100 – 210 |
| poutre | 95 – 185 |
| voile | 65 – 140 |
| dalle pleine | 75 – 145 |

Hors plage → `RPS2011_RATIO_OUT_OF_BOUNDS` (bloquant), avec diagnostic (cm lu en m, barre oubliée, diamètre doublé).

### 4.4 Optimisation de coupe (feuille 5)

Algorithme **First-Fit Decreasing** par diamètre : les longueurs développées sont triées par ordre décroissant et placées dans la première barre de 12,00 m où elles tiennent. Sortie : nombre de barres à commander, longueur utile, chutes et taux de chute $\tau = (L_{commandée} - L_{utile}) / L_{commandée}$.

---

## 5. Les 4 portes de validation

`PlanBAGatekeeper` (`core/moroccan_bet_engine.py`) :

| Porte | Règle |
|---|---|
| **1 — Unités** | Sections `b,h,a,e` : $\ge 100 \Rightarrow /1000$ (mm), $> 3{,}5 \Rightarrow /100$ (cm). Grandes dims `A,B,L,H` : $\ge 500 \Rightarrow /1000$, $> 25 \Rightarrow /100$. |
| **2 — Déduplication** | Clé `(niveau, repère)`. Union des files d'axes ; les occurrences sur le plan de coffrage **se cumulent** (`qty_plan_coffrage`), la quantité du tableau est unique (`qty_table_schedule`). Quantité retenue $= \max(\lvert\text{axes}\rvert,\ qty_{coffrage})$ sinon $qty_{tableau}$. |
| **3 — Armatures** | Regex déterministes : `(\d+)\s*(HA|T|Ø|PHI)\s*(\d{1,2})`, `L\s*=\s*(\d+[.,]?\d*)`, cadres `(CAD|EP|ETRIER|ST)…(HA|T|Ø)(\d{1,2})\s*e=(\d+)`. Longueur `L=` explicite en cm si > 25. |
| **4 — Contrôle croisé** | `qty_plan_coffrage == qty_table_schedule` sinon `CROSS_CHECK_QTY_MISMATCH` ; ratio kg/m³ dans la plage (§4.3). |

---

## 6. Computer Vision hybride

`extractors/vision_extractor.py` — `PlanBAVisionExtractor`.

**Bascule automatique** (`needs_vision_fallback`) : page scannée ($< 15$ mots) **ou** polices AutoCAD SHX explosées ($> 1500$ vecteurs et $\text{vecteurs}/\text{mots} > 25$).

**Tableaux** : `adaptiveThreshold(~gray, 255, MEAN_C, BINARY, 15, −2)` → ouvertures morphologiques avec noyaux $(\max(30, W/80), 1)$ et $(1, \max(30, H/80))$ → masque `h+v`, intersections `h∧v` → `boundingRect` retenus si $w > 250$, $h > 120$ et $\ge 8$ intersections.

**Contours structurels** : Canny (50,150) → `approxPolyDP(0,02·périmètre)` à 4 sommets convexes, $25 \le w,h \le 800$ px, ratio $0{,}2 \ldots 5$.

**Bulles d'axes** : `HoughCircles` + token OCR `^[A-Z]$` ou `^\d{1,2}$` dans le cercle ; orientation (lettres en x ou y) déduite de la dispersion ; chaque repère reçoit `LETTRE-NUMÉRO` des bulles les plus proches.

**Graphe spatial** : `cKDTree` sur les centroïdes. Pour chaque repère : contour le plus proche ($\le 280$ px), section la plus proche, aciers dans un rayon $1{,}4 \times 280$ px.

**Anti-hallucination géométrique** : on compare le ratio lu $b/h$ au ratio pixel $w/h$ du contour. Parmi les candidats $(b\cdot10^i, h\cdot10^j)$, on retient celui qui minimise $\lvert \ln\frac{b/h}{w/h}\rvert$ s'il est plausible ($0{,}10 \le b,h \le 3{,}00$ m). Exemple : `25x400` → `0,25 × 0,40 m`, avertissement tracé, confiance réduite.

**VLM** (`extractors/vlm_router.py`) : uniquement les crops des tableaux, PNG base64, `response_format=json_object`, `temperature=0`, schéma `{"elements":[{category,label,quantity,dimensions,rebar_spec,grid_axes}]}`. Backends : Ollama `http://localhost:11434/v1` (défaut si vivant) ou Groq (`GROQ_API_KEY`). `PLANBA_VLM_BACKEND=auto|ollama|groq|off`.

**Overlay** : vert si `confidence ≥ 0,90` sans avertissement, orange à vérifier, rouge anomalie BET → `output/audit_overlays/page_{n}.png`.

---

## 7. Métré des surfaces

`extractors/surface_extractor.py` — `PlanSurfaceExtractor`.

1. **OCR** des libellés (lexique FR/EN + mots majuscules dans l'emprise) avec fusion des lignes empilées (`MASTER` + `BATH`).
2. **Cotes** : `parse_length` accepte `3.50`, `3,50 m`, `350`, `350 cm`, `3500 mm`, `16'-0"`, `13-6"`, `12.3'` ; `parse_room_dims` lit `L x l` (`18'×10"`, `4.20x3.50`, `420 x 350`).
3. **Échelle** $s$ (px/m) : pour deux cotes consécutives d'une même chaîne (centres $c_i$, valeurs $L_i$) :
   $$s_i = \frac{c_{i+1} - c_i}{(L_i + L_{i+1})/2}$$
   plus les cotes hors-tout vs enveloppe des murs. $s = \text{médiane}$, confiance $= 1 - 2\,\text{MAD}/s$. Deux cotes hors-tout centrées de rapport $> 1{,}8$ → **[ALERTE ÉCHELLE]**. Calibration manuelle prioritaire : $s = w_{enveloppe}/\text{largeur saisie}$.
4. **Murs** : Otsu → ouverture morphologique adaptative (noyau réduit jusqu'à ce qu'une composante couvre $\ge 25\%$ de l'image) ; enveloppe = union des composantes longues **et épaisses** ($\text{aire}/(w+h) \ge 2{,}5$ px, ce qui exclut les lignes de cotes).
5. **Pièces** : fermeture morphologique de noyau $0{,}85\,s$ (scelle une porte de 0,80–0,90 m sans fermer un dégagement de 1,00 m) → composantes de l'espace libre → remplissage des trous (mobilier) → suppression des régions incluses dans une plus grande (intérieur d'un lit, îlot).
6. **Surface retenue** : cote explicite $L \times l$ si présente (prioritaire), sinon $\text{aire}_{px}/s^2$ ; périmètre $= \text{arcLength}/s$. Régions partagées par plusieurs libellés → ligne **ZONE OUVERTE** $= \text{aire segmentée} - \sum \text{cotes explicites}$ (aucun double comptage). Région $< 3$ m² → « région suspecte », confiance ÷ 2.
7. **Second œuvre** (`core/surface_metre_excel.py`) : sol $=\sum S$, plinthes $=\sum P$, enduit et peinture $=\sum P \times HSP$, plafond $=\sum S$.

---

## 8. Classeurs Excel

`metre_certifie_BET.xlsx` (`core/populate_modele_bet.py`) :

| Feuille | Formules clés |
|---|---|
| `1_Avant_Metre_BA` | `J = ROUND(F*G*H*I, 3)` ; total `ROUND(SUM(J6:Jn), 3)` ; colonne témoin Python et écart `ROUND(J−K, 3)` |
| `2_Nomenclature_Acier` | `H = ROUND(E*F*G, 2)` ; ventilation `=IF($D{r}={d}, ROUND($H{r}*m_d, 2), 0)` pour HA6…HA32 ; sous-totaux et total général |
| `3_Bordereau_DQE` | `=ROUND(SUMIF('1_Avant_Metre_BA'!$A$6:$A$500, A{r}, '1_Avant_Metre_BA'!$J$6:$J$500), 3)` ; acier = cellule total de la feuille 2 ; HT, TVA 20 %, TTC (MAD) |
| `4_Controle_BET_Ratios` | volume/acier par famille (`SUMIF`), `=IF(B=0,0,ROUND(C/B,1))`, statut `IF(AND(D>=E, D<=F), "CONFORME BET MAROC", "ALERTE HORS PLAGE RPS 2011")`, mise en forme conditionnelle, journal des anomalies, vignettes OpenCV |
| `5_Optimisation_Barres_12m` | `D = B*12`, chutes `ROUND(D−C, 2)`, taux `IF(D=0,0,ROUND(E/D*100,2))`, poids `ROUND(D*m_d, 2)` |

`metre_surfaces.xlsx` (`core/surface_metre_excel.py`) : `F = IF(AND(ISNUMBER(D),ISNUMBER(E)), ROUND(D*E,2), "")`, `H = IF(ISNUMBER(F), F, IF(ISNUMBER(G), G, 0))`, sous-totaux par étage `SUMIF`, second œuvre lié à `$B$4` (HSP).

Le test `tests/test_bet_cv_pipeline.py::test_recalcul_excel_reel_sans_erreur` ouvre le classeur dans Excel (COM, sous-processus isolé), force `CalculateFullRebuild`, vérifie l'absence de `#REF!/#NAME?/#VALUE!` et l'égalité **Excel = témoin Python** (béton, acier, HT/TVA/TTC).

---

## 9. Ligne de commande

```bash
python main.py                                            # interface graphique
python main.py --cli --input plan.pdf --out sortie/       # pipeline complet
python main.py --cli --input plan.pdf --vision force      # Computer Vision sur toutes les pages
python main.py --cli --input plan.pdf --vision off        # chemin vectoriel pur
python main.py --cli --input plan_archi.jpg --largeur-hors-tout 18.90   # surfaces, échelle calibrée
```

Variables d'environnement : `PLANBA_VLM_BACKEND` (`auto|ollama|groq|off`), `OLLAMA_BASE_URL`, `OLLAMA_VLM_MODEL`, `GROQ_API_KEY`, `GROQ_VLM_MODEL`.

API Python :

```python
from extractors.vision_extractor import PlanBAVisionExtractor
from core.populate_modele import PlanBAExcelPopulator

res = PlanBAVisionExtractor().extract_pdf("plan.pdf")            # pages scannées / SHX
PlanBAExcelPopulator("Villa R+1").process_and_export(res["records"], "output/metre_certifie_BET.xlsx")

from core.vision_bridge import run_surface_metre
run_surface_metre("plan_archi.jpg", "output", "Villa R+1", known_width_m=18.90)
```

---

## 10. Tests, build, déploiement

```bash
pip install -r requirements.txt
python -m pytest tests -q                       # ~280 tests (Excel COM requis pour le recalcul réel, sinon skip)
python assets/make_branding.py                  # icône .ico multi-tailles + splash depuis assets/app_icon_source.png
python build_executable.py                      # dist/PlanBA_Metre_Extractor/ (onedir, windowed, icône)
```

Déploiement : copier **tout le dossier** `dist/PlanBA_Metre_Extractor/` (ex. `C:\PlanBA_App\PlanBA_Metre_Extractor\`), créer un raccourci vers `PlanBA_Metre_Extractor.exe` (icône `_internal\assets\app_icon.ico`). Les livrables sont écrits dans `output/` à côté de l'exécutable (`core/paths.py`). L'exécutable est `--windowed` : aucune console n'est ouverte.

Modules exclus du build pour la taille : scipy (repli NumPy intégré pour le graphe spatial), pandas, torch, sklearn. OCR embarqué : RapidOCR + onnxruntime.

---

## 11. Limites connues

- **Plans d'architecture au mobilier épais** (traits de lits/canapés aussi épais que les murs, images basse résolution) : la segmentation peut découper les pièces ; les cotes explicites `L x l` restent exactes et prioritaires, les régions douteuses sont signalées (confiance < 0,5).
- **Plans incohérents** (cotes hors-tout contradictoires) : détectés et signalés ; la largeur hors-tout saisie par l'ingénieur fait foi.
- **Tableaux de ferraillage sans VLM** : les aciers lus par OCR sont rattachés au repère le plus proche (rayon 392 px) ; un tableau à colonnes `P1 P2 P3` peut rattacher la même ligne à plusieurs repères → vérifier l'overlay ou activer Ollama.
- **OCR** : RapidOCR tourne sur CPU (~15 s par tuile de 2400 px) ; une page A3 à 300 DPI prend 1 à 2 minutes.
- Les prix unitaires du DQE et du second œuvre sont des valeurs **indicatives** à aligner sur le CPS du marché.
