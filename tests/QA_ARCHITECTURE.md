# 🛡️ QA Architecture: Modernized ODIS Testing Framework

This document details the quality assurance architecture of the ODIS project, explaining the 4-level test hierarchy, core design patterns, and instructions for running and extending the test suite.

---

## 🧭 Testing Philosophy

ODIS follows a **Spec-Driven Development (SDD)** approach. Our QA architecture is designed to prevent regression, enforce data contracts, and validate AI agent behavior while keeping test execution extremely fast, cost-efficient, and maintainable. 

Rather than pinning mathematical scoring outputs to brittle float-based snapshots (which break with any minor configuration or formula change), ODIS uses **Logical Invariants** to verify that scoring remains directionally correct, correctly bounded, and complete.

---

## 📂 Test Suite Hierarchy

The tests are organized into isolated directories corresponding to their level of concern:

```text
tests/
├── e2e/                     # Level 3: E2E and UI tests (Mocked Streamlit, Invariants)
├── evals/                   # Level 4: Live LLM evaluations (Golden dataset, skip by default)
├── integration/             # Level 2: Data contracts, loaders, API schema validations
├── unit/                    # Level 1: Core math, helper methods, offline agent contracts
├── QA_ARCHITECTURE.md       # This file
├── conftest.py              # Shared fixtures (default config, live yaml loading)
└── pytest.ini               # Pytest runtime configuration
```

### 1. Level 1: Unit Tests (`tests/unit/`)
*   **Scope**: Validates individual functions, mathematical scoring sub-methods, CCAS matching rules, and proximity calculations.
*   **Offline Agent Contracts**: Contains offline tests for ODIS Pydantic AI agents (the coordinator `ts_agent` and all 6 expert agents) utilizing `FunctionModel`. These mock the LLM output to assert that agents correctly interpret user queries and call tools with the expected parameters (e.g. correct query terms and location formats) without making live LLM calls.
*   **Service & Utility Hardening**: Fully covers core caching/garbage collection lifecycle (`memory.py`), query input sanitization and UI configuration mapping (`agents/utils.py`), offline semantic RAG search mock lookups (`rna_rag.py`), and telemetry logging format mapping (`telemetry.py`).
*   **Execution Time**: Extremely fast (<0.1s per test).

### 2. Level 2: Integration Tests (`tests/integration/`)
*   **Scope**: Verifies integration between code and local datasets/databases.
*   **Schema & Metrics Contract**: Ensures all 45 YAML-configured metrics in `scores_config.yaml` exist as valid columns in the loaded Parquet files, catching structural data shifts before execution.
*   **Reference Assertions**: Tests data loader and reference lookups (e.g., ROME code lookups, Waldec associations) using actual local data.

### 3. Level 3: Functional & System E2E (`tests/e2e/`)
*   **Scope**: Tests full multi-criteria scoring flows, PDF generation, rehydration, and Streamlit UI journeys.
*   **Logical Invariants Check**: Instead of checking exact numbers, the E2E runner asserts:
    - `weighted_score` is within `[0.0, 1.0]`.
    - Key columns (`weighted_score`, `libgeo`) contain no `NaN` values.
    - Results are sorted in descending order of compatibility score.
*   **Differential Sensitivity**: Validates that changes to weight profiles (e.g., prioritizing Employment vs Housing) directionally shift recommended communes.
*   **UI Test Optimization**: UI/auth tests are optimized to bypass large Parquet data loaders. They use `unittest.mock.patch` to mock `streamlit.session_state` and Streamlit widgets, allowing complete interface flows to execute in milliseconds.
*   **Streamlit AppTest Happy Path**: In [test_apptest_happy_path.py](file:///Users/jacques/dev/13_odis_stream2/tests/e2e/test_apptest_happy_path.py), we simulate the complete user journey through the 9 wizard steps of `2_Formulaire.py` and submission to `3_Resultats.py`. We mock expensive background AI agent tasks and BigQuery calls to run the test in under 4 seconds, verifying config propagation and scoring criteria activation.

### 4. Level 4: AI Quality Evaluation (`tests/evals/`)
*   **Scope**: Tests live LLM agent graph runs against golden datasets to evaluate routing, expert capabilities, and synthesis quality.
*   **Two Evaluation Sub-suites**:
    - **Full Analysis Evaluation (`test_golden_evals.py`)**: Runs the entire multi-agent LangGraph map-reduce swarm. Programmatically validates graph state routing, token count aggregation, and asserts that each expert successfully generated its specific qualitative report. Evaluated via `LLMJudge` to ensure the final synthesized report meets the target user needs.
    - **Brief Refinement Evaluation (`test_brief_evals.py`)**: Runs the isolated `refiner_agent` directly. This bypasses the expensive map-reduce swarm and evaluates the single agent briefing logic in 2-3 seconds at a cost of ~$0.0016 per run, enabling rapid iterations.
*   **Pydantic Evals Integration**: Utilizes the official `pydantic_evals` framework (`Dataset`, `Case`, `LLMJudge`) to run evaluation datasets.
*   **LLM-as-a-Judge (Rubric-Based)**: Instead of using brittle string asserts, `LLMJudge` is instantiated with a semantic rubric to evaluate whether the generated synthesis covers all specific constraints in the candidate's profile (job, housing, education, health, inclusion, and proximity preferences).
*   **Skipping Condition**: Because these tests make live Vertex AI calls and cost money/tokens, they are skipped by default and must be run explicitly.

---

## 🛠️ Key Design Patterns

### 1. The Safe Divisor Pattern
*   **Problem**: Float divisions on dynamically computed metrics (such as category score aggregation) can trigger `RuntimeWarning: divide by zero` or populate dataframes with `NaN` / `inf`.
*   **Solution**: In [app/core/scoring.py](file:///Users/jacques/dev/13_odis_stream2/app/core/scoring.py), avoid hardcoded replacements (like substituting 0 with 1) that can lead to misleading or skewed scoring distributions. Instead, use NumPy's `np.divide` with a conditional `where` mask:
    ```python
    raw_scores = np.divide(
        numerator,
        denominator,
        out=np.zeros_like(denominator, dtype=float),
        where=denominator > 0
    )
    ```
    This mathematically bypasses division where the denominator is zero, returning a clean `0.0` output safely.

### 2. Dynamic Fixtures (Anti-Drift)
*   **Problem**: Hardcoding expected criteria scores in unit tests causes tests to drift and fail whenever production YAML configuration changes.
*   **Solution**: Load active configuration directly in tests. In [tests/conftest.py](file:///Users/jacques/dev/13_odis_stream2/tests/conftest.py), `live_scores_cat` dynamically parses the production [scores_config.yaml](file:///Users/jacques/dev/13_odis_stream2/app/scores_config.yaml), keeping tests completely in sync with configuration.

### 3. FunctionModel Contract Testing
*   **Problem**: We need to verify that AI agents invoke tools correctly, but invoking live LLM endpoints in unit tests is slow, non-deterministic, and expensive.
*   **Solution**: Use Pydantic AI's `FunctionModel` in unit tests to mock the LLM's response. The mock model intercepts the request, asserts that the agent is sending the expected prompt/messages, returns a deterministic `ToolCallPart`, and validates that the agent successfully executes the local Python tool.

### 4. Streamlit AppTest & Page Navigation
*   **Problem**: In Streamlit's test runner, testing a sub-page directly (e.g., `AppTest.from_file("app/pages/2_Formulaire.py")`) prevents relative page switching (`st.switch_page`) from resolving correctly, throwing `StreamlitAPIException` because the page paths are evaluated relative to the initial entrypoint script. Additionally, custom Javascript/HTML bidirectional components (like `inject_idle_sleep` or Leaflet maps) lack a rendering DOM browser environment and crash when executed in a raw python process.
*   **Solution**: Always initialize `AppTest` from the root entrypoint (`app/main.py`). This establishes the correct execution directory, allowing manual or button-triggered redirections to resolve correctly (e.g., `at.switch_page("pages/2_Formulaire.py")`). Mock or patch custom frontend components (e.g., mocking `inject_idle_sleep` with `@patch`) to bypass browser-bound serialization and focus testing on execution flow, widgets inputs, and session state transitions.

### 5. Multi-Threaded Code Coverage
*   **Problem**: Because Streamlit's `AppTest` runs the target script inside a separate thread runner, standard coverage collection can miss the executed code lines in the sub-thread.
*   **Solution**: We run coverage profiling using standard thread tracing (supported natively by `pytest-cov`), which automatically captures the lines executed in `AppTest` threads, ensuring we report accurate coverage metrics (e.g. **90%** on `2_Formulaire.py` and **87%** on `3_Resultats.py`).

### 6. Spelling & Value Alignment
*   **Problem**: String-matching indicators (e.g., health needs selection) can drift between UI radio options (`config.py`) and scoring logic keys (`scoring.py`), causing critical score columns (like `sante_hopital_scaled`) to silently fail to activate.
*   **Solution**: Enforce strict alignment using correct human-readable spellings (e.g. `"Hôpital"` with the French accent) in the UI options, while supporting robust alias lookups (e.g. both `"Hôpital"` and `"Hopital"`) inside map loaders and scoring functions to maintain compatibility with ETL datasets and historical tests.

### 7. LLM-as-a-Judge Evaluation (`pydantic_evals`)
*   **Problem**: Stochastically generated LLM text outputs are highly fluid, making direct string matching (e.g. `assert "éducation" in output`) extremely fragile and prone to false negatives.
*   **Solution**: We run the evaluation using `pydantic_evals.Dataset`. We define evaluation cases using the candidate's `odis_brief` as input. We attach `LLMJudge` from `pydantic_evals.evaluators` to assess the actual output against a structured rubric using the live GCP Vertex AI model configuration, printing the detailed reasoning and pass/fail results.

### 8. Hooking Live Evaluations Dynamically
*   **Problem**: Standard batch/offline evaluations run via `Dataset.evaluate()` only populate the offline "Evals" tab in the Logfire UI. They do not populate Logfire's dedicated "Live Evals" (Online Evaluations) panel, which requires registering the `OnlineEvaluation` capability on target agents.
*   **Solution**: Inside the evaluation task closure, we dynamically hook the `OnlineEvaluation` capability to the target agent (e.g., `refiner_agent` or `synthesizer_agent`) inside a `try...finally` block. This ensures that the evaluation is registered as a live online evaluation in the Logfire UI without leaking or mutating the production agent's definition globally:
    ```python
    from pydantic_evals.online_capability import OnlineEvaluation
    online_eval = OnlineEvaluation(evaluators=[judge])
    agent.root_capability.capabilities.append(online_eval)
    try:
        result_run = await agent.run(...)
    finally:
        agent.root_capability.capabilities.remove(online_eval)
    ```

### 9. Programmatic Verification of Expert Swarm Outputs
*   **Problem**: We must confirm that the map-reduce swarm actually invokes the requested expert agents and that they each produce valid outputs, rather than failing silently or returning empty analyses.
*   **Solution**: During evaluation runs of the full swarm (in `test_golden_evals.py`), we programmatically verify that all expected experts (e.g., jobs, housing, health) successfully generated a report. We do this by checking the dictionary of generated expert reports inside the state and asserting a minimum character length:
    ```python
    for expert in scenario["expected_experts"]:
        assert expert in city_res.expert_analysis, f"Expert analysis for '{expert}' was not generated"
        assert len(city_res.expert_analysis[expert]) > 50, f"Analysis for '{expert}' is too short/empty"
    ```

---

## 💻 CLI Commands

Always run tests using the local virtual environment executable to avoid system-level package conflicts.

### Run All Default Tests (Unit, Integration, E2E)
```bash
.venv/bin/pytest
```

### Run Specific Levels
```bash
# Level 1: Unit
.venv/bin/pytest tests/unit/

# Level 2: Integration
.venv/bin/pytest tests/integration/

# Level 3: E2E & UI
.venv/bin/pytest tests/e2e/
```

### Run Live AI Evaluations (Level 4)
Evaluating live graphs requires the `RUN_EVALS` environment variable to be set to `true` (along with active Google Cloud Vertex AI credentials in your terminal). To prevent Pytest's default plugin from blocking Logfire's cloud telemetry export, run with `-p no:logfire` and `-s`:

```bash
# Run all evaluations (both brief refiner and full graph)
RUN_EVALS=true .venv/bin/pytest tests/evals/ -s -p no:logfire

# Run ONLY the fast & cheap brief refiner evaluations (~3s, $0.001)
RUN_EVALS=true .venv/bin/pytest tests/evals/test_brief_evals.py -s -p no:logfire

# Run ONLY the full map-reduce swarm graph evaluations (~80s)
RUN_EVALS=true .venv/bin/pytest tests/evals/test_golden_evals.py -s -p no:logfire
```

---

## 📈 Extension Guidelines

1.  **Adding a Metric**: If you add an indicator to `scores_config.yaml`, run the integration test suite (`tests/integration/test_data_contracts.py`). It will automatically check if the corresponding columns are present in the Parquet files.
2.  **Updating a Scoring Formula**: If you modify a normalization formula, run E2E checks (`tests/e2e/test_e2e.py`). You do **not** need to recreate snapshot files; the logical invariants will automatically validate that the output remains sorted, complete, and properly bounded.
3.  **Adding Golden Scenarios**: To evaluate new conversational patterns, append scenarios to [golden_scenarios.json](file:///Users/jacques/dev/13_odis_stream2/tests/evals/golden_scenarios.json) specifying the target query, expected experts, and expected keywords. Run evaluations using the command in section 3 above.
