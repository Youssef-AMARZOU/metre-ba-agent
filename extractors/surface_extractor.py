#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extractors/surface_extractor.py -- Metre des surfaces sur plans d'architecture.

Pour un plan de distribution (pieces, cotes), ce module calcule, 100 % en
local (OpenCV + OCR local) :
  - la surface de chaque piece (m2) : cote explicite 'L x l' si presente,
    sinon segmentation geometrique de la piece calibree par l'echelle ;
  - le perimetre de chaque piece (plinthes, enduits, peinture) ;
  - les totaux par etage et le total general ;
  - une calibration d'echelle (px / m) deduite des chaines de cotes, avec
    un indice de confiance et des alertes si les cotes sont incoherentes.

Unites acceptees : metres (3.50), centimetres (350), millimetres (3500),
pieds/pouces (16'-0", 13-6", 12.3', 18'x10"). Sortie TOUJOURS en metres.

Regle : aucune surface n'est inventee. Sans echelle fiable ni cote
explicite, la piece est listee avec surface vide et une alerte.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

FT_TO_M = 0.3048
IN_TO_M = 0.0254

# Lexique FR / EN / conventions Maroc (les mots inconnus en majuscules sont
# aussi acceptes s'ils sont situes a l'interieur du batiment)
ROOM_LEXICON = (
    "chambre", "ch", "salon", "sejour", "séjour", "cuisine", "sdb",
    "salle", "bain", "douche", "wc", "toilette", "hall", "entree", "entrée",
    "couloir", "degagement", "dégagement", "bureau", "terrasse", "balcon",
    "garage", "buanderie", "patio", "cellier", "placard", "dressing",
    "rangement", "cage", "escalier", "local", "loggia", "veranda", "veranda",
    "bibliotheque", "atelier", "cave", "jardin", "cour", "porche",
    "bedroom", "kitchen", "living", "dining", "bath", "bathroom", "closet",
    "foyer", "laundry", "office", "room", "master", "walk-in", "pantry",
    "garage", "porch", "storage", "study", "den", "nook", "mudroom",
    "powder", "utility", "lobby", "corridor",
)
EXCLUDED_WORDS = (
    "scale", "echelle", "échelle", "plan", "floorplan", "residential",
    "niveau", "etage", "étage", "rdc", "coupe", "facade", "façade", "nord",
    "north", "detail", "détail", "legende", "légende", "date", "dessin",
    "projet", "client", "architecte", "ingenieur", "ingénieur", "titre",
    "notes", "note", "sheet", "drawn", "checked",
)
LEVEL_RX = re.compile(
    r"\b(RDC|REZ[-\s]?DE[-\s]?CHAUSS[ÉE]E|SOUS[-\s]?SOL|MEZZANINE|"
    r"R\s*\+\s*\d+|[ÉE]TAGE\s*\d*|NIVEAU\s*\w+|\d+(?:ER|EME|ÈME)\s*[ÉE]TAGE|"
    r"(?:GROUND|FIRST|SECOND|THIRD|\d+(?:ST|ND|RD|TH))\s+FLOOR|"
    r"LEVEL\s*\d+|PLAN\s+[A-Z0-9]{1,3})\b", re.IGNORECASE)

# --- Longueurs ------------------------------------------------------------
_NUM = r"(\d+(?:[.,]\d+)?)"
RX_FT_IN = re.compile(
    rf"^{_NUM}\s*['′’]\s*[-–]?\s*(?:(\d{{1,2}}(?:[.,]\d+)?)\s*[\"″”]?)?$")
RX_FT_DASH = re.compile(r"^(\d+)\s*[-–]\s*(\d{1,2})\s*[\"″”]?$")
RX_IN_ONLY = re.compile(rf"^{_NUM}\s*[\"″”]$")
RX_METRIC = re.compile(rf"^{_NUM}\s*(m|cm|mm)?\.?$", re.IGNORECASE)
RX_DIM_PAIR = re.compile(r"^(.+?)\s*[x×X*]\s*(.+?)$")
RX_HAS_UNIT = re.compile(r"['′’\"″”]|\bm\b|\bcm\b|\bmm\b|\d-\d", re.IGNORECASE)


def parse_length(text: str, room_context: bool = False) -> Optional[float]:
    """Texte de cote -> metres. `room_context` : dans 'L x l', un nombre
    suivi de \" seul (OCR ' -> \") et >= 3 est lu en pieds."""
    if text is None:
        return None
    t = str(text).strip().replace("\u2032", "'").replace("\u2033", '"')
    t = t.replace("’", "'").replace("”", '"').strip("()[] ")
    t = t.lstrip(".")
    if not t:
        return None
    m = RX_FT_IN.match(t)
    if m:
        ft = float(m.group(1).replace(",", "."))
        inch = float(m.group(2).replace(",", ".")) if m.group(2) else 0.0
        return round(ft * FT_TO_M + inch * IN_TO_M, 3)
    m = RX_FT_DASH.match(t)
    if m:
        return round(int(m.group(1)) * FT_TO_M + int(m.group(2)) * IN_TO_M, 3)
    m = RX_IN_ONLY.match(t)
    if m:
        v = float(m.group(1).replace(",", "."))
        if room_context and v >= 3:
            return round(v * FT_TO_M, 3)
        return round(v * IN_TO_M, 3)
    m = RX_METRIC.match(t)
    if m:
        v = float(m.group(1).replace(",", "."))
        unit = (m.group(2) or "").lower()
        if unit == "mm":
            return round(v / 1000.0, 3)
        if unit == "cm":
            return round(v / 100.0, 3)
        if unit == "m":
            return round(v, 3)
        # Sans unite : heuristique BTP (plans marocains cotes en cm)
        if v >= 1000:
            return round(v / 1000.0, 3)
        if v >= 30:
            return round(v / 100.0, 3)
        return round(v, 3)
    return None


def parse_room_dims(text: str) -> Optional[Tuple[float, float]]:
    """'18\\'x10\"', '4.20x3.50', '420 x 350' -> (L, l) en metres."""
    if not text:
        return None
    t = str(text).strip()
    m = RX_DIM_PAIR.match(t)
    if not m:
        return None
    a = parse_length(m.group(1), room_context=True)
    b = parse_length(m.group(2), room_context=True)
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    if a > 40 or b > 40 or a < 0.5 or b < 0.5:
        return None
    return (a, b)


def is_dimension_token(text: str) -> bool:
    t = str(text or "").strip()
    if not t or RX_DIM_PAIR.match(t):
        return False
    if parse_length(t) is None:
        return False
    # Avec unite, ou entier 3-4 chiffres (cm/mm), ou decimal 'x.xx' (m)
    return (bool(RX_HAS_UNIT.search(t)) or bool(re.match(r"^\d{3,4}$", t))
            or bool(re.match(r"^\d{1,2}[.,]\d{1,2}$", t)))


# --- Structures -------------------------------------------------------------

def _box_intersection(a: Tuple[int, int, int, int],
                      b: Tuple[int, int, int, int]) -> int:
    """Aire (px) de l'intersection de deux boites (x, y, w, h)."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    return max(0, w) * max(0, h)


@dataclass
class RoomSurface:
    label: str
    level: str = "RDC"
    width_m: Optional[float] = None
    length_m: Optional[float] = None
    area_explicit_m2: Optional[float] = None
    area_segmented_m2: Optional[float] = None
    perimeter_m: Optional[float] = None
    source: str = ""              # 'cote_explicite' | 'segmentation' | 'aucune'
    confidence: float = 0.0
    bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)
    region_bbox: Optional[Tuple[int, int, int, int]] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def area_m2(self) -> Optional[float]:
        if self.area_explicit_m2:
            return self.area_explicit_m2
        return self.area_segmented_m2

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["area_m2"] = self.area_m2
        return d


@dataclass
class SurfaceReport:
    level: str = "RDC"
    page_num: int = 1
    rooms: List[RoomSurface] = field(default_factory=list)
    scale_px_per_m: Optional[float] = None
    scale_confidence: float = 0.0
    scale_estimates: List[Dict[str, Any]] = field(default_factory=list)
    building_bbox: Optional[Tuple[int, int, int, int]] = None
    warnings: List[str] = field(default_factory=list)
    n_ocr_tokens: int = 0
    ocr_engine: Optional[str] = None
    overlay_path: Optional[str] = None
    image_shape: Tuple[int, int] = (0, 0)

    @property
    def total_area_m2(self) -> float:
        return round(sum(r.area_m2 or 0.0 for r in self.rooms), 2)

    @property
    def total_perimeter_m(self) -> float:
        return round(sum(r.perimeter_m or 0.0 for r in self.rooms), 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level, "page_num": self.page_num,
            "rooms": [r.to_dict() for r in self.rooms],
            "scale_px_per_m": self.scale_px_per_m,
            "scale_confidence": self.scale_confidence,
            "scale_estimates": self.scale_estimates,
            "building_bbox": self.building_bbox,
            "warnings": list(self.warnings),
            "total_area_m2": self.total_area_m2,
            "total_perimeter_m": self.total_perimeter_m,
            "n_ocr_tokens": self.n_ocr_tokens, "ocr_engine": self.ocr_engine,
            "overlay_path": self.overlay_path,
        }


# --- Extracteur -------------------------------------------------------------

class PlanSurfaceExtractor:
    """Plan d'architecture (image / page PDF) -> SurfaceReport."""

    def __init__(self, ocr_router: Optional[Any] = None, dpi: int = 200,
                 max_side_px: int = 3000):
        self._ocr = ocr_router
        self.dpi = dpi
        self.max_side_px = max_side_px

    @property
    def ocr(self):
        if self._ocr is None:
            from extractors.ocr_router import LocalOCRRouter
            self._ocr = LocalOCRRouter()
        return self._ocr

    # ------------------------------------------------------------------
    # Entrees
    # ------------------------------------------------------------------
    def extract_image(self, image_path: str, level: Optional[str] = None,
                      page_num: int = 1, overlay_dir: Optional[str] = None,
                      known_width_m: Optional[float] = None,
                      known_height_m: Optional[float] = None) -> SurfaceReport:
        import cv2
        import numpy as np
        data = np.fromfile(str(image_path), dtype=np.uint8)  # chemins accentues
        img = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
        if img is None:
            rep = SurfaceReport(page_num=page_num)
            rep.warnings.append(f"Image illisible : {image_path}")
            return rep
        return self.extract_from_image(img, level=level, page_num=page_num,
                                       overlay_dir=overlay_dir,
                                       known_width_m=known_width_m,
                                       known_height_m=known_height_m)

    def extract_pdf(self, pdf_path: str, overlay_dir: Optional[str] = None,
                    progress_callback=None) -> List[SurfaceReport]:
        import fitz
        import numpy as np
        import cv2
        reports: List[SurfaceReport] = []
        with fitz.open(str(pdf_path)) as doc:
            for idx in range(len(doc)):
                page = doc[idx]
                zoom = self.dpi / 72.0
                if max(page.rect.width, page.rect.height) * zoom > self.max_side_px:
                    zoom = self.max_side_px / max(page.rect.width, page.rect.height)
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False,
                                      colorspace=fitz.csRGB)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n)[:, :, :3]
                img = cv2.cvtColor(np.ascontiguousarray(img), cv2.COLOR_RGB2BGR)
                # Mots vectoriels natifs -> tokens OCR (plus fiables que l'OCR)
                native = []
                for x0, y0, x1, y1, w, *_ in page.get_text("words"):
                    native.append({"text": w, "conf": 1.0,
                                   "bbox": (int(x0 * zoom), int(y0 * zoom),
                                            int((x1 - x0) * zoom), int((y1 - y0) * zoom))})
                if progress_callback:
                    progress_callback(idx + 1, len(doc), "surfaces")
                rep = self.extract_from_image(
                    img, page_num=idx + 1, overlay_dir=overlay_dir,
                    ocr_items=native if len(native) >= 8 else None)
                if rep.rooms:
                    reports.append(rep)
        return reports

    # ------------------------------------------------------------------
    # Pipeline image
    # ------------------------------------------------------------------
    def extract_from_image(self, img_bgr, level: Optional[str] = None,
                           page_num: int = 1, overlay_dir: Optional[str] = None,
                           ocr_items: Optional[List[Dict[str, Any]]] = None,
                           known_width_m: Optional[float] = None,
                           known_height_m: Optional[float] = None
                           ) -> SurfaceReport:
        """`known_width_m` / `known_height_m` : largeur / hauteur hors-tout
        reelle du batiment saisie par l'ingenieur -> calibration manuelle
        prioritaire sur les cotes lues (plans incoherents ou sans cotes)."""
        import cv2
        H, W = img_bgr.shape[:2]
        if max(H, W) > self.max_side_px:
            f = self.max_side_px / max(H, W)
            img_bgr = cv2.resize(img_bgr, (int(W * f), int(H * f)),
                                 interpolation=cv2.INTER_AREA)
            H, W = img_bgr.shape[:2]
        rep = SurfaceReport(page_num=page_num, image_shape=(H, W))

        # Scan inversé (tirage bleu, fond sombre) : les murs sont CLAIRS sur
        # fond foncé. On inverse l'image, sinon le seuillage detecte le papier
        # comme « mur » et plus aucune piece n'est mesuree.
        img_bgr, inverted = self._normalize_polarity(img_bgr)
        if inverted:
            rep.warnings.append(
                "Scan inversé (fond sombre / tirage bleu) : image inversée "
                "avant analyse.")

        # Poussiere / bruit de scan (speckle) : isole les cotes et les libelles
        # de leurs voisins, l'OCR ne lit alors plus « 192'-0" » et une cote de
        # chaine prend sa place (erreur d'echelle x3 constatee).
        img_bgr, speckle = self._denoise_speckle(img_bgr)
        if speckle > 0.02:
            rep.warnings.append(
                f"Bruit de scan (speckle {speckle*100:.1f} % de pixels isolés) : "
                "image débruitée avant analyse.")

        from_engine = ocr_items is None
        if ocr_items is None:
            ocr_items = self.ocr.recognize(img_bgr) if self.ocr.available else []
            rep.ocr_engine = self.ocr.engine_name
        else:
            rep.ocr_engine = "pdf_text"
        rep.n_ocr_tokens = len(ocr_items)
        if not ocr_items:
            rep.warnings.append("Aucun texte lu (OCR indisponible ou image vide).")
            return rep
        # Un moteur OCR renvoie parfois DEUX fois le meme jeton (meme boite)
        # sur un plan dense : la piece est alors mesuree deux fois et coupee
        # en deux (SALON en L : 36,5 m2 -> 21,7 + 13,3). Dedoublonnage
        # geometrique, sans aucun lexique de noms.
        dedup = self._merge_ocr_items([], ocr_items, same_text_only=True)
        if len(dedup) < len(ocr_items):
            rep.warnings.append(
                f"Jetons OCR en double : {len(ocr_items) - len(dedup)} "
                "doublon(s) fusionné(s) avant mesure.")
            ocr_items = dedup
            rep.n_ocr_tokens = len(ocr_items)

        rep.level = level or self.detect_level(ocr_items) or "RDC"
        texte_boxes = [it["bbox"] for it in ocr_items if it.get("bbox")]
        wall_mask = self.wall_mask(img_bgr, text_boxes=texte_boxes)
        rep.building_bbox = self.building_bbox(wall_mask)

        def _analyse(items, rebuild_mask=False):
            """Libelles, cotes, echelle et regions pour un jeu de tokens."""
            nonlocal wall_mask
            if rebuild_mask:
                wall_mask = self.wall_mask(
                    img_bgr,
                    text_boxes=[it["bbox"] for it in items if it.get("bbox")])
                rep.building_bbox = self.building_bbox(wall_mask)
            lbs = self.room_labels(items, rep.building_bbox, (H, W))
            dims = [it for it in items if is_dimension_token(it["text"])]
            pairs = [(it, parse_room_dims(it["text"])) for it in items]
            pairs = [(it, d) for it, d in pairs if d]
            sc, cf, est, wr = self.calibrate_scale(
                dims, rep.building_bbox, (H, W))
            reg = self.segment_rooms(wall_mask, rep.building_bbox,
                                     scale_px_per_m=sc)
            return lbs, dims, pairs, sc, cf, est, wr, reg

        (labels, dim_tokens, pair_tokens, scale, conf, estimates, warns,
         regions) = _analyse(ocr_items)

        # --- OCR renforce : un plan hachure/pointille peut faire perdre des
        # libelles a l'OCR (DINING lu « DINHNG », BATH 2 lu « BATHE »). Le
        # signal d'alerte est geometrique : il y a plus de PIECES FERMEES que
        # de libelles lus. On relit alors une image Debarrassee des traits
        # longs (murs), ce qui coute un second passage (~8 s) — declenche
        # uniquement dans ce cas.
        if from_engine and regions and len(labels) < len(regions):
            extra = self._ocr_text_pass(img_bgr)
            merged = self._merge_ocr_items(ocr_items, extra)
            if [t.get("text") for t in merged] != [t.get("text")
                                                   for t in ocr_items]:
                alt = _analyse(merged, rebuild_mask=True)
                if len(alt[0]) >= len(labels):
                    avant = len(labels)
                    ocr_items = merged
                    rep.n_ocr_tokens = len(ocr_items)
                    (labels, dim_tokens, pair_tokens, scale, conf, estimates,
                     warns, regions) = alt
                    rep.warnings.append(
                        f"OCR renforcé (traits retirés) : {len(labels) - avant} "
                        f"libellé(s) récupéré(s) sur {len(regions)} pièce(s) "
                        f"fermée(s).")
                else:
                    rep.warnings.append(
                        "OCR renforcé tenté (traits retirés) : lecture moins "
                        "complète, première lecture conservée.")

        if rep.building_bbox and (known_width_m or known_height_m):
            bx, by, bw, bh = rep.building_bbox
            manual = []
            if known_width_m:
                manual.append(bw / float(known_width_m))
            if known_height_m:
                manual.append(bh / float(known_height_m))
            scale = round(sum(manual) / len(manual), 3)
            conf = 0.95
            estimates.insert(0, {"method": "manuel_hors_tout", "px_per_m": scale,
                                 "a": f"{known_width_m or ''} x {known_height_m or ''} m",
                                 "b": f"{bw}x{bh}px"})
            warns = [w for w in warns if not w.startswith("[ALERTE ECHELLE]")]
            warns.append("Echelle calibrée manuellement (largeur/hauteur hors-tout saisie).")
        rep.scale_px_per_m, rep.scale_confidence = scale, conf
        rep.scale_estimates = estimates
        rep.warnings.extend(warns)

        regions = self.segment_rooms(wall_mask, rep.building_bbox,
                                     scale_px_per_m=scale)
        rep.rooms = self.build_rooms(labels, pair_tokens, regions, wall_mask,
                                     scale, conf, rep.level, (H, W),
                                     bbox=rep.building_bbox)
        if not rep.rooms:
            rep.warnings.append("Aucune piece identifiee (libelles absents).")
        if overlay_dir:
            rep.overlay_path = self.render_overlay(
                img_bgr, rep, Path(overlay_dir) / f"surfaces_page_{page_num}.png")
        return rep

    # ------------------------------------------------------------------
    @staticmethod
    def detect_level(ocr_items: Sequence[Dict[str, Any]]) -> Optional[str]:
        for it in ocr_items:
            m = LEVEL_RX.search(str(it["text"]))
            if m:
                return m.group(1).upper().replace("  ", " ")
        return None

    def _ocr_text_pass(self, img_bgr) -> List[Dict[str, Any]]:
        """Second passage OCR sur une image privee des TRAITS LONGS.

        Sur un plan hachure ou dense en graphics, l'OCR perd ou deforme des
        libelles (`DINING` -> `DINHNG`, `BATH 2` -> `BATHE`). En retirant les
        murs (traits longs H/V dilates) et en ne gardant que le texte, la
        lecture redevient fiable. Cout : un second passage OCR.
        """
        import cv2
        import numpy as np
        if not self.ocr.available:
            return []
        ink = self._binarize_ink(img_bgr)
        H, W = ink.shape[:2]
        u = max(H, W) / 1600.0
        L = max(12, int(round(40 * u)))
        nus = cv2.bitwise_or(
            cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1))),
            cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (1, L))))
        efface = cv2.dilate(nus, np.ones((5, 5), np.uint8))
        texte = cv2.bitwise_and(ink, cv2.bitwise_not(efface))
        # Tesseract attend du texte NOIR sur fond BLANC.
        texte = cv2.cvtColor(255 - texte, cv2.COLOR_GRAY2BGR)
        return self.ocr.recognize(texte)

    @staticmethod
    def _merge_ocr_items(base: Sequence[Dict[str, Any]],
                         extra: Sequence[Dict[str, Any]],
                         same_text_only: bool = False
                         ) -> List[Dict[str, Any]]:
        """Fusionne deux lectures OCR sans doublon.

        Deux tokens qui se recouvrent fortement (meme zone) designent le meme
        texte : on garde celui de meilleure confiance. Les autres sont ajoutes.

        ``same_text_only`` : ne fusionne que des tokens de MEME texte. Deux
        lectures DIFFERENTES au meme endroit (ex. deux cotes hors-tout
        concurrentes `8.00` / `9.50`) ne sont alors PAS fusionnees : elles
        doivent rester visibles pour declencher l'alerte de contradiction.
        """
        out = [dict(it) for it in base]
        for it in extra:
            box = tuple(int(v) for v in it.get("bbox", (0, 0, 0, 0)))
            aire = max(1, box[2] * box[3])
            connu = False
            for i, cur in enumerate(out):
                cbox = tuple(int(v) for v in cur.get("bbox", (0, 0, 0, 0)))
                inter = _box_intersection(box, cbox)
                if inter >= 0.4 * min(aire, max(1, cbox[2] * cbox[3])):
                    if same_text_only and re.sub(
                            r"[^A-Z0-9]", "", str(it.get("text", "")).upper()
                    ) != re.sub(r"[^A-Z0-9]", "",
                                str(cur.get("text", "")).upper()):
                        continue
                    if float(it.get("conf", 0)) > float(cur.get("conf", 0)):
                        out[i] = dict(it)
                    connu = True
                    break
            if not connu:
                out.append(dict(it))
        return out

    @staticmethod
    def _binarize_ink(img_bgr):
        """Encre = noir, fond = blanc, meme sur un scan a eclairage inegal.

        Seuillage ADAPTATIF (moyenne locale) plutot que global : un scan
        photographie ou un degrade de photocopieuse fait echouer Otsu (une
        moitie du plan devient « encre »). Un garde-fou global (gray > 205)
        evite que le papier texture ne devienne de l'encre.
        """
        import cv2
        import numpy as np
        gray = (cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
                if getattr(img_bgr, "ndim", 2) == 3 else img_bgr)
        H, W = gray.shape[:2]
        block = int(max(15, min(61, round(max(H, W) / 25)))) | 1
        ink = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                    cv2.THRESH_BINARY_INV, block, 12)
        ink[gray > 205] = 0
        return ink

    @staticmethod
    def _keep_axis_aligned(mask, min_len_px: int, max_thick_px: int):
        """Ne garde que les traits LONGS et ORIENTES H ou V.

        Une hachure est longue mais OBLIQUE : sa boite englobante est presque
        carree, alors qu'un mur (ou son nu) a une boite tres allongee dans un
        seul axe. C'est ce critere geometrique — et non l'epaisseur — qui
        separe une hachure d'un mur.
        """
        import cv2
        import numpy as np
        n, lbl, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        out = np.zeros_like(mask)
        for i in range(1, n):
            x = int(stats[i, cv2.CC_STAT_LEFT])
            y = int(stats[i, cv2.CC_STAT_TOP])
            w = int(stats[i, cv2.CC_STAT_WIDTH])
            h = int(stats[i, cv2.CC_STAT_HEIGHT])
            horizontale = w >= min_len_px and h <= max_thick_px
            verticale = h >= min_len_px and w <= max_thick_px
            if horizontale or verticale:
                out[y:y + h, x:x + w] = np.maximum(
                    out[y:y + h, x:x + w], lbl[y:y + h, x:x + w] > 0)
        return out

    @staticmethod
    def _link_dot_chains(points, thickness: int, min_len: int,
                         max_gap: int, tol: int, min_dots: int = 4):
        """Reconstruit les murs POINTILLES a partir des centres de points.

        Une fermeture morphologique fusionne les points d'un mur avec ceux des
        murs voisins : le reseau obtenu a une boite englobante carree et tout
        filtre d'elongation le rejette. On relie donc les points UN PAR UN,
        uniquement s'ils sont alignes (H ou V), proches (``max_gap``) et assez
        nombreux (``min_dots``) — une hachure ou un texte isole ne peut pas
        former une telle chaine.

        Retourne un masque de segments (epaisseur ``thickness``).
        """
        import cv2
        import numpy as np
        n, _lbl, _stats, cents = cv2.connectedComponentsWithStats(points, 8)
        pts = [(float(cents[i][0]), float(cents[i][1])) for i in range(1, n)]
        out = np.zeros_like(points)
        if len(pts) < min_dots:
            return out

        def _flush(group, vertical: bool):
            if len(group) < min_dots:
                return
            group.sort(key=lambda p: p[1] if vertical else p[0])
            span = abs((group[-1][1] if vertical else group[-1][0])
                       - (group[0][1] if vertical else group[0][0]))
            if span < min_len:
                return
            moy = sum(p[0] if vertical else p[1] for p in group) / len(group)
            if vertical:
                p1 = (int(round(moy)), int(max(0, group[0][1])))
                p2 = (int(round(moy)), int(group[-1][1]))
            else:
                p1 = (int(max(0, group[0][0])), int(round(moy)))
                p2 = (int(group[-1][0]), int(round(moy)))
            cv2.line(out, p1, p2, 255, thickness)

        for vertical in (False, True):
            # H : on balaie par y croissant ; V : par x croissant
            pts_sorted = sorted(pts, key=lambda p: (p[1], p[0]) if not vertical
                                else (p[0], p[1]))
            groupe: list = []
            for p in pts_sorted:
                if not groupe:
                    groupe = [p]
                    continue
                moy = sum(q[1] if not vertical else q[0]
                          for q in groupe) / len(groupe)
                ecart = abs((p[1] if not vertical else p[0]) - moy)
                avance = ((p[0] - groupe[-1][0]) if not vertical
                          else (p[1] - groupe[-1][1]))
                if ecart <= tol and 0 <= avance <= max_gap:
                    groupe.append(p)
                else:
                    _flush(groupe, vertical)
                    groupe = [p]
            _flush(groupe, vertical)
        return out

    @staticmethod
    def _estimate_band_gap(nu_h, nu_v, fallback: int = 9,
                           lo: int = 3, hi: int = 41, cap: Optional[int] = None) -> int:
        """Ecart entre deux nus paralleles = epaisseur du mur, en pixels.

        Mesure faite SUR LE DOCUMENT, pas sur la resolution de l'image : on
        releve la distance entre nus successifs dans chaque colonne (nus
        horizontaux) et dans chaque ligne (nus verticaux).

        Garde-fous, parce que le MOBILIER produit aussi des paires de lignes
        paralleles proches (contour d'un lit, d'un placard), souvent plus
        frequentes que les vrais murs :
        - on ne retient que le PLUS PETIT pas a fort support (les murs en
          double trait sont plus fins que les contours de mobilier) ;
        - le pas doit rester inferieur a `cap` (epaisseur de mur plausible,
          ~0,35 m a l'echelle du plan) : un contour de lit de 26 px n'est pas
          un mur et ne doit jamais elargir la fermeture de bande.

        Sans pas valide, on retombe sur la valeur de repli, liee a la taille
        de l'image.
        """
        import numpy as np
        vals = []
        for mask, axis in ((nu_h, 0), (nu_v, 1)):
            if mask is None or getattr(mask, "ndim", 0) != 2 or not mask.any():
                continue
            m = mask > 0
            sub = m[:, ::2] if axis == 0 else m[::2, :]
            for i in range(sub.shape[1 - axis]):
                line = sub[:, i] if axis == 0 else sub[i, :]
                pos = np.flatnonzero(line)
                if pos.size > 1:
                    d = np.diff(pos)
                    vals.append(d[(d >= lo) & (d <= hi)])
        if not vals:
            return fallback
        allv = np.concatenate(vals)
        if allv.size < 25:
            return fallback
        counts = np.bincount(allv, minlength=hi + 1)
        if cap is None:
            cap = hi
        seuil = max(25, int(0.15 * allv.size))
        # plus petit pas a fort support, borne par l'epaisseur de mur max
        for g in range(lo, min(cap, hi) + 1):
            if counts[g] >= seuil:
                gg = g + 2
                if gg % 2 == 0:
                    gg += 1
                return int(max(3, min(gg, hi + 2)))
        return fallback

    @staticmethod
    def wall_mask(img_bgr, min_wall_px: Optional[int] = None,
                  text_boxes: Optional[Sequence[Sequence[int]]] = None):
        """Murs = structures LONGUES, EPAISSES et CONTINUES.

        Un plan reel ne dessine pas toujours les murs en aplat noir :
        - murs en **poche hachuree** (deux nus + hachures a 45 deg) ;
        - murs en **double trait** (deux nus, vide entre eux) ;
        - murs **pointilles** ou en tirets.
        Un simple seuillage + ouverture ne voit dans ces cas qu'une fine
        ligne (ou rien) et la piece n'est plus fermee.

        Pipeline :
          1. binarisation adaptative (cf. `_binarize_ink`) ;
          2. **murs pleins** : ouverture -> les hachures fines disparaissent ;
          3. **traits longs H/V** : fermeture directionnelle (recolle les
             pointilles), puis filtrage geometrique (`_keep_axis_aligned`) qui
             elimine les hachures obliques et le parquet ;
          4. **bandes** : fermeture perpendiculaire -> elle remplit le vide
             entre les deux nus d'un mur creux et la poche hachuree ;
          5. fusion, puis suppression des ilots non muraux.
        """
        import cv2
        import numpy as np
        H, W = img_bgr.shape[:2]
        u = max(H, W) / 1600.0          # facteur d'echelle (plans 900..3000 px)
        Lmin = max(12, int(round(40 * u)))    # longueur mini d'un trait de mur
        Lgap = max(7, int(round(14 * u)))     # espace max entre deux pointilles

        ink = PlanSurfaceExtractor._binarize_ink(img_bgr)

        # --- 2. murs pleins (aplat noir) ------------------------------------
        k = max(3, int(round((min_wall_px or 3 * u))) | 1)
        plein = cv2.morphologyEx(
            ink, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))

        # --- 3. nus de murs : traits LONGS et ORIENTES H ou V ---------------
        # Ouverture (et non fermeture) : une hachure oblique ne contient aucun
        # segment horizontal de Lmin px, elle disparait donc ici — contrairement
        # a une fermeture qui la fusionnerait avec ses voisines.
        nu_h = cv2.morphologyEx(
            ink, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (Lmin, 1)))
        nu_v = cv2.morphologyEx(
            ink, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, Lmin)))
        nus = cv2.bitwise_or(nu_h, nu_v)

        # --- 3 bis. epaisseur de mur MESUREE sur le document ----------------
        # L'ecart entre deux nus paralleles est l'epaisseur reelle du mur
        # dessine. On ne le deduit pas de la resolution : un plan peut etre
        # 900 px ou 3000 px de large avec la meme epaisseur relative, et deux
        # bureaux d'etudes n'utilisent pas la meme plume. Noyau IMPAIR
        # obligatoire : une fermeture de taille paire epaissit les traits de
        # 1 px (asymetrie d'ancre OpenCV), ce qui laisse survivre le mobilier
        # filaire et decoupe les pieces.
        G = PlanSurfaceExtractor._estimate_band_gap(
            nu_h, nu_v, fallback=max(5, int(round(9 * u))),
            cap=max(12, int(round(22 * u))))
        if G % 2 == 0:
            G += 1

        # --- 4. murs pointilles / en tirets ---------------------------------
        # On retire les nus, on ne garde que de PETITS elements (les points),
        # puis on les recolle SEULEMENT s'ils s'alignent en H ou en V : les
        # points d'un mur s'alignent, les hachures obliques non.
        reste = cv2.bitwise_and(
            ink, cv2.bitwise_not(cv2.dilate(nus, np.ones((2 * k + 1,) * 2,
                                                         np.uint8))))
        taille_max = max(8, int(round(9 * u)))
        n, lbl, stats, _ = cv2.connectedComponentsWithStats(reste, 8)
        points = np.zeros_like(ink)
        for i in range(1, n):
            x = int(stats[i, cv2.CC_STAT_LEFT])
            y = int(stats[i, cv2.CC_STAT_TOP])
            w = int(stats[i, cv2.CC_STAT_WIDTH])
            h = int(stats[i, cv2.CC_STAT_HEIGHT])
            if w <= taille_max and h <= taille_max:
                points[y:y + h, x:x + w] = np.maximum(
                    points[y:y + h, x:x + w],
                    (lbl[y:y + h, x:x + w] == i).astype(np.uint8))
        # Les points sont relies SEULEMENT s'ils sont alignes, proches et
        # assez nombreux (cf. `_link_dot_chains`).
        pointilles = PlanSurfaceExtractor._link_dot_chains(
            points, thickness=k, min_len=Lmin,
            max_gap=max(Lgap, int(round(16 * u))),
            tol=max(2, int(round(2.5 * u))))

        traits = cv2.bitwise_or(nus, cv2.dilate(
            pointilles, np.ones((k, k), np.uint8)))

        # --- 5. bandes : remplir le vide entre deux nus paralleles ----------
        # Ecart max comble = G px (~0,43 m a 21 px/m) : epaisseur d'un mur
        # courant, tres inferieur a une piece ou a un couloir.
        bandes = cv2.bitwise_or(
            cv2.morphologyEx(traits, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (1, G))),
            cv2.morphologyEx(traits, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (G, 1))))

        walls = cv2.bitwise_or(plein, bandes)

        # --- 5 bis. les LIBELLES ne sont pas des murs -----------------------
        # Un mot est un ilot compact a faible remplissage ; ses lettres
        # courtes traversent l'ouverture et, reliees par la fermeture de
        # bande, forment une barre qui coupe la piece en deux (mesure :
        # SALON -41 %, CUISINE -56 % sur un plan 12 px/m). On retire l'encre
        # situee dans les boites de texte, SAUF ce qui appartient deja a un
        # mur long (nus) : un libelle colle a une cloison ne perce donc pas
        # le mur. Aucune regle sur le NOM des pieces.
        if text_boxes:
            txt = np.zeros(walls.shape, np.uint8)
            for box in text_boxes:
                x, y, bw, bh = (int(v) for v in box[:4])
                if bw <= 0 or bh <= 0:
                    continue
                marge = max(2, int(round(1.5 * u)))
                x0, y0 = max(0, x - marge), max(0, y - marge)
                x1 = min(walls.shape[1], x + bw + marge)
                y1 = min(walls.shape[0], y + bh + marge)
                txt[y0:y1, x0:x1] = 255
            protege = cv2.dilate(traits, np.ones((2 * k + 1,) * 2, np.uint8))
            walls = cv2.bitwise_and(walls, cv2.bitwise_not(
                cv2.bitwise_and(txt, cv2.bitwise_not(protege))))

        # --- 5. nettoyage : ilots courts et fins ---------------------------
        walls = cv2.morphologyEx(
            walls, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))
        n, lbl, stats, _ = cv2.connectedComponentsWithStats(walls, 8)
        garde = np.zeros_like(walls)
        aire_min = (6 * u) ** 2
        for i in range(1, n):
            x, y = int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP])
            w, h = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
            area = int(stats[i, cv2.CC_STAT_AREA])
            long_seg = max(w, h) >= max(10, int(round(28 * u)))
            if long_seg or area >= aire_min:
                garde[stats[i, cv2.CC_STAT_TOP]:y + h,
                      x:x + w] = np.maximum(
                    garde[y:y + h, x:x + w], (lbl[y:y + h, x:x + w] == i))
        return (garde * 255).astype(np.uint8)

    @staticmethod
    def _normalize_polarity(img_bgr):
        """Inverse l'image si le fond est sombre (scan negatif, tirage bleu).

        Un plan ordinaire a un fond papier clair (mediane > 110). Un tirage
        inverse (murs clairs sur fond bleu/noir, calque de nuit, photocopie
        negative) a une mediane basse : sans inversion, le seuillage Otsu
        prend tout le papier pour du mur et plus aucune piece n'est mesuree.

        Retourne ``(image, inverse)``.
        """
        import cv2
        import numpy as np
        if img_bgr is None or getattr(img_bgr, "size", 0) == 0:
            return img_bgr, False
        gray = (cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
                if getattr(img_bgr, "ndim", 2) == 3 else img_bgr)
        med = float(np.median(gray))
        if med >= 110.0:
            return img_bgr, False
        return cv2.bitwise_not(img_bgr), True

    @staticmethod
    def _denoise_speckle(img_bgr):
        """Debruite les scans poussiereux (bruit « sel et poivre »).

        Mesure la part de pixels sombres isoles (aucun voisin sombre) : un
        plan propre en a ~0 %, un scan poussiereux plusieurs pourcents. Au
        dela de 2 %, un filtre median 3x3 est applique (deux passes si le
        bruit depasse 15 %). Les traits et les textes, epais de plusieurs
        pixels, sont conserves ; seuls les points isoles disparaissent.

        Retourne ``(image, taux_de_speckle)``.
        """
        import cv2
        import numpy as np
        if img_bgr is None or getattr(img_bgr, "size", 0) == 0:
            return img_bgr, 0.0
        gray = (cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
                if getattr(img_bgr, "ndim", 2) == 3 else img_bgr)
        dark = (gray < 128).astype(np.uint8)
        total = int(dark.sum())
        if total == 0:
            return img_bgr, 0.0
        neigh = cv2.filter2D(dark, cv2.CV_16U, np.ones((3, 3), np.uint8))
        isolated = int(((dark == 1) & (neigh <= 2)).sum())
        ratio = isolated / total
        if ratio <= 0.02:
            return img_bgr, ratio
        out = img_bgr
        for _ in range(2 if ratio > 0.15 else 1):
            out = cv2.medianBlur(out, 3)
        return out, ratio

    @staticmethod
    def building_bbox(wall_mask) -> Optional[Tuple[int, int, int, int]]:
        """Union des grandes composantes murales (>= 20 % de l'image)."""
        import cv2
        import numpy as np
        H, W = wall_mask.shape
        n, lbl, stats, _ = cv2.connectedComponentsWithStats(wall_mask, 8)
        if n <= 1:
            return None
        # Grandes composantes EPAISSES (murs) : les lignes de cotes, longues
        # mais fines (area/(w+h) < 2.5 px), sont exclues de l'enveloppe.
        big = [i for i in range(1, n)
               if (stats[i, cv2.CC_STAT_WIDTH] >= 0.20 * W
                   or stats[i, cv2.CC_STAT_HEIGHT] >= 0.20 * H)
               and stats[i, cv2.CC_STAT_AREA] / max(
                   1, stats[i, cv2.CC_STAT_WIDTH] + stats[i, cv2.CC_STAT_HEIGHT]) >= 2.5]
        if not big:
            big = [max(range(1, n), key=lambda i: stats[i, cv2.CC_STAT_WIDTH]
                       * stats[i, cv2.CC_STAT_HEIGHT])]
        x0 = min(int(stats[i, cv2.CC_STAT_LEFT]) for i in big)
        y0 = min(int(stats[i, cv2.CC_STAT_TOP]) for i in big)
        x1 = max(int(stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH]) for i in big)
        y1 = max(int(stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT]) for i in big)
        return (x0, y0, x1 - x0, y1 - y0)

    @staticmethod
    def _fill_holes(mask):
        """Remplit les trous interieurs (mobilier, ilots) d'une region."""
        import cv2
        import numpy as np
        h, w = mask.shape
        flood = mask.copy()
        ff_mask = np.zeros((h + 2, w + 2), np.uint8)
        cv2.floodFill(flood, ff_mask, (0, 0), 255)
        holes = cv2.bitwise_not(flood)
        return cv2.bitwise_or(mask, holes)

    @staticmethod
    def segment_rooms(wall_mask, bbox: Optional[Tuple[int, int, int, int]],
                      seal_px: Optional[int] = None,
                      scale_px_per_m: Optional[float] = None) -> List[Dict[str, Any]]:
        """Espaces libres fermes. Les portes sont scellees par FERMETURE
        morphologique (dilatation puis erosion) : les ouvertures < k px sont
        bouchees sans epaissir les murs ni fragmenter les pieces."""
        import cv2
        import numpy as np
        H, W = wall_mask.shape
        if bbox is None:
            return []
        bx, by, bw, bh = bbox
        if seal_px:
            k = seal_px
        elif scale_px_per_m:
            # 0.85 m : bouche une porte standard (0.80-0.90 m) sans fermer un
            # degagement reglementaire (>= 1.00 m)
            k = int(round(0.85 * scale_px_per_m))
            k = max(7, min(k, int(0.12 * max(bw, bh))))
        else:
            k = max(7, int(round(max(bw, bh) * 0.02)))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
        sealed = cv2.morphologyEx(wall_mask, cv2.MORPH_CLOSE, kernel)
        free = cv2.bitwise_not(sealed)
        # Hors batiment -> ignore
        outside = np.ones_like(free)
        outside[by:by + bh, bx:bx + bw] = 0
        free[outside == 1] = 0
        n, lbl, stats, cents = cv2.connectedComponentsWithStats(free, 4)
        regions: List[Dict[str, Any]] = []
        min_area = 0.002 * bw * bh
        max_area = 0.95 * bw * bh
        for i in range(1, n):
            area = stats[i, cv2.CC_STAT_AREA]
            if area < min_area or area > max_area:
                continue
            x, y, w, h = (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                          int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]))
            # Region collee au bord de l'enveloppe sur 3 cotes = exterieur
            touches = sum((x <= bx + 2, y <= by + 2, x + w >= bx + bw - 2,
                           y + h >= by + bh - 2))
            if touches >= 3:
                continue
            comp = (lbl == i).astype(np.uint8) * 255
            # Mobilier / ilots interieurs : la piece est la region REMPLIE
            filled = PlanSurfaceExtractor._fill_holes(comp)
            area_px = int(np.count_nonzero(filled))
            cnts, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not cnts:
                continue
            main = max(cnts, key=cv2.contourArea)
            perim_px = float(cv2.arcLength(main, True))
            rx, ry, rw, rh = cv2.boundingRect(main)
            regions.append({"id": i, "mask": filled, "area_px": area_px,
                            "perimeter_px": perim_px,
                            "bbox": (int(rx), int(ry), int(rw), int(rh)),
                            "center": (float(cents[i][0]), float(cents[i][1]))})
        # Une region incluse dans le remplissage d'une autre (interieur d'un
        # lit, ilot de cuisine) n'est pas une piece : on la retire.
        keep: List[Dict[str, Any]] = []
        for rg in sorted(regions, key=lambda r: -r["area_px"]):
            cx, cy = int(rg["center"][0]), int(rg["center"][1])
            enclosed = any(k2["mask"][cy, cx] > 0 and k2["area_px"] > rg["area_px"] * 1.5
                           for k2 in keep)
            if not enclosed:
                keep.append(rg)
        return keep

    @staticmethod
    def room_labels(ocr_items: Sequence[Dict[str, Any]],
                    bbox: Optional[Tuple[int, int, int, int]],
                    shape: Tuple[int, int]) -> List[Dict[str, Any]]:
        """Libelles de pieces (mots alphabetiques, fusion des lignes empilees)."""
        H, W = shape
        cands = []
        nums = []
        for it in ocr_items:
            t = str(it["text"]).strip()
            # Numero de piece isole par l'OCR ('BEDROOM' / '2') : rattache
            # ensuite au libelle qu'il accompagne.
            if re.fullmatch(r"\d{1,3}", t):
                cx0 = it["bbox"][0] + it["bbox"][2] / 2.0
                cy0 = it["bbox"][1] + it["bbox"][3] / 2.0
                if bbox is not None:
                    bx0, by0, bw0, bh0 = bbox
                    if not (bx0 <= cx0 <= bx0 + bw0 and by0 <= cy0 <= by0 + bh0):
                        continue
                nums.append({"text": t, "bbox": it["bbox"],
                             "conf": float(it.get("conf", 0.8))})
                continue
            core = re.sub(r"[\d\s\-_.]+$", "", t)
            if len(core) < 2 or not re.search(r"[A-Za-zÀ-ÿ]{2,}", core):
                continue
            if re.search(r"\d\s*[x×X*]\s*\d", t) or is_dimension_token(t):
                continue
            low = core.lower()
            if any(low.startswith(w) or w in low for w in EXCLUDED_WORDS):
                continue
            x, y, w, h = it["bbox"]
            cx, cy = x + w / 2.0, y + h / 2.0
            if bbox is not None:
                bx, by, bw, bh = bbox
                margin = 0.02 * max(bw, bh)
                if not (bx - margin <= cx <= bx + bw + margin
                        and by - margin <= cy <= by + bh + margin):
                    continue
            known = any(w_ in low for w_ in ROOM_LEXICON)
            if not known and not re.match(r"^[A-ZÀ-Ý][A-ZÀ-Ý\-]{2,}\d?$", t):
                continue
            cands.append({"text": t, "bbox": it["bbox"], "conf": float(it.get("conf", 0.8)),
                          "pt": (cx, cy), "known": known})
        # Fusion des libelles empiles ('MASTER' / 'BATH'). Les boites OCR de
        # deux lignes peuvent se CHEVAUCHER de 1-2 px : le test de proximite
        # doit donc tolerer un recouvrement, sinon le libelle reste scinde.
        cands.sort(key=lambda c: (c["pt"][1], c["pt"][0]))
        merged: List[Dict[str, Any]] = []
        for c in cands:
            x, y, w, h = c["bbox"]
            for m in merged:
                mx, my, mw, mh = m["bbox"]
                gap = y - (my + mh)
                if (abs((mx + mw / 2) - (x + w / 2)) < max(mw, w) * 0.6
                        and -0.6 * min(h, mh) <= gap <= 1.2 * max(h, mh)):
                    m["text"] = f"{m['text']} {c['text']}"
                    nx0, ny0 = min(mx, x), min(my, y)
                    nx1, ny1 = max(mx + mw, x + w), max(my + mh, y + h)
                    m["bbox"] = (nx0, ny0, nx1 - nx0, ny1 - ny0)
                    m["pt"] = (nx0 + (nx1 - nx0) / 2.0, ny0 + (ny1 - ny0) / 2.0)
                    m["conf"] = min(m["conf"], c["conf"])
                    m["known"] = m["known"] or c["known"]
                    break
            else:
                merged.append(dict(c))

        # Numero de piece rattache au libelle ('BEDROOM' + '2')
        merged.sort(key=lambda m: m["bbox"][1] + m["bbox"][3])
        for num in nums:
            nx, ny, nw, nh = num["bbox"]
            ncx, ncy = nx + nw / 2.0, ny + nh / 2.0
            best, best_d = None, None
            for m in merged:
                mx, my, mw, mh = m["bbox"]
                # soit juste sous le libelle, soit sur la meme ligne a droite
                below = (-0.6 * min(nh, mh) <= ny - (my + mh) <= 1.2 * mh
                         and abs((mx + mw / 2) - ncx) < 0.8 * max(mw, nw))
                right = (abs((my + mh / 2) - ncy) < 0.5 * mh
                         and 0 <= nx - (mx + mw) <= 1.2 * mh)
                if not (below or right):
                    continue
                dist = abs(ncy - (my + mh / 2)) + abs(ncx - (mx + mw / 2))
                if best_d is None or dist < best_d:
                    best, best_d = m, dist
            if best is None:
                continue
            mx, my, mw, mh = best["bbox"]
            best["text"] = f"{best['text']} {num['text']}"
            nx0, ny0 = min(mx, nx), min(my, ny)
            nx1, ny1 = max(mx + mw, nx + nw), max(my + mh, ny + nh)
            best["bbox"] = (nx0, ny0, nx1 - nx0, ny1 - ny0)
            best["pt"] = (nx0 + (nx1 - nx0) / 2.0, ny0 + (ny1 - ny0) / 2.0)
            best["conf"] = min(best["conf"], num["conf"])
        return merged

    # ------------------------------------------------------------------
    def calibrate_scale(self, dim_tokens: Sequence[Dict[str, Any]],
                        bbox: Optional[Tuple[int, int, int, int]],
                        shape: Tuple[int, int]
                        ) -> Tuple[Optional[float], float, List[Dict[str, Any]], List[str]]:
        """px/m par (1) chaines de cotes consecutives, (2) cote hors-tout vs
        enveloppe des murs. Mediane + dispersion -> confiance."""
        import numpy as np
        H, W = shape
        toks = []
        for it in dim_tokens:
            L = parse_length(it["text"])
            if not L or L <= 0.2 or L > 200:
                continue
            x, y, w, h = it["bbox"]
            toks.append({"L": L, "cx": x + w / 2.0, "cy": y + h / 2.0,
                         "h": h, "horiz": w >= h, "text": it["text"]})
        estimates: List[Dict[str, Any]] = []
        tol = max(8.0, 0.012 * max(H, W))

        def chains(items, key_group, key_sort):
            groups: List[List[dict]] = []
            for t in sorted(items, key=lambda t: t[key_group]):
                for g in groups:
                    if abs(g[-1][key_group] - t[key_group]) <= tol:
                        g.append(t)
                        break
                else:
                    groups.append([t])
            for g in groups:
                g.sort(key=lambda t: t[key_sort])
                for a, b in zip(g, g[1:]):
                    gap = b[key_sort] - a[key_sort]
                    expect = (a["L"] + b["L"]) / 2.0
                    if gap > 5 and expect > 0:
                        estimates.append({"method": "chaine", "px_per_m": gap / expect,
                                          "a": a["text"], "b": b["text"]})

        chains([t for t in toks if t["horiz"]], "cy", "cx")
        chains([t for t in toks if not t["horiz"]], "cx", "cy")

        warns: List[str] = []
        bbox_est: List[Dict[str, Any]] = []
        axis_stats: Dict[str, Dict[str, Any]] = {}
        n_cotes = {"x": sum(1 for t in toks if t["horiz"]),
                   "y": sum(1 for t in toks if not t["horiz"])}
        chain_vals = [e["px_per_m"] for e in estimates if e["method"] == "chaine"]
        chain_med = float(np.median(chain_vals)) if chain_vals else None
        if bbox is not None and toks:
            bx, by, bw, bh = bbox
            # Cotes hors-tout : situees HORS de l'enveloppe (au-dessus /
            # en dessous pour les horizontales, a gauche / droite pour les
            # verticales) et CENTREES sur l'emprise.
            horiz = [t for t in toks if t["horiz"]
                     and (t["cy"] < by or t["cy"] > by + bh)]
            vert = [t for t in toks if not t["horiz"]
                    and (t["cx"] < bx or t["cx"] > bx + bw)]
            for group, dim_px, tag in ((horiz, bw, "x"), (vert, bh, "y")):
                if not group:
                    continue
                axis = "cx" if tag == "x" else "cy"
                across = "cy" if tag == "x" else "cx"
                origin = bx if tag == "x" else by
                lo_edge, hi_edge = (by, by + bh) if tag == "x" else (bx, bx + bw)

                def dist_out(t, _axis=across, _lo=lo_edge, _hi=hi_edge):
                    """Eloignement de la cote par rapport au nu des murs
                    (coordonnee perpendiculaire a l'axe de la cote)."""
                    v = t[_axis]
                    return v - _hi if v > _hi else _lo - v

                # Candidates = cotes centrees sur l'emprise (meme ligne que la
                # plus eloignee). Une cote de chaine n'est pas centree.
                center = origin + dim_px / 2.0
                near = [t for t in group if abs(t[axis] - center) < 0.05 * dim_px]
                if not near:
                    near = [min(group, key=lambda t: abs(t[axis] - center))]
                near.sort(key=lambda t: -dist_out(t))

                # Le hors-tout est la plus GRANDE cote de l'axe : rien ne peut
                # etre plus long que la dimension totale. Une cote de chaine
                # isolee (ex. 15'-0" seule sur l'axe) ne peut donc pas etre
                # prise pour un hors-tout.
                near.sort(key=lambda t: -t["L"])
                big = near[0]
                if chain_med:
                    coherent = [t for t in group
                                if abs(dim_px / t["L"] - chain_med) / chain_med <= 0.25]
                    if coherent:
                        # la plus grande des cotes coherentes avec les chaines
                        big = max(coherent, key=lambda t: t["L"])
                    else:
                        warns.append(
                            f"[ALERTE ECHELLE] aucune cote hors-tout en {tag} "
                            f"coherente avec les chaines ({chain_med:.1f} px/m) : "
                            f"{', '.join(t['text'] for t in near)}. Cote retenue "
                            f"= {big['text']} — verifier la lecture du plan.")
                est = {"method": f"hors_tout_{tag}",
                       "px_per_m": dim_px / big["L"],
                       "a": big["text"], "b": f"{dim_px}px"}
                estimates.append(est)
                bbox_est.append(est)
                axis_stats[tag] = {"value": dim_px / big["L"],
                                   "n": n_cotes[tag],
                                   "dist": dist_out(big),
                                   "text": big["text"]}

                # Autres cotes de la MEME ligne et centrees sur l'emprise :
                # conservees pour tracabilite. Deux valeurs tres proches
                # (ratio >= 0.55, soit moins de 1.8x) = lecture douteuse de la
                # meme cote -> ALERTE. Un ecart plus grand = cote de chaine
                # (ex. 51'-0" sous un hors-tout de 192'-0") -> ignoree.
                for t in near:
                    if t is big:
                        continue
                    ratio = min(t["L"], big["L"]) / max(t["L"], big["L"])
                    conflict = (ratio >= 0.55
                                and abs(t["L"] - big["L"]) / big["L"] > 0.03)
                    ppm = dim_px / t["L"]
                    estimates.append({
                        "method": f"hors_tout_{tag}_conflit" if conflict
                                  else f"hors_tout_{tag}_ignoree",
                        "px_per_m": ppm, "a": t["text"], "b": f"{dim_px}px"})
                    if conflict:
                        warns.append(
                            f"[ALERTE ECHELLE] cotes hors-tout contradictoires en "
                            f"{tag} : {big['text']} vs {t['text']} pour la même "
                            f"emprise. Saisir la largeur hors-tout réelle (m).")
                    elif dist_out(t) > dist_out(big) + 1.0 and chain_med:
                        warns.append(
                            f"[ALERTE ECHELLE] cote hors-tout {t['text']} en {tag} "
                            f"incoherente avec les chaines ({ppm:.1f} px/m contre "
                            f"{chain_med:.1f} px/m) : cote retenue = {big['text']}. "
                            f"Verifier la lecture sur le plan.")

        # --- Agregation : le hors-tout prime, les chaines servent de controle
        # (un plan cote en pieds a des chaines bruitees par l'OCR ; la cote
        # hors-tout relie directement l'emprise dessinee a la dimension reelle)
        used = [e for e in estimates
                if not e["method"].endswith(("_ignoree", "_conflit"))]
        vals = np.array([e["px_per_m"] for e in used if 0.5 < e["px_per_m"] < 5000])
        if len(vals) == 0:
            warns.append("Echelle non calibrable : aucune chaine de cotes lisible.")
            return None, 0.0, estimates, warns

        # Les deux axes sont traites SEPAREMENT : sur un plan ou l'axe Y n'a
        # qu'une cote de chaine, la moyenne des deux axes inventerait une
        # echelle fausse (erreur x3 constatee sur les plans basse resolution).
        vx = axis_stats.get("x", {}).get("value")
        vy = axis_stats.get("y", {}).get("value")
        if vx and vy:
            ecart = abs(vx - vy) / max(vx, vy)
            if ecart <= 0.15:
                base = (vx + vy) / 2.0
                conf = 0.9
                support = [v for v in vals if abs(v - base) / base <= 0.20]
                if len(support) >= 2:
                    base = float(np.median(support))
                    conf = 0.92
            else:
                # Axes contradictoires : on garde l'axe le mieux cote (le plus
                # de cotes), sinon celui dont la cote est la plus eloignee.
                sx, sy = axis_stats["x"], axis_stats["y"]
                if sx["n"] != sy["n"]:
                    keep = "x" if sx["n"] > sy["n"] else "y"
                else:
                    keep = "x" if sx["dist"] >= sy["dist"] else "y"
                base = sx["value"] if keep == "x" else sy["value"]
                conf = 0.55
                warns.append(
                    f"[ALERTE ECHELLE] axes X ({vx:.2f} px/m) et Y ({vy:.2f} px/m) "
                    f"incoherents ({ecart*100:.0f} % d'ecart) : echelle retenue "
                    f"sur l'axe {keep.upper()} (cote {axis_stats[keep]['text']}). "
                    f"Verifier les cotes du plan.")
        elif vx or vy:
            base = vx or vy
            conf = 0.6
            warns.append(
                "[ALERTE ECHELLE] une seule cote hors-tout exploitable "
                f"({axis_stats['x' if vx else 'y']['text']}) : echelle "
                "calibree sur un seul axe, surfaces a verifier sur l'overlay.")
        else:
            bbox_vals = np.array([e["px_per_m"] for e in bbox_est
                                  if 0.5 < e["px_per_m"] < 5000])
            med = float(np.median(vals))
            spread = float(np.median(np.abs(vals - med)) / med) if med else 1.0
            conf = max(0.0, min(1.0, 1.0 - spread * 2.0))
            if len(vals) < 2:
                conf = min(conf, 0.5)
            if spread > 0.25:
                warns.append(
                    f"[ALERTE ECHELLE] cotes incoherentes entre elles (dispersion "
                    f"{spread*100:.0f} %) : surfaces segmentees a verifier.")
            for e in estimates:
                e["px_per_m"] = round(e["px_per_m"], 2)
            return round(med, 3), round(conf, 2), estimates, warns

        support = [v for v in vals if abs(v - base) / base <= 0.20]
        if len(support) >= 2:
            base = float(np.median(support))
        if len(support) < 2:
            conf = min(conf, 0.75)
        for e in estimates:
            e["px_per_m"] = round(e["px_per_m"], 2)
        return round(base, 3), round(conf, 2), estimates, warns

    # ------------------------------------------------------------------
    def build_rooms(self, labels, pair_tokens, regions, wall_mask, scale, conf,
                    level, shape, bbox=None) -> List[RoomSurface]:
        import numpy as np
        H, W = shape
        radius = 0.22 * math.hypot(H, W)
        rooms: List[RoomSurface] = []
        # Affectation gloutonne des cotes explicites (distance croissante)
        pairs: List[Tuple[float, int, int]] = []
        for li, lb in enumerate(labels):
            for pi, (it, _) in enumerate(pair_tokens):
                x, y, w, h = it["bbox"]
                d = math.hypot(lb["pt"][0] - (x + w / 2), lb["pt"][1] - (y + h / 2))
                if d <= radius:
                    pairs.append((d, li, pi))
        pairs.sort()
        used_l, used_p, assign = set(), set(), {}
        for d, li, pi in pairs:
            if li in used_l or pi in used_p:
                continue
            assign[li] = pi
            used_l.add(li)
            used_p.add(pi)

        # Region geometrique contenant chaque libelle (la plus grande qui le
        # contient : un libelle place dans un lit remonte a la chambre)
        region_of: Dict[int, Optional[Dict[str, Any]]] = {}
        for li, lb in enumerate(labels):
            x, y, w, h = (int(v) for v in lb["bbox"])
            x0, y0 = max(0, x - 4), max(0, y - 4)
            x1, y1 = min(W, x + w + 4), min(H, y + h + 4)
            best, best_cov = None, 0
            for rg in regions:
                cov = int(np.count_nonzero(rg["mask"][y0:y1, x0:x1]))
                # recouvrement majoritaire, puis la plus grande region
                if cov > best_cov or (cov == best_cov and best is not None
                                      and cov > 0 and rg["area_px"] > best["area_px"]):
                    best, best_cov = rg, cov
            region_of[li] = best if best_cov > 0 else None
        labels_in_region: Dict[int, List[int]] = {}
        for li, rg in region_of.items():
            if rg is not None:
                labels_in_region.setdefault(rg["id"], []).append(li)

        # Un MEME libelle ecrit deux fois dans une meme region (grande piece en
        # L, piece traversante, libelle repete pour lisibilite) ne doit pas
        # produire deux lignes : la region est exclusive et mesuree une fois.
        # Deux libelles DIFFERENTS dans une region restent, eux, un espace
        # ouvert partage (cf. `shared` plus bas).
        doublon: Dict[int, int] = {}
        for _rid, lis in labels_in_region.items():
            par_nom: Dict[str, int] = {}
            for li in lis:
                nom = re.sub(r"[^A-Z0-9]", "", labels[li]["text"].upper())
                if nom in par_nom:
                    garde = par_nom[nom]
                    if float(labels[li]["conf"]) > float(labels[garde]["conf"]):
                        par_nom[nom] = li
                        doublon[garde] = li
                    else:
                        doublon[li] = par_nom[nom]
                else:
                    par_nom[nom] = li
        if doublon:
            labels_in_region = {
                rid: [li for li in lis if li not in doublon]
                for rid, lis in labels_in_region.items()}

        boxes = {b["label_index"]: b
                 for b in self.room_boxes(wall_mask, labels, bbox)}
        env_rooms: List[RoomSurface] = []
        room_of_label: Dict[int, RoomSurface] = {}
        region_of_room: Dict[int, Dict[str, Any]] = {}

        for li, lb in enumerate(labels):
            if li in doublon:
                continue                      # libelle repete dans la meme piece
            room = RoomSurface(label=lb["text"].upper(), level=level,
                               bbox=tuple(int(v) for v in lb["bbox"]),
                               confidence=lb["conf"])
            region = region_of[li]
            shared = region is not None and len(labels_in_region[region["id"]]) > 1

            # Surface mesuree pour ce libelle seul (rayons vers les murs) :
            # insensible aux portes ouvertes, contrairement au scellement.
            box = boxes.get(li) or {}
            box_px = box.get("box")
            env_px = box.get("box_env")
            clamped_sides = box.get("clamped_sides") or []
            box_area = box_perim = None
            if scale and box_px:
                _, _, bwp, bhp = box_px
                box_area = round((bwp * bhp) / (scale ** 2), 2)
                box_perim = round(2 * (bwp + bhp) / scale, 2)

            seg_area = None
            if scale and region is not None and not shared:
                seg_area = round(region["area_px"] / (scale ** 2), 2)

            if region is not None:
                room.region_bbox = tuple(int(v) for v in region["bbox"])
            if box_px:
                room.region_bbox = tuple(int(v) for v in box_px)

            if li in assign:
                it, (a, b) = pair_tokens[assign[li]]
                room.length_m, room.width_m = max(a, b), min(a, b)
                room.area_explicit_m2 = round(a * b, 2)
                if room.perimeter_m is None:
                    room.perimeter_m = round(2 * (a + b), 2)
                room.source = "cote_explicite"
                room.confidence = round(min(lb["conf"], float(it.get("conf", 0.8))), 2)
                for measured, tag in ((box_area, "boite ancree"),
                                      (seg_area, "segmentation")):
                    if measured:
                        ratio = measured / max(room.area_explicit_m2, 1e-6)
                        if not (0.6 <= ratio <= 1.6):
                            room.warnings.append(
                                f"Ecart cote explicite ({room.area_explicit_m2} m²) vs "
                                f"{tag} ({measured} m²) : a verifier.")
            elif seg_area and seg_area >= 2.0:
                # Region EXCLUSIVE au libelle : geometrie reelle des murs,
                # donc exacte meme pour une piece en L (la boite ancree, elle,
                # la surestimerait). La boite ne sert plus que de controle.
                room.area_segmented_m2 = seg_area
                region_of_room[id(room)] = region
                room.perimeter_m = round(region["perimeter_px"] / scale, 2)
                _rx, _ry, _rw, _rh = region["bbox"]
                room.width_m, room.length_m = sorted(
                    (round(_rh / scale, 2), round(_rw / scale, 2)))
                room.source = "segmentation"
                room.confidence = round(min(lb["conf"], conf), 2)
                if conf < 0.5:
                    room.warnings.append("Echelle peu fiable : surface indicative.")
                if box_area and not (0.75 <= box_area / max(seg_area, 1e-6) <= 1.35):
                    room.warnings.append(
                        f"Boite ancree {box_area} m² vs region {seg_area} m² : piece "
                        f"non rectangulaire (en L) ou cloison ouverte — surface "
                        f"retenue = region mesuree.")
                if seg_area < 3.0:
                    room.confidence = round(room.confidence * 0.5, 2)
                    room.warnings.append(
                        "Région suspecte (< 3 m²) : mobilier ou cloison non fermée ? "
                        "À vérifier sur l'overlay.")
            elif box_area and box_area >= 2.0:
                # Region partagee (ouverture, porte) : la boite ancree au
                # libelle isole la piece, insensible aux portes ouvertes.
                room.area_segmented_m2 = box_area
                room.perimeter_m = box_perim
                room.width_m, room.length_m = sorted(
                    (round(box_px[3] / scale, 2), round(box_px[2] / scale, 2)))
                room.source = "segmentation"
                room.confidence = round(min(lb["conf"], conf, 0.85), 2)
                if conf < 0.5:
                    room.warnings.append("Echelle peu fiable : surface indicative.")
            elif scale and env_px and shared:
                # Cote ouvert (grande ouverture, pas de cloison) : mesure
                # bornee par l'ENVELOPPE du batiment, jamais au-dela.
                _, _, ewp, ehp = env_px
                room.area_segmented_m2 = round((ewp * ehp) / (scale ** 2), 2)
                room.perimeter_m = round(2 * (ewp + ehp) / scale, 2)
                room.width_m, room.length_m = sorted(
                    (round(ehp / scale, 2), round(ewp / scale, 2)))
                room.region_bbox = tuple(int(v) for v in env_px)
                room.source = "segmentation"
                room.confidence = round(min(lb["conf"], conf, 0.7), 2)
                room.warnings.append(
                    "Cote(s) ouvert(s) : " + ", ".join(clamped_sides)
                    + " — mesure bornee par l'enveloppe du batiment "
                    "(aucune cloison trouvee). A verifier sur l'overlay.")
                env_rooms.append(room)
                if shared:
                    room.warnings.append(
                        "Espace partage avec "
                        + ", ".join(labels[j]["text"].upper()
                                    for j in labels_in_region[region["id"]]
                                    if j != li)
                        + " : surfaces reparties au prorata des pieces mesurees.")
            elif shared:
                others = [labels[j]["text"].upper()
                          for j in labels_in_region[region["id"]] if j != li]
                room.source = "zone_ouverte"
                room.confidence = round(min(lb["conf"], conf), 2)
                room.warnings.append(
                    "Zone ouverte partagée avec " + ", ".join(others)
                    + " : surface comptée dans la ligne ZONE OUVERTE.")
            else:
                room.source = "aucune"
                room.confidence = 0.0
                room.warnings.append(
                    "Surface NON TROUVEE : piece non fermee, ni cote 'L x l' "
                    "ni echelle exploitable.")
            rooms.append(room)
            room_of_label[li] = room

        # Regions partagees (ouverture large, plan libre) : la surface de la
        # region est la seule mesure fiable. On repartit cette surface entre
        # les pieces au prorata de leurs mesures individuelles -> la somme
        # reste egale a la surface reelle (aucun double comptage, aucun
        # metre carre invente).
        if scale:
            for rid, lis in labels_in_region.items():
                if len(lis) < 2:
                    continue
                region = next(r for r in regions if r["id"] == rid)
                seg = region["area_px"] / (scale ** 2)
                indiv = [room_of_label[j].area_segmented_m2 for j in lis
                         if j in room_of_label]
                if any(v is None for v in indiv) or sum(indiv) <= 0:
                    continue
                total = float(sum(indiv))
                if abs(total - seg) / max(seg, 1e-6) <= 0.02:
                    continue
                factor = seg / total
                for j, v in zip([j for j in lis if j in room_of_label], indiv):
                    room_of_label[j].area_segmented_m2 = round(v * factor, 2)
                    if room_of_label[j].perimeter_m:
                        room_of_label[j].perimeter_m = round(
                            room_of_label[j].perimeter_m
                            * math.sqrt(factor), 2)
                    room_of_label[j].warnings.append(
                        f"Region partagee {seg:.2f} m² repartie au prorata des "
                        f"pieces mesurees (facteur {factor:.3f}) : la somme "
                        f"egale la surface de la region.")

        # Zones ouvertes : une ligne par region partagee, surface segmentee
        # moins ce qui est deja compte (cotes explicites ET boites mesurees).
        if scale:
            for rid, lis in labels_in_region.items():
                if len(lis) < 2:
                    continue
                region = next(r for r in regions if r["id"] == rid)
                seg = region["area_px"] / (scale ** 2)
                explicit = sum(room_of_label[j].area_explicit_m2 or 0.0
                               for j in lis if j in room_of_label)
                measured = sum(room_of_label[j].area_segmented_m2 or 0.0
                               for j in lis if j in room_of_label)
                residual = round(seg - explicit - measured, 2)
                # Toutes les pieces de la zone sont mesurees : la ligne de
                # zone ferait doublon.
                if all(room_of_label[j].area_m2 for j in lis
                       if j in room_of_label) or residual < 0.5:
                    continue
                names = " + ".join(labels[j]["text"].upper() for j in lis)
                zone = RoomSurface(
                    label=f"ZONE OUVERTE ({names})", level=level,
                    area_segmented_m2=residual,
                    perimeter_m=round(region["perimeter_px"] / scale, 2),
                    source="segmentation", confidence=round(conf, 2),
                    bbox=tuple(int(v) for v in region["bbox"]),
                    region_bbox=tuple(int(v) for v in region["bbox"]))
                zone.warnings.append(
                    f"Région segmentée {seg:.2f} m² − cotes explicites "
                    f"{explicit:.2f} m² − pieces mesurees {measured:.2f} m² "
                    f"= {residual:.2f} m².")
                rooms.append(zone)

        # Anti double comptage : deux MESURES qui se recouvrent reellement
        # (faux libelle scinde, placard dans une chambre) -> on retire
        # l'intersection de la plus petite et on trace l'ajustement.
        # Attention : deux regions segmentees DISTINCTES sont disjointes par
        # construction ; comparer leurs boites englobantes effacait la piece
        # voisine (CUISINE 7,3 m2 -> 0,0 m2 sous la boite du SALON en L).
        for i, a in enumerate(rooms):
            for b in rooms[i + 1:]:
                # Les mesures bornees par l'enveloppe se recouvrent par
                # construction : la repartition au prorata ci-dessus a deja
                # garanti l'absence de double comptage.
                if a in env_rooms or b in env_rooms:
                    continue
                if not (a.area_segmented_m2 and b.area_segmented_m2):
                    continue
                ra, rb = region_of_room.get(id(a)), region_of_room.get(id(b))
                if ra is not None and rb is not None:
                    if ra["id"] == rb["id"]:
                        continue          # meme region : deja reparti au prorata
                    inter_px = int(np.count_nonzero(
                        np.logical_and(ra["mask"] > 0, rb["mask"] > 0)))
                    if inter_px <= 0:
                        continue          # regions disjointes : faux positif
                    inter = inter_px
                    inter_area = inter_px / (scale ** 2)
                else:
                    if not (a.region_bbox and b.region_bbox):
                        continue
                    inter = _box_intersection(a.region_bbox, b.region_bbox)
                    if inter <= 0:
                        continue
                    inter_area = inter / (scale ** 2)
                small, big = (a, b) if a.area_segmented_m2 <= b.area_segmented_m2 else (b, a)
                share = inter / max(
                    small.region_bbox[2] * small.region_bbox[3], 1)
                if share < 0.15:
                    continue
                small.area_segmented_m2 = round(
                    max(0.0, small.area_segmented_m2 - inter_area), 2)
                small.warnings.append(
                    f"Recouvrement avec « {big.label} » : {inter_area:.2f} m² "
                    f"retires pour eviter un double comptage.")
        return rooms

    @staticmethod
    def room_boxes(wall_mask, labels: Sequence[Dict[str, Any]],
                   bbox: Optional[Tuple[int, int, int, int]],
                   max_open_ratio: float = 0.6) -> List[Dict[str, Any]]:
        """Boite de piece ancree sur son libelle (4 rayons vers les murs).

        Methode volontairement conservatrice, independante du scellement des
        portes : depuis le libelle, on cherche le premier mur a gauche, a
        droite, en haut et en bas. Un cote sans mur proche est declare
        OUVERT (piece non fermee) : aucune surface n'est inventee.

        Retourne, par libelle, la boite en PIXELS et ses distances ; la
        conversion en metres se fait avec l'echelle calibree.
        """
        import numpy as np

        H, W = wall_mask.shape
        bx, by, bw, bh = bbox or (0, 0, W, H)
        out: List[Dict[str, Any]] = []

        for li, lb in enumerate(labels):
            x, y, w, h = (int(v) for v in lb["bbox"])
            cx = min(max(1, x + w // 2), W - 2)
            cy = min(max(1, y + h // 2), H - 2)

            # 5 rayons par direction (medianes) : un trou de porte sur un
            # rayon isole ne fausse pas la mesure.
            ys = [min(max(1, cy + dy), H - 2)
                  for dy in (-h, -h // 2, 0, h // 2, h)]
            xs = [min(max(1, cx + dx), W - 2)
                  for dx in (-w, -w // 2, 0, w // 2, w)]

            def dist_left(yy):
                row = wall_mask[yy, :cx]
                idx = np.flatnonzero(row)
                return int(cx - idx[-1]) if idx.size else None

            def dist_right(yy):
                row = wall_mask[yy, cx + 1:]
                idx = np.flatnonzero(row)
                return int(idx[0] + 1) if idx.size else None

            def dist_up(xx):
                col = wall_mask[:cy, xx]
                idx = np.flatnonzero(col)
                return int(cy - idx[-1]) if idx.size else None

            def dist_down(xx):
                col = wall_mask[cy + 1:, xx]
                idx = np.flatnonzero(col)
                return int(idx[0] + 1) if idx.size else None

            def robust(getter, probes):
                vals = [v for v in (getter(p) for p in probes)
                        if v is not None and v > 2]
                return int(np.median(vals)) if vals else None

            dl = robust(dist_left, ys)
            dr = robust(dist_right, ys)
            du = robust(dist_up, xs)
            dd = robust(dist_down, xs)

            open_sides = []
            if dl is None or dl > max_open_ratio * bw:
                open_sides.append("gauche")
            if dr is None or dr > max_open_ratio * bw:
                open_sides.append("droite")
            if du is None or du > max_open_ratio * bh:
                open_sides.append("haut")
            if dd is None or dd > max_open_ratio * bh:
                open_sides.append("bas")

            # Mesure de repli : un cote ouvert (grande ouverture, absence de
            # cloison) est borne par l'ENVELOPPE du batiment. Aucune piece ne
            # peut depasser l'enveloppe : on ne fabrique donc aucune surface,
            # on borne une mesure deja ancree sur les murs.
            env = {"gauche": cx - bx, "droite": (bx + bw) - cx,
                   "haut": cy - by, "bas": (by + bh) - cy}
            res = {"gauche": dl, "droite": dr, "haut": du, "bas": dd}
            clamped = []
            for side in open_sides:
                res[side] = env[side]
                clamped.append(side)
            box_env = None
            if clamped and all(isinstance(v, int) and v > 2 for v in res.values()):
                box_env = (cx - res["gauche"], cy - res["haut"],
                           res["gauche"] + res["droite"],
                           res["haut"] + res["bas"])

            out.append({
                "label_index": li,
                "point": (cx, cy),
                "dist": {"left": dl, "right": dr, "up": du, "down": dd},
                "open_sides": open_sides,
                "clamped_sides": clamped,
                "box_env": box_env,
                "box": None if open_sides or None in (dl, dr, du, dd) else
                       (cx - dl, cy - du, dl + dr, du + dd),
            })
        return out

    # ------------------------------------------------------------------
    @staticmethod
    def render_overlay(img_bgr, rep: SurfaceReport, out_path: Path) -> str:
        import cv2
        canvas = img_bgr.copy()
        if rep.building_bbox:
            x, y, w, h = rep.building_bbox
            cv2.rectangle(canvas, (x, y), (x + w, y + h), (200, 120, 0), 2)
        for r in rep.rooms:
            if r.region_bbox:
                x, y, w, h = r.region_bbox
                color = ((0, 170, 0) if r.source == "cote_explicite"
                         else (0, 140, 255) if r.source == "segmentation" else (0, 0, 255))
                cv2.rectangle(canvas, (x, y), (x + w, y + h), color, 2)
            x, y, w, h = r.bbox
            txt = f"{r.label}: {r.area_m2 if r.area_m2 is not None else '?'} m2"
            cv2.putText(canvas, txt, (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (30, 30, 200), 2, cv2.LINE_AA)
        footer = (f"{rep.level} | total {rep.total_area_m2} m2 | echelle "
                  f"{rep.scale_px_per_m or '?'} px/m (conf {rep.scale_confidence})")
        cv2.putText(canvas, footer, (10, canvas.shape[0] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2, cv2.LINE_AA)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_path), canvas)
        return str(out_path)
