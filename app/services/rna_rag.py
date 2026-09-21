import logging
import math
import os
import re
import unicodedata
from collections import Counter
from typing import Any, Dict, List, Optional

import numpy as np
from google.cloud import bigquery

from agents.agent_config import get_gemini_client

logger = logging.getLogger("RNARagService")

# French stopwords to filter common syntactic noise while preserving domain terms
FRENCH_STOPWORDS = {
    "de",
    "la",
    "le",
    "et",
    "les",
    "des",
    "en",
    "un",
    "une",
    "du",
    "pour",
    "dans",
    "qui",
    "que",
    "sur",
    "au",
    "aux",
    "par",
    "ce",
    "cette",
    "ces",
    "sa",
    "son",
    "ses",
    "leur",
    "leurs",
    "ou",
    "mais",
    "donc",
    "or",
    "ni",
    "car",
    "avec",
    "sans",
    "sous",
    "vers",
    "chez",
    "d",
    "l",
    "l'",
    "d'",
    "qu'",
    "s'",
    "n'",
    "a",
    "à",
    "y",
    "se",
    "si",
    "te",
    "me",
    "nous",
    "vous",
    "ils",
    "elles",
    "on",
}


def strip_accents(text: Any) -> str:
    """Strips combining diacritics and accents using Unicode NFKD normalization.

    Args:
        text: Input string.

    Returns:
        String without accents.
    """
    if not isinstance(text, str) or not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_tokens(text: Any) -> List[str]:
    """Tokenizes text, strips accents, and filters French stopwords.

    Args:
        text: Input string to tokenize.

    Returns:
        List of cleaned lowercase tokens of length >= 2.
    """
    if not isinstance(text, str) or not text:
        return []
    ascii_clean = strip_accents(text).lower()
    clean = re.sub(r"[^a-z0-9]", " ", ascii_clean)
    return [t for t in clean.split() if len(t) >= 2 and t not in FRENCH_STOPWORDS]


class BM25Ranker:
    """Field-weighted, accent-insensitive BM25 ranker for associations.

    Follows the Robertson-Spärck Jones BM25 formulation calibrated in the
    Gironde RNA pilot:
    - title_boost: 3.0
    - waldec_boost: 2.0
    - objet_boost: 1.0
    - k1: 1.5, b: 0.75
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

    def rank(
        self,
        candidates: List[Dict[str, Any]],
        query: str,
        title_boost: float = 3.0,
        waldec_boost: float = 2.0,
        objet_boost: float = 1.0,
        top_k: int = 10,
        threshold: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Ranks candidate associations using field-weighted BM25.

        Args:
            candidates: List of candidate row dictionaries from BigQuery.
            query: The search query text.
            title_boost: Weight boost for matches in title.
            waldec_boost: Weight boost for matches in WALDEC category.
            objet_boost: Weight boost for matches in description.
            top_k: Max results to return.
            threshold: Minimum score threshold (default 0.0).

        Returns:
            List of candidate dictionaries sorted by BM25 score descending.
        """
        q_tokens = normalize_tokens(query)
        if not q_tokens or not candidates:
            return []

        N = len(candidates)
        df_freq: Counter[str] = Counter()
        doc_freqs: List[Counter[str]] = []
        doc_lens: List[float] = []

        for row in candidates:
            doc_counter: Counter[str] = Counter()
            name_text = row.get("name") or row.get("titre_court")
            if isinstance(name_text, str):
                for t in normalize_tokens(name_text):
                    doc_counter[t] += title_boost
            cat_text = row.get("categorie")
            if isinstance(cat_text, str):
                for t in normalize_tokens(cat_text):
                    doc_counter[t] += waldec_boost
            desc_text = row.get("description")
            if isinstance(desc_text, str):
                for t in normalize_tokens(desc_text):
                    doc_counter[t] += objet_boost

            doc_freqs.append(doc_counter)
            doc_lens.append(sum(doc_counter.values()))
            for term in doc_counter.keys():
                df_freq[term] += 1

        avgdl = sum(doc_lens) / N if N > 0 else 1.0
        idf: Dict[str, float] = {}
        for term, freq in df_freq.items():
            val = math.log((N - freq + 0.5) / (freq + 0.5) + 1.0)
            idf[term] = max(val, 0.01)

        scored: List[Dict[str, Any]] = []
        for idx, row in enumerate(candidates):
            doc_counter = doc_freqs[idx]
            doc_len = doc_lens[idx]
            score = 0.0
            matched_terms = []

            for q in q_tokens:
                if q in doc_counter:
                    tf = doc_counter[q]
                    q_idf = idf.get(q, 0.0)
                    num = tf * (self.k1 + 1.0)
                    den = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / avgdl))
                    score += q_idf * (num / den)
                    matched_terms.append(q)

            if score > threshold:
                item = dict(row)
                item["score"] = round(score, 4)
                item["matched_terms"] = matched_terms
                scored.append(item)

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]


class RNARagService:
    """Service for RNA RAG associative search.

    Implements BM25 Pondéré search (BigQuery SEARCH pre-filtering + in-process
    Robertson-Spärck Jones BM25 ranking), resolving the 128d compression and
    NaN embedding issues.
    """

    def __init__(self):
        try:
            self.data_project = os.getenv("ODIS_DATA_PROJECT") or os.getenv(
                "GOOGLE_CLOUD_PROJECT"
            )
            if not self.data_project:
                raise RuntimeError(
                    "ODIS_DATA_PROJECT or GOOGLE_CLOUD_PROJECT must be configured"
                )

            self.bq_client = bigquery.Client(project=self.data_project)
            self.bm25_ranker = BM25Ranker()

            # Optional GenAI client for backwards compatibility
            self.embedding_model = "text-multilingual-embedding-002"
            try:
                embedding_location = (
                    os.getenv("ODIS_EMBEDDING_LOCATION")
                    or os.getenv("GOOGLE_CLOUD_LOCATION")
                    or "europe-west1"
                )
                self.genai_client = get_gemini_client(location=embedding_location)
            except Exception as e:
                logger.warning(
                    f"GenAI client initialization omitted for RNARagService: {e}"
                )
                self.genai_client = None

        except Exception as e:
            logger.error(f"Failed to initialize RNARagService: {e}")
            raise RuntimeError(
                f"RNARagService Initialization Error: {e}. Check GCP credentials."
            )

    def _flatten_embedding(self, raw_embedding: Any) -> np.ndarray:
        """Normalizes various BigQuery embedding formats into a flat numpy array.

        Args:
            raw_embedding: Raw embedding representation from BigQuery.

        Returns:
            Flat numpy array of float64.

        Raises:
            ValueError: If structure is unsupported.
        """
        if isinstance(raw_embedding, (list, np.ndarray)):
            return np.array(raw_embedding, dtype=np.float64)

        if isinstance(raw_embedding, dict) and "list" in raw_embedding:
            try:
                flat_list = [float(item["element"]) for item in raw_embedding["list"]]
                return np.array(flat_list, dtype=np.float64)
            except (KeyError, TypeError) as e:
                logger.error(f"Failed to flatten nested embedding dict: {e}")
                raise ValueError(
                    f"Unexpected embedding dict structure: {raw_embedding}"
                )

        raise ValueError(f"Unsupported embedding type: {type(raw_embedding)}")

    def _get_embedding(self, text: str) -> np.ndarray:
        """Generates embedding for text (kept for backwards compatibility).

        Args:
            text: Input string.

        Returns:
            Normalized float numpy vector.
        """
        if not self.genai_client:
            raise RuntimeError("GenAI client is not initialized.")
        try:
            response = self.genai_client.models.embed_content(
                model=self.embedding_model,
                contents=[text],
                config={"output_dimensionality": 128},
            )
            v = np.array(response.embeddings[0].values)
            norm = np.linalg.norm(v)
            return v / norm if norm > 0 else v
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            raise

    def get_associations_semantic(
        self,
        query: str,
        codgeos: Optional[List[str]] = None,
        bv_code: Optional[str] = None,
        top_k: int = 10,
        inclusion_only: bool = False,
        threshold: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Performs BM25-ranked lookup for associations in a commune or Bassin de Vie.

        Uses BigQuery SEARCH to pre-filter candidates by geography and keywords,
        then ranks them using field-weighted BM25 (Robertson-Spärck Jones).

        Args:
            query: The search term (e.g. 'football', 'hébergement', 'mosquée').
            codgeos: List of 5-digit INSEE codes (fallback or commune filter).
            bv_code: Optional Bassin de Vie code for broader search.
            top_k: Number of results to return.
            inclusion_only: If True, filters strictly for is_inclusion_relevant.
            threshold: Minimum BM25 score threshold (default 0.0).

        Returns:
            List of matching associations sorted by BM25 score descending.
        """
        if isinstance(codgeos, str):
            codgeos = [codgeos]
        elif codgeos is None:
            codgeos = []

        codgeos = [str(c) for c in codgeos]

        # 1. Tokenize query and build bivalent search expression (unaccented + raw)
        q_tokens = normalize_tokens(query)
        if not q_tokens:
            logger.debug(f"No valid tokens extracted from query '{query}'")
            return []

        raw_tokens = [
            re.sub(r"[^a-zA-Z0-9àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]", "", t).lower()
            for t in query.split()
        ]
        raw_tokens = [
            t for t in raw_tokens if len(t) >= 2 and t not in FRENCH_STOPWORDS
        ]

        # Combine unaccented and raw tokens for maximum candidate recall in BigQuery
        search_terms = set(q_tokens) | set(raw_tokens)
        search_expr = " OR ".join(
            f'"{t}"' if " " in t else t for t in sorted(search_terms)
        )

        try:
            # 2. Query BigQuery candidates using SEARCH and clustering
            table_id = f"{self.data_project}.rna_rag.rna_rag_clustered"
            where_geo = (
                "code_bdv = @bv_code" if bv_code else "codgeo IN UNNEST(@codgeos)"
            )
            filter_inclusion = (
                "AND is_inclusion_relevant = TRUE" if inclusion_only else ""
            )

            query_bq = f"""
                SELECT id, 
                       COALESCE(titre_court, '') as name, 
                       primary_category, 
                       code_waldec, 
                       COALESCE(categorie, '') as categorie, 
                       COALESCE(description, '') as description, 
                       codgeo
                FROM `{table_id}`
                WHERE {where_geo}
                  {filter_inclusion}
                  AND SEARCH((titre_court, categorie, description), @search_expr)
                LIMIT 500
            """

            params = [
                bigquery.ScalarQueryParameter("search_expr", "STRING", search_expr),
            ]
            if bv_code:
                params.append(
                    bigquery.ScalarQueryParameter("bv_code", "STRING", bv_code)
                )
            else:
                params.append(
                    bigquery.ArrayQueryParameter("codgeos", "STRING", codgeos)
                )

            job_config = bigquery.QueryJobConfig(query_parameters=params)
            df = self.bq_client.query(query_bq, job_config=job_config).to_dataframe()

            if df.empty:
                return []

            candidates = df.to_dict(orient="records")

            # 3. In-process BM25 Robertson-Spärck Jones ranking
            return self.bm25_ranker.rank(
                candidates=candidates,
                query=query,
                top_k=top_k,
                threshold=threshold,
            )

        except Exception as e:
            logger.error(f"get_associations_semantic failed: {e}")
            raise RuntimeError(f"BigQuery BM25 candidate search failed: {e}")

    def get_associations_by_codgeo(self, codgeos: List[str]) -> List[Dict[str, Any]]:
        """Fetches all inclusion-relevant associations for a list of communes.

        Args:
            codgeos: List of 5-digit INSEE codes.

        Returns:
            List of associations with their name and primary_category.
        """
        codgeos = [str(c) for c in codgeos]
        logger.debug(f"Fetching all associations for {len(codgeos)} communes")

        try:
            table_id = f"{self.data_project}.rna_rag.rna_rag_clustered"
            query_bq = f"""
                SELECT id, titre_court as name, primary_category, code_waldec, categorie, description, max_score, is_refugee_focused, codgeo
                FROM `{table_id}`
                WHERE codgeo IN UNNEST(@codgeos) 
                  AND (is_refugee_focused = TRUE OR is_inclusion_relevant = TRUE)
                ORDER BY max_score DESC
            """

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ArrayQueryParameter("codgeos", "STRING", codgeos),
                ]
            )

            df = self.bq_client.query(query_bq, job_config=job_config).to_dataframe(
                create_bqstorage_client=True
            )
            return df.to_dict("records")

        except Exception as e:
            logger.error(f"get_associations_by_codgeo failed: {e}")
            raise RuntimeError(f"BigQuery connection failed: {e}")
