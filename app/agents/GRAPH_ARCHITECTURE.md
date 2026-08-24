# ODIS Graph Architecture (v7.0)

This document defines the technical architecture of the ODIS multi-agent orchestration, powered by `pydantic-graph`.

## 🏗️ Pipeline Topology (PM-Driven MapReduce Swarm & Local Workers)

ODIS follows a PM-driven **MapReduce (Spreading)** pattern for high-performance territorial analysis. The graph is designed to process user requests via a Project Manager (`ts_agent`) triage step that plans the swarm execution, combining parallel LLM expert agents with fast, zero-cost deterministic Python workers.

```mermaid
graph TD
    Input([SearchCriterias + User Question]) --> Graph
    
    subgraph Graph [pydantic-graph: PM-Driven Swarm & Local Workers]
        direction TB
        GStart((START)) --> Triage[1. TRIAGE / TS_AGENT Node]
        
        %% PM Planning / Routing
        Triage -.->|1. Lookup instructions by ID| Db[(Skills: Markdown Files)]
        Triage -->|2. Route: Direct Answer| GEnd((END))
        Triage -->|2. Route: Parallel Fan-out| Map[2. MAP: Domains to Run]
        
        subgraph ParallelSwarm [Parallel Swarm: LLM Experts & Local Deterministic Workers]
            direction LR
            Map --> JH[job_hunter (LLM)]
            Map --> HE[housing_expert (LLM)]
            Map --> ME[mobility_expert (LLM)]
            Map --> HC[healthcare_expert (LLM)]
            Map --> EE[education_expert (LLM)]
            Map --> SI[social_integration_expert (LLM)]
            Map --> CC[city_comparator (Local Python, 0 tokens)]
            Map --> CL[ccas_locator (Local Python, 0 tokens)]
            
            JH & HE & ME & HC & EE & SI & CC & CL --> Join[3. REDUCE: Join Results]
        end
        
        Join --> Synth[4. COMPOSITE SYNTHESIZER Node]
        Synth --> GEnd
    end
    
    GEnd --> UI[/Streamlit UI / PDF/]
    
    style Graph fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    style ParallelSwarm fill:#fff,stroke:#333
```

### 🧠 Core Orchestration Nodes

1.  **Triage Node / TS_AGENT (The Project Manager)**:
    *   **Planning**: Receives the user question, search criteria, and context. Runs a fast LLM (`ts_agent`) to evaluate the dossier, identify which experts to run, formulate custom task missions for each domain, and select the relevant **Skill Cards** by ID from the Markdown catalogue.
    *   **Validated routing contract**: `SwarmPlan` is the sole routing authority after triage. It rejects empty expert plans, direct answers without an answer, tasks attached to direct answers, duplicate experts, unknown Skill Cards, and Skill Cards owned by a different expert. The validated `swarm_mode` is copied into `GraphState` before any downstream node runs.
    *   **Decoupled File Fetching**: In a single synchronous pass, triage loads the selected skill card instructions from Markdown files using `KnowledgeStore`, storing them in `GraphState.expert_skill_instructions` to prevent concurrent file I/O during parallel worker runs.
    *   **Direct Answer Bypass**: If the user's question can be answered completely using the existing context, `ts_agent` generates a `direct_answer` and the triage node returns `End(direct_answer)` immediately, completely bypassing the MapReduce swarm and the Synthesizer.
    *   **Deterministic Workers Inclusion**: In `full_analysis` mode, `city_comparator` and `ccas_locator` are automatically appended to the fan-out execution list.
2.  **Extract Domains (Map)**: Fans out the chosen list of active domains into parallel worker nodes.
3.  **Parallel Workers (6 LLM Domain Experts + 2 Local Deterministic Workers)**:
    *   `job_hunter`: Finds ROME job offers (France Travail / SIAE).
    *   `housing_expert`: Evaluates rent m², housing delay, and housing availability.
    *   `mobility_expert`: Examines bus/tram networks and transit solidary pricing.
    *   `healthcare_expert`: Analyzes APL access indexes, hospitals, and PMI.
    *   `education_expert`: Lists local schools, kindergartens, and registration processes.
    *   `social_integration_expert`: Identifies refugee support associations, CCAS, and RNA resources.
    *   `city_comparator` (Local Python Worker): Compares indicators between the focus city and reference city using direction-aware scoring catalogue deltas and relative weights. Consumes 0 tokens, 0 cost, <15ms.
    *   `ccas_locator` (Local Python Worker): Deterministically retrieves and formats CCAS contact details for the commune or Bassin de Vie fallback. Consumes 0 tokens, 0 cost, <15ms.
    *   Each active expert returns an `AgentArtifact` (or `DomainArtifact`).
4.  **Join Node (Reduce)**: Accumulates `AgentArtifact` payloads from all parallel worker threads, merging their results and cumulative usage.
5.  **Composite Synthesizer Node**: Consumes pre-digested artifact snippets (Beneficiary briefing, Comparator metrics, CCAS card, and Expert summaries) to generate a concise **Executive Overview (150–250 words)**, a **Digested Territorial Comparison (short table + qualitative synthesis without recalculating math)**, **Unverified Elements**, and **Actionable Next Steps** (~300–400 tokens, ~1.5–2.0s), leaving domain expert cards to render directly in the UI without LLM rewriting.

---

## 🔀 Swarm Routing & Execution Paths

The triage node (`ts_agent`) dynamically determines how the user query should be processed, selecting one of three execution paths:

1. **Initial Full Analysis (`full_analysis`)**
   - **Trigger**: Occurs when the user initiates a search/analysis for a recommended city (e.g., initial page load) or when the user explicitly requests it (e.g., queries starting with or containing *"Fais une analyse complète de"*). This is also triggered if the focus city's expert report cache (`expert_analysis` / `"Analyses experts"`) is empty or missing.
   - **Flow**: The `ts_agent` evaluates criteria and plans individual missions (`ExpertTask`) for all relevant experts. Direct answer generation is strictly forbidden here. The graph fans out to run the parallel expert swarm, and then passes their responses to the `synthesizer` to build the full city briefing.

2. **Follow-up Specific Ask (`specific_ask`)**
   - **Trigger**: Subsequent conversational questions about a city where expert analysis reports are already present in the context.
   - **Flow (Swarm Route)**: If the follow-up question requires new external queries (e.g., asking for specific jobs or live transit/housing queries not covered in the cached report), the `ts_agent` plans specific expert tasks, executing the MapReduce swarm for only those thématiques before synthesizing.

3. **Direct Answer Bypass (`direct_answer`)**
   - **Trigger**: A follow-up conversational question that can be answered entirely using the existing expert reports and metrics already cached in the dossier's context.
   - **Flow**: The `ts_agent` sets `swarm_mode` to `'direct_answer'` and generates the final answer in French inside the `direct_answer` field, leaving the `tasks` list empty. The graph detects this bypass and returns `End(direct_answer)` immediately, completely skipping the expert swarm and synthesizer nodes.

---

## 💾 State & Data Flow

### ⚛️ `GraphState` (Dataclass)
We use a pure Python `@dataclass` for graph state to ensure compatibility with Streamlit's serialization. New fields include:
- `expert_tasks`: Maps active experts to custom task instructions generated by `ts_agent`.
- `expert_skill_instructions`: Holds the resolved skill cards instructions retrieved by the triage node from Markdown files.
- `usage`: Tracks cumulative token counts, requests, costs, and breakdown details.
- `run_id`, `run_attempt`, `run_deadline_at`, and `organization_id`: identify the detached optional-AI attempt and preserve its execution contract in graph telemetry/state.

### ⏱️ Session-local background run contract
`launch_background_city_analysis()` snapshots the input before starting a daemon worker. Every visible task has a random `run_id`, an `attempt` number, an owner/organization marker, a configurable `ODIS_GRAPH_RUN_TIMEOUT_SECONDS` deadline (60 seconds by default), and a cancellation event. A retry keeps the logical run ID, increments the attempt, and clears the prior city AI analysis from both the visible state and the retry snapshot. Completion uses run ID plus attempt matching, so a cancelled or superseded worker cannot overwrite the current result.

This is deliberately **best effort and session-local**: Cloud Run restart or session loss still discards the task. The UI exposes cancellation and retry. Cancellation prevents publication of a result, while an upstream provider request may still finish in the background. A durable queue/run store is required only if the product later promises continuation across restart or an independent service boundary.

### 🧩 Result Aggregation
Expert findings are encapsulated in `AgentArtifact` objects:
- `domain`: The expert name.
- `result`: Markdown analysis.
- `usage`: Token and cost breakdown.

---

## ⚡ Production Patterns

### 🔀 Type-Safe Decision Branching
We use `g.decision().branch()` to route flows based on the return type of the triage step (`ExpertList` vs `End`), ensuring the topology is clean and robust.

### 📊 Cumulative Usage & Cost Tracking
*   Every node execution captures `pydantic-ai` `UsageStats`.
*   A custom `.merge()` method on `UsageStats` accumulates inputs, outputs, request counts, cost USD, and breakdown mappings.
*   Merged usage is stored in `ctx.state.usage` at every step, ensuring BigQuery logs and token reports are complete.

### 🧪 Integration Testing
Tested and verified via:
- [test_direct_answer.py](file:///Users/jacques/dev/13_odis_stream2/tests/unit/test_direct_answer.py): Asserts direct-answer bypass plus the coordinator-to-synthesizer route handoff.
- [test_swarm_plan_validation.py](file:///Users/jacques/dev/13_odis_stream2/tests/unit/test_swarm_plan_validation.py): Rejects invalid routing and Skill Card ownership combinations.
- [test_graph_run_contract.py](file:///Users/jacques/dev/13_odis_stream2/tests/unit/test_graph_run_contract.py): Verifies snapshots, attempts, stale-write rejection, timeout, and cancellation.
- [test_skills_store.py](file:///Users/jacques/dev/13_odis_stream2/tests/unit/test_skills_store.py): Verifies file-based Markdown store CRUD and domain search operations.

### 🔧 Pydantic AI Upgrade & Tool Grounding Support
* **Native Tool Combination**: ODIS leverages Gemini's native tools (e.g. Google Search Grounding for web searches) alongside custom Python function tools registered through the agent constructor.
* **Library Version**: The current dependency pin is `pydantic-ai-slim[google,logfire]==2.27.0`.
* **Seamless Integration**: PydanticAI v2.27 manages the registered function tools alongside the `WebSearch()` capability on the configured Google provider profile.

---

## 🧠 Dynamic Context & Agent Support Subsystems

### 1. One-Shot Interviewer (Memory Injection)
The legacy multi-turn Interviewer has been replaced by a standalone **one-shot PydanticAI agent** that supports **state re-injection**.
- **Role**: Extract `SearchCriterias` from unstructured text.
- **Memory**: Supports memory injection via the `ODISContextBuilder`, allowing it to "see" previously identified criteria in its system prompt.
- **Location**: [interviewer.py](file:///Users/jacques/dev/13_odis_stream2/app/agents/interviewer.py)
- **Trigger**: Direct UI call via `run_autodetect_safe`.
- **Benefit**: Zero state management overhead for the UI while maintaining context awareness.

### 2. Metadata-Driven Context Injection (ACL)
To avoid manual "cherry-picking" of fields for each agent, ODIS uses a dynamic, metadata-driven architecture for prompt context construction.
- **Visibility Tags (`odis_visibility`)**: Pydantic models in `core/models.py` are decorated with visibility tags in `json_schema_extra`. This allows for a formal Access Control List (ACL) directly in the data models.
- **Generic Recursive Builder**: The `ODISContextBuilder` in `agents/state.py` automatically generates context blocks by:
  1. Iterating over model fields recursively.
  2. Filtering fields based on the component's `visibility_key`.
  3. Using `Field.description` as the human-readable JSON key for the LLM.
  4. Automatically simplifying complex objects (like `CriteriaItem`) into plain strings.

For the full visibility matrix and contract details, see [AGENT_CONTEXTS.md](file:///Users/jacques/dev/13_odis_stream2/app/agents/AGENT_CONTEXTS.md).

### 3. Swarm Prompt DRY Design
To prevent prompt duplication and avoid exposing internal system code names (like `ODIS`), agent prompt generation is centralized via `get_swarm_boilerplate(agent_type)` in [agent_config.py](file:///Users/jacques/dev/13_odis_stream2/app/agents/agent_config.py). 
- **Roles & Context**: It unifies system prompts by family (expert, coordinator, synthesizer), explicitly establishing the context of collaboration where the final user is a human Social Worker accompanying a beneficiary.
- **Token Efficiency**: It keeps agent prompts clean and concise, avoiding redundant context and reducing token usage.

### 4. Native BigQuery Vector Search (`ML.DISTANCE`)
For RAG-based search of inclusion-relevant associations, ODIS uses native BigQuery vector distance metrics instead of local Python-side calculations:
- **Database-Level Distance**: In `get_associations_semantic` in [rna_rag.py](file:///Users/jacques/dev/13_odis_stream2/app/services/rna_rag.py), the query embedding is generated via Vertex AI, L2-normalized, and passed to BigQuery, which calculates similarity natively using `1.0 - ML.DISTANCE(..., 'COSINE')`.
- **Minimal Network Overhead**: This avoids transferring large float arrays (representing candidate embeddings) over the network for local NumPy dot-product comparisons, minimizing memory footprint and network latency.
- **Search Query Optimization**: To prevent geographical words from diluting semantic match scores, expert tools enforce a strict rule instructing LLMs not to include the city name in the query. Partitioning and filtering are handled via `codgeo` at the SQL level.
