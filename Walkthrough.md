# Walkthrough — Migration Capabilities & Contexts Phase 1 (Étapes 6 & 7)

## Contexte & Objectif

Les étapes 0 à 5 ont mis en place le filet de sécurité, les modèles `CapabilitySpec` et le catalogue sous `app/agents/capabilities/`, les blocs de contexte déclaratifs, l'instanciation des agents sans outils statiques (`tools=[]`), et la migration du coordinateur `ts_agent`.

Les étapes 6 et 7 complètent la Phase 1 :
1. **Étape 6 — Drapeau de chargement différé (`ODIS_CAPABILITY_DEFERRED_LOADING`)** :
   - Ajout de la configuration `ODIS_CAPABILITY_DEFERRED_LOADING: bool = False` et du helper `is_capability_deferred_loading_enabled() -> bool` dans `app/config.py`.
   - Application stricte de la Règle 4 (Parité Cloud Run) : ajout de `ODIS_CAPABILITY_DEFERRED_LOADING=false` dans `app/.env` et de `ODIS_CAPABILITY_DEFERRED_LOADING=False` dans les déploiements Cloud Build (`cloudbuild.yaml` et `cloudbuild-promote.yaml`).
   - Branchement dynamique dans `app/agents/graph.py` (`triage_step` et fallback `expert_worker_step`) via `CapabilityRegistry.build_capabilities_for_expert(..., defer_loading=is_capability_deferred_loading_enabled())`.
   - Couverture par tests unitaires de la propagation du drapeau.

2. **Étape 7 — Nettoyage final, élagage et architecture** :
   - **Élagage du doublon `{SKILL_INSTRUCTIONS}`** : suppression de la balise `{SKILL_INSTRUCTIONS}` et de son formatage manuel dans les 6 agents experts (`housing_expert.py`, `mobility_expert.py`, `healthcare_expert.py`, `education_expert.py`, `social_integration_expert.py`, `job_hunter.py`). Les instructions sont désormais portées de façon unique et native par `Capability(instructions=...)` dans PydanticAI.
   - **Suppression du legacy** :
     - Suppression définitive de `app/services/knowledge_store.py` et de son test `tests/unit/test_skills_store.py`.
     - Suppression définitive du dossier `app/agents/skills/` (et de ses 8 fichiers markdown).
     - Nettoyage de `FALLBACK_CAPABILITIES_DIR` dans `CapabilityRegistry`.
   - **Documentation & Living Memory** :
     - Mise à jour de `app/APP_ARCHITECTURE.md`, `app/agents/GRAPH_ARCHITECTURE.md`, et ajout du bandeau d'obsolescence dans `app/agents/AGENT_CONTEXTS.md`.
     - Mise à jour de la mémoire du projet dans `.agents/knowledge/topics/architecture_ai_agents.md` et `.agents/knowledge/ALWAYSREADFIRST.md`.

---

## 1. Modifications réalisées

### Configuration & Parité Cloud Run (`app/config.py`, `app/.env`, `cloudbuild.yaml`, `cloudbuild-promote.yaml`)
- `app/config.py` : ajout de `ODIS_CAPABILITY_DEFERRED_LOADING: bool` et de la fonction typée `is_capability_deferred_loading_enabled() -> bool`.
- `app/.env` : ajout de `ODIS_CAPABILITY_DEFERRED_LOADING=false`.
- `cloudbuild.yaml` & `cloudbuild-promote.yaml` : ajout de `ODIS_CAPABILITY_DEFERRED_LOADING=False` dans `--set-env-vars`.

### Moteur de graphe (`app/agents/graph.py`)
- Import au top-level de `is_capability_deferred_loading_enabled` (règle 3 AGENTS.md).
- Dans `triage_step` : construction des capabilities avec `defer_loading=is_capability_deferred_loading_enabled()`.
- Dans `expert_worker_step` (fallback standalone) : construction avec `defer_loading=is_capability_deferred_loading_enabled()`.

### Élagage des prompts système (6 Experts thématiques)
- `app/agents/housing_expert.py`
- `app/agents/mobility_expert.py`
- `app/agents/healthcare_expert.py`
- `app/agents/education_expert.py`
- `app/agents/social_integration_expert.py`
- `app/agents/job_hunter.py`
- Retrait de la section `# Consignes additionnelles issues des Skill Cards actives : {SKILL_INSTRUCTIONS}` et du paramètre `SKILL_INSTRUCTIONS=...` dans les fonctions de construction de prompt.

### Suppression du code déprécié
- `app/services/knowledge_store.py` (supprimé).
- `tests/unit/test_skills_store.py` (supprimé).
- Répertoire `app/agents/skills/` (8 fichiers markdown supprimés).
- `app/agents/capability_registry.py` : retrait de `FALLBACK_CAPABILITIES_DIR`.

### Tests & Validation
- `tests/unit/test_capabilities_migration_baseline.py` :
  - Validation que `{SKILL_INSTRUCTIONS}` est absent des prompts rendus.
  - Validation de la structure complète des prompts système.
  - Validation de l'injection des instructions au niveau `Capability`.
  - Ajout des tests `test_baseline_deferred_loading_flag_and_capability_propagation` et `test_triage_step_deferred_loading_flag`.
- `tests/unit/test_capability_registry.py` :
  - Ajout du test `test_build_capabilities_defer_loading`.
- `tests/unit/test_agent_tools_offline.py` :
  - Typage strict validé sans diagnostic `ty`.

---

## 2. Vérifications exécutées

- **Tests unitaires** : `uv run pytest tests/unit/ -k "not test_places and not test_live" --tb=line`
  - **661 tests passés**, 1 ignoré, 1 désélectionné (100% vert).
- **Linter Ruff** : `uv run ruff check`
  - 0 avertissement, 0 erreur sur l'ensemble du projet.
- **Vérification de types Ty** : `uv run ty check app tests`
  - 0 diagnostic, 100% vert.
