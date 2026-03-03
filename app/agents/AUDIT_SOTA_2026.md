# Audit de l'Architecture Agent ODIS (Février 2026)

L'architecture actuelle (v4.0) est très mature pour un système en production. Elle s'inspire clairement des designs "State-Machine" via LangGraph, qui sont aujourd'hui la norme SOTA (State of the Art) pour les workflows où la garantie de résultat et la prévisibilité priment sur l'autonomie totale (comme avec les systèmes de type "Swarm" non dirigés).

Cependant, comme identifié sur la question du "Bypass", certaines logiques sont encore implicites et couplées en dur. Voici un audit des forces actuelles et des axes d'amélioration selon les standards de l'industrie début 2026.

---

## 🟢 1. Ce qui est "State of the Art" (Les Forces)

- **L'Orchestration "State-Machine" (LangGraph)** : Séparer "qui réfléchit" (l'Agent) de "qui décide du flux" (le Graphe) est la meilleure pratique actuelle (_Authority Split_). Cela empêche les agents de s'enfermer dans des boucles mortes.
- **PydanticAI pour le Structured Output** : L'utilisation de Pydantic pour contraindre les sorties du modèle (`InterviewerResult`, `RoutingResult`) est essentielle. C'est plus robuste que le prompt engineering classique.
- **Le système de Hash et Caching (`commune_artifacts`)** : La stratégie de calculer un MD5 sur les `SearchCriterias` pour invalider ou utiliser le cache des experts (Scout, Web, JobHunter) est une excellente technique (_LLM Engineering_) pour réduire drastiquement la latence et les coûts, tout en garantissant la cohérence des données.
- **Le "Router Bypass"** : Empêcher le Routeur d'intervenir lors de la phase active de `DISCOVERY` (Interviewer) économise des tokens de contexte inutiles.

---

## 🟠 2. Les Limites de l'Architecture Actuelle

### 2.1 Le Couplage Fort "Agent <=> Intention" (Le problème soulevé)

Actuellement, c'est le Graphe (le code en dur) qui déduit l'intention de la requête en fonction de la destination choisie par le Routeur.

- _Si le Routeur cible `scout` $\rightarrow$ Le programme assume un mode `specific_ask` $\rightarrow$ Le Cache est bypassé._
  Le Routeur (LLM) est complètement "aveugle" à cette dynamique. Il ne sait pas qu'en appelant un module, il déclenche un effacement du cache. Si on veut lancer une recherche complète (Trio) _mais_ forcer un rafraîchissement des données, l'architecture v4.0 ne le permet pas sans bricoler le code Python.

### 2.2 LLM Routing vs. Semantic/Tool Routing

Aujourd'hui, le Routeur est un agent LLM complet qui nécessite d'injecter une grande partie du contexte (Briefing, Historique) juste pour sortir une classification (`target_agent`).
En 2026, la tendance est de s'éloigner des LLM génériques (coûteux/lents) pour le routage de base, au profit du **Semantic Routing** (calculs de similarité d'Embeddings très rapides) ou de **Classifier models** ultra-légers.

### 2.3 Context Window Management (Le "Sac à Dos")

L'état global accumule l'historique complet des messages. Même si la fenêtre de contexte des LLMs (Gemini 1.5/2.0) est immense, les performances de raisonnement ("needle in a haystack") diminuent avec un prompt qui gonfle au fil d'une longue session de travailleur social. Le `Refiner` aide pour le Briefing, mais l'historique continue de grandir.

---

## 🚀 3. Recommandations & Évolutions SOTA

### Évolution 1 : Explicitation des Modes de Routage (Priorité Haute)

Il faut découpler la cible (`target_agent`) de l'intention (`execution_mode`) directement dans le cerveau du système (le Routeur).
**Mise en œuvre :** Modifier `RoutingResult` pour que le LLM décide de la stratégie.

```python
class RoutingResult(BaseModel):
    target_agent: Literal['interviewer', 'scorer', 'analysis', 'scout', 'web', 'job_hunter', 'synthesizer']
    execution_mode: Literal['cache_first', 'force_refresh'] = Field(
        default='cache_first',
        description="cache_first: pour explorer/synthétiser avec les données connues. force_refresh: IGNORER le cache pour répondre à une question ad-hoc ou forcer une mise à jour."
    )
    focus_city: Optional[FocusCity] = None
```

_Le graphe exécutera bêtement le `execution_mode` choisi par le Routeur au lieu de le deviner._

### Évolution 2 : Consolidation de la Mémoire Épisodique (Moyen Terme)

Pour régler le gonflement de l'état, l'intégration d'un système de **Working Memory** (inspiré de Mem0 ou Zep) :

- Périodiquement (par ex. tous les 5 messages), un background process (un nœud LangGraph asynchrone) condense l'historique des messages en "Faits Utilisateurs".
- Le `messages: list` de l'état ne conserve que les 5 à 10 derniers échanges (Sliding Window), tandis que le `search_criteria` et le `briefing` assurent la persistante à long terme des intentions.

### Évolution 3 : Modularité via "Multi-Agent Tools" (Long Terme)

Au lieu d'avoir un "Routeur" qui choisit un nœud, utiliser le paradigme **Tool-Calling Supervisor** :

- L'agent principal possède des "outils" nommés `deleguate_to_scout`, `deleguate_to_analysis_trio`, `score_cities`.
- C'est le support natif du Tool Calling des LLM qui gère le routage vers les sous-agents (Swarm architecture contrôlée), réduisant considérablement la complexité du script `graph.py` et s'appuyant sur les capacités d'orchestration natives de Gemini 2.0.
