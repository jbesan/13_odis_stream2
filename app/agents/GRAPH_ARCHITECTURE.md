# ODIS Graph Architecture (v5.0)

This document defines the technical architecture of the ODIS multi-agent orchestration, powered by `pydantic-graph`.

## 🏗️ Pipeline Topology (MapReduce)

ODIS follows a native **MapReduce (Spreading)** pattern for high-performance territorial analysis. The graph is designed to be a unidirectional data processing pipe.

```mermaid
graph TD
    Input([Notes/Projet de vie]) --> Interviewer[[Interviewer Agent]]
    Interviewer -->|SearchCriteria| Engine[Scoring Engine]
    Engine -->|Top 5 Cities| Graph
    
    subgraph Graph [pydantic-graph: Deep Analysis]
        direction TB
        GStart((START)) --> Triage[TRIAGE Node]
        Triage -->|Route: Specific Ask| Router{Router LLM}
        Triage -->|Route: Full Analysis| Map[Map: Domains]
        
        Router -->|Action: Analysis| Map
        Router -->|Action: Direct Answer| Synth[SYNTHESIZER Node]
        
        subgraph Parallel [MapReduce Spreading]
            Map --> S[Expert: Scout]
            Map --> W[Expert: Web]
            Map --> J[Expert: Job Hunter]
            S & W & J --> Join[Join: Collect Results]
        end
        
        Join --> Synth
        Synth --> GEnd((END))
    end
    
    Engine --> Refiner[[AI Refiner Agent]]
    GEnd --> UI[/Streamlit UI / PDF/]
    Refiner -->|Unified Briefing & Pitch| UI
    
    style Graph fill:#f9f9f9,stroke:#333,stroke-dasharray: 5 5
    style Interviewer fill:#e1f5fe,stroke:#01579b
    style Refiner fill:#e1f5fe,stroke:#01579b
    style Parallel fill:#fff,stroke:#333
```

### 🧠 Core Orchestration Nodes

1.  **Triage Node (The Router)**: The entry point. 
    - **Logic**: If `execution_mode` is `full_analysis`, it automatically triggers all experts. If `specific_ask`, it calls the **Router Agent** (LLM) to analyze the user's intent.
    - **Decision**: Returns either an `ExpertList` (for parallel fan-out) or a `DirectSynthesis` DTO (if the LLM can answer using existing context).
2.  **Extract Domains (Map)**: A standard mapping node that fans out the `ExpertList` into parallel worker instances.
3.  **Expert Worker**: A generic node instance that delegates to specialized agents (`scout`, `web`, `job_hunter`).
4.  **Join Node**: A `g.join()` node that merges `AgentArtifacts` into a single list.
5.  **Synthesizer Node**: The final stage. It merges expert findings into the state and generates the final user-facing Markdown response.

---

## 🧩 Decoupled AI Components

While the `odis_graph` handles deep city analysis (on-demand), other specialized tasks are decoupled for performance and UX:

### 1. The Interviewer (Technical Extraction)
- **Role**: Extracts `SearchCriterias` from unstructured text (Auto-Detect) or conversation.
- **Output**: A structured Pydantic model mapped to the UI session state.
- **Flow**: Acts as the first gate. Its text response is minimal, confirming only the technical extraction.

### 2. The Refiner (Synthesis & Pitch)
- **Role**: The "Brain" of the search results. It performs three critical syntheses:
    - **Dossier Summary** (`odis_brief`) : A narrative summary of the user's situation.
    - **Global Pitch** : An introduction to the search results.
    - **City Pitches** : Narrative "pros/cons" for each city in the Top 5.
- **Location**: Triggered as a background task in `app/agents/utils.py` immediately after the `ScoringEngine` runs.
- **UI Interaction**: Blocks the UI (Guardrail) until the synthesis is ready to ensure data stability.

---

## 💾 State & Data Flow

### ⚛️ `GraphState` (Dataclass)
Unlike legacy versions, we use a pure Python `@dataclass` for state. This ensures:
- **Streamlit Stability**: Avoids `PydanticRedefinitionError` during code changes.
- **Statelessness**: The graph is instantiated and run from scratch for every request, with state passed explicitly.

### 🧩 Result Aggregation
Expert findings are encapsulated in `AgentArtifact` objects:
- `domain`: The expert name.
- `result`: Markdown content.
- `usage`: Captured token metrics.

Merging happens in the `synthesizer_step` by matching the `focus_city` codgeo in the `search_results`.

---

## ⚡ Production Patterns

### 🔀 Decision Branching (SOTA)
We use `g.decision().branch()` for clean, type-safe routing. This replaces legacy complex edge functions and ensures the graph topology is visible in the code.

### 📊 Usage Instrumentation
Every node (Triage, Expert, Synthesizer) captures `pydantic-ai` `UsageStats`. These are merged into the global state to provide accurate cost and token tracking for the session.

### 🧪 Integration Testing
The architecture is verified via:
- `test_graph_verification.py`: End-to-end execution of the full MapReduce flow.
- `test_interviewer_agent.py`: Testing the one-shot extraction logic.

---

## 📝 Configuration Standard

- **Model Mapping**: 
    - `router`: GPT-4o-mini or Gemini Flash (Fast).
    - `experts`: Gemini Flash (Multimodal/Search capability).
    - `synthesizer`: Gemini Flash (Context handling).
- **Tooling**: Experts leverage `GoogleSearchGrounding` (Web) and `BraveSearch` / `GoogleMaps` (Scout) for real-time data.
