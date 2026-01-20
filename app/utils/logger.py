import os
import logging
import json
import sys
import warnings
from datetime import datetime
from dataclasses import asdict
from typing import Optional, Dict, Any, List
import pandas as pd
from core.models import ScoringConfig

logger = logging.getLogger(__name__)

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
        indent = None
        if hasattr(record, 'extra_data'):
            log_record.update(record.extra_data) # type: ignore
            indent = 4

        return json.dumps(log_record, ensure_ascii=False, default=str, indent=indent)

def setup_logging() -> None:
    """
    Configures the root logger to output JSON to stderr.
    """
    handler = logging.StreamHandler(sys.stderr)
    
    if os.environ.get("MCP_SIMPLE_LOGS") == "true":
        formatter = logging.Formatter("[%(levelname)s] %(message)s")
    else:
        formatter = JsonFormatter()
        
    handler.setFormatter(formatter)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Remove existing handlers to avoid duplicates
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
        
    root_logger.addHandler(handler)
    
    # --- Ignore specific deprecation warnings from libraries ---
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="google.genai")
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="google.genai.types")

    # --- Reduce verbosity of third-party libraries ---
    loggers_to_silence = [
        "google_genai",
        "httpx",
        "google",
        "FPDF",
        "fpdf.svg",
        "fontTools.subset",
        "fontTools",
        "urllib3",
        "httpcore",
        "streamlit",
        "watchdog"
    ]
    
    for logger_name in loggers_to_silence:
        l = logging.getLogger(logger_name)
        l.setLevel(logging.WARNING)
        l.propagate = False # Prevent leaking to root/streamlit handlers
        if l.hasHandlers():
            l.handlers.clear()
        
    # Specifically for google_genai.models which is very chatty
    m_logger = logging.getLogger("google_genai.models")
    m_logger.setLevel(logging.WARNING)
    m_logger.propagate = False
    if m_logger.hasHandlers():
        m_logger.handlers.clear()

    try:
        from absl import logging as absl_logging
        absl_logging.set_verbosity(absl_logging.ERROR)
    except ImportError:
        pass

# Initialize logging when module is imported
setup_logging()

def log_search_results(
    config: ScoringConfig, 
    results_df: pd.DataFrame, 
    unaggregated_df: Optional[pd.DataFrame] = None,
    scores_cat: Optional[pd.DataFrame] = None,
    prefix: str = "search_results"
) -> None:
    """
    Logs the search configuration and the top 5 results using the standard logger.
    This logging is skipped if the application is detected to be running on Cloud Run.
    """
    # Skip logging if running on Cloud Run (identified by K_SERVICE environment variable)
    if os.environ.get('K_SERVICE'):
        return

    # --- Markdown Generation ---
    search_params = asdict(config)
    
    # Create a mapping from score column to category if scores_cat is provided
    score_to_cat = {}
    if scores_cat is not None:
        # Ensure we have the necessary columns
        if 'score' in scores_cat.columns and 'cat' in scores_cat.columns:
            score_to_cat = dict(zip(scores_cat['score'], scores_cat['cat']))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Log directory is now in app/.logs (one level up from utils)
    log_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.logs'))
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{prefix}_{timestamp}.md")

    md_lines = []
    md_lines.append(f"# Search Results - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    md_lines.append("")

    # 1. Configuration Section
    md_lines.append("## Configuration")
    
    # Search Criteria
    md_lines.append("### Search Criteria")
    md_lines.append("| Parameter | Value |")
    md_lines.append("| :--- | :--- |")
    for key, value in search_params.items():
        if not key.startswith('poids_'):
            # Format lists nicely
            if isinstance(value, list):
                val_str = ", ".join(map(str, value)) if value else "None"
            else:
                val_str = str(value)
            md_lines.append(f"| {key} | {val_str} |")
    md_lines.append("")

    # Weights
    md_lines.append("### Weights")
    md_lines.append("| Category | Weight |")
    md_lines.append("| :--- | :--- |")
    
    # Extract weights dynamically
    for key, value in search_params.items():
        if key.startswith('poids_'):
            category = key.replace('poids_', '').capitalize()
            md_lines.append(f"| {category} | {value} |")
    md_lines.append("")

    # 2. Top 5 Results Table
    md_lines.append("## Top 5 Results")
    
    if not results_df.empty:
        # Determine columns for the summary table
        cat_scores = [col for col in results_df.columns if col.endswith('_cat_score')]
        headers = ["Rank", "Commune/Bassin", "Weighted Score"] + [c.replace('_cat_score', '').capitalize() for c in cat_scores]
        
        md_lines.append("| " + " | ".join(headers) + " |")
        md_lines.append("| " + " | ".join([":---"] * len(headers)) + " |")

        top_5_rows = results_df.head(5)

        # Let's iterate with enumerate on the head
        for i, (index, row) in enumerate(top_5_rows.iterrows()):
            rank = i + 1
            name = row.get('libgeo', index)
            score = f"{row.get('weighted_score', 0):.2f}"
            
            row_vals = [str(rank), str(name), score]
            for cat in cat_scores:
                row_vals.append(f"{row.get(cat, 0):.2f}")
            
            md_lines.append("| " + " | ".join(row_vals) + " |")
        
        md_lines.append("")

        # 3. Detailed Breakdown
        md_lines.append("## Detailed Breakdown")
        
        for i, (index, row) in enumerate(top_5_rows.iterrows()):
            rank = i + 1
            name = row.get('libgeo', index)
            md_lines.append(f"### {rank}. {name}")
            
            # Re-use the logic to extract underlying details
            # We need to do this again or reuse the logic. 
            # Since we are rewriting the function, let's just do it here.
            
            target_codgeos = []
            if 'communes' in row and isinstance(row['communes'], list):
                target_codgeos = row['communes']
            elif 'codgeo' in row:
                target_codgeos = [row['codgeo']]
                if row.get('binome') and row.get('codgeo_binome'):
                    target_codgeos.append(row['codgeo_binome'])
            else:
                # Fallback to index if 'codgeo' column is not found (it's the index)
                target_codgeos = [index]
            
            if unaggregated_df is not None:
                for codgeo in target_codgeos:
                    if codgeo in unaggregated_df.index:
                        commune_data = unaggregated_df.loc[codgeo]
                        c_name = commune_data.get('libgeo', codgeo)
                        pop = int(commune_data.get('population', 0)) if pd.notna(commune_data.get('population')) else 0
                        
                        md_lines.append(f"#### {c_name} ({codgeo})")
                        md_lines.append(f"* **Population**: {pop}")
                        md_lines.append("* **Criteria Scores**:")
                        
                        # Extract and group scores
                        criteria_by_cat: Dict[str, Dict[str, float]] = {}
                        if score_to_cat:
                            for score_col, category in score_to_cat.items():
                                if score_col in commune_data:
                                    val = commune_data[score_col]
                                    try:
                                        score_val = float(val) if pd.notna(val) else 0.0
                                    except (ValueError, TypeError):
                                        score_val = 0.0
                                    # Always include the score, even if 0
                                    if category not in criteria_by_cat:
                                        criteria_by_cat[category] = {}
                                    criteria_by_cat[category][score_col] = score_val
                        else:
                             # Fallback
                            for k, v in commune_data.items():
                                if isinstance(k, str) and (k.endswith('_scaled') or k.endswith('_score')):
                                    try:
                                        score_val = float(v) if pd.notna(v) else 0.0
                                    except: score_val = 0.0
                                    category = 'other'
                                    if category not in criteria_by_cat:
                                        criteria_by_cat[category] = {}
                                    criteria_by_cat[category][k] = score_val

                        for cat, scores in criteria_by_cat.items():
                            md_lines.append(f"    * **{cat.capitalize()}**:")
                            for s_name, s_val in scores.items():
                                md_lines.append(f"        * `{s_name}`: {s_val:.2f}")
                        
                        md_lines.append("")
    else:
        md_lines.append("No results found.")

    # Write to file
    try:
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(md_lines))
    except Exception as e:
        logger.error(f"Failed to write log file: {e}")
