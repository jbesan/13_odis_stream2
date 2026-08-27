# ODIS Domain Context Routing Specification (Object-Level ACL)

This document defines the data access contract across all ODIS agents and presentation layers.
Instead of fragile field-level metadata annotations, context assembly is governed by an **Object-Level Domain Routing** model managed deterministically by `ODISContextBuilder`.

---

## 1. Context Consumers (Actors)

| Key | Consumer / Agent | Role in Swarm |
| :--- | :--- | :--- |
| `ui_details` | Streamlit UI | Direct model dump — full access to all objects and attributes without ACL filtering |
| `pdf_report` | PDF Export Generator | Direct model dump — full access to all objects and attributes |
| `agent_refiner` | Refiner Agent | Produces initial narrative briefing (`odis_brief`) and city pitch |
| `agent_ts_agent` | TS Coordinator | Evaluates beneficiary needs, plans expert tasks, and selects skill cards |
| `agent_synthesizer` | Synthesizer Agent | Produces executive cross-domain overview and next steps (from pre-digested cards) |
| `agent_job_hunter` | Job Hunter Expert | France Travail job offers, SIAE insertion opportunities, ROME skills matching |
| `agent_housing_expert` | Housing Expert | Rents, social housing wait times, temporary shelters (CADA/CHRS), J'Accueille |
| `agent_mobility_expert` | Mobility Expert | Public transport density, train/bus/tram stops, interurban travel, routes |
| `agent_healthcare_expert` | Healthcare Expert | Health access index (APL), hospitals, clinics, maternal and child healthcare (PMI) |
| `agent_education_expert` | Education Expert | School infrastructure (crèche, école, collège, lycée), local schooling registration |
| `agent_social_integration_expert` | Social Integration Expert | Refugee aid associations, Data Inclusion services (Soliguide/Dora), civic fabric |

---

## 2. Standardized Context Envelope

Every agent receives a clean, domain-scoped JSON context envelope structured as follows:

```json
{
  "Résumé du dossier (Briefing)": "Synthèse narrative complète...",
  "Critères de recherche": {
    "Commune actuelle": "Paris (75056)",
    "Nombre d'adultes": 1,
    "Nombre d'enfants": 2,
    "Niveaux scolaires recherchés": ["École élémentaire", "Collège"],
    "Métiers ciblés": [["Boulangerie"]],
    "Notes qualitatives": ["Proche de la mer", "Transports accessibles"]
  },
  "Commune analysée (Identité)": {
    "Code INSEE": "33063",
    "Nom": "Bordeaux",
    "Population": 260958,
    "Bassin de vie": "Bordeaux",
    "Score global": 85
  },
  "Données <domaine>": {
    "... Données métriques et objets détails spécifiques au domaine ..."
  }
}
```

---

## 3. Object-Level Domain Routing Table

| Component / Agent | Criteria Injected | Target Commune Data Injected | Pre-loaded Detail Collections (`XxxDetail`) |
| :--- | :--- | :--- | :--- |
| **UI & PDF** | Full `SearchCriterias` | Full `CommuneResult` (100%) | All collections (`JobOfferDetail`, `AssociationDetail`, `InclusionServiceDetail`, `facility_details`) |
| **Refiner** | Full `SearchCriterias` + Notes | Full `CommuneResult` summary + `scores` map | High-level summary of top 5 communes |
| **TS Coordinator** | Full `SearchCriterias` + `odis_brief` | Base Commune Identity + Category Scores Overview | Territory strategic flags (`is_strategic`) |
| **Synthesizer** | Pre-digested composite snippets | Composite cards (Briefing, Comparator, Expert summaries, CCAS, Territory) | Executive artifact summaries |
| **Job Hunter** | Full `SearchCriterias` + `odis_brief` | Base Identity + `commune.employment` (`EmploymentMetrics`) | `matching_job_offers` (`JobOfferDetail` in compact format) |
| **Housing Expert** | Full `SearchCriterias` + `odis_brief` | Base Identity + `commune.housing` (`HousingMetrics`) | `housing_price_variants`, `log_soc_delay`, `host_count` |
| **Mobility Expert** | Full `SearchCriterias` + `odis_brief` | Base Identity + `commune.mobility` (`MobilityMetrics`) | Stop counts (`bus_stops`, `train_stops`, etc.), density, distance |
| **Healthcare Expert** | Full `SearchCriterias` + `odis_brief` | Base Identity + `commune.health` (`HealthMetrics`) | `facility_details` (hospitals, PMI, specialized centers) |
| **Education Expert** | Full `SearchCriterias` + `odis_brief` | Base Identity + `commune.education` (`EducationMetrics`) | `facility_details` (school names by level: crèche, maternelle, primaire, etc.) |
| **Social Integration** | Full `SearchCriterias` + `odis_brief` | Base Identity + `commune.inclusion` (`InclusionMetrics`) | `asso_refugee_list`, `asso_inclusion_list_by_cat` (`AssociationDetail`), `services_detailed` (`InclusionServiceDetail`) |

---

## 4. Token & Representation Invariants

1. **Deduplication of Briefing**:
   * The briefing narrative `odis_brief` is injected exactly once at root key `"Résumé du dossier (Briefing)"`.
   * When serializing `SearchCriterias`, the field `odis_brief` is excluded (`exclude={"odis_brief"}`) to prevent prompt duplication.

2. **Compact Detail Formatting**:
   * Detail objects (`AssociationDetail`, `InclusionServiceDetail`, `JobOfferDetail`) are serialized as concise pipe-separated strings (`ID | Name | Key Details`) rather than verbose nested JSON trees to preserve context window and model attention.

3. **Zero Empty Dictionaries**:
   * Domain experts receive only their corresponding domain metric container. Unrelated domain containers (`Données logement: {}`, `Données éducation: {}`) are never emitted in an expert's context.

4. **Territory Metrics**:
   * `TerritoryMetrics` (`commune.territoire`) is surfaced for the `Synthesizer`, `Refiner`, and `TS_AGENT` (with eventual migration of CTAI, ANVITA, and SIAE indicators to `inclusion`).
