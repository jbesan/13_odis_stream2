# ⚙️ Logique de Scoring ODIS (Recherche Inversée)

Ce document détaille le fonctionnement interne du moteur de scoring de l'application ODIS. Contrairement aux moteurs de recherche classiques qui filtrent par critères binaires, ODIS calcule un **score de compatibilité** entre une commune et le projet de vie d'une personne.

---

## 🏗️ L'Architecture du Score

![Explication de la logique de scoring](./images/Screenshot-4.png)

### Flux Global du Score (Pre-scoring vs Live-scoring)

Le flux de données et de calculs se décompose entre la phase de préparation offline (ETL) et la phase d'évaluation live (Streamlit) :

```mermaid
graph TD
    %% Offline Pipeline (Pre-scoring)
    subgraph Offline ["⚡ Pipeline de Prescoring (Offline ETL)"]
        A["Données Brutes (INSEE, RNA, SSMSI, USH, CAF, etc.)"] --> B["pipeline/prescoring.py"]
        B --> C["Calcul des Ratios & Indicateurs"]
        C --> D["Scaling & Bornage (Standard p1/p99, Sensible p5/p95, Bornes fixes)"]
        D --> E["Consolidation PLM (Paris, Lyon, Marseille)"]
        E --> F[("Dataset final (odis_communes.parquet)")]
    end

    %% Online Engine (Live-scoring)
    subgraph Online ["🟢 Moteur de Scoring Dynamique (Live Streamlit)"]
        G["Profil Utilisateur & Critères (SearchCriterias)"] --> H["Moteur de Scoring (app/core/scoring.py)"]
        F --> H
        
        %% Dynamic Calculations
        H --> I["Calculs Live / Dynamiques"]
        I --> I1["Proximité Géographique (Décroissance linéaire)"]
        I --> I2["Taille de Ville (Courbe Gaussienne)"]
        I --> I3["Matchs Directs & Live (Emploi FT, Formations, Santé, Affinités)"]
        
        %% BdV Boost
        I1 & I2 & I3 --> J["Application du Boost Bassin de Vie (BdV)"]
        
        %% Category aggregation & Percentile normalisation
        J --> K["Agrégation & Normalisation par Centiles (Percentile Ranking par Catégorie)"]
        
        %% Weighted Sum & Map limit
        K --> L["Somme Pondérée Finale (weighted_score)"]
        L --> M["Cutoff Map (Top 1000 pour la Carte Folium)"]
    end
```

Le score final d'une commune est le résultat d'un processus en six étapes :

### 1. Filtrage et Proximité Géographique

Le moteur commence par délimiter la zone de recherche (Département, Région ou France entière). Un score de distance est ensuite calculé par rapport à la localisation actuelle de l'utilisateur :

- On utilise une **décroissance linéaire** : plus la commune est proche, plus le score est élevé ($Score = 1 - \frac{distance}{distance\_max}$).
- Par défaut, la recherche est optimisée dans un rayon de 50km (ajustable via les bornes de configuration).

### 2. Le Score de Taille (Fonction Gaussienne)

ODIS ne filtre pas par "nombre d'habitants" minimum. Il utilise une **courbe de Gauss** pour favoriser les villes moyennes (l'idéal d'accueil).

- **Moyenne ($\mu$)** : 50 000 habitants.
- **Écart-type ($\sigma$)** : 40 000 habitants.
- Cela signifie qu'une ville de 50 000 habitants obtiendra un score de 1.0, tandis qu'une métropole géante ou un petit village obtiendront des scores plus faibles.

### 3. L'Enrichissement par le Bassin de Vie (Boost Opportunity) 🚀

C'est le cœur de l'innovation ODIS. On considère qu'une commune n'est pas une île : elle bénéficie des services de son **Bassin de Vie**.

Pour chaque critère (Emploi, Santé, Éducation), nous appliquons une logique de **Boost non-pénalisant** :

- **Formule** : $Score = S_{commune} + (1 - S_{commune}) \times (S_{BassinDeVie} \times factor)$
- **Avantages** :
  - **Jamais pénalisant** : Si le bassin de vie est pauvre en services, le score local n'est pas impacté.
  - **Bonus de proximité** : Si des opportunités existent à proximité, elles viennent combler le "manque" local.
  - **Factor** : Chaque critère a un facteur de pondération (ex: 0.5 pour l'emploi, 0.8 pour les formations) qui réduit le poids du Bassin de Vie s'il est jugé plus éloigné.

### 4. Le Pattern "Baseline Criteria" (Mandatory Metrics) 🛡️

Depuis 2026, ODIS intègre des **critères de référence (Baselines)**. Contrairement aux critères classiques qui s'activent selon les besoins de l'utilisateur, les Baselines sont **systématiquement actives** et contribuent au score global avec des poids fixes.

- **Objectif** : Garantir qu'un standard minimum de qualité territoriale (sécurité, accès aux soins, mobilité durable) soit évalué pour chaque dossier.
- **Visibilité** : Ces critères sont visibles dans les rapports détaillés et utilisés par l'IA pour justifier ses recommandations.

### 5. Normalisation des Scores par Catégorie (Percentile Ranking) 📊

Depuis mai 2026, afin de résoudre le problème des disparités d'écarts de scores entre les catégories (par exemple, la catégorie Logement qui avait historiquement des scores bruts faibles, tandis que la Santé ou l'Éducation avaient des scores très élevés, créant un biais de pondération implicite), ODIS applique une **normalisation par centiles (percentile ranking)** au niveau de chaque catégorie :

- **Principe** : Les scores bruts agrégés d'une catégorie pour toutes les communes qualifiées sont convertis en rangs centiles uniformes dans l'intervalle $[0, 1]$.
- **Protection Zéro Absolu** : Les communes obtenant un score brut de exactement `0.0` (aucun indicateur actif rencontré) sont exclues de l'opération de classement et restent fixées à `0.0` pour éviter une inflation artificielle.
- **Protection Variance Nulle** : Si toutes les communes qualifiées obtiennent le même score de catégorie (cas de recherche sur un ensemble minuscule ou mocké), le classement est ignoré et le score brut uniforme est conservé.
- **Résultat** : Toutes les catégories actives ont désormais une distribution uniforme centrée autour de 0.5. Les coefficients de pondération (ex: Famille, Économique) choisis par l'utilisateur sont ainsi parfaitement respectés.

### 6. Agrégation et Pondération

Enfin, les scores normalisés de chaque catégorie sont regroupés puis pondérés selon les préférences de l'utilisateur (Profil Expert ou Prédéfini) pour obtenir le score final global.

---

## 📊 Synthèse de la Configuration (Tous les Critères)

Voici l'intégralité des 45 critères configurés dans le moteur de scoring OD&IS (`scores_config.yaml` + Distance).

### 🏠 Logement

| Critère                       | Calcul | Poids | Baseline | Boost BdV | Description                                     |
| :---------------------------- | :----- | :---- | :------- | :-------- | :---------------------------------------------- |
| **Vacance Structurelle**      | Pre-scoring | 1.0   | Non      | 0.0       | Taux de vacance (> 2 ans) sur le parc total.    |
| **Logements Sociaux Vacants** | Pre-scoring | 3.0   | Non      | 0.0       | Taux de logements sociaux inoccupés.            |
| **Délai Logement Social**     | Pre-scoring | 3.0   | **Oui**  | 0.0       | Délai moyen d'attente (Demande/Attribution).    |
| **Sous-occupation**           | Pre-scoring | 1.0   | Non      | 0.0       | Part des logements sous-occupés.                |
| **Loyer Moyen (Tous Appt)**   | Pre-scoring | 3.0   | Non      | 0.0       | Loyer moyen d'annonce au m² (Ensemble).         |
| **Loyer Moyen (T1/T2)**       | Pre-scoring | 3.0   | Non      | 0.0       | Loyer spécifiquement pour petits appartements.  |
| **Loyer Moyen (T3+)**         | Pre-scoring | 3.0   | Non      | 0.0       | Loyer spécifiquement pour grands appartements.  |
| **Loyer Moyen (Maisons)**     | Pre-scoring | 3.0   | Non      | 0.0       | Loyer moyen pour les maisons.                   |
| **Associations IML**          | Pre-scoring | 1.0   | Non      | 0.8       | Location avec Intermédiation (Solibail, etc.).  |
| **Centres d'Hébergement**     | Pre-scoring | 2.0   | Non      | 0.5       | Capacité en CHRS / CPH.                         |
| **Foyers & Pensions**         | Pre-scoring | 2.0   | Non      | 0.5       | Densité FJT, Pensions de famille, Migrants.     |
| **Hébergement Citoyen**       | Pre-scoring | 2.0   | Non      | 0.8       | Associations d'accueil chez l'habitant.         |
| **Accueils J'Accueille**      | Pre-scoring | 3.0   | Non      | 1.0       | Présence active d'accueillants (Bassin de Vie). |

### 💼 Emploi & Formation

| Critère                       | Calcul | Poids | Baseline | Boost BdV | Description                         |
| :---------------------------- | :----- | :---- | :------- | :-------- | :---------------------------------- |
| **Opportunités Emploi (A1)**  | Live-scoring | 3.0   | Non      | 0.5       | Match direct métiers Adulte 1 (FT). |
| **Opportunités Emploi (A2)**  | Live-scoring | 3.0   | Non      | 0.5       | Match direct métiers Adulte 2 (FT). |
| **Tension recrutement (A1)**  | Live-scoring | 1.0   | Non      | 0.0       | Métiers en tension Adulte 1.        |
| **Tension recrutement (A2)**  | Live-scoring | 1.0   | Non      | 0.0       | Métiers en tension Adulte 2.        |
| **Offres SIAE (A1)**          | Live-scoring | 3.0   | Non      | 0.5       | Insertion (SIAE) Adulte 1.          |
| **Offres SIAE (A2)**          | Live-scoring | 3.0   | Non      | 0.5       | Insertion (SIAE) Adulte 2.          |
| **Centres de Formation (A1)** | Live-scoring | 2.0   | Non      | 0.8       | Formations recherchées Adulte 1.    |
| **Centres de Formation (A2)** | Live-scoring | 2.0   | Non      | 0.8       | Formations recherchées Adulte 2.    |
| **Déclin Population Active**  | Pre-scoring | 3.0   | **Oui**  | 0.0       | Baisse de la population des actifs. |

### 🤝 Inclusion & Lien Social

| Critère                     | Calcul | Poids | Baseline | Boost BdV | Description                               |
| :-------------------------- | :----- | :---- | :------- | :-------- | :---------------------------------------- |
| **Lien Social (Général)**   | Pre-scoring | 1.0   | **Oui**  | 0.8       | Densité associative globale (RNA).        |
| **Accompagnement Réfugiés** | Pre-scoring | 1.0   | **Oui**  | 0.8       | Associations spécialisées (RNA).          |
| **SIAE (Densité)**          | Pre-scoring | 1.0   | **Oui**  | 0.8       | Présence de structures d'insertion.       |
| **Affinités (Thématiques)** | Live-scoring | 1.0   | Non      | 0.8       | Assos correspondant aux loisirs/intérets. |
| **Services Inclusion**      | Live-scoring | 1.0   | Non      | 0.8       | Match avec les services sélectionnés.     |

### 🗺️ Territoire

| Critère                | Calcul | Poids | Baseline | Boost BdV | Description                                |
| :--------------------- | :----- | :---- | :------- | :-------- | :----------------------------------------- |
| **Population Commune** | Live-scoring | 3.0   | **Oui**  | -0.5      | Score basé sur la taille de ville (Gauss). |
| **Sécurité (Taux)**    | Pre-scoring | 1.0   | **Oui**  | 0.5       | Indice d'insécurité (Vols/Dégradations).   |
| **Couleur Politique**  | Pre-scoring | 1.0   | **Oui**  | 0.0       | Orientation politique locale de la commune. |
| **Zone Stratégique**   | Live-scoring | 3.0   | Non      | 0.0       | Zone d'action privilégiée partenaire.      |


### 🎓 Éducation

| Critère                    | Calcul | Poids | Baseline | Boost BdV | Description                               |
| :------------------------- | :----- | :---- | :------- | :-------- | :---------------------------------------- |
| **Petite Enfance**         | Pre-scoring | 3.0   | Non      | 0.0       | Taux de couverture Crèches/Assmat.        |
| **Ecole Maternelle**       | Live-scoring | 2.0   | Non      | 0.0       | Présence locale ou à proximité.           |
| **Ecole Elémentaire**      | Live-scoring | 2.0   | Non      | 0.0       | Présence locale ou à proximité.           |
| **Collège**                | Live-scoring | 1.0   | Non      | 0.5       | Présence locale ou à proximité.           |
| **Lycée**                  | Live-scoring | 1.0   | Non      | 0.8       | Présence locale ou à proximité.           |
| **Classes à risque**       | Pre-scoring | 1.0   | Non      | 0.5       | Écoles avec un besoin de nouveaux élèves. |
| **Evolution Démog. Jeune** | Pre-scoring | 2.0   | Non      | 0.0       | Baisse de la population des -15 ans.      |

### 🩺 Santé

| Critère                | Calcul | Poids | Baseline | Boost BdV | Description                                 |
| :--------------------- | :----- | :---- | :------- | :-------- | :------------------------------------------ |
| **Accessibilité Soins** | Pre-scoring | 2.0   | **Oui**  | 0.0       | Potentiel de RDV médicaux (APL DREES).      |
| **Structure de Santé** | Live-scoring | 1.0   | Non      | 0.5       | Match spécifique (Hôpital, Maternité, Psy). |

### 🧭 Mobilité

| Critère                | Calcul | Poids | Baseline | Boost BdV | Description                               |
| :--------------------- | :----- | :---- | :------- | :-------- | :---------------------------------------- |
| **Mobilité Durable**   | Pre-scoring | 3.0   | **Oui**  | 0.5       | Part des déplacements durables (Ecolab).  |
| **Densité Transports** | Live-scoring | 2.0   | **Oui**  | 0.0       | Nombre d'arrêts de transport / 1000 hab.  |
| **Gare SNCF**          | Pre-scoring | 1.0   | **Oui**  | 0.0       | Présence d'une gare dans la commune.      |
| **Bonus EPCI**         | Live-scoring | 1.0   | Non      | 0.0       | Appartenance à la même agglomération.     |
| **Distance Proximité** | Live-scoring | 1.0   | Non      | 0.0       | Décroissance linéaire (Proximité réelle). |

---

## ⚡ La Phase de Prescoring (Offline Pipeline)

Avant d'être utilisés dans l'application, les scores passent par une phase de **Prescoring** dans le pipeline ETL (`pipeline/prescoring.py`). Cette étape est cruciale pour la performance :

1.  **Calcul des Ratios** : Conversion des données brutes en indicateurs comparables.
    - Exemple : (Nombre de logements vacants / Parc total) → Taux de vacance.
    - Exemple : (Nombre d'associations / Population) \* 1000 → Densité associative.
2.  **Harmonisation (Scaling)** : Les données sont normalisées entre 0.0 (le moins favorable) et 1.0 (le plus favorable). Pour garantir la robustesse face aux données aberrantes (ouliers), ODIS utilise un **Scaling par Quantiles** (`get_min_max_quant`) :
    - **Cas Standard (1%)** : Par défaut, si aucune borne n'est fixée dans la configuration, le moteur utilise les quantiles **p1** et **p99** comme bornes Min/Max.
    - **Cas Sensibles (5%)** : Pour les données très dispersées comme le **Logement** (Suroccupation, Loyers) ou l'**Éducation** (Petite Enfance, Classes à risque), le filtrage est plus agressif avec les quantiles **p5** et **p95**.
    - **Bornes Fixes** : Si `min_bound` et `max_bound` sont définis dans `scores_config.yaml`, ils priment sur le calcul par quantiles. Par exemple, l'**Indice de Sécurité** (`ter_insecurite_scaled`) utilise des bornes fixes de `min_bound=0` (sécurité maximale) à `max_bound=100` (insécurité/délinquance maximale, correspondant aux 0.5% de communes les moins sûres de France) afin de ne pas artificiellement pénaliser les villes moyennes et grandes par rapport aux très petits villages ruraux.
3.  **Filtrage Qualité** : Les valeurs aberrantes extrêmes sont ainsi écrêtées (clipping) entre 0 et 1.
4.  **Consolidation des Métropoles PLM (Paris, Lyon, Marseille)** : 
    Pour éviter que les arrondissements de Paris, Lyon et Marseille apparaissent comme des communes individuelles (ce qui fausse la visualisation cartographique et l'analyse), l'ETL consolide les données au niveau de la commune parente globale (codes INSEE `75056`, `69123`, `13055`) :
    - **Somme** : Les variables en valeurs absolues (ex: nombre d'associations, capacité d'hébergement, places en crèche) sont sommées sur l'ensemble des arrondissements.
    - **Moyenne pondérée** : Les indicateurs de taux ou ratios (ex: taux de vacance, taux de couverture petite enfance, délai d'attente logement social, loyer moyen) sont moyennés en utilisant la population de chaque arrondissement comme poids.
    - **Gares SNCF** : La présence de gares majeures est explicitement assurée pour les communes parentes consolidées.
    - **Filtrage des enfants** : Tous les codes d'arrondissements individuels sont supprimés du jeu de données final (`odis_communes.parquet`) et de toutes les tables associées (CCAS, POIs, RNA, Formations) afin que les données et les fiches descriptives ne fassent référence qu'au niveau commune globale.

---

## 🩺 Santé et Mobilité : Logiques Spécifiques

### Santé

Le score de Santé est **dynamique** et dépend du besoin exprimé :

- Si l'utilisateur sélectionne un besoin (ex: "Maternité"), le moteur cherche la structure la plus proche dans la commune.
- Le **Boost BdV** intervient ici : si la commune n'a pas de maternité mais que le Bassin de Vie en possède une, un score partiel (facteur 0.5) est attribué.

### Mobilité

La mobilité est évaluée sur trois axes :

- **Transports en commun** : Basé sur la densité d'arrêts (GTFS) par habitant.
- **Accès Ferroviaire** : Bonus pour la présence d'une gare SNCF dans la commune.
- **Proximité (Distance Decay)** : Utilisation de la décroissance linéaire par rapport au point de départ.
- **Bonus EPCI** : Un bonus est accordé si la commune appartient à la même intercommunalité que la ville actuelle, favorisant les déplacements au sein d'un même bassin d'emploi.

---

## ⚡ Limitations de Performance (Map Cutoff) 🏁

Pour garantir une expérience utilisateur fluide sur la carte Folium (évitant le gel du navigateur avec des dizaines de milliers de polygones), le moteur applique une **limitation automatique** :

- **Seuil** : 1 000 communes maximum (`MAX_MAP_POLYGONS` dans `app/config.py`).
- **Logique** : Seules les 1 000 meilleures communes selon le `weighted_score` sont conservées pour l'affichage cartographique.
- **Exception** : La **commune actuelle** (départ) est systématiquement conservée dans le jeu de données, même si son score est faible, afin de servir de point de repère visuel.

> Cette optimisation permet de réduire drastiquement l'empreinte mémoire côté client tout en conservant les résultats les plus pertinents pour le projet de vie.
