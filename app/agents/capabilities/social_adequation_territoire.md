---
id: social_adequation_territoire
name: Adéquation Territoriale - Insertion Sociale
description: Consignes pour localiser services aux réfugiés et les associations pertinentes pour l'intégration et l'insertion locale.
experts:
- social_integration_expert
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
1. Exploite en priorité les structures déjà pré-injectées dans `commune.inclusion` (services Data·Inclusion et associations thématiques d'action sociale sur le Bassin de Vie, associations dédiées aux réfugiés sur le Bassin de Vie, et recherches de centres d'intérêt spécifiques).
2. N'utilise `search_rna_rag_batch_tool` (recherche hybride vecteur + mots-clés sur le Bassin de Vie) qu'en complément si une thématique pertinente pour le bénéficiaire est absente ou vide dans le contexte pré-injecté, ou pour une recherche ad-hoc hors critères déjà sélectionnés ; distingue bien la commune cible (`is_same_commune`) des communes voisines (`commune_nom`) et privilégie les structures actives (`has_employees`, `date_decla`, `assoce_url`).
3. Utilise `search_places_batch_tool` pour localiser d'autres structures ou tiers-lieux sur Google Maps si nécessaire.
4. Si les données pré-injectées et le RNA officiel ne recensent aucune association d'aide aux réfugiés ou communauté ciblée, vérifie via `search_web_batch_tool` s'il existe des initiatives citoyennes ou collectifs locaux.
5. Produis une analyse sélective pour le Travailleur Social : retiens seulement les faits qui changent l'appréciation ou les prochaines actions pour ce bénéficiaire. Ne restitue pas un inventaire exhaustif des résultats.
