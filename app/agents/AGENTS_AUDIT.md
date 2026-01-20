# 🏗️ Technical Specification: ODIS Agents Refactoring (v2)

## 1. Context & Objective

The current ODIS architecture relies on a manual imperative orchestration (`MultiAgentOrchestrator`) and "vanilla" agent implementations. While functional, it suffers from strong coupling with the UI (Streamlit), rigid control flow, and fragile prompt engineering.

**Objective:** Refactor the system into a SOTA "Agentic" architecture using **LangGraph** for orchestration and **PydanticAI** for individual agent logic.

## 2. Current Architecture Audit (Pain Points)

### A. The "God Object" Orchestrator (`orchestrator.py`)

- **Issue:** `process_message` mixes routing logic (LLM decisions), execution logic (calling agents), and business logic (the "Decoration" cascade).
- **Consequence:** Implementing parallel execution (Scout + Web) or complex feedback loops (Interviewer validation) requires messy nested `if/else` blocks. The workflow is imperative, not declarative.

### B. UI/Logic Coupling in Tools (`tools.py`)

- **Issue:** Tool functions contain side effects like `st.toast()` and directly mutate `st.session_state.agent.context`.
- **Consequence:** Tools are not testable outside Streamlit. They violate the "Pure Function" principle.
- **Target:** Tools should accept inputs and return data. The Orchestrator manages the state; the UI layer manages the feedback.

### C. Fragile Prompt Injection (`base.py`)

- **Issue:** System prompts are constructed using `str.replace("{BRIEFING}", ...)`.
- **Consequence:** High risk of prompt injection or formatting errors if the injected content contains curly braces.
- **Target:** Use proper dependency injection and PydanticAI's robust prompt templating.

---

## 3. Target Architecture: Hybrid SOTA Stack

We will adopt a hybrid approach combining the strengths of two frameworks:

1.  **LangGraph:** To manage the global workflow, state persistence, and routing (The "Skeleton").
2.  **PydanticAI:** To enforce type safety and validation within each agent node (The "Muscles").

### 3.1 Global State (LangGraph)

Replace the current `AgentContext` with a structured `TypedDict` or Pydantic model managed by LangGraph.

- **State:** Holds `messages`, `user_profile`, `search_criteria`, `top_cities`, `briefing`.
- **Persistence:** Enables "Time Travel" (debugging) and distinct conversational threads.

### 3.2 Component Refactoring Plan

#### Step 1: Decouple Tools (`tools.py`)

- Refactor all tools to be **Pure Functions**.
- **Input:** Typed arguments (Pydantic models or primitives).
- **Output:** Structured data (Dict or Pydantic Model).
- **REMOVE:** All `st.toast`, `st.write`, and `st.session_state` references.
- **Action:** Move UI feedback to the Streamlit `main.py` loop (listening to graph events).

#### Step 2: Agent Standardization (`agents/*.py`)

- Convert `InterviewerAgent`, `ScorerAgent`, etc., to **PydanticAI Agents**.
- **Dependency Injection:** Inject database connections or API clients via `deps`.
- **Validation:** Use `result_type` to enforce structured outputs (e.g., `ScorerAgent` MUST return a `TopCities` object).

#### Step 3: Graph Implementation (`orchestrator.py`)

- Replace `MultiAgentOrchestrator` class with a `StateGraph`.
- **Nodes:**
  - `router_node`: Determines intent.
  - `interviewer_node`: Handles discovery loop.
  - `scorer_node`: Computes results.
  - `refiner_node`: (New) Condenses history into the `briefing` field after each turn.
- **The "Decoration" Sub-Graph:**
  - Implement `scout_node` and `web_node` in **PARALLEL**.
  - Wait for both to finish -> trigger `job_hunter_node`.
  - Trigger `synthesizer_node` (Final answer).

---

## 4. Implementation Guidelines (Best Practices)

1.  **Separation of Concerns:**
    - **Agent Layer:** Only logic and data processing. Returns objects.
    - **Orchestration Layer:** Routing and State updates.
    - **UI Layer (Streamlit):** Rendering and User Feedback. It observes the Graph execution.

2.  **State Management:**
    - Do not mutate state inside Tools.
    - Nodes return state updates (diffs), LangGraph merges them.

3.  **Parallelization:**
    - Use `asyncio.gather` or LangGraph's parallel node execution for independent tasks (Scout + Web) to reduce latency.

4.  **Prompt Engineering:**
    - Move prompts to `prompts.py` or keep them inside the Agent definition using PydanticAI's `@agent.system_prompt` decorator for better readability.

## 5. Next Steps for Antigravity

1.  Create `app/agents/graph.py` to define the LangGraph structure.
2.  Refactor `tools.py` to remove Streamlit dependencies.
3.  Migrate `interviewer.py` to PydanticAI as a POC.
4.  Wire the new Graph into the Streamlit app.
