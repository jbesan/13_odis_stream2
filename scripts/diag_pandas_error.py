
import sys
import os
import pandas as pd
import logging

# Add app to path
sys.path.append(os.path.join(os.getcwd(), 'app'))

from services.mcp_server import DATA_CONTEXT, ensure_data_context
from utils.common import normalize_text, calculate_fuzzy_match_score

# Configure Logging
logging.basicConfig(level=logging.INFO)

def diag():
    print("--- Diagnostic for Pandas Error ---")
    ensure_data_context()
    df = DATA_CONTEXT['referentiels_raw']
    print(f"DataFrame columns: {df.columns.tolist()}")
    print(f"DataFrame shape: {df.shape}")
    
    query = "mecanicien automobile"
    query_norm = normalize_text(query)
    query_tokens = set(query_norm.split())
    STOP_WORDS = set() # Doesn't matter for types
    weights = {'exact': 100, 'token_overlap': 20, 'contains': 20, 'starts_with': 50}

    def calculate_relevance(row):
        score = calculate_fuzzy_match_score(
            query_norm, 
            row['label_norm'], 
            query_tokens, 
            set(row['label_norm'].split()), 
            STOP_WORDS,
            weights
        )
        if query_norm in row['code_norm']:
            score += 50
        return score

    work_df = df.head(5).copy()
    work_df['label_norm'] = work_df['label'].apply(normalize_text)
    work_df['code_norm'] = work_df['code'].apply(normalize_text)
    
    print("\nApplying calculate_relevance testing one row...")
    row = work_df.iloc[0]
    res = calculate_relevance(row)
    print(f"Type of result for 1 row: {type(res)}")
    print(f"Result value: {res}")

    print("\nApplying to whole head(5)...")
    res_apply = work_df.apply(calculate_relevance, axis=1)
    print(f"Type of res_apply: {type(res_apply)}")
    if isinstance(res_apply, pd.DataFrame):
        print(f"Columns in res_apply: {res_apply.columns.tolist()}")

if __name__ == "__main__":
    diag()
