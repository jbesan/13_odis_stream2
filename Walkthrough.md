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

- **Tests unitaires** : `uv run pytest tests/unit/ --tb=line -q`
  - **668 tests passés**, 1 ignoré (100% vert).
- **Linter Ruff** : `uv run ruff check app tests`
  - 0 avertissement, 0 erreur sur l'ensemble du projet.
- **Vérification de types Ty** : `uv run ty check app`
  - 0 diagnostic, 100% vert.

---

## 3. Revue de PR : Résolution de B1 et M1 (2026-10-12)

### B1 — Correction du test unitaire périmé
- `tests/unit/test_capability_registry.py:345` : assertion corrigée pour tester `"search_places_batch_tool" in housing.instructions` suite à la suppression des personas dans les cartes markdown.

### M1 — Câblage déclaratif des contextes depuis les Capabilities
- **Éradication de `EXPERT_CONTEXT_MAP`** : La table codée en dur dans `app/agents/context_blocks.py` et `app/agents/context_registry.py` a été supprimée.
- **Fail-closed dans `CapabilityRegistry`** : `load_all()` et `register()` valident que chaque identifiant déclaré dans `spec.contexts` existe bien dans `ContextRegistry` (échoue immédiatement en cas d'incohérence).
- **Résolution dynamique dans `ODISContextBuilder.expert_prompt_contexts()`** :
  - Résout les capability IDs actifs (`capability_ids` explicites ou via `state.expert_capabilities`, avec fallback sur `get_base_capability_id(agent_name)`).
  - Collecte et dédoublonne les blocs de contexte déclarés par les capabilities actives.
  - Préserve 100% du *prompt prefix caching* de Gemini pour les 3 blocs socle (`core.briefing`, `core.criteria`, `core.commune_identity`).
- **Tests unitaires ajoutés** :
  - `test_registry_rejects_unknown_context_block_on_register` & `test_registry_accepts_valid_context_blocks_on_register` dans `test_capability_registry.py`.
  - `test_expert_prompt_contexts_resolves_dynamically_from_capabilities` & `test_expert_prompt_contexts_deduplicates_overlapping_capability_contexts` dans `test_context_blocks.py`.

---

## 4. Revue de PR : Résolution de S1, S2, S3, S4 (Zero Retro-Compatibility) (2026-10-12)

### S1 — Élimination de la duplication d'état & `GraphState` 100% sérialisable
- **`app/agents/state.py`** :
  - Suppression définitive des champs résiduels `active_skills`, `expert_skill_instructions` et `expert_skill_tools`.
  - Typage de `expert_capabilities` révisé en `Dict[str, List[str]]` (stockant uniquement des identifiants de capabilities au lieu d'objets `Capability` non sérialisables).
- **`app/agents/evidence/projector.py`** :
  - `project_social_context` résout dynamiquement les instructions via `get_default_capability_registry()` à partir des capability IDs présents dans `state.expert_capabilities`.

### S2 — Éradication du fallback legacy & double chemin d'outils
- **`app/agents/graph.py`** :
  - Suppression de la fonction `_dynamic_toolsets_for_expert` et retrait des imports `FunctionToolset`, `search_web_batch_tool`, et `WEB_SEARCH_TOOL_ID`.
  - Dans `expert_worker_step` : instanciation à la volée des objets `Capability` via `CapabilityRegistry.build_capabilities_for_expert(cap_ids, defer_loading=...)` et exécution directe via `agent.run(..., capabilities=capabilities)` sans aucun fallback `toolsets`.

### S3 — Purge des alias rétro-compatibles & conformité stricte Spec §4.1
- **`app/agents/ts_agent.py`** :
  - Suppression de la constante dormante `SKILL_CARD_EXPERT`.
  - Nettoyage du modèle `ExpertTask` : suppression de `AliasChoices("capabilities", "skill_cards")`, suppression de `_normalize_skill_cards_to_capabilities`, suppression de la propriété `skill_cards`. Seul le champ `capabilities: List[str]` est supporté (avec coercition d'une string unique en liste si nécessaire).
- **`app/agents/capability_registry.py` & `app/agents/capability_spec.py`** :
  - Suppression du dictionnaire `LEGACY_SKILL_TO_CAPABILITY`.
  - Suppression du champ `domain: Optional[str]` dans `CapabilitySpec` et son sérialiseur markdown `to_markdown()`.
  - Simplification de `resolve_capability_id(id)` déléguant directement à `self.get(id).id`.
- **`app/agents/capabilities/*.md`** :
  - Retrait de la ligne `domain:` des 8 fichiers markdown de capabilities.
- **`app/agents/graph.py`** :
  - `triage_step` alimente `state.expert_tasks` et `state.expert_capabilities` directement depuis `plan.tasks` sans conversions ni shims dict.

### S4 — Unification de la validation des capabilities dans `ExpertTask`
- **`app/agents/ts_agent.py`** :
  - `ExpertTask.validate_task_capabilities` est désormais l'unique autorité de validation des capabilities (existence et assignation à l'expert).
  - Suppression de la boucle de validation redondante et de la mutation `task.capabilities` dans `SwarmPlan.validate_execution_contract`.

### Tests & Validation
- Nettoyage des tests unitaires dépendants des anciens alias ou structures :
  - `test_swarm_plan_validation.py`
  - `test_capability_registry.py`
  - `test_web_search_tool.py`
  - `test_capabilities_migration_baseline.py`
  - `test_direct_answer.py`
  - `test_agent_tools_offline.py`
- Tous les tests passent : **658 passés, 1 ignoré en ~10-12s**. Ruff et Ty sont 100% verts.

---

## 5. Revue de PR : Résolution de S5, S6, S7, S8, S9, S10 (2026-10-15)

### S5 — Sémantique de chargement différé & Hot-Start
- **`app/agents/capability_registry.py`** :
  - Dans `build_capabilities_for_expert` : les capabilities sélectionnées par le TS (`capability_ids`) constituent le *hot start* et sont **toujours eagerly loaded** (`defer_loading=False`).
  - Lorsque `defer_loading=True` et qu'un `expert` est fourni : les autres capabilities de l'expert déclarées avec `loading_policy == "deferrable"` sont injectées avec `defer_loading=True`.
  - Dédoublonnage strict des outils entre les capabilities eagerly loaded et deferred.
  - Le drapeau reste `False` par défaut en Phase 1.

### S7 — Observabilité des outils exposés par expert (Spec §5)
- **`app/agents/graph.py`** :
  - Dans `expert_worker_step` : extraction et journalisation console des outils exposés (`🛠️ [EXPERT] Exposed tools: ...`).
  - Enregistrement dans Logfire via `trace_attrs["exposed_tools"]` permettant la surveillance immédiate des sous-sélections éventuelles par le `ts_agent`.

### S8 — Évaluation Fail-Closed au démarrage de l'application
- **`app/agents/ts_agent.py`** :
  - Appel immédiat de `get_default_capability_registry()` au niveau import du module.
  - Tout défaut dans les cartes `.md`, outil manquant ou contexte invalide lève une exception au boot plutôt qu'en cours de requête utilisateur.

### S9 — Purge intégrale de l'API morte dans `CapabilityRegistry`
- **`app/agents/capability_registry.py`** :
  - Suppression de `to_markdown()` sur `CapabilitySpec`.
  - Suppression de `build_capability()`, `resolve_tools()`, `resolve_tools_for_expert()`, `register()`, `reload()`, `__iter__`, et `__getitem__`.
- **`tests/unit/test_capability_registry.py`** :
  - Migration de tous les tests de validation de contextes et de chargement vers des fichiers `.md` temporaires sur disque via `tmp_path`.
  - Suppression des tests ciblant les méthodes mortes.

### S10 — Dédoublonnage de configuration
- **`app/config.py`** :
  - Suppression de la constante inutilisée `ODIS_CAPABILITY_DEFERRED_LOADING: bool`.
  - Centralisation sur l'unique fonction typée `is_capability_deferred_loading_enabled() -> bool` lisant dynamiquement la variable d'environnement (alimentée par `app/.env` et les manifests Cloud Run).

### S6 — Alignement documentaire & Mémoire
- **`app/agents/GRAPH_ARCHITECTURE.md`** : documentation de l'observabilité des outils exposés (S7), du boot fail-closed (S8) et de la sémantique hot start (S5).
- **`docs/AGENTS_CONTEXT_SPEC.md`** : mise à jour du statut à « Implémenté & validé (Phase 1) ».
- **`.agents/knowledge/topics/architecture_ai_agents.md`** : consolidation de la mémoire living memory sur les points S5 à S10.

### Nettoyages post-audit
- **`tests/unit/test_swarm_plan_validation.py`** : nettoyage du docstring du module pour supprimer le reliquat textuel sur la rétro-compatibilité `skill_cards`.
- **`app/agents/state.py`** : suppression du ré-import local redondant `from core.models import CommuneResult` dans `GraphState.__post_init__` (respect strict de la règle Mandatory Top-Level Imports).

### Tests & Qualité
- **Tests unitaires** : `uv run pytest tests/unit/ --tb=line -q` → **654 passés, 1 ignoré** (100% vert en ~12s).
- **Ruff** : `uv run ruff check app tests` → **All checks passed!**
- **Ty** : `uv run ty check app` → **All checks passed!**



