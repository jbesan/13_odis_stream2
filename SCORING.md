# ⚙️ Logique de Scoring ODIS (Recherche Inversée)

Ce document détaille le fonctionnement interne du moteur de scoring de l'application ODIS. Contrairement aux moteurs de recherche classiques qui filtrent par critères binaires, ODIS calcule un **score de compatibilité** entre une commune et le projet de vie d'une personne.

---

## 🏗️ L'Architecture du Score

Le score final d'une commune est le résultat d'un processus en quatre étapes :

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

### 5. Agrégation et Pondération

Enfin, les scores sont regroupés par catégories (Emploi, Logement, Santé, etc.) puis pondérés selon les préférences de l'utilisateur (Profil Expert ou Prédéfini).

---

## 📊 Synthèse de la Configuration (Tous les Critères)

Voici l'intégralité des 40 critères configurés dans le moteur de soring OD&IS (`scores_config.yaml` + Distance).

### 🏠 Logement

| Critère                       | Poids | Baseline | Boost BdV | Description                                     |
| :---------------------------- | :---- | :------- | :-------- | :---------------------------------------------- |
| **Vacance Structurelle**      | 1.0   | Non      | 0.0       | Taux de vacance (> 2 ans) sur le parc total.    |
| **Logements Sociaux Vacants** | 3.0   | Non      | 0.0       | Taux de logements sociaux inoccupés.            |
| **Délai Logement Social**     | 3.0   | **Oui**  | 0.0       | Délai moyen d'attente (Demande/Attribution).    |
| **Sous-occupation**           | 1.0   | Non      | 0.0       | Part des logements sous-occupés.                |
| **Loyer Moyen (Tous Appt)**   | 3.0   | Non      | 0.0       | Loyer moyen d'annonce au m² (Ensemble).         |
| **Loyer Moyen (T1/T2)**       | 3.0   | Non      | 0.0       | Loyer spécifiquement pour petits appartements.  |
| **Loyer Moyen (T3+)**         | 3.0   | Non      | 0.0       | Loyer spécifiquement pour grands appartements.  |
| **Loyer Moyen (Maisons)**     | 3.0   | Non      | 0.0       | Loyer moyen pour les maisons.                   |
| **Associations IML**          | 1.0   | Non      | 0.8       | Location avec Intermédiation (Solibail, etc.).  |
| **Centres d'Hébergement**     | 2.0   | Non      | 0.5       | Capacité en CHRS / CPH.                         |
| **Foyers & Pensions**         | 2.0   | Non      | 0.5       | Densité FJT, Pensions de famille, Migrants.     |
| **Hébergement Citoyen**       | 2.0   | Non      | 0.8       | Associations d'accueil chez l'habitant.         |
| **Accueils J'Accueille**      | 3.0   | Non      | 1.0       | Présence active d'accueillants (Bassin de Vie). |

### 💼 Emploi & Formation

| Critère                       | Poids | Baseline | Boost BdV | Description                         |
| :---------------------------- | :---- | :------- | :-------- | :---------------------------------- |
| **Opportunités Emploi (A1)**  | 3.0   | Non      | 0.5       | Match direct métiers Adulte 1 (FT). |
| **Opportunités Emploi (A2)**  | 3.0   | Non      | 0.5       | Match direct métiers Adulte 2 (FT). |
| **Tension recrutement (A1)**  | 1.0   | Non      | 0.0       | Métiers en tension Adulte 1.        |
| **Tension recrutement (A2)**  | 1.0   | Non      | 0.0       | Métiers en tension Adulte 2.        |
| **Offres SIAE (A1)**          | 3.0   | Non      | 0.5       | Insertion (SIAE) Adulte 1.          |
| **Offres SIAE (A2)**          | 3.0   | Non      | 0.5       | Insertion (SIAE) Adulte 2.          |
| **Centres de Formation (A1)** | 2.0   | Non      | 0.8       | Formations recherchées Adulte 1.    |
| **Centres de Formation (A2)** | 2.0   | Non      | 0.8       | Formations recherchées Adulte 2.    |
| **Déclin Population Active**  | 3.0   | **Oui**  | 0.0       | Baisse de la population des actifs. |

### 🤝 Inclusion & Lien Social

| Critère                     | Poids | Baseline | Boost BdV | Description                               |
| :-------------------------- | :---- | :------- | :-------- | :---------------------------------------- |
| **Lien Social (Général)**   | 1.0   | **Oui**  | 0.8       | Densité associative globale (RNA).        |
| **Accompagnement Réfugiés** | 1.0   | **Oui**  | 0.8       | Associations spécialisées (RNA).          |
| **SIAE (Densité)**          | 1.0   | **Oui**  | 0.8       | Présence de structures d'insertion.       |
| **Affinités (Thématiques)** | 1.0   | Non      | 0.8       | Assos correspondant aux loisirs/intérets. |
| **Services Inclusion**      | 1.0   | Non      | 0.8       | Match avec les services sélectionnés.     |

### 🗺️ Territoire

| Critère                | Poids | Baseline | Boost BdV | Description                                |
| :--------------------- | :---- | :------- | :-------- | :----------------------------------------- |
| **Population Commune** | 3.0   | **Oui**  | -0.5      | Score basé sur la taille de ville (Gauss). |
| **Sécurité (Taux)**    | 1.0   | **Oui**  | 0.5       | Indice d'insécurité (Vols/Dégradations).   |
| **Zone Stratégique**   | 3.0   | Non      | 0.0       | Zone d'action privilégiée partenaire.      |


### 🎓 Éducation

| Critère                    | Poids | Baseline | Boost BdV | Description                               |
| :------------------------- | :---- | :------- | :-------- | :---------------------------------------- |
| **Petite Enfance**         | 3.0   | Non      | 0.0       | Taux de couverture Crèches/Assmat.        |
| **Ecole Maternelle**       | 2.0   | Non      | 0.0       | Présence locale ou à proximité.           |
| **Ecole Elémentaire**      | 2.0   | Non      | 0.0       | Présence locale ou à proximité.           |
| **Collège**                | 1.0   | Non      | 0.5       | Présence locale ou à proximité.           |
| **Lycée**                  | 1.0   | Non      | 0.8       | Présence locale ou à proximité.           |
| **Classes à risque**       | 1.0   | Non      | 0.5       | Écoles avec un besoin de nouveaux élèves. |
| **Evolution Démog. Jeune** | 2.0   | Non      | 0.0       | Baisse de la population des -15 ans.      |

### 🩺 Santé

| Critère                | Poids | Baseline | Boost BdV | Description                                 |
| :--------------------- | :---- | :------- | :-------- | :------------------------------------------ |
| **Accessibilité Soins** | 2.0   | **Oui**  | 0.0       | Potentiel de RDV médicaux (APL DREES).      |
| **Structure de Santé** | 1.0   | Non      | 0.5       | Match spécifique (Hôpital, Maternité, Psy). |

### 🧭 Mobilité

| Critère                | Poids | Baseline | Boost BdV | Description                               |
| :--------------------- | :---- | :------- | :-------- | :---------------------------------------- |
| **Mobilité Durable**   | 3.0   | **Oui**  | 0.5       | Part des déplacements durables (Ecolab).  |
| **Densité Transports** | 2.0   | **Oui**  | 0.0       | Nombre d'arrêts de transport / 1000 hab.  |
| **Gare SNCF**          | 1.0   | **Oui**  | 0.0       | Présence d'une gare dans la commune.      |
| **Bonus EPCI**         | 1.0   | Non      | 0.0       | Appartenance à la même agglomération.     |
| **Distance Proximité** | 1.0   | Non      | 0.0       | Décroissance linéaire (Proximité réelle). |

---

## ⚡ La Phase de Prescoring (Offline Pipeline)

Avant d'être utilisés dans l'application, les scores passent par une phase de **Prescoring** dans le pipeline ETL (`pipeline/prescoring.py`). Cette étape est cruciale pour la performance :

1.  **Calcul des Ratios** : Conversion des données brutes en indicateurs comparables.
    - Exemple : (Nombre de logements vacants / Parc total) → Taux de vacance.
    - Exemple : (Nombre d'associations / Population) \* 1000 → Densité associative.
2.  **Harmonisation (Scaling)** : Les données sont normalisées entre 0.0 (le moins favorable) et 1.0 (le plus favorable). Pour garantir la robustesse face aux données aberrantes (ouliers), ODIS utilise un **Scaling par Quantiles** (`get_min_max_quant`) :
    - **Cas Standard (1%)** : Par défaut, si aucune borne n'est fixée dans la configuration, le moteur utilise les quantiles **p1** et **p99** comme bornes Min/Max.
    - **Cas Sensibles (5%)** : Pour les données très dispersées comme le **Logement** (Suroccupation, Loyers) ou l'**Éducation** (Petite Enfance, Classes à risque), le filtrage est plus agressif avec les quantiles **p5** et **p95**.
    - **Bornes Fixes** : Si `min_bound` et `max_bound` sont définis dans `scores_config.yaml`, ils priment sur le calcul par quantiles.
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
