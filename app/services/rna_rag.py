import os
import logging
import numpy as np
import pandas as pd
from google import genai
from google.cloud import bigquery
from typing import List, Dict, Any, Optional

logger = logging.getLogger("RNARagService")

class RNARagService:
    """
    Service for RNA RAG Semantic Lookup.
    Handles embedding generation via Vertex AI and vector similarity search
    using BigQuery data and local processing.
    """
    
    def __init__(self):
        try:
            # BigQuery Client (Explicit Project ID for local runs)
            self.bq_client = bigquery.Client(project="odis-stream2")
            # Vertex-based GenAI Client for embeddings
            # (Ensures compatibility with text-multilingual-embedding-002)
            self.genai_client = genai.Client(
                vertexai=True, 
                project="odis-stream2", 
                location="europe-west1"
            )
            self.embedding_model = "text-multilingual-embedding-002"
        except Exception as e:
            logger.error(f"Failed to initialize RNARagService: {e}")
            raise RuntimeError(f"RNARagService Initialization Error: {e}. Check GCP credentials and API Key.")

    def _flatten_embedding(self, raw_embedding: Any) -> np.ndarray:
        """
        Normalizes various BigQuery embedding formats into a flat numpy array.
        Handles:
        - List of floats
        - Numpy array of floats
        - Dict with 'list' key containing list of dicts with 'element' key
        """
        if isinstance(raw_embedding, (list, np.ndarray)):
            return np.array(raw_embedding, dtype=np.float64)
        
        if isinstance(raw_embedding, dict) and 'list' in raw_embedding:
            # Handle nested format: {'list': [{'element': 0.1}, ...]}
            try:
                flat_list = [float(item['element']) for item in raw_embedding['list']]
                return np.array(flat_list, dtype=np.float64)
            except (KeyError, TypeError) as e:
                logger.error(f"Failed to flatten nested embedding dict: {e}")
                raise ValueError(f"Unexpected embedding dict structure: {raw_embedding}")
        
        raise ValueError(f"Unsupported embedding type: {type(raw_embedding)}")

    def _get_embedding(self, text: str) -> np.ndarray:
        """Generates 128-dim embedding for the given text."""
        try:
            response = self.genai_client.models.embed_content(
                model=self.embedding_model,
                contents=[text],
                config={'output_dimensionality': 128}
            )
            return np.array(response.embeddings[0].values)
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            raise

    def get_associations_semantic(self, query: str, codgeos: List[str] = None, bv_code: str = None, top_k: int = 10, inclusion_only: bool = True, threshold: float = 0.8) -> List[Dict[str, Any]]:
        """
        Performs semantic lookup for associations in a specific commune or Bassin de Vie.
        Fetches vectors from BQ, computes similarity locally.
        
        Args:
            query: The search term (e.g. 'football', 'hébergement')
            codgeos: List of 5-digit INSEE codes (used as fallback or specific filter)
            bv_code: Optional Bassin de Vie code for broader search
            top_k: Number of results to return
            inclusion_only: If True, filters for is_inclusion_relevant associations
            threshold: Minimum similarity score (default 0.8)
            
        Returns:
            List of matching associations with scores > threshold, sorted by score DESC.
            Includes 'codgeo' for each result.
        """
        # Support single codgeo for backward compatibility or singular input
        if isinstance(codgeos, str):
            codgeos = [codgeos]
        elif codgeos is None:
            codgeos = []

        codgeos = [str(c) for c in codgeos]
        
        log_msg = f"Searching associations for query='{query}'"
        if bv_code:
            log_msg += f" in BV {bv_code}"
        else:
            log_msg += f" in {len(codgeos)} codgeos"
        logger.info(log_msg)
        
        try:
            # 1. Generate query embedding
            query_vector = self._get_embedding(query)
            
            # 2. Fetch vectors from BigQuery
            table_id = "odis-stream2.rna_rag.rna_rag"
            import config as cfg
            
            # Build WHERE clause dynamically
            where_geo = "code_bdv = @bv_code" if bv_code else "codgeo IN UNNEST(@codgeos)"
            
            query_bq = f"""
                SELECT id, titre_court as name, primary_category, code_waldec, categorie, embedding_128 as embedding, description, codgeo
                FROM `{table_id}`
                WHERE {where_geo}
                  AND SUBSTR(code_waldec, 1, 3) IN UNNEST(@allowed_prefixes)
                {"AND is_inclusion_relevant = TRUE" if inclusion_only else ""}
            """
            
            params = [
                bigquery.ArrayQueryParameter("allowed_prefixes", "STRING", cfg.WALDEC_CATEGORIES)
            ]
            if bv_code:
                params.append(bigquery.ScalarQueryParameter("bv_code", "STRING", bv_code))
            else:
                params.append(bigquery.ArrayQueryParameter("codgeos", "STRING", codgeos))

            job_config = bigquery.QueryJobConfig(query_parameters=params)
            
            df = self.bq_client.query(query_bq, job_config=job_config).to_dataframe()
            
            if df.empty:
                logger.info(f"No associations found in BigQuery for the given criteria.")
                return []
            
            # 3. Vectorized Similarity Computation (Dot Product)
            # Flatten all embeddings into a matrix (N x 128)
            embeddings_matrix = np.stack(df['embedding'].apply(self._flatten_embedding).values)
            
            # Compute similarity scores for all rows at once
            dot_products = np.dot(embeddings_matrix, query_vector)
            df['score'] = np.round(dot_products.astype(float), 4)
            
            # 4. Filter, Sort and Format
            results_df = df[df['score'] > threshold].sort_values(by='score', ascending=False)
            
            # Rename/select columns for output
            output_cols = ['id', 'name', 'primary_category', 'code_waldec', 'categorie', 'description', 'score', 'codgeo']
            results = results_df[output_cols].head(top_k).to_dict(orient='records')
            
            return results

        except Exception as e:
            logger.error(f"get_associations_semantic failed: {e}")
            raise RuntimeError(f"BigQuery/Vertex connection failed: {e}")

    def get_associations_by_codgeo(self, codgeos: List[str]) -> List[Dict[str, Any]]:
        """
        Fetches all inclusion-relevant associations for a list of communes.
        
        Args:
            codgeos: List of 5-digit INSEE codes
            
        Returns:
            List of associations with their name and primary_category.
        """
        codgeos = [str(c) for c in codgeos]
        logger.info(f"Fetching all associations for {len(codgeos)} communes")
        
        try:
            import config as cfg
            table_id = "odis-stream2.rna_rag.rna_rag"
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
                    bigquery.ArrayQueryParameter("allowed_prefixes", "STRING", cfg.WALDEC_CATEGORIES)
                ]
            )
            
            df = self.bq_client.query(query_bq, job_config=job_config).to_dataframe()
            return df.to_dict('records')

        except Exception as e:
            logger.error(f"get_associations_by_codgeo failed: {e}")
            raise RuntimeError(f"BigQuery connection failed: {e}")
