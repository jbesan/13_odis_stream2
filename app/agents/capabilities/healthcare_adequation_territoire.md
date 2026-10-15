---
id: healthcare_adequation_territoire
name: Adéquation Territoriale - Santé
description: Consignes pour évaluer l'indice d'accessibilité aux médecins et identifier les hôpitaux ou PMI de la commune.
experts:
- healthcare_expert
tools:
- search_places_batch_tool
- search_rna_rag_batch_tool
contexts:
- commune.health
loading_policy: eager_only
version: 1.0.0
tags:
- santé
- médecin
- hôpital
- pmi
---

Consignes pour évaluer l'indice d'accessibilité aux médecins et identifier les hôpitaux ou PMI de la commune:
1. Évalue l'accessibilité potentielle localisée (APL index) aux professionnels de santé de la commune.
2. Localise les hôpitaux ou centres PMI nécessaires via `search_places_batch_tool`.
3. Identifie les réseaux d'entraide, associations de santé ou services d'interprétariat médical pour les personnes allophones à l'aide de `search_rna_rag_batch_tool`.
