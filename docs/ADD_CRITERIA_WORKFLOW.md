# 🛠️ Workflow : Ajout d'un Nouveau Critère de Scoring

Ce document décrit la procédure rigoureuse à suivre pour ajouter un critère dans le moteur de recherche inversée ODIS. Il garantit que le critère est correctement calculé, affiché et intégré dans la logique de pertinence.

---

## 1. Phase de Cadrage (Questions à Clarifier)
Avant de toucher au code, clarifiez ces points avec l'utilisateur :
- **Nom Technique** : Format `cat_nom_critere_scaled`.
- **Catégorie** : Doit-il rejoindre une catégorie existante (Logement, Emploi) ou en créer une nouvelle ?
- **Type de Score** : Binaire (0/1), Discret (échelle de 0 à 5) ou Continu (normalisé 0-1) ?
- **Pertinence** : Dans quelles conditions ce critère doit-il être "actif" ?
- **Poids** : Quel est le poids par défaut (weight) et le poids de la catégorie ?

---

## 2. Configuration (`app/scores_config.yaml`)
C'est la source de vérité pour l'affichage et l'agrégation. Ajoutez l'entrée du critère :
- **id** : Le nom exact de la colonne (ex: `mon_critere_scaled`).
- **category** : La catégorie parent (ex: `emploi`, `territoire`).
- **computation: live/precomputed** : 
  - `live` : Calculé à la volée par le `ScoringEngine` (dépend des inputs de l'utilisateur).
  - `precomputed` : Calculé en amont lors de la phase ETL (`pipeline/prescoring.py`).
- **source_metric** : Le nom de la colonne brute *avant* normalisation (pour affichage).
- **show: true/false** : Définit si le critère apparaît dans les tableaux détaillés.
- **tooltip & description** : Indispensables pour l'explicabilité (Agent Scorer et PDF).

---

## 3. Modèles de Données (`app/core/models.py`)
Le typage strict est obligatoire pour assurer la cohérence entre le formulaire et le moteur.
- **Input** : Ajoutez le champ correspondant dans `SearchCriterias` (avec types Pydantic et Field).
- **Output** : 
    - Ajoutez le champ dans la classe `*Metrics` concernée (ex: `EmploymentMetrics`).
    - Créez une nouvelle classe `Metrics` si la catégorie est nouvelle.
    - Enregistrez cette catégorie dans `CommuneResult`.

---

## 4. Implémentation du Calcul (ETL vs Live)
Selon la valeur de `computation` choisie :
- **Cas `precomputed` (ETL)** : 
  - Modifiez `pipeline/prescoring.py`.
  - Utilisez la fonction `process_scaling(df, 'colonne_brute', 'colonne_scaled')` pour générer le score normalisé qui sera sauvegardé dans le parquet.
- **Cas `live` (ScoringEngine)** : 
  - Modifiez `app/core/scoring.py`.
  - Créez une méthode `_compute_XYZ_scores` et appelez-la dans `_compute_criteria_scores`.

## 5. Activation et Moteur de Scoring (`app/core/scoring.py`)
C'est l'étape la plus critique et la plus sujette aux oublis :
1. **Activation (`_get_active_criteria`)** : Ajoutez la logique métier qui rend le critère "actif" (ex: `if config.mon_besoin: active.add('mon_critere_scaled')`). *⚠️ Sans cela, le score sera ignoré.*
2. **Pondération Globale (Automatique)** : Grâce à la découverte dynamique, le moteur calcule automatiquement le poids de la catégorie en cherchant l'attribut `poids_XYZ` dans le modèle `SearchCriterias` (où `XYZ` est le nom de la catégorie dans le YAML).

---

## 5. Interface Utilisateur (`app/ui/forms.py`)
1. **Widget** : Créez la fonction de rendu (ex: `render_my_criteria_form`). Utilisez des labels lisibles (via `data_loader.get_app_data()` si nécessaire).
2. **Intégration Page** : Si le critère est une nouvelle étape, modifiez `PAGES` dans `2_Formulaire.py`.
3. **Persistance** : Vérifiez que la valeur est stockée dans `st.session_state` avec le préfixe `ui_`.

---

## 6. Initialisation & Deep-linking (`app/utils/data_loader.py`)
Mettez à jour `ensure_data_initialized` et ses fonctions satellites :
- **Default Values** : Ajoutez le critère dans `config.DEMO_DATA_DEFAULT`.
- **Query Params** : Gérez l'injection via URL (ex: `?org=...` ou `?demo=...`).
- **Sync UI** : Ajoutez la logique de synchronisation dans `apply_search_criteria_to_ui`.

---

## 8. Vérification & Télémétrie
- **Télémétrie Automatique** : Grâce à Pydantic, toute modification de `SearchCriterias` est automatiquement capturée dans les logs BigQuery via `model_dump()`.
- **Génération PDF Automatique** : Les tableaux détaillés du PDF bouclent dynamiquement sur `commune.scores` généré par `scores_config.yaml`.
- **Audit UI** : Lancez une recherche et vérifiez dans le détail d'une ville que le score est calculé et sa valeur brute (KPI) affichée.
- **Expert Pitch** : Vérifiez que l'agent Scorer prend bien en compte ce nouveau critère dans ses explications.

---

## ⚠️ Points de Fragilité (Technical Debt)
Idéalement, l'ajout d'un critère ne devrait nécessiter qu'une entrée dans `scores_config.yaml`, les widgets UI et la logique métier de calcul. Actuellement, le système présente ces points de friction :
1. **Redondance des Modèles Pydantic** : Il faut déclarer manuellement les champs dans `SearchCriterias` (input) et dans les sous-modèles `*Metrics` (output).
2. **Gestion de l'Activation** : L'ajout explicite dans `_get_active_criteria` est obligatoire, car le système ne sait pas déduire tout seul quand un critère est pertinent (ce qui est normal pour des règles métier complexes, mais propice aux oublis).

> [!IMPORTANT]
> **Règle d'or :** Si vous ajoutez une catégorie, assurez-vous de créer le champ `poids_nom_categorie` dans `SearchCriterias`. Le `ScoringEngine` s'occupera du reste automatiquement.
