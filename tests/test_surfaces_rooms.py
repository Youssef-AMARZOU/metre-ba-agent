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
