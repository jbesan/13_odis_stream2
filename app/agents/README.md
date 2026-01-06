# Multi-Agent Architecture ODIS

Cette architecture remplace l'ancien prompt monolithique par un système d'agents spécialisés capables de mieux gérer le contexte et les transitions de phase.

## 🏗️ Structure des Agents

Les agents sont situés dans `app/agents/` :

- **`orchestrator.py`** : Le "cerveau" du système. Il décide quel agent doit répondre en fonction de la phase actuelle et du message utilisateur.
- **`interviewer.py`** : Gère la phase `DISCOVERY`. Pose des questions pour remplir les critères de recherche (SearchCriterias).
- **`scorer.py`** : Gère la phase `SCORING`. Utilise l'outil `compute_top_cities` et explique les résultats.
- **`scout.py`** : Gère la phase `DECORATION`. Répond aux questions sur le terrain (écoles, transports) via Google Maps.
- **`job_hunter.py`** : Gère les questions spécifiques à l'emploi et aux offres concrètes.
- **`state.py`** : Définit l'objet `AgentContext` (le "Golden Record") partagé par tous les agents.
- **`base.py`** : Classe de base `BaseAgent` fournissant la boucle d'exécution d'outils (Tool Use).
- **`tools.py`** : Définition des outils (wrappers) mis à disposition des agents.

## 🔄 Workflow & Phases

L'orchestrateur pilote la conversation à travers trois phases principales :

1. **DISCOVERY** : L'interviewer collecte les données.
2. **SCORING** : Une fois les critères minimums réunis, le Scorer calcule le Top 3.
3. **DECORATION** : L'utilisateur demande des détails sur une ville. Le Scout ou JobHunter prend le relais.

## 🛠️ Outils Disponibles

- `search_commune` : Recherche de codes INSEE.
- `search_referentiels` : Recherche dans les référentiels métiers (FAP), formations, etc.
- `compute_top_cities` : Calcul du score ODIS (utilisant `scoring.py`).
- `search_places` : Recherche de POIs via Google Places.
- `compute_routes` : Calcul de trajets via Google Maps.
- `set_focus_city` : Mémorise la ville actuellement discutée pour conserver le contexte.

## 📝 Configuration des Modèles

Les modèles sont configurés dans `orchestrator.py` :

- Orchestrator/Interviewer : `gemini-3-flash-preview` (pour le raisonnement complexe).
- Scorer/Scout/JobHunter : `gemini-2.5-flash-lite` (vitesse et efficacité).
