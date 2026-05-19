# PRD (Product Requirements Document) - OD&IS "Stream 2"

**Version :** 1.6
**Projet :** Prototype de Recherche Inversée (Aide à la Localisation)
**Auteur :** D4G: OD&IS (revu le 08/05/2026)

---

## 1. Contexte et Objectif

**Objectif :** Fournir un prototype d'outil d'aide à la décision pour les **travailleurs sociaux** accompagnant des personnes/familles en parcours d'intégration (statut régularisé, post-CADA).

**Principe (Recherche Inversée) :** L'outil ne part pas d'un lieu, mais du **"projet de vie"** de la personne pour identifier les territoires (communes ou paires de communes) les plus pertinents.

**Fondations Techniques :**

- **Données :** Exclusivement Open Data.
- **Moteur :** Scoring de pertinence (`scoring.py`) basé sur un profil utilisateur.
- **Stack :** Streamlit (UI), Pandas/GeoPandas (Data), Folium (Carto).

---

## 2. Persona Cible

- **Utilisateur :** Le **travailleur social**.
- **Rôle :** Utilise l'outil _pendant_ l'entretien comme support de médiation et d'exploration.
- **Besoin Clé :** **Explicabilité**. L'outil doit justifier _pourquoi_ un territoire est recommandé (points forts, radar de scores).

---

## 3. Parcours Fonctionnel (Version Actuelle 1.1)

_(Omitted for brevity in this section, see original)_

---

## 4. Idées d'améliorations

Liste en vrac d'idées d'amélioration

- [x] Ajouter un call-to-action dans les fiches du top 5 (e.g. appeler TS local)
- [x] Génerer un prompt pour une Deep Research sur un des territoires recommandés
- [x] Normaliser les scores de catégorie pour éviter une surévaluation de certains critères
- [x] Ajouter des profils de pondération (famille vs célibataire)
- [x] Ajouter la base J'accueille d'accueil chez l'habitant
- [x] Ajouter la base des transports publics ?
- [ ] Critere 'Niveau de vie' de l'INSEE ?
- [ ] Enlever la couleur politique
- [ ] Ajouter le salaire moyen + comparaison avec loyer moyen
- [ ] Regarder pourquoi les grandes villes ressortent plus
- [ ] Ajouter le critère insse d'acces aux instratuctures (mobilité): https://www.insee.fr/fr/statistiques/1908098
- [ ] Ajouter le critère insse d'affordabilité
- [ ] Temps d'accès à un médecin / Déserts médicaux
- [x] Regarder les Entreprises de l'Insertion (et leur soffres d'emploi)
- [x] [F-43] Upgrade to Gemini 3.1 Flash-Lite for all agents.
- [x] [F-44] Standardize all agents to return Pydantic structured outputs instead of raw strings.
- [x] [F-55] Pydantic-Graph Migration: Move from LangGraph to native pydantic-graph MapReduce.
- [x] [F-57] Metadata-Driven Context Architecture (ACL)
- [x] AI Bot: ajouter le contact CCAS (passer dans get_city_details? )

---

## 🚀 Feature [F-58]: Unified Profiling & Refinement Flow

### 📝 User Story
- En tant que travailleur social, je veux que l'IA me propose une synthèse narrative de la situation ("Dossier") après chaque recherche, afin de valider qu'elle a bien compris mon projet de vie.
- En tant qu'architecte, je veux séparer l'extraction technique (Interviewer) de la synthèse narrative (Refiner), et garantir que tous les agents experts utilisent ce briefing consolidé comme source de vérité.

### 🔑 Key Features
- **Centralized Briefing** : Déplacement du `odis_brief` dans `SearchCriterias` (Source of Truth).
- **Refiner Agent** : Transformation de l'agent Scorer en un agent de synthèse global (Dossier + Pitches).
- **Cascading Context** : Le briefing généré par le Refiner est injecté dans le graphe MapReduce pour un grounding de haute qualité.

### 📊 Status
- **May 2026**: Complete.

---

## 🚀 Feature [F-59]: PLM Arrondissement Consolidation & Filtering

### 📝 User Story
- En tant que travailleur social, je veux que les résultats pour Paris, Lyon et Marseille soient présentés au niveau global de la commune et non par arrondissement, afin d'éviter la pollution visuelle sur la carte et de garantir que les scores reflètent les services consolidés du territoire entier.
- En tant qu'administrateur de données, je veux que toutes les fiches détaillées (CCAS, POIs, associations, formations) soient agrégées au niveau de la commune parente globale pour assurer la cohérence et l'exhaustivité des informations.

### 🔑 Key Features
- **ETL Aggregation**: Somme et moyenne pondérée par la population de toutes les variables et ratios d'arrondissements PLM dans le script de build.
- **Reference Table Cascade**: Regroupement des tables CCAS, POIs, RNA, Formations sur les codes INSEE parents (`75056`, `69123`, `13055`) et filtrage complet des arrondissements.
- **Pure Configuration-Driven Weights**: Retrait de tous les multiplicateurs hardcodés dans l'interface formulaire pour s'assurer que les calculs de scoring reposent uniquement sur les poids définis dans `scores_config.yaml`.

### 📊 Status
- **May 2026**: Complete. Fully integrated, tested (100% green pytest suite), and E2E snapshots updated.

