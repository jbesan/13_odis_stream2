# ODIS Architecture Guide (Technical / AI Agent)

## 1. Core Philosophy: Model-First

The ODIS application follows a **Model-First** architecture. All business logic, data persistence, and UI rendering MUST revolve around shared Pydantic models.

### Primary Models (`app/core/models.py`)
- **`SearchCriterias`**: Captures user input and intent.
- **`SearchResultsData`**: The container for all scoring results and agent artifacts. Includes `odis_brief` for the user profile summary.
- **`CommuneResult`**: Detailed data for a specific city, including specific agent analysis (`expert_analysis`), `odis_synthesis`, and `scorer_pitch`.

> [!IMPORTANT]
> **Single Source of Truth**: Never use untyped dictionaries (e.g., `st.session_state['app_data']`) for business-critical state. If it's a search result or an analysis, it belongs in the `SearchResultsData` model.

---

## 2. State Machine: LangGraph + Reducers

ODIS uses **LangGraph** to manage complex, multi-agent flows. The graph is **stateless** and relies on **functional updates (reducers)** to maintain the single source of truth.

### The Reducer Pattern (`app/agents/state.py`)
- When a node returns an update (e.g., a city analysis), it is merged into the global state via `merge_search_results`.
- **Robust Matching**: Cities are matched using a dual-key strategy: `Codgeo` (preferred) or `Normalized Name` (fallback). This ensures that artifacts from different tools (Scout, Web, Jobs) are linked even if identifiers vary slightly.
- **Bootstrapping**: The Graph is initialized with a "Shell" model (scores + population) which agents populate. Agents do not create new city records; they enrich existing ones.

---

## 3. Persistence & UI Sync

Since the UI (Streamlit) and the Graph run in different contexts, state synchronization is critical.

### UI -> Graph (Input)
- When triggering an analysis, the UI dumps the current `st.session_state.search_results` into the graph input using `.model_dump()`.

### Graph -> UI (Output)
- The UI MUST catch the updated `search_results` from the graph's final state and re-instantiate it as a `SearchResultsData` object in the session state.
- **Async Sync**: For background tasks (like the initial Scorer), the UI uses `odis_get_bg_result` to poll for completion and merges the results back into the session state model.

---

## 4. Implementation Guidelines for AI Agents

Before adding a new feature or agent, follow this checklist:

1.  **Update the Model**: If your agent generates new data, add a field to `CommuneResult` or `SearchResultsData` in `app/core/models.py`.
2.  **Use the Reducer**: Ensure your node returns a partial state update (e.g., `{"search_results": {...}}`). The `merge_search_results` reducer will handle the target city lookup and merge.
3.  **Identify by Codgeo**: Always include the `codgeo` in your response to ensure the reducer finds the correct city in the results list.
4.  **No Direct Mutation**: Agents MUST NOT attempt to mutate the session state directly. They return data; the Graph reducer and UI back-sync logic handle the persistence.
5.  **Revalidate Instances**: When working with Pydantic models in Streamlit, ensure `model_config = ConfigDict(revalidate_instances='never')` is set to avoid `ValidationError` when Streamlit reloads classes.

---

## 5. Directory Structure

- `app/core/models.py`: The single source of truth (Pydantic).
- `app/agents/graph.py`: The logic flow definition.
- `app/agents/state.py`: The State definition and Reducers.
- `app/ui/components.py`: The bridge between the Model and the User.

---

## 6. Data Architecture & Memory Optimization

To ensure scalability and prevent Out-Of-Memory (OOM) errors, ODIS uses a **Decoupled Data Architecture**.

### Global Singleton (`@st.cache_resource`)
- Heavy datasets (Communes, POIs, Roman index, etc.) are loaded **once** into a global, immutable cache.
- Access is centralized via `utils.data_loader.get_app_data()`.
- This eliminates per-session copies of multi-megabyte DataFrames.

### Lean Scoring & JIT Hydration
- **Global Data Split**: The main commune dataset is split into `odis` (numerical/categorical metadata) and `odis_geo` (raw WKB bytes).
- **Index-Driven Joins**: Both `odis` and `odis_geo` are indexed by `codgeo`. This allows $O(1)$ lookups and extremely fast `.join()` operations without redundant searching.
- **WKB-until-render**: To save memory and serialization time, geometries stay in **WKB (Well-Known Binary) bytes** throughout the entire pipeline. 
- **Just-in-Time (JIT) Hydration**: 
    - `3_Resultats.py`: Merges WKB bytes into the top-N results using a simple `.join(odis_geo)`.
    - `maps.py`: Decodes WKB into Shapely objects using `gpd.GeoSeries.from_wkb()` only at the moment of drawing. 
- **No Pre-computation**: Unused legacy pre-computations (like `area_geo` / dissolved department boundaries) have been removed to significantly speed up application startup.
- **Isolation**: Each user session only stores ~1-5MB of computed data, even when scoring 36,000+ communes.

---

## 7. Post-Scoring Background Orchestration

To maintain a highly responsive UI, all "side-effect" operations (Logging, Telemetry) and heavy AI tasks are decoupled from the main scoring execution.

### Unified Orchestrator (`app/agents/utils.py`)
- **`launch_post_scoring_tasks`**: The central entry point called by the UI after `ScoringEngine.run_optimized`.
- **Parallel Execution**: Uses `threading.Thread` to launch:
    - **AI Scorer**: Generates personalized pitches.
    - **Enrichment**: Fetches detailed association data (RAG).
    - **Audit & Telemetry**: Writes local Markdown logs and BigQuery events without blocking.
- **State Sync**: Results are stored in a `@st.cache_resource` singleton (`odis_bg_store`) and polled via fragments for JIT UI updates.
