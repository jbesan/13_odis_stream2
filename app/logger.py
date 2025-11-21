import json
import pandas as pd
from config import ScoringConfig
from dataclasses import asdict

def log_search_results(config: ScoringConfig, results_df: pd.DataFrame) -> None:
    """
    Logs the search configuration and the top 5 results as a JSON string to the console.
    
    Args:
        config: The scoring configuration used for the search.
        results_df: The dataframe containing the search results.
    """
    # Extract search parameters from config
    # We use asdict to convert the dataclass to a dictionary
    search_params = asdict(config)
    
    # Extract top 5 results
    top_5_results = []
    if not results_df.empty:
        # Ensure we have the necessary columns, handling potential missing ones gracefully
        cols_to_keep = ['codgeo', 'libgeo', 'weighted_score']
        # Add category scores if available
        cat_scores = [col for col in results_df.columns if col.endswith('_cat_score')]
        cols_to_keep.extend(cat_scores)
        
        # Filter columns that actually exist in the dataframe
        available_cols = [col for col in cols_to_keep if col in results_df.columns]
        
        top_5_df = results_df.head(5)[available_cols]
        
        # Convert to list of dictionaries
        top_5_results = top_5_df.to_dict(orient='records')

    # Construct the log entry
    log_entry = {
        "event": "search_completed",
        "params": search_params,
        "top_5_results": top_5_results
    }

    # Print as JSON string
    print(json.dumps(log_entry, ensure_ascii=False, default=str))
