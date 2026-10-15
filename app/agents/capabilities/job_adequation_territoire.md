---
id: job_adequation_territoire
name: Adéquation Territoriale - Emploi
description: Consignes pour analyser les opportunités France Travail, les codes ROME, les offres détaillées et les structures d'insertion (SIAE) dans la commune.
experts:
- job_hunter
tools:
- search_job_offers_batch_tool
- get_job_details_tool
- search_inclusion_jobs_batch_tool
- get_inclusion_job_details_tool
- search_referentiels_batch_tool
contexts:
- commune.employment
loading_policy: eager_only
version: 1.0.0
tags:
- emploi
- siae
- recrutement
- insertion
---

Consignes pour analyser les opportunités France Travail, les codes ROME, les offres détaillées et les structures d'insertion (SIAE) dans la commune :
1. Examine les opportunités d'emploi pré-chargées ou issues de France Travail correspondant aux métiers recherchés (codes ROME) avec le tool `search_job_offers_batch_tool` (en passant le `Code INSEE` de la commune dans `city_code_insee`, jamais un code postal) et explore les détails d'offres ciblées via `get_job_details_tool`.
2. Si "Rechercher également dans les SIAE" = true, identifie les structures d'insertion par l'activité économique (SIAE) et les offres d'inclusion locales à l'aide du tool `search_inclusion_jobs_batch_tool` (en passant le `Code INSEE` de la commune dans `city_code_insee`, jamais un code postal) et obtiens leurs détails avec `get_inclusion_job_details_tool`.
3. Si nécessaire, recherche des codes ROME ou communes additionnels à l'aide du tool `search_referentiels_batch_tool`.
4. Priorise les offres les plus proches géographiquement et adaptées au niveau de langue du candidat.
