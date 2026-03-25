import os
import logging
import json
import sys
import warnings
from datetime import datetime
from dataclasses import asdict
from typing import Optional, Dict, Any, List
import pandas as pd
from core.models import SearchCriterias, SearchResultsData, CommuneResult

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

        json_out = json.dumps(log_record, ensure_ascii=False, default=str, indent=indent)
        
        # Super ease developer trick: if multiline and local dev, append raw message
        message = record.getMessage()
        if "\n" in message and not os.environ.get("K_SERVICE"):
            # We use a clear separator to distinguish from the JSON blob
            return f"{json_out}\n\n[HUMAN READABLE MESSAGE]\n{message}\n"
            
        return json_out

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
        "watchdog",
        "pydantic",
        "pydantic_ai"
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

    # Specifically for Streamlit fragment session warnings (harmless racy reruns)
    for fragment_logger in ["streamlit.runtime.fragment", "streamlit.runtime.fragment_manager"]:
        f_logger = logging.getLogger(fragment_logger)
        f_logger.setLevel(logging.ERROR)
        f_logger.propagate = False

# Initialize logging when module is imported
setup_logging()

def log_search_results(
    config: SearchCriterias, 
    search_results: SearchResultsData,
    prefix: str = "search_results"
) -> None:
    """
    Logs the search configuration and the top results using the standard logger.
    This logging is skipped if the application is detected to be running on Cloud Run.
    """
    # Skip logging if running on Cloud Run
    if os.environ.get('K_SERVICE'):
        return

    # --- Markdown Generation ---
    search_params = config.model_dump()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.logs'))
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{prefix}_{timestamp}.md")

    md_lines = []
    md_lines.append(f"# Search Results - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    md_lines.append("")

    # 1. Configuration Section
    md_lines.append("## Configuration")
    md_lines.append("### Search Criteria")
    md_lines.append("| Parameter | Value |")
    md_lines.append("| :--- | :--- |")
    
    criteria_keys = ['commune_actuelle', 'loc_search_area', 'situation_famille', 'nb_enfants', 'besoin_emploi', 'besoin_sante']
    for key in criteria_keys:
        if key in search_params:
            val = search_params[key]
            # Handle SearchCriterias nested models if they exist
            if hasattr(val, 'label'): val = val.label
            elif isinstance(val, dict) and 'label' in val: val = val['label']
            md_lines.append(f"| {key} | {val} |")
    
    # Weights
    md_lines.append("")
    md_lines.append("### Weights")
    md_lines.append("| Category | Weight |")
    md_lines.append("| :--- | :--- |")
    for key, value in search_params.items():
        if key.startswith('poids_'):
            category = key.replace('poids_', '').capitalize()
            md_lines.append(f"| {category} | {value} |")
    md_lines.append("")

    # 2. Results Summary
    md_lines.append("## Top Results")
    if search_results.top_communes:
        # Get active categories from the first result
        first = search_results.top_communes[0]
        cat_keys = sorted(first.scores.keys())
        headers = ["Rank", "Commune", "Score"] + [k.capitalize() for k in cat_keys]
        
        md_lines.append("| " + " | ".join(headers) + " |")
        md_lines.append("| " + " | ".join([":---"] * len(headers)) + " |")

        for i, commune in enumerate(search_results.top_communes):
            row_vals = [str(i + 1), commune.name, f"{commune.global_score:.2f}"]
            # Calculate category averages from structured scores
            for cat in cat_keys:
                details = commune.scores.get(cat, [])
                if details:
                    avg_cat = sum(d.score_normalise for d in details) / len(details)
                    row_vals.append(f"{avg_cat:.2f}")
                else:
                    row_vals.append("0.00")
            md_lines.append("| " + " | ".join(row_vals) + " |")
    else:
        md_lines.append("No results found.")
    md_lines.append("")

    # 3. Current Location Comparison
    if search_results.current_geo:
        md_lines.append("## Current Location Reference")
        cg = search_results.current_geo
        md_lines.append(f"**Name**: {cg.name} ({cg.codgeo})")
        md_lines.append(f"**Global Score (Simulated)**: {cg.global_score:.2f}")
        md_lines.append("")

    # 4. Detailed Breakdown
    md_lines.append("## Detailed Breakdown")
    for i, commune in enumerate(search_results.top_communes):
        md_lines.append(f"### {i+1}. {commune.name} ({commune.codgeo})")
        md_lines.append(f"* **Population**: {commune.population:,}")
        md_lines.append(f"* **Global Score**: {commune.global_score:.2f}")
        md_lines.append("* **Criteria Details**:")
        
        for cat, details in commune.scores.items():
            md_lines.append(f"    * **{cat.capitalize()}**:")
            for d in details:
                # Use value_kpi and label
                val_kpi = d.valeur_kpi if d.valeur_kpi is not None else "N/A"
                md_lines.append(f"        * `{d.label}`: {val_kpi} {d.unit} (Score: {d.score_normalise:.2f}, Weight: {d.relative_weight}%)")
        md_lines.append("")

    # Write to file
    try:
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(md_lines))
    except Exception as e:
        logger.error(f"Failed to write log file: {e}")
