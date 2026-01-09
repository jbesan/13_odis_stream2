# PRD : OD&IS 2.0 - AI Native & MCP Architecture

**Version** : 2.0 (Pivot Agentique)
**Date** : 13 Décembre 2025
**Auteur** : D4G: OD&IS (Social Worker & Data Scientist)
**Stack Cible** : Google Cloud (Vertex AI), Gemini 2.0, MCP, Python.

---

## 1. Vision du Produit

Passer d'un **outil de calcul** (Dashboard passif) à un **assistant d'aide à la décision** (Agent actif).
L'objectif est de masquer la complexité des 50 critères de l'algorithme OD&IS derrière une conversation naturelle, tout en garantissant la rigueur scientifique du scoring via une architecture hybride.

### Le Concept "Tri-Grounding"

L'agent ne doit jamais halluciner sur la donnée. Il doit s'appuyer sur trois piliers de vérité (Grounds) :

1.  **Hard Data (ODIS MCP)** : La réalité statistique et structurelle (via ton moteur de scoring).
2.  **Soft Data (Google Search)** : La réalité sociale, l'actualité et les signaux faibles.
3.  **Spatial Data (Google Maps)** : La réalité physique et l'environnement immédiat.

---

## 2. Architecture Technique : Le Pattern MCP

L'architecture repose sur un **Client MCP** (L'Agent Gemini) et un **Serveur MCP** (Ton moteur OD&IS).

### A. Le Client (L'Interface Chat)

- **Technologie** : Streamlit Chat widget.
- **Cerveau** : Gemini
- **Rôle** : Gérer le dialogue, détecter les intentions, et orchestrer les appels aux outils. Le prompt doit poser des questions essentiels et deviner des questions optionelles pour affiner la recherche et la pondération des critères.

### B. Le Serveur MCP "ODIS-Core" (Ton focus d'apprentissage)

- **Technologie** : Python (FastMCP ou SDK mcp-python).
- **Hébergement** : Google Cloud Run.
- **Responsabilité** : Exposer le moteur de calcul `scoring.py` comme un "Outil" standardisé que n'importe quel LLM peut appeler.
- **Données** : Charge les fichier du data_loader() et expose les resultats avec la fonction compute_odis_score(). L'agent passe les paramètres de recherches + pondérations et reçoit en retour un top 10 avec le détail des sous scores.

---

## 3. Spécifications Fonctionnelles des 3 "Grounds"

### Ground 1 : ODIS-MCP (Le Moteur Heuristique)

C'est l'autorité pour le filtrage initial.

- **Nom de l'outil MCP** : `compute_top_cities`
- **Input (JSON généré par le LLM)** :
  ```json
  {
    "weights": {
      "emploi": 0.8,
      "sante": 0.5,
      "ecole": 1.0,
      "loyer": 0.9
      // ... mappé depuis la conversation
    },
    "filters": {
      "region": "Nouvelle-Aquitaine",
      "population_min": 5000,
      "must_have_train_station": true
    }
  }
  ```
- **Output** : Liste JSON des 10 meilleures communes avec leurs sous-scores détaillés.
- **Comportement attendu du LLM** : L'agent doit traduire "J'ai peur de ne pas trouver de travail" par `weights.emploi = 0.9` et `weights.chomage = 1.0`.

### Ground 2 : Google Search (Contextualisation Sociale)

Utilisé uniquement sur le **Top 5** identifié par le Ground 1.

- **Technologie** : Google Search Grounding (Vertex AI).
- **Objectif** : Vérifier la "viabilité sociale" et l'actualité.
- **Prompts automatiques (Exemples)** :
  - _"Actualité récente accueil migrants [Nom de la Ville] 2024 2025"_
  - _"Tension locative réelle [Nom de la Ville] témoignages"_
  - _"Fermeture classe école [Nom de la Ville]"_
  - _"Entreprises qui recrutent soudeurs [Bassin de Vie]"_

### Ground 3 : Google Maps (Contextualisation Spatiale)

Utilisé pour valider le cadre de vie du **Top 3**.

- **Technologie** : Google Maps Platform (Places API via LangChain Google Tools ou appel direct).
- **Objectif** : Donner du concret visuel et pratique.
- **Cas d'usage** :
  - Calculer le temps de trajet réel (Transit) entre le centre-ville et la zone industrielle (ZE).
  - Identifier la présence de lieux de culte, d'épiceries sociales ou de parcs.
  - _Note :_ Peut être coûteux, à utiliser avec parcimonie (uniquement sur demande utilisateur ou validation finale).

---

## 4. User Flow (Scénario Cible)

1.  **Phase d'Enquête (Chat)**

    - _User_ : "Je cherche une ville pour une famille de 4, le père est maçon, ils n'ont pas de voiture."
    - _Agent_ : "Pas de voiture, c'est noté. On va privilégier les villes avec gare et transports urbains. Pour la maçonnerie, je vais chercher des zones où le BTP recrute. Quel âge ont les enfants ?" (Affinement des poids 'Education').

2.  **Phase de Calcul (Appel MCP)**

    - L'Agent détecte qu'il a assez d'infos.
    - Il appelle `odis_mcp.compute_top_cities(...)`.
    - Le serveur MCP renvoie : `[Guéret (Score 85), Marmande (Score 82), ...]`

3.  **Phase de Deep Research (Grounding Web)**

    - L'Agent "réfléchit" : _"Je vais vérifier la situation du BTP à Guéret et Marmande sur le web."_
    - Il interroge Google Search.

4.  **Phase de Restitution (Synthèse)**
    - _Agent_ : "Voici 3 options solides :
      - **Option 1 : Marmande.** Excellent score (82). Le secteur BTP est en tension (Source: France Travail via Search). Gare en centre-ville.
      - _Attention :_ J'ai vu un article mentionnant des loyers en hausse récente."

---

## 5. Refactoring du Code Existant (Pré-requis)

Pour activer cette architecture, le code actuel doit évoluer :

1.  **Découplage UI/Logique (`app/scoring.py`)** :

    - Actuellement, `scoring.py` est peut-être trop lié à la session `st.session_state` de Streamlit.
    - _Action_ : Rendre la fonction de scoring **stateless** (pure). Elle doit prendre des arguments explicites et renvoyer un Dict, sans dépendre d'aucun contexte web.

2.  **Création du Serveur MCP (`app/mcp_server.py`)** :

    - Nouveau fichier. Utilise une librairie comme `mcp` ou `fastmcp`.
    - Définit les "Resources" (le dataset Parquet) et les "Tools" (la fonction de scoring).

3.  **Mise à jour Data Pipeline** :
    - Le fichier `odis_june_2025_jacques.parquet` doit être parfaitement propre, car le LLM ne pourra pas "deviner" des colonnes manquantes.

---

## 6. Risques & Mitigations

- **Latence** : Faire une "Deep Research" sur 10 villes peut prendre 30 secondes.
  - _Solution_ : Streamer la réponse ("Je commence à analyser Guéret...") pour faire patienter.
- **Coût Search** :
  - _Solution_ : Ne déclencher le Search Grounding que sur le Top 3 ou 5 final, pas avant.
- **Hallucination des Poids** : L'IA peut mettre des poids aberrants.
  - _Solution_ : Contraindre le schéma JSON de l'input MCP (valeurs bornées 0.0 - 1.0) et définir des valeurs par défaut robustes dans le code Python.
