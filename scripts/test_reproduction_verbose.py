
import sys
import os
import pandas as pd
import logging

# Add app to path
sys.path.append(os.path.join(os.getcwd(), 'app'))

from services.mcp_server import _search_referentiels_logic, set_data_context
from utils.common import calculate_fuzzy_match_score, normalize_text
from utils.data_loader import load_all_data_raw

# Configure Logging
logging.basicConfig(level=logging.INFO)

def test_verbose_scoring():
    print("--- Verbose Scoring Test ---")
    
    query = "mecanicien automobile"
    query_norm = normalize_text(query)
    query_tokens = set(query_norm.split())
    
    STOP_WORDS = {
        "le", "la", "les", "l", "d", "de", "du", "des", 
        "un", "une", "et", "ou", "au", "aux", "en", 
        "par", "pour", "sur", "dans", "a", "à"
    }
    
    weights = {'exact': 100, 'token_overlap': 20, 'contains': 20, 'starts_with': 50}
    
    test_cases = [
        {"label": "Brasseur / Brasseuse de bière", "code": "A1413"},
        {"label": "Mécanicien automobile", "code": "I1604"},
        {"label": "Expert en construction", "code": "F1106"}
    ]
    
    for case in test_cases:
        label_norm = normalize_text(case["label"])
        target_tokens = set(label_norm.split())
        code_norm = normalize_text(case["code"])
        
        score = calculate_fuzzy_match_score(
            query_norm, 
            label_norm, 
            query_tokens, 
            target_tokens, 
            STOP_WORDS,
            weights
        )
        
        if query_norm in code_norm:
            score += 50
            
        print(f"Query: '{query_norm}' | Target: '{label_norm}' ({case['code']}) | Score: {score}")

def test_logic_return():
    print("\n--- Testing MCP Server Logic Return ---")
    context = load_all_data_raw()
    set_data_context(context)
    
    query = "mecanicien automobile"
    domain = "rome_codes"
    
    results = _search_referentiels_logic(query, domain)
    print(f"Results for '{query}' in '{domain}':")
    for r in results:
        print(f" - {r}")

if __name__ == "__main__":
    test_verbose_scoring()
    test_logic_return()
