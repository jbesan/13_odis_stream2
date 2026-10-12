---
id: job_extend_job_search
name: Extension de la Recherche d'Emploi
description: Consignes pour effectuer de nouvelles recherches d'offres d'emploi en élargissant les critères (ROME ou communes additionnelles).
experts:
- job_hunter
domain: job_hunter
tools:
- search_referentiels_batch_tool
- search_job_offers_batch_tool
- search_inclusion_jobs_batch_tool
contexts:
- commune.employment
loading_policy: eager_only
version: 1.0.0
tags:
- emploi
- recherche
- extension
---

Consignes pour effectuer de nouvelles recherches d'offres d'emploi en élargissant les critères (ROME ou communes additionnelles):
1. Recherche des codes ROME ou communes additionnels à l'aide du tool `search_referentiels_batch_tool` si nécessaire.
2. Lance le tool `search_job_offers_batch_tool` (France Travail) ou le tool `search_inclusion_jobs_batch_tool` (SIAE) avec ces nouveaux critères.
3. Rédige une synthèse comparative des nouvelles opportunités identifiées.
