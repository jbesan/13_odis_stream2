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
            # GenAI Client (Uses GOOGLE_API_KEY)
            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                logger.warning("GOOGLE_API_KEY not found in environment. GenAI client may fail.")
            self.genai_client = genai.Client(api_key=api_key)
            self.embedding_model = "text-multilingual-embedding-002"
        except Exception as e:
            logger.error(f"Failed to initialize RNARagService: {e}")
            raise RuntimeError(f"RNARagService Initialization Error: {e}. Check GCP credentials and API Key.")

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

    def get_associations_semantic(
        self, 
        query: str, 
        codgeo: str, 
        top_k: int = 10, 
        inclusion_only: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Performs semantic lookup for associations in a specific commune.
        Fetches vectors from BQ, computes similarity locally.
        
        Args:
            query: The search term (e.g. 'football', 'hébergement')
            codgeo: The 5-digit INSEE code of the commune
            top_k: Number of results to return
            inclusion_only: If True, filters for is_inclusion_relevant associations
            
        Returns:
            List of matching associations with scores > 0.8, sorted by score DESC.
        """
        logger.info(f"Searching associations for query='{query}' in codgeo='{codgeo}'")
        
        try:
            # 1. Generate query embedding
            query_vector = self._get_embedding(query)
            
            # 2. Fetch vectors from BigQuery for this commune
            # Columns in BQ: id, titre_court, primary_category, embedding_128, is_inclusion_relevant
            table_id = "odis-stream2.rna_rag.rna_rag_mini"
            
            query_bq = f"""
                SELECT id, titre_court as name, primary_category, embedding_128 as embedding
                FROM `{table_id}`
                WHERE codgeo = @codgeo
                {"AND is_inclusion_relevant = TRUE" if inclusion_only else ""}
            """
            
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("codgeo", "STRING", codgeo)
                ]
            )
            
            df = self.bq_client.query(query_bq, job_config=job_config).to_dataframe()
            
            if df.empty:
                logger.info(f"No associations found in BigQuery for codgeo {codgeo}")
                return []
            
            # 3. Local Similarity Computation (Dot Product)
            # embeddings in DF are lists or numpy arrays
            
            scores = []
            for _, row in df.iterrows():
                # BQ to_dataframe converts arrays to lists
                vec = np.array(row['embedding'])
                score = float(np.dot(query_vector, vec))
                
                # Apply 0.8 threshold
                if score > 0.8:
                    scores.append({
                        "id": row['id'],
                        "name": row['name'],
                        "primary_category": row['primary_category'],
                        "score": round(score, 4)
                    })
            
            # 4. Sort by score DESC
            results = sorted(scores, key=lambda x: x['score'], reverse=True)
            
            return results[:top_k]

        except Exception as e:
            logger.error(f"get_associations_semantic failed: {e}")
            raise RuntimeError(f"BigQuery/Vertex connection failed: {e}")

    def get_associations_by_codgeo(self, codgeo: str) -> List[Dict[str, Any]]:
        """
        Fetches all inclusion-relevant associations for a specific commune.
        
        Args:
            codgeo: The 5-digit INSEE code of the commune
            
        Returns:
            List of associations with their name and primary_category.
        """
        logger.info(f"Fetching all associations for codgeo='{codgeo}'")
        
        try:
            table_id = "odis-stream2.rna_rag.rna_rag_mini"
            query_bq = f"""
                SELECT id, titre_court as name, primary_category
                FROM `{table_id}`
                WHERE codgeo = @codgeo AND is_inclusion_relevant = TRUE
                ORDER BY titre_court ASC
            """
            
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("codgeo", "STRING", codgeo)
                ]
            )
            
            df = self.bq_client.query(query_bq, job_config=job_config).to_dataframe()
            return df.to_dict('records')

        except Exception as e:
            logger.error(f"get_associations_by_codgeo failed: {e}")
            raise RuntimeError(f"BigQuery connection failed: {e}")
