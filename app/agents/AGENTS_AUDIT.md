# 🏗️ Technical Spec: Advanced ODIS Graph Architecture (v3)

## 🎯 Objective

Refactor the LangGraph architecture to eliminate redundancy (`_solo` nodes), ensure data consistency (prevent stale context), and implement strict execution control (Dispatcher/Joiner pattern).

## 1. State Management Upgrade (`state.py`)

We need to correlate expert results with the specific version of search criteria used to generate them.

### A. Criteria Versioning (Hashing)

Introduce a hash mechanism to track changes in user criteria.

**Update `ODISGraphState`:**

- **`criteria_hash`** (`str`): An MD5 hash of the `search_criteria` object.
  - _Logic:_ Re-calculate this hash whenever criteria are updated (e.g., in `Interviewer` or `Scorer`).
- **`city_memory`** (`Dict[str, Dict[str, Dict[str, Any]]]`): Structured storage for expert results.
  - _Structure:_ `{ "CityName": { "CriteriaHash": { "scout": Result, "web": Result } } }`
  - _Benefit:_ The Synthesizer can check if the available results match the _current_ criteria hash. If not, it knows the data is stale.

### B. Execution Control Flags

Replace implicit logic with explicit control fields in the State.

**Update `ODISGraphState`:**

- **`pending_experts`** (`List[str]`): A list of expert node names that need to run (e.g., `['scout', 'web']` or `['job_hunter']`).
- **`execution_mode`** (`Literal['full_decoration', 'solo_query']`):
  - `'full_decoration'`: All experts run, then converge to `Synthesizer`.
  - `'solo_query'`: Experts run, then go to `END`.

---

## 2. Logic Refactoring: The "Dispatcher & Joiner" Pattern

We are moving away from hardcoded edges to a dynamic routing system.

### A. The Dispatcher (Logic Function)

Located in `graph.py` (edges logic). This function replaces complex conditional edges from Router/Refiner.

**Logic:**

1.  Read `state.pending_experts`.
2.  If empty, return `END`.
3.  Else, return the list of strings (e.g., `["scout", "web"]`).
4.  **LangGraph Behavior:** This triggers parallel execution of all listed nodes.

### B. The Unified Expert Nodes (Scout, Web, JobHunter)

**Action:** Delete all `_solo` nodes. Keep only one generic node per expert.

**Node Logic (`scout_node`, `web_node`, etc.):**

1.  Execute the Agent logic (PydanticAI).
2.  Calculate current `criteria_hash`.
3.  **Return:**
    - Update `city_memory` at path `[focus_city][criteria_hash][expert_name]`.
    - Do **NOT** return `next_node`. The graph edge handles the flow.

### C. The Joiner (Logic Function)

Located in `graph.py` (edges logic). Connects all expert nodes to the next step.

**Logic:**

1.  Check `state.execution_mode`.
2.  **If 'solo_query'**: Return `END`.
3.  **If 'full_decoration'**:
    - _Synchronization Check:_ Verify if ALL experts in `state.pending_experts` have data in `city_memory` for the current hash.
    - _(Note: LangGraph's native parallel execution usually waits for all branches to finish before proceeding if they converge to a single node. If using explicit conditional edges from experts, simply direct them to 'synthesizer' if mode is full)._
    - Return `"synthesizer"`.

---

## 3. Implementation Steps for Antigravity

### Step 1: Modify `state.py`

1.  Import `hashlib`.
2.  Add `criteria_hash`, `city_memory`, `pending_experts`, `execution_mode` to `ODISGraphState`.
3.  Implement a helper method `compute_hash(criteria)` to generate the fingerprint.

### Step 2: Update `router.py` & `refiner.py` outputs

1.  When Router decides to call an expert (or a group), it must **NOT** return a target node directly.
2.  Instead, it updates the state:
    - **Case Decoration:** `pending_experts=['scout', 'web', 'job_hunter']`, `execution_mode='full_decoration'`.
    - **Case Solo:** `pending_experts=['scout']`, `execution_mode='solo_query'`.
3.  The Router/Refiner returns `next_node="dispatcher"`.

### Step 3: Refactor `graph.py` Nodes

1.  **Delete** `scout_standalone_node`, `web_standalone_node`, `job_standalone_node`.
2.  **Update** `scout_node`, `web_node`, `job_hunter_node` to write to `city_memory` using the current hash.

### Step 4: Rewire `graph.py` Edges

1.  **Remove** static edges like `builder.add_edge("scout", "synthesizer")`.
2.  **Add Conditional Edge** from `router` and `refiner` using the **Dispatcher** logic.
3.  **Add Conditional Edge** from `scout`, `web`, `job_hunter` using the **Joiner** logic.

### Step 5: Update Synthesizer

1.  Ensure `synthesizer_agent` reads from `city_memory[focus_city][current_hash]`.
2.  If data is missing for the current hash (mismatch), it should either explicitly state it or fallback gracefully, but never use stale data from a previous hash.

---

## 4. Expected Behavior (Testing Scenarios)

- **Scenario A (Full Search):**
  - Router sets `mode='full'`, `pending=['scout', 'web']`.
  - Dispatcher launches Scout + Web.
  - Both finish.
  - Joiner sees `mode='full'` -> sends both to Synthesizer.
  - Synthesizer runs -> END.

- **Scenario B (Specific Question):**
  - Router sets `mode='solo'`, `pending=['scout']`.
  - Dispatcher launches Scout.
  - Scout finishes.
  - Joiner sees `mode='solo'` -> sends to END. (Synthesizer is skipped).
