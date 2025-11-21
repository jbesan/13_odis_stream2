import logging
import json
import sys
from typing import Any, Dict
import pandas as pd
from config import ScoringConfig
from dataclasses import asdict

class JsonFormatter(logging.Formatter):
    """
    Formatter that outputs JSON strings after parsing the LogRecord.
    """
    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "funcName": record.funcName,
            "line": record.lineno
        }
        # Merge extra attributes if available
        if hasattr(record, 'extra_data'):
            log_record.update(record.extra_data) # type: ignore

        return json.dumps(log_record, ensure_ascii=False, default=str)

def setup_logging() -> None:
    """
    Configures the root logger to output JSON to stdout.
    """
    handler = logging.StreamHandler(sys.stdout)
    formatter = JsonFormatter()
    handler.setFormatter(formatter)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Remove existing handlers to avoid duplicates
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
        
    root_logger.addHandler(handler)

# Initialize logging when module is imported
setup_logging()

def log_search_results(config: ScoringConfig, results_df: pd.DataFrame) -> None:
    """
    Logs the search configuration and the top 5 results using the standard logger.
    """
    # Extract search parameters
    search_params = asdict(config)
    
    # Extract top 5 results
    top_5_results = []
    if not results_df.empty:
        cols_to_keep = ['codgeo', 'libgeo', 'weighted_score']
        cat_scores = [col for col in results_df.columns if col.endswith('_cat_score')]
        cols_to_keep.extend(cat_scores)
        available_cols = [col for col in cols_to_keep if col in results_df.columns]
        top_5_df = results_df.head(5)[available_cols]
        top_5_results = top_5_df.to_dict(orient='records')

    # Log with extra data
    logging.info(
        "Search completed",
        extra={'extra_data': {
            "event": "search_completed",
            "params": search_params,
            "top_5_results": top_5_results
        }}
    )
