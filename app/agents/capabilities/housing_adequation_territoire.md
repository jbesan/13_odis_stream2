---
id: housing_adequation_territoire
name: Adéquation Territoriale - Logement
description: Consignes pour l'analyse des loyers moyens, du logement social et de l'hébergement d'urgence dans la commune.
experts:
- housing_expert
tools:
- search_places_batch_tool
- compute_routes_tool
- search_web_batch_tool
contexts:
- commune.housing
loading_policy: eager_only
version: 1.0.0
tags:
- logement
- hébergement
- social
---

Consignes pour l'analyse des loyers moyens, du logement social et de l'hébergement d'urgence dans la commune:
1. Analyse le loyer moyen au m² pour le parc privé et le parc social.
2. Identifie les structures d'hébergement temporaires ou d'urgence de la commune (CADA, CHRS, CPH) en utilisant `search_places_batch_tool`.
