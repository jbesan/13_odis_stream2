---
id: social_adequation_territoire
name: Adéquation Territoriale - Insertion Sociale
description: Consignes pour localiser services aux réfugiés et les associations pertinentes pour l'intégration et l'insertion locale.
experts:
- social_integration_expert
domain: social_integration_expert
tools:
- search_places_batch_tool
- search_rna_rag_batch_tool
- search_web_batch_tool
contexts:
- commune.inclusion
- commune.territory
loading_policy: eager_only
version: 1.0.0
tags:
- social
- intégration
- fle
- réfugiés
---

Consignes pour localiser services aux réfugiés et les associations pertinentes pour l'intégration et l'insertion locale:
1. Utilise `search_rna_rag_batch_tool` pour rechercher les associations locales dans le Répertoire National des Associations.
2. Utilise `search_places_batch_tool` pour rechercher d'autres types de structures sur Google Maps si nécessaire.
3. Si le RNA officiel ne recense aucune association d'aide aux réfugiés, vérifie via `search_web_batch_tool` si des initiatives citoyennes ou collectifs locaux existent.
4. Produis une analyse sélective pour le Travailleur Social : retiens seulement les faits qui changent l'appréciation ou les prochaines actions pour ce bénéficiaire. Ne restitue pas un inventaire exhaustif des résultats.
