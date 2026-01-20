
import sys
import os
import pandas as pd
import numpy as np

# Add app to path
sys.path.append(os.path.join(os.getcwd(), 'app'))

import config as cfg
from utils.common import normalize_text, calculate_fuzzy_match_score

def diag():
    print("--- Robust Diagnostic for Pandas Error ---")
    data_path = os.path.join(cfg.get_data_path(), cfg.REFERENTIELS_FILE)
    print(f"Loading data from: {data_path}")
    df = pd.read_parquet(data_path)
    
    print(f"DataFrame columns: {df.columns.tolist()}")
    print(f"Duplicate columns: {[c for c in df.columns if df.columns.tolist().count(c) > 1]}")
    
    query = "mecanicien automobile"
    query_norm = normalize_text(query)
    query_tokens = set(query_norm.split())
    STOP_WORDS = set()
    weights = {'exact': 100, 'token_overlap': 20, 'contains': 20, 'starts_with': 50}

    def calculate_relevance(row):
        # We simulate the exact logic in mcp_server.py
        try:
            label_norm = row['label_norm']
            code_norm = row['code_norm']
            
            score = calculate_fuzzy_match_score(
                query_norm, 
                label_norm, 
                query_tokens, 
                set(label_norm.split()), 
                STOP_WORDS,
                weights
            )
            if query_norm in code_norm:
                score += 50
            return score
        except Exception as e:
            return str(e)

    work_df = df.head(10).copy()
    work_df['label_norm'] = work_df['label'].apply(normalize_text)
    work_df['code_norm'] = work_df['code'].apply(normalize_text)
    
    print("\nApplying calculate_relevance...")
    res_apply = work_df.apply(calculate_relevance, axis=1)
    print(f"Result type: {type(res_apply)}")
    print(f"First 3 results:\n{res_apply.head(3)}")
    
    if isinstance(res_apply, pd.DataFrame):
        print(f"Result is a DataFrame! Columns: {res_apply.columns.tolist()}")
        print(f"Sample data:\n{res_apply.head(3)}")
    else:
        print("Result is not a DataFrame.")

if __name__ == "__main__":
    diag()
