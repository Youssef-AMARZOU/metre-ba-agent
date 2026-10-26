#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extractors/vlm_router.py -- Routeur VLM multimodal freemium (OpenAI-compatible).

Backends supportes (aucune API payante obligatoire) :
  - Ollama local   : http://localhost:11434/v1  (qwen2.5-vl:7b)   [defaut]
  - Groq Free Tier : https://api.groq.com/openai/v1
                     (meta-llama/llama-4-scout-17b-16e-instruct) si GROQ_API_KEY

Le VLM recoit UNIQUEMENT des crops ROI haute resolution (PNG base64) et
retourne un JSON strict `{"elements": [...]}`. Il n'effectue AUCUN calcul :
les volumes et poids sont la responsabilite exclusive de MoroccanBETEngine.

Variables d'environnement :
  PLANBA_VLM_BACKEND = auto | ollama | groq | off
  OLLAMA_BASE_URL    (defaut http://localhost:11434/v1)
  OLLAMA_VLM_MODEL   (defaut qwen2.5-vl:7b)
  GROQ_API_KEY, GROQ_VLM_MODEL (defaut meta-llama/llama-4-scout-17b-16e-instruct)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_VLM_MODEL = os.environ.get("OLLAMA_VLM_MODEL", "qwen2.5-vl:7b")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_VLM_MODEL = os.environ.get(
    "GROQ_VLM_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

VLM_SYSTEM_PROMPT = (
    "Tu es un ingénieur structure BTP expert en lecture de plans de béton "
    "armé marocains (BAEL 91, RPS 2000 v2011). Tu LIS uniquement : tu ne "
    "calcules jamais de volume ni de poids."
)

VLM_USER_PROMPT = (
    "Analyse cet extrait de plan (tableau de ferraillage, nomenclature, "
    "tableau de poteaux/semelles ou détail d'ouvrage) et retourne UNIQUEMENT "
    "un objet JSON valide de la forme : "
    '{"elements": [{"category": "semelle|poteau|poutre|longrine|chainage|'
    'voile|dalle_pleine|nomenclature", "label": "S1 / P4 / N12", '
    '"quantity": 1, "dimensions": {"b": 0.25, "h": 0.40, "L": 4.50}, '
    '"rebar_spec": ["6HA14 L=4.80", "Cad HA8 e=15"], '
    '"grid_axes": ["A-1"]}]}. '
    "Pour les semelles utilise les clés A, B, H (et h_glacis si trapèze). "
    "Convertis toutes les dimensions de coffrage en mètres (25 cm -> 0.25). "
    "Les espacements de cadres restent en cm (e=15). "
    "Si une valeur est illisible, omets la clé : n'invente jamais."
)


@dataclass
class VLMBackend:
    name: str
    base_url: str
    model: str
    api_key: str = ""

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"


def _extract_json(text: str) -> Optional[dict]:
    """Tolere un JSON entoure de texte ou de ```json ... ```."""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


class VLMRouter:
    """Client HTTP minimal (requests) OpenAI-compatible, sans SDK payant."""

    def __init__(self, backend: Optional[str] = None,
                 timeout_s: float = 90.0, max_tokens: int = 2048):
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens
        self.backend: Optional[VLMBackend] = self._resolve(
            backend or os.environ.get("PLANBA_VLM_BACKEND", "auto"))
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------
    def _resolve(self, choice: str) -> Optional[VLMBackend]:
        choice = (choice or "auto").lower()
        groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        if choice == "off":
            return None
        if choice == "groq":
            return (VLMBackend("groq", GROQ_BASE_URL, GROQ_VLM_MODEL, groq_key)
                    if groq_key else None)
        if choice == "ollama":
            return VLMBackend("ollama", OLLAMA_BASE_URL, OLLAMA_VLM_MODEL)
        # auto : Ollama local en priorite (100% hors-ligne), sinon Groq free
        if self._ollama_alive():
            return VLMBackend("ollama", OLLAMA_BASE_URL, OLLAMA_VLM_MODEL)
        if groq_key:
            return VLMBackend("groq", GROQ_BASE_URL, GROQ_VLM_MODEL, groq_key)
        return None

    @staticmethod
    def _ollama_alive(timeout: float = 1.5) -> bool:
        try:
            import requests
            root = OLLAMA_BASE_URL.rsplit("/v1", 1)[0]
            r = requests.get(root + "/api/tags", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False

    @property
    def available(self) -> bool:
        return self.backend is not None

    # ------------------------------------------------------------------
    @staticmethod
    def encode_png_b64(img_bgr) -> Optional[str]:
        import cv2
        ok, buf = cv2.imencode(".png", img_bgr)
        if not ok:
            return None
        return base64.b64encode(buf.tobytes()).decode("utf-8")

    def chat_json(self, img_bgr, prompt: str = VLM_USER_PROMPT,
                  system: str = VLM_SYSTEM_PROMPT) -> Optional[dict]:
        """Envoie un crop (BGR) + prompt, retourne le JSON parse ou None."""
        self.last_error = None
        if self.backend is None:
            self.last_error = "Aucun backend VLM (Ollama absent, GROQ_API_KEY vide)."
            return None
        b64 = self.encode_png_b64(img_bgr)
        if b64 is None:
            self.last_error = "Encodage PNG impossible."
            return None

        payload: Dict[str, Any] = {
            "model": self.backend.model,
            "temperature": 0.0,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ]},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.backend.api_key:
            headers["Authorization"] = f"Bearer {self.backend.api_key}"

        try:
            import requests
            resp = requests.post(self.backend.chat_url, json=payload,
                                 headers=headers, timeout=self.timeout_s)
            if resp.status_code != 200:
                self.last_error = f"HTTP {resp.status_code} : {resp.text[:200]}"
                logger.warning("VLM %s : %s", self.backend.name, self.last_error)
                return None
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            logger.warning("VLM %s injoignable : %s", self.backend.name, e)
            return None

        parsed = _extract_json(content if isinstance(content, str)
                               else json.dumps(content))
        if parsed is None:
            self.last_error = "Réponse VLM non-JSON."
        return parsed

    # ------------------------------------------------------------------
    @staticmethod
    def parse_elements(raw: Optional[dict], page_num: int, bbox: tuple,
                       source_engine: str = "cv_vlm_roi",
                       confidence: float = 0.92) -> List:
        """JSON VLM -> DrawingElement (validation stricte des types)."""
        from core.bet_contracts import CATEGORIES, DrawingElement, category_from_label
        out: List[DrawingElement] = []
        if not isinstance(raw, dict):
            return out
        for el in raw.get("elements", []) or []:
            if not isinstance(el, dict):
                continue
            label = str(el.get("label") or "").strip().upper()
            if not label:
                continue
            cat = str(el.get("category") or "").strip().lower()
            if cat not in CATEGORIES or cat in ("autre", "nomenclature"):
                inferred = category_from_label(label)
                cat = inferred if inferred != "autre" else (cat or "autre")
            dims: Dict[str, float] = {}
            for k, v in (el.get("dimensions") or {}).items():
                try:
                    dims[str(k)] = float(str(v).replace(",", "."))
                except (TypeError, ValueError):
                    continue
            specs = [str(s).strip() for s in (el.get("rebar_spec") or [])
                     if str(s).strip()]
            axes = [str(a).strip().upper() for a in (el.get("grid_axes") or [])
                    if str(a).strip()]
            try:
                qty = int(el.get("quantity", 1) or 1)
            except (TypeError, ValueError):
                qty = 1
            out.append(DrawingElement(
                category=cat, label=label, dimensions=dims,
                rebar_spec=specs, quantity=qty, grid_axes=axes,
                page_num=page_num, bbox=tuple(bbox), confidence=confidence,
                source_engine=source_engine,
            ))
        return out
