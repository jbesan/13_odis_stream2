# 🤖 ODIS Agent Documentation

The ODIS Graph uses a dynamic orchestration pattern where every node is aware of the current state consistency:

1.  **Criteria Hashing**: Every set of `SearchCriterias` is fingerprinted with an MD5 hash.
2.  **Cache Detection**: The `scorer_node` checks the state's `search_results.search_hash`. If it matches the current criteria, the heavy Scoring Engine is bypassed.
3.  **Unified Node Pattern**: Expert nodes are shared between "Full Analysis" and "Solo Deep-Dive" paths, ensuring code consistency and easier maintenance.

## 🏗️ Graph Architecture

The graph follow a Dispatcher/Expert pattern:
 to minimize redundant computations:
- **Hash-Driven Bypassing**: The `scorer` node detects if search results are already fresh in the state (matching hash) and instructs the agent to use them directly.
- **Unified & Solo Experts**: Expert nodes (`scout`, `web`, `job_hunter`) are unified and mode-aware, supporting both full search analysis and specific city deep-dives through intelligent graph wiring.
- **Model-First Integrity**: Strict Pydantic enforcement ensures all payloads (`search_hash`, `current_geo`, `population`) are valid at every step.

## 🎯 Project Overview

ODIS Stream 2 is a "reverse search" tool designed for social workers. It identifies the most relevant French communes for individuals or families based on their specific life project (employment, housing, education, health, inclusion).

## 🏗️ Core Architecture

- **Scoring Engine (`app/scoring.py`)**: The brain of the application. Calculates relevance scores.
- **Data Ingestion (`app/data_loader.py`)**: Loads optimized Parquet files from `data/`.
- **MCP Server (`app/mcp_server.py`)**: Exposes project data and scoring as tools for the AI Agent.
- **ETL Pipeline (`pipeline/`)**: Handles data gathering, building, and pre-scoring (now with Brotli compression for production files).
- **UI (`app/`)**: Streamlit-based interface for social workers.

## ⚙️ Development Workflows (CRITICAL)

Always follow the **Spec-Driven Development** process:

1.  **PRD First**: Ensure the feature/fix is documented in [PRD.md](file:///Users/jacques/dev/13_odis_stream2/PRD.md).
2.  **Implementation Plan**: Create/update `implementation_plan.md` in `.agent/` and get approval.
3.  **Task Tracking**: Use `task.md` in `.agent/` to track granular steps.
4.  **Continuous Testing**: Run `pytest --tb=line` after **every** significant change.
5.  **Documentation**: Update READMEs, the PRD, and `SCORE_EXAMPLE.md` after completing a feature.

## 🧪 Testing & Verification

- **Targeted Testing**: Run relevant tests first (e.g., `pytest app/tests/test_scoring.py --tb=line`).
- **Full Suite**: Run `pytest app/tests/ --tb=line` before any deployment or finalization.
- **Venv**: Always use the virtual environment (`.venv`) for running commands.

## 💬 Communication Style

- **Persona**: Senior Software Engineer mentor.
- **Tone**: Direct, professional, no fluff.
- **Evaluation**: Always evaluate multiple options before suggesting the best approach.
- **Git**: Ask for confirmation before any `git commit` or `git push`.

## 📂 Data Summary

- `odis_communes.parquet`: Base commune data.
- `odis_referentiels.parquet`: Centralized names and codes for matching.
- `odis_*_agg.parquet`: Aggregated metrics for métiers, associations, and formations.
- `scores_config.yaml`: Descriptions and labels for the scoring criteria.
