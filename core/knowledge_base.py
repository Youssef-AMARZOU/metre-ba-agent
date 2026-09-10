#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/knowledge_base.py — Module RAG (Retrieval-Augmented Generation)
pour les regles de metr\u00e9e BTP.

Base de connaissances locale interrogeable par le moteur de calcul
ou l'interface de reporting. Utilise ChromaDB + sentence-transformers
pour la vectorisation locale, et Groq API pour la synth\u00e8se LLM.

Source principale : "Le M\u00e9tr\u00e9 : B\u00e2timent et Travaux Publics"
par Michel Manteau (7e \u00e9dition, 1993).
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

_GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
_GROQ_MODEL = "llama-3.3-70b-versatile"
_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Modele d'embedding local (pas de dependance cloud)
_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Chemin de stockage ChromaDB
_DB_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge_base_db"

# ============================================================================
# Chunking BTP par mots-cles
# ============================================================================

# Mots-cles BTP servant de frontieres de chunk (titres de chapitres/sections)
_BTP_SPLIT_POINTS = [
    r"TERRASSEMENTS?",
    r"FOUILLES?",
    r"D[E\u00c9]BLA[I\u00ce]S?",
    r"REMBLA[I\u00ce]S?",
    r"FONDATIONS?",
    r"BASSE[S]?\s*FONDATIONS?",
    r"FONDATIONS?\s*PROFONDES?",
    r"B\u00c9TON\s*ARM\u00c9",
    r"B\u00c9TON",
    r"COFFRAGES?",
    r"COFFRAGE\s*(HORIZONTAL|VERTICAL|CINTR\u00c9)",
    r"ACIERS?",
    r"FERRAILLAGE",
    r"SEMM?ELLES?",
    r"POTEAUX?",
    r"POUTRES?",
    r"LONGRINES?",
    r"CHAINAGES?",
    r"VOILES?",
    r"DALLES?",
    r"ESCALIERS?",
    r"MA\u00c7ONNERIE",
    r"MURS?",
    r"\u00c9TANCH\u00c9IT\u00c9",
    r"ENDUITS?",
    r"PEINTURE",
    r"CHARPENTE",
    r"MENUISERIE",
    r"PLOMBERIE",
    r"\u00c9LECTRICIT\u00c9",
    r"HOURDIS",
    r"LINTAUX?",
    r"RADIER",
    r"QUANTIT\u00c9S?\s*\u00c0\s*DUIRE",
    r"D\u00c9DUCTION\s*DES\s*VIDES",
    r"MAJORATION",
    r"RECOUVREMENT",
    r"ENROBAGE",
    r"FORMULES?\s*(DE|DU)",
    r"R\u00c9GL?ES?\s*(DE|DU)",
    r"ABR\u00c9VIATIONS?",
    r"UNITS?",
    r"M\u00c9TRER?",
    r"DIRE",
    r"MEMOIRE",
    r"DEVIS",
    r"BORDEREAU",
    r"S\u00c9RIE\s*CENTRALE",
]

# Patterns pour detecter les categories BTP dans un chunk
_CATEGORY_PATTERNS = {
    "SEMELLE": re.compile(r"semelles?|fondations?\s*(basses?|isol\u00e9es?)", re.I),
    "POTEAU": re.compile(r"poteaux?|f\u00fbts?\s*de\s*poteau", re.I),
    "POUTRE": re.compile(r"poutres?|poutrelles?|linteaux?", re.I),
    "LONGRINE": re.compile(r"longrines?", re.I),
    "CHAINAGE": re.compile(r"chainages?|chainage\s*d'angle", re.I),
    "MUR": re.compile(r"murs?|cloisons?|refends?|bandes?\s*noy\u00e9es?", re.I),
    "VOILE": re.compile(r"voiles?|voile\s*(porteur|de\s*fachade)", re.I),
    "DALLE": re.compile(r"dalles?|planchers?|hourdis", re.I),
    "ESCALIER": re.compile(r"escaliers?|marches?|paliers?", re.I),
    "TERRASSEMENT": re.compile(r"terrassements?|fouilles?|d\u00e9blais?|remblais?|d\u00e9capage|rigoles?", re.I),
    "MACONNERIE": re.compile(r"ma\u00e7onnerie|pierres?|briques?", re.I),
    "COFFRAGE": re.compile(r"coffrages?|coffrage", re.I),
    "BETON": re.compile(r"b\u00e9ton|dosage|confection", re.I),
    "FERRAILLAGE": re.compile(r"ferraillage|acier|armatures?|tor|ha\d|cadres?|\u00e9triers?", re.I),
    "ETANCHEITE": re.compile(r"\u00e9tanch\u00e9it\u00e9|membrane|couvertine", re.I),
    "ENDUIT": re.compile(r"enduits?|ragr\u00e9age|pl\u00e2tre", re.I),
    "PEINTURE": re.compile(r"peinture|peintures?", re.I),
    "MENUISERIE": re.compile(r"menuiserie|portes?|fen\u00eatres?|volets?", re.I),
    "CHARPENTE": re.compile(r"charpente|fer|bois", re.I),
}

# ============================================================================
# Ingestion et chunking
# ============================================================================

def _detect_categories(text: str) -> list[str]:
    """Detecte les categories BTP presentes dans un chunk de texte."""
    cats = []
    for cat, pattern in _CATEGORY_PATTERNS.items():
        if pattern.search(text):
            cats.append(cat)
    return cats if cats else ["GENERAL"]


def _detect_categories_with_context(
    text: str, context_before: str
) -> list[str]:
    """Detecte les categories en combinant le chunk et le contexte precedent."""
    combined = (context_before[-1000:] if context_before else "") + " " + text
    return _detect_categories(combined)


def _estimate_page(text_before: str) -> int:
    """Estime le numero de page approximatif depuis le texte OCR."""
    # Chercher les marqueurs de page "--- PAGE XX ---"
    pages = re.findall(r"---\s*PAGE\s+(\d+)\s*---", text_before)
    if pages:
        return int(pages[-1])
    # Compter les sauts de page
    return max(1, text_before.count("\f") + 1)


def ingest_manteau_ocr(txt_path: str) -> list[dict[str, Any]]:
    """Lit la sortie OCR du livre Manteau et produit des chunks metier.

    Strategie de chunking :
    - Decoupage aux frontieres BTP (titres de chapitres/sections)
    - Chaque chunk contient ~500-2000 caracteres
    - Metadonnees : source, categories, page_approx, chunk_id

    Args:
        txt_path: chemin vers le fichier .txt OCR

    Returns:
        Liste de chunks {"text": str, "metadata": dict}
    """
    path = Path(txt_path)
    if not path.exists():
        logger.error("Fichier OCR introuvable : %s", txt_path)
        return []

    raw = path.read_text(encoding="utf-8", errors="replace")

    # Construire le pattern de split combine
    split_rx = re.compile(
        r"(?=\b(?:" + "|".join(_BTP_SPLIT_POINTS) + r")\b)",
        re.I,
    )

    # Decouper
    parts = split_rx.split(raw)
    parts = [p for p in parts if p and p.strip()]

    # Fusionner les petits fragments en chunks significatifs (300-2000 chars)
    merged = []
    buffer = ""
    for part in parts:
        if len(buffer) + len(part) < 2000:
            buffer += "\n\n" + part if buffer else part
        else:
            if buffer:
                merged.append(buffer)
            buffer = part
    if buffer:
        merged.append(buffer)

    chunks = []
    text_before = ""

    for i, part in enumerate(merged):
        chunks.append(_make_chunk(part, i, text_before))
        text_before += part + "\n"

    logger.info("Ingestion de %d chunks depuis %s", len(chunks), txt_path)
    return chunks


def _make_chunk(text: str, chunk_id: int, text_before: str) -> dict[str, Any]:
    """Cree un chunk avec metadonnees."""
    cats = _detect_categories_with_context(text, text_before)
    page = _estimate_page(text_before)
    return {
        "text": text[:3000],  # Limiter a 3000 chars
        "metadata": {
            "source": "Manteau",
            "categories": cats,
            "primary_category": cats[0],
            "page_approx": page,
            "chunk_id": chunk_id,
        },
    }


# ============================================================================
# Vector Store local (ChromaDB)
# ============================================================================

class MetreKnowledgeBase:
    """Base de connaissances vectorielle locale pour les regles de metree."""

    def __init__(self, db_path: str | None = None):
        """Initialise ChromaDB et le modele d'embedding."""
        import chromadb
        from sentence_transformers import SentenceTransformer

        self._db_path = Path(db_path) if db_path else _DB_DIR
        self._db_path.mkdir(parents=True, exist_ok=True)

        # Charger le modele d'embedding local
        logger.info("Chargement du modele d'embedding : %s", _EMBED_MODEL)
        self._embed_model = SentenceTransformer(_EMBED_MODEL)

        # Initialiser ChromaDB avec persistance
        self._client = chromadb.PersistentClient(path=str(self._db_path))
        self._collection = self._client.get_or_create_collection(
            name="metre_rules",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "Base de connaissances initialisee (%d chunks en base)",
            self._collection.count(),
        )

    def ingest(self, chunks: list[dict[str, Any]], batch_size: int = 100) -> int:
        """Ingeere des chunks dans la base vectorielle.

        Args:
            chunks: Liste de {"text": str, "metadata": dict}
            batch_size: Taille des lots pour l'embedding

        Returns:
            Nombre de chunks ingerees
        """
        if not chunks:
            return 0

        # Preparer les donnees
        texts = [c["text"] for c in chunks]
        metadatas = []
        ids = []

        for i, c in enumerate(chunks):
            meta = c.get("metadata", {})
            # ChromaDB exige des valeurs string/int/float/bool
            clean_meta = {
                "source": str(meta.get("source", "unknown")),
                "primary_category": str(meta.get("primary_category", "GENERAL")),
                "categories": ",".join(meta.get("categories", ["GENERAL"])),
                "page_approx": int(meta.get("page_approx", 0)),
                "chunk_id": int(meta.get("chunk_id", i)),
            }
            metadatas.append(clean_meta)
            ids.append(f"chunk_{meta.get('chunk_id', i)}")

        # Embed et inserer par lots
        ingested = 0
        for start in range(0, len(texts), batch_size):
            end = min(start + batch_size, len(texts))
            batch_texts = texts[start:end]
            batch_meta = metadatas[start:end]
            batch_ids = ids[start:end]

            # Generer les embeddings
            embeddings = self._embed_model.encode(
                batch_texts, show_progress_bar=False
            ).tolist()

            self._collection.add(
                documents=batch_texts,
                embeddings=embeddings,
                metadatas=batch_meta,
                ids=batch_ids,
            )
            ingested += len(batch_texts)

        logger.info("Ingere %d chunks dans la base vectorielle", ingested)
        return ingested

    def search(
        self,
        query: str,
        category: str | None = None,
        n_results: int = 3,
    ) -> list[dict[str, Any]]:
        """Recherche hybride (vectorielle + filtre metadonnee).

        Args:
            query: Question en langage naturel
            category: Filtre par categorie BTP (ex: "SEMELLE", "POUTRE")
            n_results: Nombre de resultats a retourner

        Returns:
            Liste de chunks pertinents avec scores
        """
        if self._collection.count() == 0:
            logger.warning("Base de connaissances vide")
            return []

        # Embedding de la requete
        query_embedding = self._embed_model.encode([query]).tolist()

        # Filtre par categorie si specifie
        # Utiliser primary_category pour un filtre fiable (pas $contains sur CSV)
        # Si la base est petite, ne pas filtrer (tous les chunks sont relates)
        where_filter = None
        if category and self._collection.count() > 10:
            where_filter = {
                "primary_category": category.upper(),
            }

        # Recherche
        try:
            results = self._collection.query(
                query_embeddings=query_embedding,
                n_results=n_results,
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.error("Erreur de recherche : %s", e)
            # Filtre sans categorie en fallback
            results = self._collection.query(
                query_embeddings=query_embedding,
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )

        # Formater les resultats
        chunks = []
        if results and results.get("documents"):
            docs = results["documents"][0]
            metas = results["metadatas"][0]
            dists = results["distances"][0]

            for doc, meta, dist in zip(docs, metas, dists):
                chunks.append({
                    "text": doc,
                    "metadata": meta,
                    "relevance_score": round(1 - dist, 4),  # cosine → similarite
                })

        return chunks

    @property
    def count(self) -> int:
        """Nombre de chunks dans la base."""
        return self._collection.count()

    def clear(self) -> None:
        """Vide la base de connaissances."""
        self._client.delete_collection("metre_rules")
        self._collection = self._client.get_or_create_collection(
            name="metre_rules",
            metadata={"hnsw:space": "cosine"},
        )


# ============================================================================
# Moteur RAG complet (Recherche + LLM)
# ============================================================================

_SYSTEM_PROMPT = (
    "Tu es un expert en m\u00e9tr\u00e9 BTP francophone/marocain, sp\u00e9cialis\u00e9 "
    "dans les r\u00e8gles de calcul des ouvrages en b\u00e9ton arm\u00e9.\n\n"
    "R\u00e8gles strictes :\n"
    "1. R\u00e9ponds EXCLUSIVEMENT en te basant sur le contexte fourni.\n"
    "2. Cite la r\u00e8gle exacte avec les formules et valeurs num\u00e9riques.\n"
    "3. Si le contexte ne contient pas la r\u00e9ponse, dis "
    "'R\u00e8gle non trouv\u00e9e dans la base de donn\u00e9es'.\n"
    "4. Pour les formules, utilise le format : "
    "Volume = L \u00d7 l \u00d7 h, Surface = L \u00d7 H, etc.\n"
    "5. Pr\u00e9cise toujours les unit\u00e9s (M3, M2, KG, ML).\n"
    "6. Mentionne la source (Manteau, S\u00e9rie Centrale 1985, BAEL 91) "
    "quand applicable.\n"
)


class MetreExpertRAG:
    """Moteur RAG : recherche de regles + synthese LLM."""

    def __init__(self, kb: MetreKnowledgeBase | None = None):
        """Initialise le moteur RAG.

        Args:
            kb: Base de connaissances existante. Si None, en cree une nouvelle.
        """
        self._kb = kb or MetreKnowledgeBase()

    def query_rule(
        self,
        element_category: str,
        question: str,
        use_llm: bool = True,
    ) -> str:
        """Interroge la base de connaissances pour une regle metier.

        Args:
            element_category: Categorie de l'element (ex: "SEMELLE", "POUTRE")
            question: Question en langage naturel
            use_llm: Utiliser le LLM pour la synthese (True) ou retourner
                     les chunks bruts (False)

        Returns:
            Reponse textuelle basee sur les regles de metree
        """
        # Recherche vectorielle avec filtre categorie
        chunks = self._kb.search(
            query=question,
            category=element_category,
            n_results=3,
        )

        if not chunks:
            return (
                "R\u00e8gle non trouv\u00e9e dans la base de donn\u00e9es. "
                f"Aucun r\u00e9sultat pour la categorie '{element_category}'."
            )

        # Construire le contexte
        context_parts = []
        for i, chunk in enumerate(chunks, 1):
            meta = chunk.get("metadata", {})
            page = meta.get("page_approx", "?")
            score = chunk.get("relevance_score", 0)
            context_parts.append(
                f"[Source: Manteau, page ~{page}, pertinence: {score}]\n"
                f"{chunk['text']}"
            )
        context = "\n\n---\n\n".join(context_parts)

        if not use_llm:
            # Retourner les chunks bruts
            return context

        # Synthese LLM via Groq
        return self._synthesize(context, question)

    def _synthesize(self, context: str, question: str) -> str:
        """Synthetise une reponse via Groq API."""
        import json
        import urllib.request
        import urllib.error

        if not _GROQ_API_KEY:
            return (
                "Cl\u00e9 API Groq non configur\u00e9e. "
                "Voici les r\u00e8gles brutes :\n\n" + context
            )

        user_msg = (
            f"CONTEXTE (extrait du livre de Michel Manteau) :\n"
            f"---\n{context}\n---\n\n"
            f"QUESTION : {question}\n\n"
            f"R\u00e9ponse (en francais, avec formules et unit\u00e9s) :"
        )

        payload = json.dumps({
            "model": _GROQ_MODEL,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.1,
            "max_tokens": 1024,
        }).encode("utf-8")

        headers = {
            "Authorization": f"Bearer {_GROQ_API_KEY}",
            "Content-Type": "application/json",
        }

        req = urllib.request.Request(_GROQ_URL, data=payload, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            logger.error("Groq API HTTP %d: %s", e.code, body[:300])
            return (
                f"Erreur API Groq ({e.code}). R\u00e8gles brutes :\n\n" + context
            )
        except Exception as e:
            logger.error("Erreur Groq : %s", e)
            return "Erreur de connexion LLM. R\u00e8gles brutes :\n\n" + context


# ============================================================================
# Utilitaire pour le generateur de rapports
# ============================================================================

# Ratios et regles de base prets a l'emploi
_DEFAULT_RULES = {
    "SEMELLE": {
        "terrassement_fouille": "Volume = (A+0.20) \u00d7 (B+0.20) \u00d7 (H+0.20) M3",
        "beton_proprete": "Volume = (A+0.10) \u00d7 (B+0.10) \u00d7 0.10 M3",
        "beton_arme": "Volume = A \u00d7 B \u00d7 H M3",
        "coffrage_joues": "Surface = 2 \u00d7 (A+B) \u00d7 H M2",
        "enrobage_cm": 5,
        "ratio_acier_kg_m3": "80-120",
        "recouvrement": "50 \u00d7 phi (Source: Manteau)",
    },
    "POTEAU": {
        "beton_arme": "Volume = a \u00d7 b \u00d7 Hauteur M3",
        "coffrage": "Surface = 2 \u00d7 (a+b) \u00d7 Hauteur M2",
        "enrobage_cm": 3,
        "ratio_acier_kg_m3": "100-150",
        "recouvrement": "50 \u00d7 phi (Source: Manteau)",
    },
    "POUTRE": {
        "beton_arme": "Volume = b \u00d7 h \u00d7 Port\u00e9e M3",
        "coffrage_joues": "Surface = 2 \u00d7 h \u00d7 Port\u00e9e M2",
        "coffrage_sous_face": "Surface = b \u00d7 Port\u00e9e M2",
        "enrobage_cm": 3,
        "ratio_acier_kg_m3": "120-180",
        "recouvrement": "50 \u00d7 phi (Source: Manteau)",
    },
    "LONGRINE": {
        "beton_arme": "Volume = b \u00d7 h \u00d7 Longueur M3",
        "coffrage_joues": "Surface = 2 \u00d7 h \u00d7 Longueur M2",
        "enrobage_cm": 3,
        "ratio_acier_kg_m3": "100-150",
    },
    "CHAINAGE": {
        "beton_arme": "Volume = b \u00d7 h \u00d7 Longueur M3",
        "coffrage_joues": "Surface = 2 \u00d7 h \u00d7 Longueur M2",
        "enrobage_cm": 3,
    },
    "DALLE": {
        "beton_arme": "Volume = Surface \u00d7 \u00e9paisseur M3",
        "coffrage_sous_face": "Surface M2",
        "enrobage_cm": 2,
        "ratio_acier_kg_m3": "60-100",
    },
    "VOILE": {
        "beton_arme": "Volume = Surface \u00d7 \u00e9paisseur M3",
        "coffrage": "Surface M2",
        "enrobage_cm": 2,
        "ratio_acier_kg_m3": "80-120",
    },
    "TERRASSEMENT": {
        "fouille": "Volume = L \u00d7 l \u00d7 (P + 0.20) M3 (encaissement)",
        "remblai": "Volume = Volume fouille - Volume fondation M3",
        "deblai": "Volume = Volume terre-plein - Volume construction M3",
    },
    "COFFRAGE": {
        "horizontal": "Surface = surface de contact beton M2",
        "vertical": "Surface = p\u00e9rim\u00e8tre \u00d7 hauteur M2",
        "majoration_hauteur": "Majoration pour H > 3.00 m (S\u00e9rie Centrale)",
    },
    "FERRAILLAGE": {
        "fourniture": "Poids = \u03a3(nb \u00d7 longueur \u00d7 masse_lin\u00e9aire) KG",
        "facon_pose": "Fourniture + mise en place (S\u00e9rie Centrale 3/020-3/023)",
        "recouvrement": "50 \u00d7 phi (Manteau)",
        "enrobage_min_cm": "5 (fondation), 3 (structure), 2 (dalle/voile)",
    },
}


def get_ratio_or_rule_fallback(element_type: str) -> dict[str, str]:
    """Retourne les regles/ ratios par defaut pour un type d'element.

    Utile pour le generateur de rapport quand les donnees sont manquantes.
    Le rapport peut appeler cette fonction pour inserer les hypotheses
    reglementaires automatiquement.

    Args:
        element_type: Type d'element (ex: "SEMELLE", "POUTRE")

    Returns:
        Dict avec les regles applicables
    """
    rules = _DEFAULT_RULES.get(element_type.upper(), {})
    if not rules:
        return {
            "error": f"Regles non trouvees pour '{element_type}'",
            "source": "Knowledge base vide",
        }
    return {
        "element_type": element_type.upper(),
        "rules": rules,
        "source": "Manteau / Serie Centrale 1985",
        "note": "Valeurs a verifier sur le plan du projet",
    }


# ============================================================================
# Fonction d'initialisation rapide
# ============================================================================

def init_knowledge_base(
    ocr_path: str | None = None,
    force_rebuild: bool = False,
) -> MetreKnowledgeBase:
    """Initialise la base de connaissances.

    Args:
        ocr_path: Chemin vers le fichier OCR Manteau. Si None, utilise
                  la base existante.
        force_rebuild: Reconstruire la base depuis le fichier OCR

    Returns:
        Instance de MetreKnowledgeBase pret a l'emploi
    """
    kb = MetreKnowledgeBase()

    if force_rebuild and ocr_path:
        kb.clear()
        chunks = ingest_manteau_ocr(ocr_path)
        if chunks:
            kb.ingest(chunks)
            logger.info("Base reconstruite avec %d chunks", kb.count)
    elif kb.count == 0 and ocr_path:
        chunks = ingest_manteau_ocr(ocr_path)
        if chunks:
            kb.ingest(chunks)
            logger.info("Base initialisee avec %d chunks", kb.count)

    return kb
