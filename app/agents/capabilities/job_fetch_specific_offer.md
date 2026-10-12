---
id: job_fetch_specific_offer
name: Consultation d'une Offre d'Emploi
description: Consignes pour consulter le détail d'une offre d'emploi spécifique à partir de son identifiant (ID).
experts:
- job_hunter
domain: job_hunter
tools:
- get_job_details_tool
contexts:
- commune.employment
loading_policy: eager_only
version: 1.0.0
tags:
- emploi
- offre
- détails
---

Consignes pour consulter le détail d'une offre d'emploi spécifique à partir de son identifiant (ID):
1. Appelle immédiatement le tool `get_job_details_tool` pour l'ID spécifié.
2. Structure ton analyse avec les informations clés : employeur, contrat, localisation, salaire, et compétences demandées.
3. Évalue l'adéquation de l'offre avec les compétences, l'expérience et les contraintes de mobilité du candidat.
