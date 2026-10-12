---
id: mobility_adequation_territoire
name: Adéquation Territoriale - Mobilité
description: Consignes pour analyser les réseaux de transports en commun locaux et les réductions tarifaires solidaires.
experts:
- mobility_expert
domain: mobility_expert
tools:
- search_places_batch_tool
- compute_routes_tool
- search_web_batch_tool
contexts:
- commune.mobility
loading_policy: eager_only
version: 1.0.0
tags:
- transports
- mobilité
- bus
- train
---

Consignes pour analyser les réseaux de transports en commun locaux et les réductions tarifaires solidaires:
1. Présente le réseau de transport en commun local (arrêts principaux, gares) identifié à l'aide de `search_places_batch_tool`.
2. Estime les temps de trajet vers les zones d'emploi ou centres administratifs principaux via le tool `compute_routes_tool`.
3. Recherche les aides régionales, départementales ou municipales pour la mobilité (aides au permis, réductions tarifaires). Si les outils du dossier et les lieux locaux ne suffisent pas, utilise `search_web_batch_tool`.
