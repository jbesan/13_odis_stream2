# Multi-Agent Architecture ODIS

Cette architecture remplace l'ancien prompt monolithique par un système d'agents spécialisés capables de mieux gérer le contexte et les transitions de phase.

## 🏗️ Structure des Agents

Les agents sont situés dans `app/agents/` :

- **`orchestrator.py`** : Le "cerveau" du système. Gère le routing, la mémoire et la synthèse finale.
- **`interviewer.py`** : Gère la phase `DISCOVERY`. Collecte les données pour remplir le diagnostic (SearchCriterias).
- **`scorer.py`** : Gère la phase `SCORING`. Calcule le Top villes et explique le match ODIS.
- **`scout.py`** : Expert **Maps**. Trouve les infrastructures locales (POIs) et calcule les trajets.
- **`web.py` [NEW]** : Expert **News/Web**. Utilise Google Search pour le contexte social et les actualités.
- **`job_hunter.py`** : Expert **Emploi**. Recherche proactive d'offres réelles sur France Travail.
- **`state.py`** : Objet `AgentContext` (Golden Record) partagé.
- **`base.py`** : Classe de base `BaseAgent` avec gestion unifiée du Tool Use et Grounding.

## 🔄 Workflow & Phases

L'orchestrateur pilote la conversation à travers trois phases :

1. **DISCOVERY** : Collecte intelligente des besoins.
2. **SCORING** : Présentation argumentée des meilleurs territoires.
3. **DECORATION** : Triple cascade automatique (**Scout + Web + JobHunter**) pour explorer une ville en détail.

## 🛠️ Outils & Capacités

- `search_commune` / `search_referentiels` : Identification précise des données.
- `compute_top_cities` : Moteur de scoring ODIS.
- `search_places` / `compute_routes` (Google Maps) : Expertise terrain.
- `google_search` (Native Capability) : Grounding web en temps réel (utilisé par l'agent WEB).
- `search_job_offers` (France Travail) : Offres d'emploi en direct.

## 📝 Configuration des Modèles (Janv 2026)

- Orchestrate/Interviewer/Scout/Web : `gemini-3-flash-preview`.
- Scorer/JobHunter : `gemini-2.5-flash-lite`.
