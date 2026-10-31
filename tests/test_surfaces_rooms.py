# -*- coding: utf-8 -*-
"""tests/test_surfaces_rooms.py -- Mesure des pieces sur plan d'architecture.

Verrouille les regles ajoutees pour les plans residentiels (cotes en
pieds/pouces, libelles sur plusieurs lignes, pieces en L, portes ouvertes) :
  - fusion des libelles empiles et des numeros de piece isoles ;
  - boite ancree au libelle (rayons vers les murs) et detection des cotes
    ouverts (aucune surface inventee) ;
  - priorite region exclusive (piece en L) sur la boite ;
  - absence de fausse ligne 'ZONE OUVERTE' quand toutes les pieces sont
    mesurables, et anti double comptage.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from extractors.surface_extractor import (  # noqa: E402
    PlanSurfaceExtractor, parse_length)


def test_parse_length_imperial():
    assert parse_length("192'-0\"") == pytest.approx(58.52, abs=0.01)
    assert parse_length("75'-0\"") == pytest.approx(22.86, abs=0.01)
    assert parse_length("37'-6\"") == pytest.approx(11.43, abs=0.01)
    assert parse_length("16'") == pytest.approx(4.877, abs=0.01)


# ---------------------------------------------------------------------------
# Plan synthetique parametrable (100 px/m)
# ---------------------------------------------------------------------------
S = 100
OX, OY = 100, 100
THICK = 8


def _blank(w_m: float, h_m: float):
    img = np.full((int(h_m * S) + 2 * OY + 200, int(w_m * S) + 2 * OX + 200, 3),
                  255, np.uint8)
    cv2.rectangle(img, (OX, OY), (OX + int(w_m * S), OY + int(h_m * S)),
                  (0, 0, 0), THICK)
    return img


def _wall(img, x0, y0, x1, y1, thick=THICK):
    """Mur entre deux points en METRES."""
    cv2.line(img, (OX + int(x0 * S), OY + int(y0 * S)),
             (OX + int(x1 * S), OY + int(y1 * S)), (0, 0, 0), thick)


def _label(text, xm, ym, w=110, h=20):
    return {"text": text,
            "bbox": (OX + int(xm * S), OY + int(ym * S), w, h), "conf": 0.97}


def _cotes(w_m: float, h_m: float):
    """Cote hors-tout horizontale (au-dessus) et verticale (a gauche)."""
    cx = OX + int(w_m * S) // 2
    cy = OY + int(h_m * S) // 2
    return [
        {"text": f"{w_m:.2f}", "bbox": (cx - 35, OY - 70, 70, 20), "conf": 0.95},
        {"text": f"{h_m:.2f}", "bbox": (OX - 70, cy - 20, 20, 40), "conf": 0.95},
    ]


def _by_label(rep):
    return {r.label: r for r in rep.rooms}


# ---------------------------------------------------------------------------
# 1) Libelles empiles + numero de piece isole
# ---------------------------------------------------------------------------
def test_fusion_libelles_empiles_et_numero():
    ocr = [
        # boites qui se CHEVAUCHENT de 1 px (cas reel d'un OCR)
        {"text": "MASTER", "bbox": (200, 200, 90, 22), "conf": 0.9},
        {"text": "BEDROOM", "bbox": (195, 221, 110, 22), "conf": 0.95},
        {"text": "2", "bbox": (240, 246, 18, 20), "conf": 0.9},
        # libelle sur deux lignes, mais tres eloigne : ne doit pas fusionner
        {"text": "KITCHEN", "bbox": (600, 600, 100, 22), "conf": 0.9},
    ]
    labels = PlanSurfaceExtractor.room_labels(ocr, None, (900, 1200))
    textes = [lb["text"] for lb in labels]
    assert "MASTER BEDROOM 2" in textes
    assert "KITCHEN" in textes
    assert len(labels) == 2


# ---------------------------------------------------------------------------
# 2) Boite ancree : piece fermee mesuree, piece ouverte signalee
# ---------------------------------------------------------------------------
def test_room_boxes_piece_fermee_et_ouverte():
    img = _blank(10, 6)
    _wall(img, 5, 0, 5, 6)             # cloison verticale au milieu
    _wall(img, 7, 4, 10, 4)            # cloison partielle (piece ouverte)
    ext = PlanSurfaceExtractor()
    mask = ext.wall_mask(img)
    bbox = ext.building_bbox(mask)
    labels = [{"text": "SALON", "bbox": (OX + 250, OY + 250, 100, 22),
               "conf": 0.9},
              {"text": "SEJOUR", "bbox": (OX + 550, OY + 350, 100, 22),
               "conf": 0.9}]
    boxes = {b["label_index"]: b for b in ext.room_boxes(mask, labels, bbox)}

    gauche = boxes[0]
    assert gauche["box"] is not None
    assert gauche["open_sides"] == []
    # Piece fermee ~5 x 6 m, nette de l'epaisseur des murs
    bx, by, bw, bh = gauche["box"]
    assert (bw / S) * (bh / S) == pytest.approx(5.0 * 6.0, rel=0.10)

    droite = boxes[1]
    # Cote droit de l'image : le mur d'enceinte est proche, mais le rayon
    # horizontal doit traverser la cloison partielle -> boite non exclusif.
    assert droite["dist"]["right"] is not None


def test_room_boxes_sans_mur_declare_cote_ouvert():
    # Aucun mur du tout : ni enveloppe ni cloison
    img = np.full((500, 800, 3), 255, np.uint8)
    ext = PlanSurfaceExtractor()
    mask = ext.wall_mask(img)
    assert ext.building_bbox(mask) is None
    labels = [{"text": "TERRASSE", "bbox": (300, 300, 100, 22), "conf": 0.9}]
    box = ext.room_boxes(mask, labels, None)[0]
    # Aucun rayon ne rencontre de mur : boite None, aucune surface inventee
    assert box["box"] is None
    assert box["open_sides"]


# ---------------------------------------------------------------------------
# 3) Piece en L : la region exacte prime sur la boite
# ---------------------------------------------------------------------------
def test_piece_en_l_utilise_la_region():
    img = _blank(10, 8)
    _wall(img, 6, 0, 6, 8)             # separation SALON / CHAMBRE
    _wall(img, 4, 3, 6, 3)             # decroche
    _wall(img, 4, 0, 4, 3)             # decroche
    ocr = [_label("SALON", 2.0, 5.0), _label("CHAMBRE", 7.5, 3.0)] + _cotes(10, 8)
    rep = PlanSurfaceExtractor().extract_from_image(img, ocr_items=ocr)

    assert rep.scale_px_per_m == pytest.approx(S, rel=0.05)
    sal = _by_label(rep)["SALON"]
    # Surface nette : 6x8 - 2x3 = 42 m2, murs deduits
    assert sal.source == "segmentation"
    assert sal.area_m2 == pytest.approx(42.0, rel=0.10)
    # La boite ancree aurait donne ~48 m2 : on verifie qu'on ne l'a pas prise
    assert sal.area_m2 < 45.0


# ---------------------------------------------------------------------------
# 4) Porte ouverte entre deux pieces : pas de fausse ZONE OUVERTE
# ---------------------------------------------------------------------------
def test_porte_ouverte_mesure_chaque_piece():
    img = _blank(10, 6)
    # Cloison avec ouverture de 1 m au milieu
    _wall(img, 5, 0, 5, 2.5)
    _wall(img, 5, 3.5, 5, 6)
    ocr = ([_label("SALON", 2.5, 3.0), _label("CUISINE", 7.5, 3.0, w=120)]
           + _cotes(10, 6))
    rep = PlanSurfaceExtractor().extract_from_image(img, ocr_items=ocr)

    rooms = _by_label(rep)
    assert set(rooms) == {"SALON", "CUISINE"}
    for r in rooms.values():
        assert r.area_m2 is not None
        assert r.area_m2 == pytest.approx(30.0, rel=0.15)
    assert not any(r.label.startswith("ZONE OUVERTE") for r in rep.rooms)
    assert rep.total_area_m2 == pytest.approx(60.0, rel=0.15)


# ---------------------------------------------------------------------------
# 5) Aucune surface inventee quand rien n'est mesurable
# ---------------------------------------------------------------------------
def test_piece_sans_cote_ni_echelle_non_inventee():
    img = _blank(10, 6)                 # murs, mais AUCUNE cote
    ocr = [_label("SALON", 2.5, 3.0)]
    rep = PlanSurfaceExtractor().extract_from_image(img, ocr_items=ocr)
    assert rep.scale_px_per_m is None
    sal = _by_label(rep)["SALON"]
    assert sal.area_m2 is None
    assert sal.source in ("aucune", "zone_ouverte")
    assert any("NON TROUVEE" in w or "non fermee" in w for w in sal.warnings)


# ---------------------------------------------------------------------------
# 6) Calibration AXE PAR AXE : une cote de chaine isolee sur un axe ne doit
#    jamais etre prise pour un hors-tout (plan basse resolution : erreur x3)
# ---------------------------------------------------------------------------
def test_echelle_par_axe_cote_de_chaine_isolee_non_prise_pour_hors_tout():
    img = _blank(10, 6)
    _wall(img, 5, 0, 5, 6)
    cx = OX + int(10 * S) // 2
    cy = OY + int(6 * S) // 2
    ocr = [
        _label("SALON", 2.5, 3.0), _label("CUISINE", 7.5, 3.0, w=120),
        # hors-tout horizontal correct
        {"text": "10.00", "bbox": (cx - 35, OY - 70, 70, 20), "conf": 0.95},
        # cote de CHAINE sur l'axe Y : 2 m pour 6 m dessines
        {"text": "2.00", "bbox": (OX - 70, cy - 20, 20, 40), "conf": 0.95},
    ]
    rep = PlanSurfaceExtractor().extract_from_image(img, ocr_items=ocr)

    # L'echelle est celle de l'axe X (100 px/m), pas 600/2 = 300 px/m
    assert rep.scale_px_per_m == pytest.approx(100.0, rel=0.05)
    assert rep.scale_confidence <= 0.7
    assert any("incoh" in w for w in rep.warnings)
    for r in _by_label(rep).values():
        assert r.area_m2 == pytest.approx(30.0, rel=0.10)


# ---------------------------------------------------------------------------
# 7) Une seule cote hors-tout : echelle acceptee mais signalee
# ---------------------------------------------------------------------------
def test_echelle_un_seul_axe_signalee():
    img = _blank(10, 6)
    _wall(img, 5, 0, 5, 6)
    cx = OX + int(10 * S) // 2
    ocr = [_label("SALON", 2.5, 3.0), _label("CUISINE", 7.5, 3.0, w=120),
           {"text": "10.00", "bbox": (cx - 35, OY - 70, 70, 20), "conf": 0.95}]
    rep = PlanSurfaceExtractor().extract_from_image(img, ocr_items=ocr)
    assert rep.scale_px_per_m == pytest.approx(100.0, rel=0.05)
    assert any("un seul axe" in w for w in rep.warnings)


# ---------------------------------------------------------------------------
# 8) Scan inverse (tirage bleu / negatif) : image redressee avant analyse
# ---------------------------------------------------------------------------
def test_normalize_polarity_detecte_le_negatif():
    clair = np.full((40, 40, 3), 240, np.uint8)
    out, inv = PlanSurfaceExtractor._normalize_polarity(clair)
    assert inv is False and out is clair

    sombre = np.full((40, 40, 3), 25, np.uint8)
    out, inv = PlanSurfaceExtractor._normalize_polarity(sombre)
    assert inv is True
    assert float(out.mean()) == pytest.approx(230, abs=2)


def test_scan_inverse_mesure_les_pieces():
    img = _blank(10, 6)
    _wall(img, 5, 0, 5, 6)
    ocr = [_label("SALON", 2.5, 3.0), _label("CUISINE", 7.5, 3.0, w=120)]
    ocr += _cotes(10, 6)

    normal = PlanSurfaceExtractor().extract_from_image(img, ocr_items=ocr)
    negatif = PlanSurfaceExtractor().extract_from_image(
        cv2.bitwise_not(img), ocr_items=ocr)

    assert any("invers" in w for w in negatif.warnings)
    assert negatif.scale_px_per_m == pytest.approx(normal.scale_px_per_m,
                                                   rel=0.05)
    ref = {r.label: r.area_m2 for r in normal.rooms if r.area_m2}
    got = {r.label: r.area_m2 for r in negatif.rooms if r.area_m2}
    assert set(got) == set(ref)
    for lbl, aire in ref.items():
        assert got[lbl] == pytest.approx(aire, rel=0.05)


# ---------------------------------------------------------------------------
# 9) Bruit de scan (sel et poivre) : debruite, echelle juste, pieces mesurees
# ---------------------------------------------------------------------------
def test_bruit_sel_poivre_debruite_et_pieces_mesurees():
    img = _blank(10, 6)
    _wall(img, 5, 0, 5, 6)
    rng = np.random.default_rng(1)
    bruite = img.copy()
    n = int(0.01 * img.shape[0] * img.shape[1])
    bruite[rng.integers(0, img.shape[0], n),
           rng.integers(0, img.shape[1], n)] = 0

    ocr = [_label("SALON", 2.5, 3.0), _label("CUISINE", 7.5, 3.0, w=120)]
    ocr += _cotes(10, 6)
    rep = PlanSurfaceExtractor().extract_from_image(bruite, ocr_items=ocr)

    assert any("speckle" in w.lower() for w in rep.warnings)
    assert rep.scale_px_per_m == pytest.approx(100.0, rel=0.05)
    for r in _by_label(rep).values():
        assert r.area_m2 == pytest.approx(30.0, rel=0.10)


# ---------------------------------------------------------------------------
# 10) Murs en DOUBLE TRAIT (deux nus paralleles, interieur blanc)
#     Un plan vectoriel dessine souvent les murs ainsi, et non en aplat : sans
#     reconstruction des bandes, la piece n'est pas fermee et n'a pas de surface.
# ---------------------------------------------------------------------------
def _blank_double(w_m: float, h_m: float, gap: int = 5, thick: int = 3):
    img = np.full((int(h_m * S) + 2 * OY + 200,
                   int(w_m * S) + 2 * OX + 200, 3), 255, np.uint8)
    for d in (0, gap):
        cv2.rectangle(img, (OX + d, OY + d),
                      (OX + int(w_m * S) - d, OY + int(h_m * S) - d),
                      (0, 0, 0), thick)
    return img


def _wall_double(img, x0, y0, x1, y1, gap=5, thick=3):
    """Cloison en double trait : deux nus paralleles separes de ``gap`` px."""
    for d in (0, gap):
        dx = d if x0 == x1 else 0
        dy = d if y0 == y1 else 0
        cv2.line(img, (OX + int(x0 * S) + dx, OY + int(y0 * S) + dy),
                 (OX + int(x1 * S) + dx, OY + int(y1 * S) + dy),
                 (0, 0, 0), thick)


def test_murs_double_trait_mesures():
    img = _blank_double(10, 6)
    _wall_double(img, 5, 0, 5, 6)
    ocr = [_label("SALON", 2.5, 3.0), _label("CUISINE", 7.5, 3.0, w=120)]
    ocr += _cotes(10, 6)
    rep = PlanSurfaceExtractor().extract_from_image(img, ocr_items=ocr)

    assert rep.scale_px_per_m == pytest.approx(100.0, rel=0.05)
    rooms = _by_label(rep)
    assert set(rooms) == {"SALON", "CUISINE"}, (
        "murs en double trait non refermes : %s" % sorted(rooms))
    for r in rooms.values():
        assert r.area_m2 == pytest.approx(30.0, rel=0.15)


# ---------------------------------------------------------------------------
# 11) Murs POINTILLES (tirets/points) : reconstruction des chaines de points
# ---------------------------------------------------------------------------
def _dotted_line(img, x0, y0, x1, y1, step=12, r=2):
    n = max(2, int(np.hypot(x1 - x0, y1 - y0) / step) + 1)
    for t in np.linspace(0.0, 1.0, n):
        cv2.circle(img, (int(round(x0 + t * (x1 - x0))),
                         int(round(y0 + t * (y1 - y0)))), r, (0, 0, 0), -1)


def test_murs_pointilles_reconstruits():
    img = np.full((int(6 * S) + 2 * OY + 200, int(10 * S) + 2 * OX + 200, 3),
                  255, np.uint8)
    x0, y0 = OX, OY
    x1, y1 = OX + int(10 * S), OY + int(6 * S)
    for a, b in (((x0, y0), (x1, y0)), ((x0, y1), (x1, y1)),
                 ((x0, y0), (x0, y1)), ((x1, y0), (x1, y1)),
                 ((OX + 5 * S, y0), (OX + 5 * S, y1))):
        _dotted_line(img, a[0], a[1], b[0], b[1])

    ocr = [_label("SALON", 2.5, 3.0), _label("CUISINE", 7.5, 3.0, w=120)]
    ocr += _cotes(10, 6)
    rep = PlanSurfaceExtractor().extract_from_image(img, ocr_items=ocr)

    assert rep.scale_px_per_m == pytest.approx(100.0, rel=0.05)
    rooms = _by_label(rep)
    assert set(rooms) == {"SALON", "CUISINE"}, (
        "murs pointilles non reconstruits : %s" % sorted(rooms))
    for r in rooms.values():
        assert r.area_m2 == pytest.approx(30.0, rel=0.18)


# ---------------------------------------------------------------------------
# 12) Hachures obliques et mobilier fin : ni mur, ni piece
#     Une hachure a 45 degres a une boite englobante quasi carree ; un mur est
#     allonge dans un seul axe. C'est ce tri geometrique qui les separe.
# ---------------------------------------------------------------------------
def test_hachures_obliques_et_mobilier_fin_ne_sont_pas_des_murs():
    img = _blank(10, 6)
    _wall(img, 5, 0, 5, 6)
    # Hachures obliques a 45 degres dans SALON (comme un sol carrele)
    for k in range(0, 500, 12):
        cv2.line(img, (OX + 40 + k, OY + 40), (OX + 40, OY + 40 + k), (0, 0, 0), 1)
    # Mobilier fin : rectangle 1 px (lit, placard) dans CUISINE
    cv2.rectangle(img, (OX + 620, OY + 300), (OX + 850, OY + 480),
                  (0, 0, 0), 1)

    ocr = [_label("SALON", 2.5, 3.0), _label("CUISINE", 7.5, 3.0, w=120)]
    ocr += _cotes(10, 6)
    rep = PlanSurfaceExtractor().extract_from_image(img, ocr_items=ocr)

    rooms = _by_label(rep)
    assert set(rooms) == {"SALON", "CUISINE"}, (
        "hachures/mobilier pris pour des murs : %s" % sorted(rooms))
    for r in rooms.values():
        assert r.area_m2 == pytest.approx(30.0, rel=0.15)


# ---------------------------------------------------------------------------
# 13) Piece en L ADJACENTE a un rectangle : pas de double comptage
#     La boite englobante du L recouvre la piece voisine ; l'anti-double-
#     comptage par boite effacait la voisine (CUISINE -> 0 m2). Les regions
#     segmentees etant disjointes, chaque piece doit etre mesuree pleinement.
# ---------------------------------------------------------------------------
def test_piece_en_l_adjacente_pas_de_double_compte():
    img = _blank(6, 6)                   # enceinte 6 x 6 m
    _wall(img, 4, 4, 4, 6)               # cloison verticale (bas droite)
    _wall(img, 4, 4, 6, 4)               # cloison horizontale (bas droite)
    ocr = ([_label("SALON", 2.0, 2.5), _label("CUISINE", 5.0, 5.0, w=120)]
           + _cotes(6, 6))
    rep = PlanSurfaceExtractor().extract_from_image(img, ocr_items=ocr)

    rooms = _by_label(rep)
    assert set(rooms) == {"SALON", "CUISINE"}
    # SALON en L = 6x6 - 2x2 = 32 m2 ; CUISINE = 2x2 = 4 m2 (net, murs deduits)
    assert rooms["SALON"].area_m2 == pytest.approx(32.0, rel=0.12)
    assert rooms["CUISINE"].area_m2 == pytest.approx(4.0, rel=0.30)
    assert rooms["CUISINE"].area_m2 > 0.5, "CUISINE effacee par le double comptage"
