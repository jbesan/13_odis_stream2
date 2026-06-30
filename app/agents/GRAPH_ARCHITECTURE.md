# ODIS Graph Architecture (v6.0)

This document defines the technical architecture of the ODIS multi-agent orchestration, powered by `pydantic-graph`.

## 🏗️ Pipeline Topology (PM-Driven MapReduce Swarm)

ODIS follows a PM-driven **MapReduce (Spreading)** pattern for high-performance territorial analysis. The graph is designed to process user requests via a Project Manager (`ts_agent`) triage step that plans the swarm execution.

```mermaid
graph TD
    Input([SearchCriterias + User Question]) --> Graph
    
    subgraph Graph [pydantic-graph: PM-Driven Swarm]
        direction TB
        GStart((START)) --> Triage[1. TRIAGE / TS_AGENT Node]
        
        %% PM Planning / Routing
        Triage -.->|1. Lookup instructions by ID| Db[(Skills: Markdown Files)]
        Triage -->|2. Route: Direct Answer| GEnd((END))
        Triage -->|2. Route: Expert Tasks| Map[2. MAP: Domains to Run]
        
        subgraph ParallelSwarm [Parallel Expert Swarm]
            direction LR
            Map --> JH[job_hunter]
            Map --> HE[housing_expert]
            Map --> ME[mobility_expert]
            Map --> HC[healthcare_expert]
            Map --> EE[education_expert]
            Map --> SI[social_integration_expert]
            
            JH & HE & ME & HC & EE & SI --> Join[3. REDUCE: Join Results]
        end
        
        Join --> Synth[4. SYNTHESIZER Node]
        Synth --> GEnd
    end
    
    GEnd --> UI[/Streamlit UI / PDF/]
    
    style Graph fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    style ParallelSwarm fill:#fff,stroke:#333
```

### 🧠 Core Orchestration Nodes

1.  **Triage Node / TS_AGENT (The Project Manager)**:
    *   **Planning**: Receives the user question, search criteria, and context. Runs a fast LLM (`ts_agent`) to evaluate the dossier, identify which experts to run, formulate custom task missions for each domain, and select the relevant **Skill Cards** by ID from the database.
    *   **Decoupled File Fetching**: In a single synchronous pass, triage loads the selected skill card instructions from Markdown files using `KnowledgeStore`, storing them in `GraphState.expert_skill_instructions` to prevent concurrent file I/O during parallel worker runs.
    *   **Direct Answer Bypass**: If the user's question can be answered completely using the existing context, `ts_agent` generates a `direct_answer` and the triage node returns `End(direct_answer)` immediately, completely bypassing the MapReduce swarm and the Synthesizer.
2.  **Extract Domains (Map)**: Fans out the chosen list of active domains into parallel worker nodes.
3.  **Expert Workers (6 Domain Experts)**:
    *   `job_hunter`: Finds ROME job offers (France Travail / SIAE).
    *   `housing_expert`: Evaluates rent m², housing delay, and CCAS.
    *   `mobility_expert`: Examines bus/tram networks and transit solidary pricing.
    *   `healthcare_expert`: Analyzes APL access indexes, hospitals, and PMI.
    *   `education_expert`: Lists local schools, kindergartens, and registration processes.
    *   `social_integration_expert`: Identifies refugee support associations, CCAS, and RNA resources.
    *   Each active expert runs in a **single turn** (no ReAct tool calling loops), reading its specific instructions directly from `GraphState.expert_skill_instructions` and querying its specific APIs/tools.
4.  **Join Node (Reduce)**: Accumulates `AgentArtifact` payloads from all parallel worker threads, merging their results and cumulative usage.
5.  **Synthesizer Node**: Consumes the aggregated expert analysis and user situation to compile the final markdown response (global pitch or targeted chatbot answer).

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
- [test_direct_answer.py](file:///Users/jacques/dev/13_odis_stream2/tests/test_direct_answer.py): Asserts the Direct Answer bypass, swarm map-reduce, and synthesizer integration.
- [test_skills_store.py](file:///Users/jacques/dev/13_odis_stream2/tests/test_skills_store.py): Verifies file-based Markdown store CRUD and domain search operations.

### 🔧 Pydantic AI Upgrade & Tool Grounding Support
* **Native Tool Combination**: ODIS leverages Gemini's native tools (e.g. Google Search Grounding for web searches) alongside custom Python function tools defined via `@agent.tool` on the expert agents.
* **Library Upgrade**: The project was upgraded to `pydantic-ai-slim[google,logfire]==1.107.0` (from `1.76.0`) to resolve a critical runtime validation error where the Google GenAI provider would throw `UserError: Google does not support function tools and built-in tools at the same time` when both types of tools were attached to the same agent.
* **Seamless Integration**: With version `1.107.0`+, `pydantic-ai` natively manages combining custom function tools and native Gemini tools on the Google provider, allowing expert agents to perform local tool lookups while concurrently utilizing the native `WebSearchTool`.

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

