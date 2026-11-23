import logging
import math
import os
import re
import unicodedata
from collections import Counter
from typing import Any, Dict, List, Optional

import numpy as np
from google.cloud import bigquery
from requests.adapters import HTTPAdapter

from agents.agent_config import get_gemini_client
from core.associations_ranking import has_active_employees
from core.models import format_assoce_url

logger = logging.getLogger("RNARagService")

_EMBEDDING_MODEL = "gemini-embedding-001"
_EMBEDDING_DIM = 768
_RRF_K = 60
_DENSE_CANDIDATE_LIMIT = 200
_LEXICAL_CANDIDATE_LIMIT = 200
_CODGEO_PLM_SQL_EXPR = """CASE
    WHEN REGEXP_CONTAINS(LPAD(TRIM(adrs_codeinsee), 5, '0'), r'^751(0[1-9]|1[0-9]|20)$') THEN '75056'
    WHEN REGEXP_CONTAINS(LPAD(TRIM(adrs_codeinsee), 5, '0'), r'^6938[1-9]$') THEN '69123'
    WHEN REGEXP_CONTAINS(LPAD(TRIM(adrs_codeinsee), 5, '0'), r'^132(0[1-9]|1[0-6])$') THEN '13055'
    ELSE LPAD(TRIM(adrs_codeinsee), 5, '0')
END"""
_PLM_PARIS_RE = re.compile(r"^751(0[1-9]|1[0-9]|20)$")
_PLM_LYON_RE = re.compile(r"^6938[1-9]$")
_PLM_MARSEILLE_RE = re.compile(r"^132(0[1-9]|1[0-6])$")


def normalize_codgeo_plm(codgeo: Any) -> str:
    """Normalize Paris/Lyon/Marseille arrondissement codes to commune INSEE code."""
    raw = str(codgeo or "").strip().zfill(5)
    if _PLM_PARIS_RE.match(raw):
        return "75056"
    if _PLM_LYON_RE.match(raw):
        return "69123"
    if _PLM_MARSEILLE_RE.match(raw):
        return "13055"
    return raw


def _clean_optional_str(value: Any) -> Optional[str]:
    """Normalize nullable/NaN dataframe scalar values to stripped string or None."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return None
    return text


def _optional_int(value: Any) -> Optional[int]:
    """Convert nullable/NaN numeric value to int or None."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return int(value)


def _optional_float(value: Any) -> Optional[float]:
    """Convert nullable/NaN numeric value to float or None."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return float(value)


def _join_text_sequence(value: Any) -> str:
    """Join list/array/scalar tag values into a single space-delimited string."""
    if isinstance(value, (list, tuple, np.ndarray)):
        return " ".join(
            str(item).strip()
            for item in value
            if item is not None and str(item).strip()
        )
    if isinstance(value, str):
        return value
    return ""

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
            estab_text = row.get("nom_etablissement")
            if isinstance(estab_text, str) and estab_text != name_text:
                for t in normalize_tokens(estab_text):
                    doc_counter[t] += title_boost
            cat_text = row.get("categorie") or row.get("waldec_label")
            if isinstance(cat_text, str):
                for t in normalize_tokens(cat_text):
                    doc_counter[t] += waldec_boost
            tags_text = _join_text_sequence(row.get("llm_tags_recherche"))
            if tags_text:
                for t in normalize_tokens(tags_text):
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


def configure_bigquery_pool(client: bigquery.Client, pool_size: int = 50) -> None:
    """Configures HTTP connection pool maxsize on a BigQuery client and its auth session.

    By default, requests.adapters.HTTPAdapter uses pool_maxsize=10. When concurrent queries
    run in parallel (e.g. parallel swarm expert executions calling search_rna_rag_batch),
    the number of concurrent requests to bigquery.googleapis.com and oauth2.googleapis.com
    exceeds 10, causing urllib3 to discard pooled connections and emit warnings.

    Args:
        client: The BigQuery client instance to configure.
        pool_size: The connection pool size (default 50).
    """
    http_session = getattr(client, "_http", None)
    if http_session is not None and hasattr(http_session, "mount"):
        adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
        http_session.mount("https://", adapter)
        http_session.mount("http://", adapter)

        auth_req = getattr(http_session, "_auth_request", None)
        if auth_req is not None:
            auth_session = getattr(auth_req, "session", None)
            if auth_session is not None and hasattr(auth_session, "mount"):
                auth_session.mount("https://", adapter)
                auth_session.mount("http://", adapter)


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
            configure_bigquery_pool(self.bq_client, pool_size=50)
            self.bm25_ranker = BM25Ranker()
            self.v6_table_id = os.getenv(
                "RNA_V6_BQ_TABLE",
                f"{self.data_project}.rna_rag.national_v6_consolidated",
            )

            self.embedding_model = _EMBEDDING_MODEL
            self.embedding_dim = _EMBEDDING_DIM
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

    def get_embeddings_batch(
        self,
        texts: List[str],
        *,
        task_type: str = "RETRIEVAL_QUERY",
    ) -> List[np.ndarray]:
        """Generate normalized 768d ``gemini-embedding-001`` vectors in a single batch call.

        Args:
            texts: List of query strings to embed.
            task_type: Vertex AI embedding task type (default ``RETRIEVAL_QUERY``).

        Returns:
            List of L2-normalized float64 numpy vectors aligned with ``texts``.

        Raises:
            RuntimeError: If GenAI client is not initialized.
        """
        if not texts:
            return []
        if not self.genai_client:
            raise RuntimeError("GenAI client is not initialized.")
        try:
            response = self.genai_client.models.embed_content(
                model=self.embedding_model,
                contents=list(texts),
                config={
                    "output_dimensionality": self.embedding_dim,
                    "task_type": task_type,
                },
            )
            vectors: List[np.ndarray] = []
            for emb in response.embeddings or []:
                vec = self._flatten_embedding(emb.values)
                norm = np.linalg.norm(vec)
                vectors.append(vec / norm if norm > 0 else vec)
            if len(vectors) != len(texts):
                raise RuntimeError(
                    f"Embedding count mismatch: requested {len(texts)}, got {len(vectors)}"
                )
            return vectors
        except Exception as e:
            logger.error(f"Batch embedding generation failed: {e}")
            raise

    def _get_embedding(self, text: str) -> np.ndarray:
        """Generates a single L2-normalized embedding vector.

        Args:
            text: Input string.

        Returns:
            Normalized float numpy vector.
        """
        return self.get_embeddings_batch([text])[0]

    def get_associations_semantic(
        self,
        query: str,
        codgeos: Optional[List[str]] = None,
        bv_code: Optional[str] = None,
        top_k: int = 10,
        inclusion_only: bool = False,
        threshold: float = 0.0,
        exclude_international: bool = True,
        query_vector: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        """Perform BDV-scoped Hybrid RRF search (768d cosine + BM25) over RNA V6.

        Always restricts execution to a Bassin de Vie (``TRIM(bdv_codeinsee) = @bv_code``)
        or explicit commune scope (``codgeos``) so full-table scans never happen.
        BigQuery computes dense top-200 (`COSINE_DISTANCE`) and lexical top-200
        (`SEARCH`) inside the target BDV cluster, transfers only scalar metadata
        (never 768d vectors), and fuses ranks in Python with BM25 via RRF (k=60).

        Args:
            query: Search query terms (2-4 concrete keywords, no city name).
            codgeos: List of 5-digit INSEE codes (used for same-commune flag and fallback geo scope).
            bv_code: Bassin de Vie INSEE code for BDV cluster filtering.
            top_k: Maximum number of deduplicated associations to return (default 10).
            inclusion_only: If True, restricts to ``llm_action_sociale = TRUE``.
            threshold: Minimum RRF score threshold (default 0.0).
            exclude_international: If True (default), excludes ``llm_perimetre_action = 'international'``.
            query_vector: Optional pre-computed 768d query vector (from batch embedding).

        Returns:
            Deduplicated associations sorted by RRF score descending.

        Raises:
            ValueError: If neither ``bv_code`` nor non-empty ``codgeos`` is provided.
            RuntimeError: If BigQuery or embedding generation fails.
        """
        if isinstance(codgeos, str):
            raw_codgeos = [codgeos]
        elif codgeos is None:
            raw_codgeos = []
        else:
            raw_codgeos = list(codgeos)

        codgeos_clean = [
            normalize_codgeo_plm(c) for c in raw_codgeos if str(c).strip()
        ]
        bv_code_clean = (
            str(bv_code).strip().removesuffix(".0")
            if bv_code is not None and str(bv_code).strip()
            else ""
        )
        if not bv_code_clean and not codgeos_clean:
            raise ValueError(
                "Semantic search requires bv_code (BDV) or non-empty codgeos; full-table scan is forbidden."
            )

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
        search_terms = set(q_tokens) | set(raw_tokens)
        search_expr = " OR ".join(
            f'"{t}"' if " " in t else t for t in sorted(search_terms)
        )

        try:
            vec_list = (
                [float(x) for x in query_vector]
                if query_vector is not None
                else self._get_embedding(query).tolist()
            )

            where_geo = (
                "TRIM(bdv_codeinsee) = @bv_code"
                if bv_code_clean
                else f"({_CODGEO_PLM_SQL_EXPR}) IN UNNEST(@codgeos)"
            )
            filter_international = (
                "AND (llm_perimetre_action IS NULL OR LOWER(TRIM(llm_perimetre_action)) != 'international')"
                if exclude_international
                else ""
            )
            filter_inclusion = (
                "AND llm_action_sociale = TRUE" if inclusion_only else ""
            )

            query_bq = f"""
                WITH scoped AS (
                    SELECT
                        id,
                        entity_id,
                        is_siege,
                        COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(titre_court), ''), 'Inconnu') AS name,
                        NULLIF(TRIM(nom_etablissement), '') AS nom_etablissement,
                        COALESCE(TRIM(objet), '') AS description,
                        COALESCE(NULLIF(TRIM(waldec_labels_hierarchical), ''), COALESCE(TRIM(objet_social1), '')) AS waldec_label,
                        {_CODGEO_PLM_SQL_EXPR} AS codgeo,
                        NULLIF(TRIM(adrs_libcommune), '') AS commune_nom,
                        date_decla,
                        tranche_effectifs,
                        llm_tags_recherche,
                        titre_court,
                        title,
                        objet,
                        embedding_gemini_768
                    FROM `{self.v6_table_id}`
                    WHERE {where_geo}
                      AND (etat_admin IS NULL OR etat_admin != 'F')
                      AND adrs_codeinsee IS NOT NULL
                      AND LENGTH(TRIM(adrs_codeinsee)) > 0
                      {filter_international}
                      {filter_inclusion}
                ),
                dense_top AS (
                    SELECT
                        * EXCEPT (titre_court, title, objet, embedding_gemini_768),
                        ROW_NUMBER() OVER (
                            ORDER BY COSINE_DISTANCE(embedding_gemini_768, @query_vec) ASC
                        ) AS dense_rank,
                        (1.0 - COSINE_DISTANCE(embedding_gemini_768, @query_vec)) AS cosine_sim
                    FROM scoped
                    WHERE embedding_gemini_768 IS NOT NULL
                      AND ARRAY_LENGTH(embedding_gemini_768) = {self.embedding_dim}
                    ORDER BY COSINE_DISTANCE(embedding_gemini_768, @query_vec) ASC
                    LIMIT {_DENSE_CANDIDATE_LIMIT}
                ),
                lexical_top AS (
                    SELECT
                        * EXCEPT (titre_court, title, objet, embedding_gemini_768),
                        CAST(NULL AS INT64) AS dense_rank,
                        IF(
                            embedding_gemini_768 IS NOT NULL AND ARRAY_LENGTH(embedding_gemini_768) = {self.embedding_dim},
                            1.0 - COSINE_DISTANCE(embedding_gemini_768, @query_vec),
                            CAST(NULL AS FLOAT64)
                        ) AS cosine_sim
                    FROM scoped
                    WHERE SEARCH(
                        (titre_court, title, nom_etablissement, objet, ARRAY_TO_STRING(llm_tags_recherche, ' ')),
                        @search_expr
                    )
                    LIMIT {_LEXICAL_CANDIDATE_LIMIT}
                )
                SELECT *
                FROM (
                    SELECT * FROM dense_top
                    UNION ALL
                    SELECT * FROM lexical_top
                )
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY entity_id
                    ORDER BY COALESCE(dense_rank, 999999) ASC
                ) = 1
            """

            params: List[Any] = [
                bigquery.ArrayQueryParameter("query_vec", "FLOAT64", vec_list),
                bigquery.ScalarQueryParameter("search_expr", "STRING", search_expr),
            ]
            if bv_code_clean:
                params.append(
                    bigquery.ScalarQueryParameter("bv_code", "STRING", bv_code_clean)
                )
            else:
                params.append(
                    bigquery.ArrayQueryParameter("codgeos", "STRING", codgeos_clean)
                )

            job_config = bigquery.QueryJobConfig(query_parameters=params)
            df = self.bq_client.query(query_bq, job_config=job_config).to_dataframe()
            if df.empty:
                return []

            raw_candidates = df.to_dict(orient="records")
            indexed_candidates: List[Dict[str, Any]] = []
            for idx, row in enumerate(raw_candidates):
                cand = dict(row)
                cand["_row_idx"] = idx
                indexed_candidates.append(cand)

            bm25_hits = self.bm25_ranker.rank(
                candidates=indexed_candidates,
                query=query,
                top_k=len(indexed_candidates),
                threshold=0.0,
            )
            bm25_by_idx: Dict[int, tuple[int, List[str]]] = {
                int(hit["_row_idx"]): (rank_pos, list(hit.get("matched_terms") or []))
                for rank_pos, hit in enumerate(bm25_hits, start=1)
            }

            target_codgeo_set = set(codgeos_clean)
            fused_rows: List[tuple[tuple[float, float, int, str], Dict[str, Any]]] = []
            for cand in indexed_candidates:
                dense_rank = _optional_int(cand.get("dense_rank"))
                cosine_sim = _optional_float(cand.get("cosine_sim"))
                bm25_rank, matched_terms = bm25_by_idx.get(
                    int(cand["_row_idx"]), (None, [])
                )

                rrf_score = (
                    (1.0 / (_RRF_K + dense_rank) if dense_rank is not None else 0.0)
                    + (1.0 / (_RRF_K + bm25_rank) if bm25_rank is not None else 0.0)
                )
                if rrf_score <= 0.0 or rrf_score < threshold:
                    continue

                assoc_id = _clean_optional_str(cand.get("id")) or ""
                codgeo_val = _clean_optional_str(cand.get("codgeo")) or ""
                if codgeo_val:
                    codgeo_val = normalize_codgeo_plm(codgeo_val)
                raw_is_siege = cand.get("is_siege")
                is_siege_val = (
                    True
                    if _clean_optional_str(raw_is_siege) is None
                    else bool(raw_is_siege)
                )

                payload = {
                    "id": assoc_id,
                    "is_siege": is_siege_val,
                    "name": _clean_optional_str(cand.get("name")) or "Inconnu",
                    "nom_etablissement": _clean_optional_str(
                        cand.get("nom_etablissement")
                    ),
                    "description": _clean_optional_str(cand.get("description")) or "",
                    "waldec_label": (
                        _clean_optional_str(cand.get("waldec_label"))
                        or _clean_optional_str(cand.get("categorie"))
                        or ""
                    ),
                    "codgeo": codgeo_val,
                    "commune_nom": _clean_optional_str(cand.get("commune_nom")),
                    "is_same_commune": bool(
                        codgeo_val and codgeo_val in target_codgeo_set
                    ),
                    "has_employees": has_active_employees(cand.get("tranche_effectifs")),
                    "date_decla": _clean_optional_str(cand.get("date_decla")),
                    "assoce_url": format_assoce_url(assoc_id),
                    "score": round(rrf_score, 6),
                    "cosine_sim": (
                        round(cosine_sim, 4) if cosine_sim is not None else None
                    ),
                    "matched_terms": matched_terms,
                }
                sort_key = (
                    -rrf_score,
                    -(cosine_sim if cosine_sim is not None else 0.0),
                    0 if is_siege_val else 1,
                    assoc_id,
                )
                fused_rows.append((sort_key, payload))

            fused_rows.sort(key=lambda item: item[0])

            deduped: List[Dict[str, Any]] = []
            seen_keys: set[tuple[str, str]] = set()
            for _, payload in fused_rows:
                dedup_key = (
                    (payload["id"], payload["codgeo"])
                    if payload["id"]
                    else (payload["name"], payload["codgeo"])
                )
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)
                deduped.append(payload)
                if len(deduped) >= top_k:
                    break

            return deduped

        except Exception as e:
            logger.error(f"get_associations_semantic failed: {e}")
            raise RuntimeError(f"BigQuery Hybrid RRF candidate search failed: {e}")

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

    def get_associations_v6_bdv(
        self,
        bdv_codes: List[str],
        requested_themes: List[str],
        requested_tags: List[str],
    ) -> List[Dict[str, Any]]:
        """Fetch deterministic V6 associations across target Bassins de Vie.

        Args:
            bdv_codes: Normalized 5-digit Bassin de Vie codes.
            requested_themes: Exact RNA V6 ``llm_thematiques`` labels to match.
            requested_tags: Canonical tags to match against ``llm_tags_normalises``.

        Returns:
            Raw BigQuery candidate dictionaries ready for per-commune ranking.

        Raises:
            RuntimeError: If the BigQuery query fails.
        """
        clean_bdvs = list(
            dict.fromkeys(str(b).strip().removesuffix(".0") for b in bdv_codes if b)
        )
        clean_themes = list(dict.fromkeys(str(t).strip() for t in requested_themes if t))
        clean_tags = list(dict.fromkeys(str(t).strip() for t in requested_tags if t))
        if not clean_bdvs or (not clean_themes and not clean_tags):
            return []

        try:
            query_bq = f"""
                SELECT
                    id,
                    is_siege,
                    COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(titre_court), ''), 'Inconnu') AS name,
                    NULLIF(TRIM(nom_etablissement), '') AS nom_etablissement,
                    COALESCE(TRIM(objet), '') AS description,
                    COALESCE(TRIM(objet_social1), '') AS waldec_label,
                    {_CODGEO_PLM_SQL_EXPR} AS codgeo,
                    NULLIF(TRIM(adrs_libcommune), '') AS commune_nom,
                    TRIM(bdv_codeinsee) AS bdv_codeinsee,
                    date_decla,
                    tranche_effectifs,
                    llm_action_sociale,
                    llm_perimetre_action,
                    ARRAY(
                        SELECT t FROM UNNEST(llm_thematiques) AS t
                        WHERE t IN UNNEST(@requested_themes)
                    ) AS matched_themes,
                    ARRAY(
                        SELECT tg FROM UNNEST(llm_tags_normalises) AS tg
                        WHERE tg IN UNNEST(@requested_tags)
                    ) AS matched_tags
                FROM `{self.v6_table_id}`
                WHERE TRIM(bdv_codeinsee) IN UNNEST(@bdv_codes)
                  AND (etat_admin IS NULL OR etat_admin != 'F')
                  AND adrs_codeinsee IS NOT NULL
                  AND LENGTH(TRIM(adrs_codeinsee)) > 0
                  AND (
                      (
                          ARRAY_LENGTH(@requested_themes) > 0
                          AND llm_action_sociale = TRUE
                          AND (llm_perimetre_action IS NULL OR llm_perimetre_action != 'international')
                          AND EXISTS(SELECT 1 FROM UNNEST(llm_thematiques) AS t WHERE t IN UNNEST(@requested_themes))
                      )
                      OR
                      (
                          ARRAY_LENGTH(@requested_tags) > 0
                          AND EXISTS(SELECT 1 FROM UNNEST(llm_tags_normalises) AS tg WHERE tg IN UNNEST(@requested_tags))
                      )
                  )
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ArrayQueryParameter("bdv_codes", "STRING", clean_bdvs),
                    bigquery.ArrayQueryParameter(
                        "requested_themes", "STRING", clean_themes
                    ),
                    bigquery.ArrayQueryParameter("requested_tags", "STRING", clean_tags),
                ]
            )
            df = self.bq_client.query(
                query_bq, job_config=job_config, location="europe-west1"
            ).to_dataframe(create_bqstorage_client=True)
            return df.to_dict("records")
        except Exception as e:
            logger.error(f"get_associations_v6_bdv failed: {e}")
            raise RuntimeError(f"BigQuery V6 associations fetch failed: {e}") from e
