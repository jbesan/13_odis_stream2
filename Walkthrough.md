# Walkthrough — Découplage Architectural Salesforce J'accueille (Ingest & Build)

## Contexte & Objectif

Dans l'architecture historique, l'ingestion Salesforce (`clean_salesforce_jaccueille`) constituait un anti-pattern architectural : elle était invoquée de manière ad-hoc dans `pipeline/etl.py` *après* la fin de la phase de build (`build.main()`), car elle mélangeait l'extraction source (leads/contacts) et l'agrégation territoriale sur le bassin de vie (qui dépendait de `odis_communes_pre.parquet` et `codes_postaux.parquet`).

Ce découplage sépare proprement le cycle de vie Salesforce en deux étapes standards et indépendantes :
1. **Étape d'Ingestion (`clean_salesforce_jaccueille`)** : extraction/mise en cache et agrégation au niveau code postal (`code_postal`, `lead_count`, `contact_count`, `total_jaccueille_count`), enregistrée dans `steps_map` et écrivant dans `CLEAN_DIR / "salesforce_jaccueille.parquet"`.
2. **Étape de Build (`build_salesforce_jaccueille_bdv`)** : lecture de `CLEAN_DIR / "salesforce_jaccueille.parquet"`, jointure avec `CLEAN_DIR / "codes_postaux.parquet"` et les communes candidates pour produire l'artefact final de release `OUTPUT_DIR / "salesforce_jaccueille_bdv.parquet"`.

---

## 1. Modifications réalisées

### Ingest Phase (`pipeline/ingest/cleaners/salesforce.py` & `pipeline/domain_contracts.yaml`)
- `clean_salesforce_jaccueille` extrait ou réutilise le cache source et écrit l'agrégat code postal directement dans `context.CLEAN_DIR / "salesforce_jaccueille.parquet"`. Elle ne dépend plus de `odis_communes_pre.parquet` ni d'aucun artefact de `build.py`.
- Imports conformes aux standards du projet (imports au niveau module top-level, utilisation directe de `context.CLEAN_DIR`).
- Mise à jour de `pipeline/domain_contracts.yaml` :
  - `runner.cleaned_sources` conserve `"salesforce_jaccueille"`.
  - `runner.step_to_source_map` conserve `salesforce_jaccueille: "salesforce_jaccueille"`.
  - `runner.clean_filenames` mappe désormais `salesforce_jaccueille: "salesforce_jaccueille.parquet"`.
  - `runner.output_dir_steps` ne contient plus `"salesforce_jaccueille"` (écrit désormais dans `CLEAN_DIR`).

### Build Phase (`pipeline/build.py`)
- Ajout de la fonction top-level `build_salesforce_jaccueille_bdv(config, logger)` :
  - Lit `CLEAN_DIR / "salesforce_jaccueille.parquet"`, `CLEAN_DIR / "codes_postaux.parquet"` et `OUTPUT_DIR / "odis_communes_pre.parquet"` (avec repli sur `odis_communes.parquet`).
  - Effectue l'agrégation territoriale BDV via `aggregate_by_bassin_de_vie` (importé au top-level).
  - Écrit l'artefact de release `OUTPUT_DIR / "salesforce_jaccueille_bdv.parquet"`.
- Enregistrement dans `steps_map` sous `"salesforce_jaccueille_bdv"` et alias `"salesforce_jaccueille"`.
- Ajout de `salesforce_jaccueille_bdv` dans `selected_steps` par défaut et dans `required_outputs`.
- Correction de l'exécution isolée : `communes_gdf` est correctement rechargé depuis `odis_communes_pre.parquet` pour l'étape `bassins_de_vie`, et `salesforce_jaccueille_bdv` ne déclenche plus arbitrairement la régénération des communes.

### Module Salesforce Ingest (`pipeline/salesforce_ingest.py`)
- Découplage de la fonction `fetch_and_aggregate_salesforce(force, output_path, raw_output_path)` extrayant les données brutes et agrégeant par code postal.
- Sécurisation de `_ttl_days()` avec lecture robuste (fallback `local_files` -> `sources` -> 7 jours par défaut).
- Normalisation robuste des types et validation de schéma dans `aggregate_by_bassin_de_vie` (codes postaux numériques / chaînes, colonnes obligatoires, retour de schéma vide valide si données absentes).
- Conservation de `run_salesforce_ingest` pour une compatibilité ascendante complète.
- Gestion du repli de cache entre `CLEAN_DIR` et le cache partagé `pipeline/cache/clean/salesforce_jaccueille.parquet`.

### Orchestration Globale (`pipeline/etl.py`)
- Suppression complète de l'appel ad-hoc `ingest.clean_salesforce_jaccueille(...)` après `build.main()` (lignes 330-338). Le build exécute désormais naturellement `salesforce_jaccueille_bdv`.

### Tests & Documentation
- `pipeline/tests/test_domain_contracts.py` : vérification que `salesforce_jaccueille` n'est plus dans `output_dir_steps` et que son nom de fichier clean est `salesforce_jaccueille.parquet`.
- `pipeline/tests/test_salesforce_ingest.py` : tests unitaires et d'intégration validant la production du fichier clean dans `CLEAN_DIR`, la production du fichier BDV dans `OUTPUT_DIR`, l'intégration CLI de build, l'alias CLI, le repli sur `odis_communes.parquet`, la robustesse sur codes postaux int/str et schémas incomplets, et la non-régression de l'étape isolée `bassins_de_vie`.
- Mise à jour de `PIPELINE_ARCHITECTURE.md` et `.agents/knowledge/topics/architecture_pipeline.md`.

---

## 2. Vérifications exécutées

- **Tests du pipeline** : `uv run pytest --tb=line pipeline/tests/` → **106 tests passés** (100 %).
- **Tests racine** : `uv run pytest --tb=line tests/` → **563 passés, 5 ignorés** (100 %).
- **Linter Ruff** : `uv run ruff check pipeline/` → **0 erreur**.
