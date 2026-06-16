# Synthesis of Agent Contexts & Visibility Matrix (ACL)

This document summarizes the data currently injected into each agent and defines the metadata-driven "Visibility Contract" (ACL) used to automate context generation.

## 1. The "Brittleness" Problem (Cherry-Picking)

The legacy architecture relied on **manual hardcoded mappings** in `ODISContextBuilder`. This created significant risks:
- **Manual Field Sync**: Adding a field in `core/models.py` did nothing for the agents until you manually updated `state.py`.
- **Thematic Silos**: Adding a category required updating multiple different methods.

---

## 2. Metadata-Driven Injection

Instead of "cherry-picking", we use Pydantic **Metadata** (`json_schema_extra`) to drive the injection.

### Concept: `odis_visibility`
We define a metadata key `odis_visibility` in the model fields:

```python
# app/core/models.py
class SearchCriterias(BaseModel):
    nb_adultes: int = Field(..., description="Nombre d'adultes", json_schema_extra={"odis_visibility": ["all"]})
    besoin_sante: Optional[str] = Field(None, description="Besoin de santé spécifique", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_healthcare_expert", "agent_job_hunter", "agent_synthesizer", "ui_details", "pdf_report"]})
```

### Concept: Automated Builder
The `ODISContextBuilder` is **generic**. It iterates over model fields recursively, filters by visibility key, and builds the JSON data context block automatically.

```python
# app/agents/state.py (Automated logic)
def _auto_build_context(model: BaseModel, visibility_key: str) -> dict:
    ctx = {}
    for name, field in model.__class__.model_fields.items():
        extra = field.json_schema_extra
        if not isinstance(extra, dict):
            continue
        
        visibility = extra.get("odis_visibility", [])
        if visibility_key not in visibility and "all" not in visibility:
            continue
            
        val = getattr(model, name)
        label = field.description or name
        
        if val is None:
            continue
            
        ctx[label] = cls._auto_build_context(val, visibility_key)
    return ctx

```

---

## 3. Agent Capabilities & Visibility Matrix (ACL)

This matrix defines which data "contracts" (ACL) are visible to each component, alongside their module file paths and registered tools/capabilities.

| Component Key | Role / Description | Module / File Path | Typical Data Included (ACL) | Registered Tools & Capabilities |
| :--- | :--- | :--- | :--- | :--- |
| `all` | Global visibility | - | Identity (Nom, INSEE), Population, Adults/Children counts | - |
| `agent_interviewer` | Criteria Extraction | [interviewer.py](file:///Users/jacques/dev/13_odis_stream2/app/agents/interviewer.py) | Full User Profile (Codes + Labels) | `search_referentiels_batch_tool` |
| `agent_ts_agent` | Project Manager (Triage) | [ts_agent.py](file:///Users/jacques/dev/13_odis_stream2/app/agents/ts_agent.py) | Bounded union of all field visibilities to evaluate plan | None (Pure Orchestrator) |
| `agent_housing_expert` | Housing Expert | [housing_expert.py](file:///Users/jacques/dev/13_odis_stream2/app/agents/housing_expert.py) | Rent m², housing Delay, J'Accueille hosts, CCAS | `search_places_batch_tool`, `compute_routes_tool`, `search_ccas_tool`<br>• `WebSearchTool` (Google Search) |
| `agent_mobility_expert` | Transport Expert | [mobility_expert.py](file:///Users/jacques/dev/13_odis_stream2/app/agents/mobility_expert.py) | Bus/tram/train stops, solidary transit prices, distance | `search_places_batch_tool`, `compute_routes_tool`<br>• `WebSearchTool` (Google Search) |
| `agent_healthcare_expert` | Healthcare Expert | [healthcare_expert.py](file:///Users/jacques/dev/13_odis_stream2/app/agents/healthcare_expert.py) | Health needs, APL health access index, hospitals, PMI | `search_places_batch_tool`, `search_rna_rag_batch_tool`<br>• `WebSearchTool` (Google Search) |
| `agent_education_expert` | Education Expert | [education_expert.py](file:///Users/jacques/dev/13_odis_stream2/app/agents/education_expert.py) | Kids levels, schools, nursery registration | `search_places_batch_tool`, `search_rna_rag_batch_tool`<br>• `WebSearchTool` (Google Search) |
| `agent_social_integration_expert` | Social Integration | [social_integration_expert.py](file:///Users/jacques/dev/13_odis_stream2/app/agents/social_integration_expert.py) | Refugee associations, CCAS details, RNA, insecurity index | `search_refugee_associations_tool`, `search_rna_rag_batch_tool`, `search_ccas_tool`, `search_places_batch_tool`<br>• `WebSearchTool` (Google Search) |
| `agent_job_hunter` | Job Hunter | [job_hunter.py](file:///Users/jacques/dev/13_odis_stream2/app/agents/job_hunter.py) | ROME codes, France Travail jobs list, SIAE offers | `search_job_offers_batch_tool`, `get_job_details_tool`, `search_inclusion_jobs_batch_tool`, `get_inclusion_job_details_tool`, `search_referentiels_batch_tool` |
| `agent_synthesizer` | Final Synthesis | [synthesizer.py](file:///Users/jacques/dev/13_odis_stream2/app/agents/synthesizer.py) | Full Metrics, All Experts summaries, Briefing, History | None (Pure Synthesizer) |
| `agent_refiner` | Global Synthesis & Pitch | [refiner.py](file:///Users/jacques/dev/13_odis_stream2/app/agents/refiner.py) | Global situation, Top 5 results, User History | None (Pure Briefing/Pitch Generator) |
| `ui_details` | "En savoir plus" (Streamlit) | - | Detailed thematic metrics, raw KPI values | - |
| `pdf_report` | PDF Synthesis | - | Expert summaries, category scores, identity | - |

---

## 4. Benefits
1. **Zero-Maintenance**: Adding a field with its label and proper visibility tag automatically updates all authorized agents.
2. **Single Source of Truth**: Data definition (model) and its representation (prompt/UI) are co-located.
3. **Security & Data Isolation**: Experts only receive data relevant to their domain (e.g., `education_expert` never receives job-hunting or health detail variables, preserving model attention).

