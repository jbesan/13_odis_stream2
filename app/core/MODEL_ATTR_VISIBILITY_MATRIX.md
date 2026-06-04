# ODIS Model Attributes Visibility Matrix

This matrix defines data access rights across all agents and interfaces based on the `odis_visibility` tags in `app/core/models.py`.

**Legend:**
- **Ref**: Refiner Agent
- **Syn**: Synthesizer Agent
- **Sct**: Scout Agent
- **Job**: Job Hunter Agent
- **Web**: Web Agent
- **UI**: Streamlit UI Details
- **PDF**: PDF Export Report

---

## 1. SearchCriterias (The User Dossier)

*All fields in SearchCriterias are visible to Refiner and included in PDF.*

| Field | Ref | Syn | Sct | Job | Web | UI | PDF |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `commune_actuelle` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `loc_search_area` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `loc_search_code` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `nb_adultes` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `nb_enfants` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `classe_enfants` | ✅ | ✅ | ✅ | | | ✅ | ✅ |
| `codes_metiers` | ✅ | ✅ | | ✅ | | ✅ | ✅ |
| `codes_formations` | ✅ | ✅ | | ✅ | | ✅ | ✅ |
| `inc_services_selection` | ✅ | ✅ | | | | ✅ | ✅ |
| `inc_asso_add` | ✅ | ✅ | | | | ✅ | ✅ |
| `hebergement_cible` | ✅ | ✅ | ✅ | | | ✅ | ✅ |
| `logement` | ✅ | ✅ | ✅ | | | ✅ | ✅ |
| `type_logement` | ✅ | ✅ | ✅ | | | ✅ | ✅ |
| `besoin_sante` | ✅ | ✅ | ✅ | | | ✅ | ✅ |
| `freq_retour` | ✅ | ✅ | ✅ | | | ✅ | ✅ |
| `notes_qualitatives` | ✅ | ✅ | ✅ | | ✅ | | ✅ |
| `weight_profile` | ✅ | ✅ | | | | | ✅ |
| `criteria_weights` | ✅ | | | | | | ✅ |
| `poids_{cat}` | ✅ | | | | | | ✅ |
| `org_context` | ✅ | ✅ | | | | | ✅ |
| `org_strategic_loc` | ✅ | | | | | | ✅ |
| `target_pop` | ✅ | | | | | | ✅ |
| `org_boosts` | ✅ | | | | | | ✅ |
| `odis_brief` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 2. CommuneResult (Main City Model)

| Field | Ref | Syn | Sct | Job | Web | UI | PDF |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `codgeo` / `name` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `population` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `name_bdv` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `global_score` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `scores` (Details) | ✅ | ✅ | | | | ✅ | ✅ |
| `employment` | | ✅ | | | | ✅ | ✅ |
| `housing` | | ✅ | | | | ✅ | ✅ |
| `education` | | ✅ | | | | ✅ | ✅ |
| `health` | | ✅ | | | | ✅ | ✅ |
| `inclusion` | | ✅ | | | | ✅ | ✅ |
| `mobility` | | ✅ | | | | ✅ | ✅ |
| `territoire` | ✅ | | | | | ✅ | ✅ |
| `refiner_pitch` | | ✅ | | | | ✅ | ✅ |
| `expert_analysis` | | ✅ | | | | ✅ | ✅ |
| `odis_synthesis` | | ✅ | | | | ✅ | ✅ |

---

## 3. Detailed Metrics

### EmploymentMetrics
| Field | Ref | Syn | Sct | Job | Web | UI | PDF |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `cat_score` | ✅ | ✅ | | | | ✅ | ✅ |
| `jobs_total` | ✅ | ✅ | | | | ✅ | ✅ |
| `jobs_summary` | ✅ | ✅ | | ✅ | | ✅ | ✅ |
| `jobs_matching_total`| ✅ | ✅ | | ✅ | | ✅ | ✅ |
| `jobs_match_summary` | ✅ | ✅ | | ✅ | | ✅ | ✅ |
| `top_professions` | ✅ | ✅ | | ✅ | | ✅ | ✅ |
| `inclusive_jobs_tot` | ✅ | ✅ | | ✅ | | ✅ | ✅ |
| `inclusive_jobs_sum` | ✅ | ✅ | | ✅ | | ✅ | ✅ |
| `training_programs` | ✅ | ✅ | | ✅ | | ✅ | ✅ |

### HousingMetrics
| Field | Ref | Syn | Sct | Job | Web | UI | PDF |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `cat_score` | ✅ | ✅ | | | | ✅ | ✅ |
| `host_count` | ✅ | ✅ | ✅ | | | ✅ | ✅ |
| `price_per_sqm` | ✅ | ✅ | ✅ | | | ✅ | ✅ |
| `housing_price_variants` | ✅ | ✅ | | | | ✅ | ✅ |

### Education & Health
| Field | Ref | Syn | Sct | Job | Web | UI | PDF |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `cat_score` | ✅ | ✅ | | | | ✅ | ✅ |
| `facility_counts` | ✅ | ✅ | | | | ✅ | ✅ |
| `facility_details` | ✅ | ✅ | ✅ | | | ✅ | ✅ |

### Inclusion Metrics
| Field | Ref | Syn | Sct | Job | Web | UI | PDF |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `cat_score` | ✅ | ✅ | | | | ✅ | ✅ |
| `asso_incl_count` | ✅ | ✅ | | | | ✅ | ✅ |
| `asso_incl_list` | | | ✅ | | | ✅ | ✅ |
| `asso_refugee_count`| ✅ | ✅ | | | | ✅ | ✅ |
| `asso_refugee_list` | | | ✅ | | | ✅ | ✅ |
| `services_grouped` | | | ✅ | | | ✅ | ✅ |

### Mobility Metrics
| Field | Ref | Syn | Sct | Job | Web | UI | PDF |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `cat_score` | ✅ | ✅ | | | | ✅ | ✅ |
| `stops_{type}` | ✅ | ✅ | ✅ | | | ✅ | ✅ |
| `total_stops` | ✅ | ✅ | ✅ | | | ✅ | ✅ |
| `stop_density` | ✅ | ✅ | ✅ | | | ✅ | ✅ |
| `is_same_epci` | ✅ | ✅ | | | | ✅ | ✅ |
| `distance_km` | ✅ | ✅ | | | | ✅ | ✅ |
