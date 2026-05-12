# Synthesis of Agent Contexts & Visibility Matrix (ACL)

This document summarizes the data currently injected into each agent and defines the metadata-driven "Visibility Contract" (ACL) used to automate context generation.

## 1. The "Brittleness" Problem (Cherry-Picking)

The legacy architecture relied on **manual hardcoded mappings** in `ODISContextBuilder`. This created significant risks:
- **Manual Field Sync**: Adding a field in `core/models.py` did nothing for the agents until you manually updated `state.py`.
- **Thematic Silos**: Adding a category (e.g., "Culture") required updating four different methods.

---

## 2. Proposed SOTA Solution: Metadata-Driven Injection

Instead of "cherry-picking", we use Pydantic **Metadata** (`json_schema_extra`) to drive the injection.

### Concept: `odis_visibility`
We define a metadata key `odis_visibility` in the model fields:

```python
# app/core/models.py
class SearchCriterias(BaseModel):
    nb_adultes: int = Field(..., description="Nombre d'adultes", json_schema_extra={"odis_visibility": ["all"]})
    has_dog: bool = Field(False, description="Présence d'animaux", json_schema_extra={"odis_visibility": ["agent_scout", "ui_details"]})
```

### Concept: Automated Builder
The `ODISContextBuilder` is **generic**. It iterates over model fields, filters by visibility key, and builds the prompt automatically.

```python
# app/agents/state.py (Automated logic)
def auto_build_context(model: BaseModel, visibility_key: str) -> dict:
    ctx = {}
    for name, field in model.__class__.model_fields.items():
        visibility = field.json_schema_extra.get("odis_visibility", []) if field.json_schema_extra else []
        if visibility_key in visibility or "all" in visibility:
            label = field.description  # Primary source for labels
            ctx[label] = getattr(model, name)
    return ctx
```

---

## 3. Visibility Matrix (Bitmask ACL)

This matrix defines which data "contracts" are visible to which components.

| Component Key | Description | Typical Data Included |
| :--- | :--- | :--- |
| `all` | Global visibility | Identity (Nom, INSEE), Population |
| `agent_scout` | Field Agent (Grounding) | Identity, Qualitative Notes, Mobility, Basic Housing |
| `agent_web` | News & Web Expert | Identity, Search Criteria, Web Artifacts |
| `agent_job_hunter` | Employment Expert | Identity, ROME Codes, Local Job Metrics |
| `agent_synthesizer` | Final Integration | Full Metrics, All Artifacts, Briefing |
| `agent_router` | Task Triage | City Identity, Basic Search Results |
| `agent_refiner` | Profile & Pitch Synthesis | Global situation, Top 5 results, User History |
| `agent_interviewer` | Criteria Extraction | Full User Profile (Codes + Labels) |
| `ui_details` | "En savoir plus" (Streamlit) | Detailed thematic metrics, raw KPI values |
| `pdf_report` | PDF Synthesis | Expert summaries, category scores, identity |

### Example Mapping Strategy

- **`SearchCriterias.nb_adultes`**: `["all"]`
- **`SearchCriterias.notes_qualitatives`**: `["agent_scout", "agent_synthesizer"]`
- **`CommuneResult.scores`**: `["agent_refiner", "agent_synthesizer", "ui_details"]`
- **`EmploymentMetrics.top_professions`**: `["agent_job_hunter", "agent_web", "ui_details"]`

---

## 4. Benefits
1. **Zero-Maintenance**: Adding a field with its label automatically updates all authorized agents.
2. **Single Source of Truth**: Data definition (model) and its representation (prompt/UI) are co-located.
3. **Auditability**: Clear view of who sees what across the entire application.
