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
    Logs the search configuration and the top results using the standard logger as a Markdown file in ./logs/.
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
    
    def format_value(v: Any) -> str:
        if v is None:
            return "N/A"
        if isinstance(v, bool):
            return "Yes" if v else "No"
        if isinstance(v, (int, float)):
            return str(v)
        if hasattr(v, 'label'):
            return str(v.label)
        if isinstance(v, dict):
            if 'label' in v: return str(v['label'])
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, (list, set)):
            if not v: return "[]"
            # Handle list/set of CriteriaItem or list of strings
            formatted_items = []
            for item in sorted(list(v)) if isinstance(v, set) else v:
                if hasattr(item, 'label'):
                    formatted_items.append(str(item.label))
                elif isinstance(item, dict) and 'label' in item:
                    formatted_items.append(str(item['label']))
                elif isinstance(item, list):
                    # Recurse for nested lists (e.g. codes_metiers)
                    formatted_items.append(f"[{format_value(item)}]")
                else:
                    formatted_items.append(str(item))
            return ", ".join(formatted_items)
        return str(v)

    # Log all search parameters except weights (logged separately)
    excluded_keys = {'criteria_weights'}
    for key, val in sorted(search_params.items()):
        if not key.startswith('poids_') and key not in excluded_keys:
            md_lines.append(f"| {key} | {format_value(val)} |")

    
    # Weights
    md_lines.append("")
    md_lines.append("### Weights")
    md_lines.append("| Category | Weight |")
    md_lines.append("| :--- | :--- |")
    for key, value in sorted(search_params.items()):
        if key.startswith('poids_'):
            category = key.replace('poids_', '').capitalize()
            md_lines.append(f"| {category} | {value} |")
    md_lines.append("")

    # 2. Results Summary
    md_lines.append("## Top Results")
    if search_results.results:
        # Get active categories from the first result
        first = search_results.results[0]
        cat_keys = sorted(first.scores.keys())
        headers = ["Rank", "Commune", "Score"] + [k.capitalize() for k in cat_keys]
        
        md_lines.append("| " + " | ".join(headers) + " |")
        md_lines.append("| " + " | ".join([":---"] * len(headers)) + " |")

        for i, commune in enumerate(search_results.results):
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

    # 3. Current Location Reference
    if search_results.current_geo:
        md_lines.append("## Current Location Reference")
        cg = search_results.current_geo
        md_lines.append(f"**Name**: {cg.name} ({cg.codgeo})")
        md_lines.append(f"**Global Score (Simulated)**: {cg.global_score:.2f}")
        md_lines.append("")
        
        # Detailed breakdown for Current Location
        md_lines.append("### Detailed Breakdown (Current)")
        for cat, details in sorted(cg.scores.items()):
            md_lines.append(f"#### {cat.capitalize()}")
            md_lines.append("| Technical ID | Label | Value | Score | Weight |")
            md_lines.append("| :--- | :--- | :--- | :--- | :--- |")
            for d in details:
                val_kpi = d.valeur_kpi if d.valeur_kpi is not None else "N/A"
                md_lines.append(f"| `{d.score_id}` | {d.label} | {val_kpi} {d.unit} | {d.score_normalise:.2f} | {d.relative_weight}% |")
            md_lines.append("")

    # 4. Detailed Breakdown (Top Results)
    md_lines.append("## Detailed Breakdown (Top Results)")
    for i, commune in enumerate(search_results.results):
        md_lines.append(f"### {i+1}. {commune.name} ({commune.codgeo})")
        md_lines.append(f"* **Population**: {commune.population:,}")
        md_lines.append(f"* **Global Score**: {commune.global_score:.2f}")
        md_lines.append("")
        
        for cat, details in sorted(commune.scores.items()):
            md_lines.append(f"#### {cat.capitalize()}")
            md_lines.append("| Technical ID | Label | Value | Score | Weight |")
            md_lines.append("| :--- | :--- | :--- | :--- | :--- |")
            for d in details:
                val_kpi = d.valeur_kpi if d.valeur_kpi is not None else "N/A"
                md_lines.append(f"| `{d.score_id}` | {d.label} | {val_kpi} {d.unit} | {d.score_normalise:.2f} | {d.relative_weight}% |")
            md_lines.append("")

    # Write to file
    try:
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(md_lines))
    except Exception as e:
        logger.error(f"Failed to write log file: {e}")


def log_agent_trace(agent_name: str, model_id: str, result: Any) -> None:
    """
    Logs the full AI agent interaction to a Markdown file in ./logs/ (Local only)
    and pushes a trace to BigQuery (Cloud).
    """
    # 1. BigQuery Telemetry (Cloud) - Aligned approach
    try:
        from services.bq_logger import log_llm_trace_to_bq
        log_llm_trace_to_bq(agent_name, model_id, result)
    except Exception as e:
        logger.warning(f"⚠️ [LOGGING] Could not log LLM trace to BigQuery: {e}")

    # 2. Markdown Logging (Local only)
    if os.environ.get('K_SERVICE'):
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.logs'))
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"trace_{agent_name}_{timestamp}.md")

    md_lines = []
    md_lines.append(f"# Agent Trace: {agent_name} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    md_lines.append(f"* **Model**: `{model_id}`")
    
    usage = result.usage()
    md_lines.append(f"* **Usage**: {usage.total_tokens} tokens (In: {usage.input_tokens}, Out: {usage.output_tokens})")
    md_lines.append("")

    # --- Conversation History ---
    md_lines.append("## Conversation History")
    
    for i, msg in enumerate(result.all_messages()):
        role = "System"
        from pydantic_ai.messages import ModelRequest, ModelResponse, SystemPromptPart, TextPart, ToolCallPart, ToolReturnPart
        
        if isinstance(msg, ModelRequest):
            # Request might contain SystemPrompt or User message Parts
            for part in msg.parts:
                if isinstance(part, SystemPromptPart):
                    md_lines.append("### 💻 System Prompt")
                    md_lines.append("```markdown")
                    md_lines.append(part.content)
                    md_lines.append("```\n")
                elif isinstance(part, TextPart):
                    md_lines.append(f"### 👤 User Message [{i}]")
                    md_lines.append(part.content)
                    md_lines.append("\n")
        
        elif isinstance(msg, ModelResponse):
            md_lines.append(f"### 🤖 Assistant Response [{i}]")
            for part in msg.parts:
                if isinstance(part, TextPart):
                    md_lines.append(part.content)
                elif isinstance(part, ToolCallPart):
                    md_lines.append(f"\n> 🛠️ **Tool Call**: `{part.tool_name}`")
                    try:
                        args = part.args.model_dump() if hasattr(part.args, 'model_dump') else part.args
                        md_lines.append(f"```json\n{json.dumps(args, indent=2, ensure_ascii=False)}\n```")
                    except:
                        md_lines.append(f"```\n{part.args}\n```")
            md_lines.append("")

        # Handle tool returns (usually grouped in ModelRequest in the next turn)
        if hasattr(msg, 'parts'):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    md_lines.append(f"### 📥 Tool Return: `{part.tool_name}`")
                    try:
                        content = part.content
                        if hasattr(content, 'model_dump'): content = content.model_dump()
                        md_lines.append(f"```json\n{json.dumps(content, indent=2, ensure_ascii=False)}\n```")
                    except:
                        md_lines.append(f"```\n{part.content}\n```")
                    md_lines.append("")

    # --- Final Output ---
    md_lines.append("## Final Structured Output")
    try:
        if hasattr(result.output, 'model_dump'):
            md_lines.append(f"```json\n{json.dumps(result.output.model_dump(), indent=2, ensure_ascii=False)}\n```")
        else:
            md_lines.append("```")
            md_lines.append(str(result.output))
            md_lines.append("```")
    except Exception as e:
        md_lines.append(f"*(Serialization failed: {e})*")
        md_lines.append(str(result.output))

    # Write to file
    try:
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(md_lines))
    except Exception as e:
        logger.error(f"Failed to write agent trace log file: {e}")
