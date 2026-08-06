import os
import logging
import json
import sys
import warnings
import logfire
from datetime import datetime
from typing import Optional, Any, List
from core.models import SearchCriterias, SearchResultsData

logger = logging.getLogger(__name__)


class JsonFormatter(logging.Formatter):
    """
    Formatter that outputs JSON strings after parsing the LogRecord.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "severity": record.levelname,
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "funcName": record.funcName,
            "line": record.lineno,
        }
        # Merge extra attributes if available. Cloud Run structured logging
        # requires one JSON object per physical line; a pretty-printed record
        # can otherwise be split before its ``severity`` is interpreted.
        indent = None
        if hasattr(record, "extra_data"):
            log_record.update(record.extra_data)  # type: ignore
            if not os.environ.get("K_SERVICE"):
                indent = 4

        json_out = json.dumps(
            log_record, ensure_ascii=False, default=str, indent=indent
        )

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
    warnings.filterwarnings(
        "ignore", category=DeprecationWarning, module="google.genai"
    )
    warnings.filterwarnings(
        "ignore", category=DeprecationWarning, module="google.genai.types"
    )

    # --- Reduce verbosity of third-party libraries ---
    loggers_to_silence = [
        "google.genai",
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
        "pydantic_ai",
        "pydantic_graph",
        "odis_graph",
    ]

    for logger_name in loggers_to_silence:
        l = logging.getLogger(logger_name)
        # Set google.genai and google_genai to ERROR to silence non-critical warnings like AFC compatibility
        if logger_name in ("google.genai", "google_genai"):
            l.setLevel(logging.ERROR)
        else:
            l.setLevel(logging.WARNING)
        l.propagate = False  # Prevent leaking to root/streamlit handlers
        if l.hasHandlers():
            l.handlers.clear()

    # Specifically for google.genai.models and google_genai.models which are very chatty (e.g. AFC warnings)
    for model_logger_name in ("google.genai.models", "google_genai.models"):
        m_logger = logging.getLogger(model_logger_name)
        m_logger.setLevel(logging.ERROR)
        m_logger.propagate = False
        if m_logger.hasHandlers():
            m_logger.handlers.clear()

    try:
        from absl import logging as absl_logging

        absl_logging.set_verbosity(absl_logging.ERROR)
    except ImportError:
        pass

    # Specifically for Streamlit fragment session warnings (harmless racy reruns)
    for fragment_logger in [
        "streamlit.runtime.fragment",
        "streamlit.runtime.fragment_manager",
    ]:
        f_logger = logging.getLogger(fragment_logger)
        f_logger.setLevel(logging.ERROR)
        f_logger.propagate = False

    # --- Logfire Instrumentation ---
    setup_logfire()


def setup_logfire() -> None:
    """
    Initializes Logfire with project-specific settings and instruments PydanticAI/HTTPX.
    """
    # If already configured (e.g. in test environment), avoid overriding the config
    if (
        getattr(logfire.DEFAULT_LOGFIRE_INSTANCE, "config", None)
        and logfire.DEFAULT_LOGFIRE_INSTANCE.config.environment == "test"
    ):
        logger.info("🔥 Logfire already configured for test environment.")
        return

    try:
        # Cloud Run staging and production receive an explicit deployment label.
        is_cloud_run = os.environ.get("K_SERVICE") is not None
        logfire_env = os.getenv("ODIS_DEPLOYMENT_ENV") or (
            "prod" if is_cloud_run else "local"
        )

        # GDPR Constraint: Disable sending telemetry to Logfire cloud on GCP/Cloud Run
        send_to_logfire = (
            False
            if (is_cloud_run or logfire_env in ("prod", "staging"))
            else "if-token-present"
        )

        logfire.configure(
            service_name="odis-stream2",
            environment=logfire_env,
            send_to_logfire=send_to_logfire,
        )
        logfire.instrument_pydantic_ai()
        logfire.instrument_httpx()
        # Suppress BigQuery automatic tracing to reduce noise (as requested)
        logfire.suppress_scopes("google.cloud.bigquery.opentelemetry_tracing")
        if send_to_logfire is False:
            logger.info(
                f"🔒 Logfire remote telemetry disabled in '{logfire_env}' environment (GCP GDPR compliance)."
            )
        else:
            logger.info(
                f"🔥 Logfire instrumentation enabled in '{logfire_env}' environment."
            )
    except Exception as e:
        logger.warning(f"⚠️ Failed to initialize Logfire: {e}")


# Initialize logging when module is imported
setup_logging()


def log_search_results(
    config: SearchCriterias,
    search_results: SearchResultsData,
    prefix: str = "search_results",
    interaction_id: Optional[str] = None,
    username: Optional[str] = None,
) -> None:
    """
    Logs the search configuration and the top results using the standard logger as a Markdown file in ./logs/.
    This logging is skipped if the application is detected to be running on Cloud Run.
    """
    # Skip logging if running on Cloud Run
    if os.environ.get("K_SERVICE"):
        return

    # --- Markdown Generation ---
    search_params = config.model_dump()
    # 🧪 SOTA: Robust Paris Timezone logic
    try:
        import zoneinfo

        paris_tz = zoneinfo.ZoneInfo("Europe/Paris")
        now_paris = datetime.now(paris_tz)
    except Exception:
        now_paris = datetime.now()

    timestamp_str = now_paris.strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".logs"))
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{timestamp_str}_{prefix}.md")

    md_lines = []
    md_lines.append(f"# Search Results - {now_paris.strftime('%Y-%m-%d %H:%M:%S')}")
    md_lines.append(f"* **User**: `{username or 'unknown'}`")
    md_lines.append(f"* **Interaction ID**: `{interaction_id or 'unknown'}`")
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
        if hasattr(v, "label"):
            return str(v.label)
        if isinstance(v, dict):
            if "label" in v:
                return str(v["label"])
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, (list, set)):
            if not v:
                return "[]"
            # Handle list/set of CriteriaItem or list of strings
            formatted_items = []
            for item in sorted(list(v)) if isinstance(v, set) else v:
                if hasattr(item, "label"):
                    formatted_items.append(str(item.label))
                elif isinstance(item, dict) and "label" in item:
                    formatted_items.append(str(item["label"]))
                elif isinstance(item, list):
                    # Recurse for nested lists (e.g. codes_metiers)
                    formatted_items.append(f"[{format_value(item)}]")
                else:
                    formatted_items.append(str(item))
            return ", ".join(formatted_items)
        return str(v)

    # Log all search parameters except weights (logged separately)
    excluded_keys = {"criteria_weights"}
    for key, val in sorted(search_params.items()):
        if not key.startswith("poids_") and key not in excluded_keys:
            md_lines.append(f"| {key} | {format_value(val)} |")

    # Weights
    md_lines.append("")
    md_lines.append("### Weights")
    md_lines.append("| Category | Weight |")
    md_lines.append("| :--- | :--- |")
    for key, value in sorted(search_params.items()):
        if key.startswith("poids_"):
            category = key.replace("poids_", "").capitalize()
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

    # Helper for pressentie codgeo
    pressentie_codgeo = (
        search_results.commune_pressentie.codgeo
        if search_results.commune_pressentie
        else None
    )

    # 3. Current & Shortlisted Location Reference Headers (if present)
    if search_results.current_geo:
        cg = search_results.current_geo
        md_lines.append("## Current Location Reference")
        md_lines.append(f"**Name**: {cg.name} ({cg.codgeo})")
        md_lines.append(f"**Global Score (Simulated)**: {cg.global_score:.2f}")
        md_lines.append("")

    if search_results.commune_pressentie:
        cp = search_results.commune_pressentie
        md_lines.append("## Shortlisted Location Reference (Ville Pressentie)")
        md_lines.append(f"**Name**: {cp.name} ({cp.codgeo})")
        md_lines.append(f"**Global Score**: {cp.global_score:.2f}")
        md_lines.append("")

    # 4. Synthetic Comparative Breakdown
    md_lines.append("## Detailed Comparative Breakdown")
    md_lines.append("")

    # Collect cities to compare: current_geo (if present) + top results + commune_pressentie (if outside top results)
    eval_cities = []
    if search_results.current_geo:
        eval_cities.append(("current", search_results.current_geo))
    for i, commune in enumerate(search_results.results):
        eval_cities.append((f"{i + 1}", commune))

    if search_results.commune_pressentie and not any(
        c.codgeo == pressentie_codgeo for _, c in eval_cities
    ):
        eval_cities.append(("pressentie", search_results.commune_pressentie))

    # Collect all categories across cities
    all_categories = set()
    for _, city in eval_cities:
        all_categories.update(city.scores.keys())

    for cat in sorted(all_categories):
        md_lines.append(f"### {cat.capitalize()}")

        # Headers: Catégorie / Critère, Poids Relatif, City columns
        table_headers = ["Catégorie / Critère", "Poids Relatif"]
        for role, city in eval_cities:
            is_press = pressentie_codgeo and city.codgeo == pressentie_codgeo
            if role == "current":
                table_headers.append(f"{city.name} (Ref)")
            elif role == "pressentie":
                table_headers.append(f"📌 {city.name} (Pressentie)")
            else:
                suffix = " 📌 (Pressentie)" if is_press else ""
                table_headers.append(f"{role}. {city.name}{suffix}")

        md_lines.append("| " + " | ".join(table_headers) + " |")
        md_lines.append(
            "| "
            + " | ".join([":---", ":---:"] + [":---:"] * (len(table_headers) - 2))
            + " |"
        )

        # Collect unique score criteria for this category
        criteria_map = {}
        for role, city in eval_cities:
            details = city.scores.get(cat, [])
            for d in details:
                sid = d.score_id
                if sid not in criteria_map:
                    criteria_map[sid] = {
                        "label": d.label,
                        "relative_weight": d.relative_weight,
                        "city_details": {},
                    }
                criteria_map[sid]["city_details"][role] = d

        for sid, cinfo in criteria_map.items():
            label_display = f"**{cinfo['label']}**<br>`{sid}`"
            rel_w_display = f"{cinfo['relative_weight']}%"
            row_cells = [label_display, rel_w_display]

            for role, city in eval_cities:
                cdetail = cinfo["city_details"].get(role)
                if cdetail is not None:
                    val_kpi = cdetail.valeur_kpi
                    unit = cdetail.unit.strip() if cdetail.unit else ""
                    if val_kpi is not None and str(val_kpi).strip() not in ("", "N/A"):
                        unit_str = f" {unit}" if unit else ""
                        val_str = f"{cdetail.score_normalise:.2f} ({val_kpi}{unit_str})"
                    else:
                        val_str = f"{cdetail.score_normalise:.2f}"
                else:
                    val_str = "-"
                row_cells.append(val_str)

            md_lines.append("| " + " | ".join(row_cells) + " |")

        md_lines.append("")

    # Write to file
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))
    except Exception as e:
        logger.error(f"Failed to write log file: {e}")


def log_agent_trace(agent_name: str, model_id: str, result: Any) -> None:
    """
    Logs the full AI agent interaction to a Markdown file in ./.logs/ (Local only).
    Skipped on Cloud Run (K_SERVICE env var).
    """
    # Disabled: Agent traces are monitored via Logfire
    return


def format_agent_result_to_md(agent_name: str, model_id: str, result: Any) -> str:
    """
    Formats a Pydantic-AI run result into a clean, readable Markdown audit trail.

    Args:
        agent_name: Name of the agent (e.g. 'refiner', 'scout').
        model_id: The model identifier string.
        result: The pydantic-ai RunResult object.

    Returns:
        A Markdown-formatted string.
    """
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        SystemPromptPart,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        NativeToolCallPart,
        NativeToolReturnPart,
    )

    md_lines: List[str] = []
    md_lines.append(
        f"# Agent Trace: {agent_name} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    md_lines.append(f"* **Model**: `{model_id}`")

    try:
        usage = result.usage
        md_lines.append(
            f"* **Usage**: {usage.total_tokens} tokens (In: {usage.input_tokens}, Out: {usage.output_tokens})"
        )
    except Exception:
        pass
    md_lines.append("")

    # --- Conversation History ---
    md_lines.append("## Conversation History")

    for i, msg in enumerate(result.all_messages()):
        if isinstance(msg, ModelRequest):
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
                elif isinstance(part, (ToolCallPart, NativeToolCallPart)):
                    md_lines.append(f"\n> 🛠️ **Tool Call**: `{part.tool_name}`")
                    try:
                        args = (
                            part.args.model_dump()
                            if part.args is not None
                            and hasattr(part.args, "model_dump")
                            else part.args
                        )
                        md_lines.append(
                            f"```json\n{json.dumps(args, indent=2, ensure_ascii=False)}\n```"
                        )
                    except Exception:
                        md_lines.append(f"```\n{part.args}\n```")
            md_lines.append("")

        # Handle tool returns (usually grouped in ModelRequest in the next turn)
        if hasattr(msg, "parts"):
            for part in msg.parts:
                if isinstance(part, (ToolReturnPart, NativeToolReturnPart)):
                    md_lines.append(f"### 📥 Tool Return: `{part.tool_name}`")
                    try:
                        content = part.content
                        if hasattr(content, "model_dump"):
                            content = content.model_dump()
                        md_lines.append(
                            f"```json\n{json.dumps(content, indent=2, ensure_ascii=False)}\n```"
                        )
                    except Exception:
                        md_lines.append(f"```\n{part.content}\n```")
                    md_lines.append("")

    # --- Final Output ---
    md_lines.append("## Final Structured Output")
    try:
        if hasattr(result.output, "model_dump"):
            md_lines.append(
                f"```json\n{json.dumps(result.output.model_dump(), indent=2, ensure_ascii=False)}\n```"
            )
        else:
            md_lines.append("```")
            md_lines.append(str(result.output))
            md_lines.append("```")
    except Exception as e:
        md_lines.append(f"*(Serialization failed: {e})*")
        md_lines.append(str(result.output))

    return "\n".join(md_lines)


def sanitize_for_json(obj: Any) -> Any:
    """
    Recursively convert an object to a JSON-serializable form.

    Args:
        obj: Any Python object.

    Returns:
        A JSON-serializable representation.
    """
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    if isinstance(obj, dict):
        return {str(k): sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [sanitize_for_json(i) for i in obj]
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return str(obj)
