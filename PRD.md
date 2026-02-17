# PRD (Product Requirements Document) - OD&IS "Stream 2"

**Version :** 1.4
**Projet :** Prototype de Recherche Inversée (Aide à la Localisation)
**Auteur :** D4G: OD&IS (revu le 08/12/2025)

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

### Étape 1 : Accueil (`1_Accueil.py`)

- Présentation du projet, logos, et information RGPD.
- L'utilisateur peut commencer le parcours pour remplir le formulaire ou aller directement à la page des résultats.
- Le parcours peut être personnalisé avec le nom de la personne accompagnée.
- Des scénarios de démonstration peuvent être chargés via des paramètres dans l'URL.

### Étape 2 : Formulaire "Projet de Vie" (`2_Formulaire.py`)

Collecte des besoins via un formulaire multi-pages (basé sur `st.session_state['form_page']`).

- **Localisation :** `ui_commune` (point de départ).
- **Famille :** `ui_nb_adultes`, `ui_nb_enfants`.
- **Éducation :** `ui_classe_enfant_{i}` (déclenche le scoring Éducation si `nb_enfants > 0`).
- **Projet Pro :**
  - `ui_metiers_adult_{i}` ([DEPRECATED] codes FAP, now using ROME).

  - `ui_formations_adult_{i}` (codes formations, alimente `form_match_adult_scaled`).

- **Logement :**
  - `ui_hebergement` (ex: "Chez l'habitant") : solution temporaire à l'arrivée.
  - `ui_logement` (ex: "Logement Social") : Active les scores pertinents (`log_soc_inoc_scaled`, `log_vac_scaled`, etc.).
- **Santé & Inclusion :**
  - `ui_besoin_sante` (ex: "Maternité").
  - `ui_inc_services_add_selection` (services d'inclusion).
  - `ui_inc_asso_add_selection` (affinités associatives).
- **Données Qualitatives (Agent uniquement) :**
  - `notes_qualitatives`: "Champ libre" (liste de notes) collectant des indices comme l'origine culturelle, la religion, les passions ou les capacités de mobilité (permis, vélo). Ces données enrichissent la recherche de l'agent SCOUT sans impacter le score numérique global.
- **Mobilité :** Sélection consolidée (`ui_mobility_region`, `ui_mobility_dept`, `ui_mobility_france`). `loc_search_area` définit le périmètre de filtrage ('france', 'region', 'departement'). Les scores de mobilité incluent la présence d'une gare (`mob_gare_scaled`) et l'appartenance à la même agglomération (`mob_epci_scaled`).

### Étape 3 : Interface de Résultats (`3_Resultats.py`)

Page principale interactive (layout `col_results`, `col_map`).

- Une barre latérale permet d'ajuster les poids des catégories et les filtres de recherche.
- Des onglets permettent de modifier rapidement les critères du "projet de vie".
- Un bouton "Mettre à jour" relance la recherche avec les nouveaux paramètres.

### Étape 4 : Moteur de Scoring (`scoring.py`)

Logique métier principale, orchestrée par `compute_odis_score()`.

1.  **Pré-filtrage :** `df[df.population > config.pop_min]`.
2.  **Filtrage Géo :** `filter_communes()` sélectionne les communes selon `loc_search_area` (Département, Région, France) ou un code spécifique (Region/Dep) fourni via `loc_search_code`.
3.  **Calcul Center :** Calcul du centroïde moyen des communes résultantes pour centrer la carte.
4.  **Scores Critères :** `_compute_criteria_scores()` calcule les scores normalisés pour les critères activés (Emploi, Formation, Santé, Inclusion...).
5.  **Logique "Binôme" :**
    - `add_neighbor_scores()` crée des paires (monôme et binôme).
    - `compute_category_scores()` calcule le score de catégorie (moyenne pondérée des critères).
    - Le score d'un critère pour un binôme est le `max(score_A, score_B * (1 - binome_penalty))`.
6.  **Score Pondéré :** `compute_weighted_score()` applique les poids des catégories (`config.poids_...`).
7.  **Sélection Finale :** `select_best_score_per_commune()` ne garde que la meilleure ligne pour chaque `codgeo`.

### Étape 5 : Visualisation des Résultats (`3_Resultats.py`)

- **Colonne de Gauche (Liste) :** Affiche le Top 5 des résultats (commune ou binôme) avec leurs points forts et un radar de scores.
- **Colonne de Droite (Carte) :** Affiche une carte choroplèthe avec le score de toutes les communes. Des couches d'information additionnelles (écoles, santé, services) peuvent être superposées.

---

## 4. Idées d'améliorations

Liste en vrac d'idées d'amélioration

- [x] Ajouter un call-to-action dans les fiches du top 5 (e.g. appeler TS local)
- [x] Génerer un prompt pour une Deep Research sur un des territoires recommandés
- [x] Normaliser les scores de catégorie pour éviter une surévaluation de certains critères
- [x] Ajouter des profils de pondération (famille vs célibataire)
- [ ] Ajouter la base J'accueille d'accueil chez l'habitant
- [x] Ajouter la base des transports publics ?
- [ ] Critere 'Niveau de vie' de l'INSEE ?
- [ ] Enlever la couleur politique
- [ ] Ajouter le salaire moyen + comparaison avec loyer moyen
- [ ] Regarder pourquoi les grandes villes ressortent plus
- [ ] Ajouter le critère insse d'acces aux instratuctures (mobilité): https://www.insee.fr/fr/statistiques/1908098
- [ ] Ajouter le critère insse d'affordabilité
- [ ] Temps d'accès à un médecin / Déserts médicaux
- [ ] Regarder les Entreprises de l'Insertion (et leur soffres d'emploi)
  - champ 'est_siae' https://recherche-entreprises.api.gouv.fr/docs/
  - Il y a aussi des tables structures + offres sur Odace
- [ ] Remplacer par y-a-t'il un CTAI/PTAI (signe que la commune s'implique dans l'intégration) + Est-ce que la commune est adhérante ANVITA ? Ajouter un label ?
- [x] Rechercher au niveau national ou viser une région/ département en particulier?
- [x] Ajouter un 'en savoir plus' pour comprendre le scoring
- [ ] Gare --> a conditionner avec une question mobilité (besoin de revenir regulierement)
- [x] FLE doit être un critere à part entiere et il faut trouver une base spécifique
- [ ] Les multiselects sont frustrants car il faut trouver la terminologie exacte
- [x] Créer et exploiter un dataset des associations dédiés aux réfugiés / demandeurs d'asile
- [ ] AI Bot: ajouter le contact CCAS (passer dans get_city_details? )

Territoires ANVITA
["ALFORTVILLE", "ALLONNES", "ANNECY", "ANNEMASSE", "ARCUEIL", "ARGENTON-SUR-CREUSE", "BAGNOLET", "BARBERAZ", "BEGLES", "BESANÇON", "BESSANCOURT", "BLOIS", "BOBIGNY", "BORDEAUX", "BOURGES", "BOURGOGNE-FRANCHE-COMTE", "Buis-les-Baronnies", "CASTANET-TOLOSAN", "CENTRE - VAL DE LOIRE", "CHAMBÉRY", "CHERBOURG-EN-COTENTIN", "CLERMONT-FERRAND", "CLUNISOIS - COMMUNAUTÉ DE COMMUNES", "COGNIN", "COURNEUVE (LA)", "DIE", "DIEULEFIT", "DIOIS - COMMUNAUTÉ DE COMMUNES", "DULLIN", "Figeac", "FLECHE (LA)", "FONTENAY-SOUS-BOIS", "FORGES", "FOURNEAUX", "FRANOIS", "GIRONDE (DEPARTEMENT)", "GRABELS", "GRANVILLE", "GRENOBLE", "GRENOBLE-ALPES METROPOLE", "GUILLESTRE", "HENDAYE", "JARCIEU", "LA TALAUDIÈRE", "Lambersart", "Le Percy", "LOOS-EN-GOHELLE", "LOUVIGNY", "LYON", "LYON METROPOLE", "MALAKOFF", "MANDAGOUT", "MARSEILLE", "MARTIGUES", "MELLE", "MÉRIGNAC", "METZ", "Mirabel et Blacons", "MONTPELLIER", "MONTREUIL", "NANCY", "NANTES", "NOTRE DAME DE L'OSIER", "OCCITANIE", "ORNANS", "PARIS", "PAYS BASQUE - COMMUNAUTÉ D'AGGLOMÉRATION", "PÉRIGUEUX", "POITIERS", "PONT-PÉAN", "PRADES-LE-LEZ", "PYRÉNÉES VALLÉES DES GAVES - COMMUNAUTÉ DE COMMUNES", "RAMONVILLE-SAINT-AGNE", "RELECQ-KERHUON (LE)", "RENNES METROPOLE", "ROUEN", "ROUEN-NORMANDIE", "SAINT-BALDOPH", "SAINT-DENIS", "SAINT-ERBLON", "SAINT-JEAN-D'ANGÉLY", "SALIÈS", "SCHILTIGHEIM", "SEINE-SAINT-DENIS", "STRASBOURG", "TOURS", "VIGAN (LE)", "VILLEURBANNE", "VIZILLE", "YQUELON"]

## 5. Features

## 🚀 Feature [F-01]: Navigation Principale

### 📝 User Story

En tant que travailleur social, je veux des points de départ clairs pour commencer un nouveau profil ou accéder directement aux résultats afin de naviguer efficacement dans l'outil.

### 🔑 Key Features

- Un bouton "Commencer le parcours" pour démarrer le formulaire.
- Un bouton "Aller directement à la page résultats" pour contourner le formulaire.

### 📊 Status

- Completed

## 🚀 Feature [F-02]: Scénarios de Démonstration

### 📝 User Story

En tant que travailleur social, je veux pouvoir charger des scénarios de démonstration pré-remplis pour comprendre rapidement les capacités de l'outil sans avoir à remplir manuellement le formulaire.

### 🔑 Key Features

- Charger un profil de démo à l'aide d'un paramètre de requête d'URL (par ex., `?demo=...`).
- Le formulaire est pré-rempli avec les données de la démo.

### 📊 Status

- Completed

## 🚀 Feature [F-03]: Personnalisation du Parcours

### 📝 User Story

En tant que travailleur social, je veux saisir le nom de la personne que j'accompagne pour personnaliser l'interface, rendant le processus plus centré sur l'humain.

### 🔑 Key Features

- Un champ de saisie pour le nom de la personne sur la page d'accueil.
- Le nom est utilisé pour personnaliser les titres et les libellés dans toute l'application.

### 📊 Status

- Completed

## 🚀 Feature [F-04]: Contrôles Interactifs des Résultats

### 📝 User Story

En tant que travailleur social, je veux ajuster les poids de notation et les filtres en temps réel pour explorer collaborativement différents scénarios avec la personne que j'accompagne.

### 🔑 Key Features

- Une barre latérale avec des curseurs pour ajuster les poids des différentes catégories de notation.
- Une barre latérale avec des champs pour filtrer les résultats (par ex., population minimale).
- Un bouton "Mettre à jour" pour relancer la recherche avec les nouvelles valeurs.

### 📊 Status

- Completed

## 🚀 Feature [F-05]: Édition Rapide des Entrées

### 📝 User Story

En tant que travailleur social, je veux pouvoir modifier rapidement les entrées initiales du "projet de vie" depuis la page des résultats pour itérer sur la recherche sans retourner au formulaire principal.

### 🔑 Key Features

- Des onglets sur la page des résultats qui reflètent les champs du formulaire.
- La possibilité de changer n'importe quelle valeur d'entrée.
- Un bouton "Mettre à jour" pour relancer la recherche avec les nouvelles valeurs.

### 📊 Status

- Completed

## 🚀 Feature [F-06]: Affichage des 5 Meilleurs Résultats

### 📝 User Story

En tant que travailleur social, je veux voir une liste claire et classée des meilleurs lieux recommandés pour discuter facilement des options les plus prometteuses.

### 🔑 Key Features

- Affiche les 5 meilleurs résultats (commune seule ou en binôme).
- Montre les points forts de chaque résultat.
- Inclut un graphique radar pour visualiser la répartition des scores par catégorie.

### 📊 Status

- Completed

## 🚀 Feature [F-07]: Visualisation sur Carte Interactive

### 📝 User Story

En tant que travailleur social, je veux voir les lieux recommandés sur une carte interactive pour mieux comprendre leur contexte géographique et explorer les services environnants.

### 🔑 Key Features

- Une carte choroplèthe montrant les scores de toutes les communes dans la zone de recherche.
- Des marqueurs pour les 5 meilleurs résultats.
- L'option "Afficher le Top 5" affiche le rang (1-5) sur la carte au centroïde de chaque résultat du top 5.
- Des couches cartographiques activables pour les écoles, les services de santé et d'autres points d'intérêt.

### 📊 Status

- Completed

## 🚀 Feature [F-08]: Ajout de l'indicateur loyer moyen

### 📝 User Stories

- En tant que travailleur social, je veux intégrer le loyer moyen dans le score pour mieux évaluer l'accessibilité financière d'une localité pour la famille que j'accompagne.

### 🔑 Key Features

- Intégrer une nouvelle source de données (API) fournissant le loyer moyen par commune.
- Créer un nouveau critère de score "loyer" dans la catégorie "Logement", où un loyer plus bas résulte en un meilleur score.
- Afficher le loyer comme un point fort (ex: "Loyer modéré") dans la liste des résultats lorsqu'il est significatif pour une localité.

### 📊 Status

- Completed

### 🔑 Key Features

- **Intégration Données :** Source "Carte des Loyers" (2023) du Ministère de la Transition Écologique (Data Gouv).
- **Indicateur Loyer :** `loyer_app_m2` (Loyer d'annonce moyen prédictif pour les appartements).
- **Scoring :** `loyer_abordable_scaled` (Logique "Lower is Better" : plus le loyer est bas, meilleur est le score).
- **Intégration UI :** Affichage dans la catégorie "Logement" si l'utilisateur choisit "Location".

## 🚀 Feature [F-09]: Résultats par bassin de vie

### 📝 User Stories

- En tant que travailleur social, je veux pouvoir agréger et visualiser les résultats par "bassin de vie" pour obtenir une perspective régionale plus pertinente et intuitive sur les territoires bien connectés.

### 🔑 Key Features

- Ajouter un bouton de bascule (toggle) dans la barre latérale des résultats pour choisir entre la vue "par commune" et la vue "par bassin de vie".
- Agréger les indicateurs de score existants au niveau du "bassin de vie" pour calculer un nouveau score global.
- Mettre à jour la carte des résultats pour afficher des polygones colorés représentant les "bassins de vie" et leur score.

### 📊 Status

- Completed

## 🚀 Feature [F-10]: Refonte du Filtrage et Score de Population

### 📝 User Stories

- En tant que travailleur social, je veux que la pertinence d'une localité ne soit plus limitée par un seuil de population fixe, mais que la population soit un facteur parmi d'autres dans le score pour des résultats plus nuancés.
- En tant que travailleur social, je souhaite que la recherche par zone géographique (département, région) soit plus précise et retourne toutes les communes réellement présentes dans cette zone.

### 🔑 Key Features

- Suppression du filtre de population minimal : la population est désormais un critère de score dans la catégorie "Inclusion", valorisant les pôles urbains sans exclure les zones rurales.
- Refonte de la logique de filtrage géographique pour utiliser des calculs géospatiaux (centroïdes et intersections) au lieu de simples distances, s'appliquant de manière cohérente aux communes et aux bassins de vie.

### 📊 Status

- Completed (Enhanced with Region/Department/France/Custom scopes in Dec 2025)

## 🚀 Feature [F-11]: Export des résultats en PDF

### 📝 User Stories

- En tant que travailleur social, je veux exporter les résultats de la recherche en PDF pour les partager facilement avec la famille accompagnée, afin de leur fournir un résumé clair et professionnel des recommandations.

### 🔑 Key Features

- Un bouton "Exporter en PDF" sur la page des résultats.
- La première page rappelle les critères de recherche, et la carte de tous les résultats avec le calque 'Top 5' actif.
- Le PDF inclut les 5 meilleurs résultats (bassins de vie ou communes), une page par résultat.
- Pour chaque recommandation, le PDF affiche les points forts et le graphique radar des scores.
- Le PDF intègre le logo "J'accueille".

### 📊 Status

- Completed

## 🚀 Feature [F-12]: Ajout de critères santé et éducation au scoring

### 📝 User Stories

- En tant que travailleur social, je veux que le score d'éducation reflète le **nombre** de niveaux scolaires pertinents présents dans une localité, pour une évaluation plus fine.
- En tant que travailleur social, je veux que le besoin de proximité d'un service de santé soit un critère de score pour que les recommandations tiennent compte de l'accès aux soins.

### 🔑 Key Features

- **Score d'Éducation Basé sur le Nombre :** Un nouveau score est ajouté à la catégorie "Éducation". Il **compte combien** de niveaux scolaires demandés (ex: "Maternelle", "Collège") sont présents dans la commune. Une commune avec 2 types d'écoles sur 2 demandés aura un score plus élevé qu'une commune avec 1 sur 2.
- **Score de Santé Basé sur la Présence :** Un nouveau score est ajouté à la catégorie "Inclusion". Il vaut 100% si une commune possède le service de santé exact sélectionné ("Hôpital", "Maternité", etc.), et 0% sinon.
- **Agrégation par Bassin de Vie :**
  - Pour le score d'**éducation**, le calcul pour le bassin de vie comptera le nombre de types d'écoles uniques présents dans l'ensemble des communes qui le composent.
  - Pour le score de **santé**, le bassin de vie obtiendra 100% si au moins une de ses communes possède le service requis.
- **Affichage dans les Résultats :** La présence de ces services est mentionnée dans les points forts du résultat.

### 📊 Status

- Completed

## 🚀 Feature [F-13]: Nouveau score d'inclusion

### 📝 User Stories

- En tant que travailleur social, je veux évaluer précisément la présence de services institutionnels essentiels pour garantir un soutien de base à la famille accompagnée.
- En tant que travailleur social, je veux mesurer le dynamisme social local via le nombre d'associations pour identifier les communautés où la famille accompagnée pourra facilement s'intégrer.
- En tant que travailleur social, je veux pouvoir faire correspondre les intérêts spécifiques de la famille accompagnée (sport, culture, nature) avec les associations locales disponibles afin de favoriser son épanouissement personnel et son intégration.

### 🔑 Key Features

- Refonte du calcul du score "Inclusion" intégrant cinq composantes : "Socle Administratif" (`inc_services_core_scaled`), "Services Spécifiques" (`inc_services_add_scaled`), "Lien Social" (`inc_asso_core_scaled`), "Affinité" (`inc_asso_add_scaled`) et "Population".
- Le "Socle Administratif" (`inc_services_core_scaled`) est basé sur la présence et la densité des services institutionnels clés issus de `annuaire_inclusion_index.csv`.
  Note: Les catégories services à cocher par défaut sont:

* logement-hebergement: etre-accompagne-pour-se-loger
* acces-aux-droits-et-citoyennete: accompagnement-juridique
* acces-aux-droits-et-citoyennete: demandeurs-dasile-et-naturalisation
* preparer-sa-candidature: realiser-un-cv-et-ou-une-lettre-de-motivation

- Le "Lien Social" (`inc_asso_core_scaled`) mesure la densité d'associations (via le RNA et les codes WALDEC) correspondant à des catégories fixes définies dans `.agent/rna_config.py`. Les données associations sont dans `csv/rna_waldec_20250901_mini_odis.csv`, l'index dans `csv/rna-associations-nomenclature-waldec.json`.
- L'"Affinité" (`inc_asso_add_scaled`) mesure la densité d'associations (via le RNA et les codes WALDEC) correspondant aux centres d'intérêt spécifiques sélectionnés par l'utilisateur.
- "Services Spécifiques" (`inc_services_add_scaled`) mesure la présence d'autres services d'inclusion sélectionnés.
- La "Population" (`inc_population_scaled`) est utilisée comme un critère direct, favorisant les communes de taille importante.
- Une nouvelle interface utilisateur pour l'étape "Inclusion" (étape 7/8 du formulaire) proposera deux champs multiselect : un pour les services institutionnels (avec des pré-sélections déselectionnables) et un pour les activités associatives.
- La métrique utilisée pour les associations est le nombre total d'associations correspondantes pour 1000 habitants, calculé au niveau communal et agrégé en moyenne pondérée par la population pour les Bassins de Vie.
- Les quatre composantes du score d'inclusion sont pondérées de manière égale au sein de la catégorie Inclusion.
- **Note sur l'agrégation** : Pour simplifier la compréhension, tous les scores (y compris Éducation, Santé et Inclusion) sont agrégés au niveau Bassin de Vie par une moyenne pondérée par la population des communes. Cela signifie qu'un service présent dans une petite commune aura moins d'impact sur le score global du bassin qu'un service présent dans une grande commune.

### 📊 Status

- Completed

## 🚀 Feature [F-14]: Support Accueil Petite Enfance

### 📝 User Stories

- En tant que parent de jeunes enfants (< 3 ans), je veux inclure la disponibilité des modes de garde (crèches, assistantes maternelles) dans le score pour identifier les communes où je pourrai faire garder mes enfants.

### 🔑 Key Features

- **Nouveau Critère "Petite Enfance" :** Ajout d'un critère de score spécifique pour l'accueil des jeunes enfants, distinct des structures scolaires classiques.
- **Source de Données :** Utilisation des données de la CAF (Taux de couverture ou nombre de places) pour évaluer l'offre.
- **Intégration UI :** Ajout d'une option "Crèche / Assistante Maternelle" dans le menu déroulant "Education" existant.
- **Scoring :**
  - Le score est calculé sur la base du taux de couverture (places pour 100 enfants) ou de la densité de places.
  - Il est intégré à la catégorie "Education" mais calculé indépendamment des écoles.
- **Agrégation :** Agrégation au niveau Bassin de Vie par moyenne pondérée par la population (cohérent avec les autres scores).

### 📊 Status

- Completed

## 🚀 Feature [F-15]: Profils de Pondération et Affinage des Critères

### 📝 User Stories

- En tant que travailleur social, je veux appliquer en un clic une configuration de poids standard (ex: "Priorité Famille") pour gagner du temps lors de l'entretien.
- En tant que travailleur social, je veux pouvoir indiquer qu'un critère spécifique (ex: "Présence d'un Lycée") est **prioritaire** au sein de sa catégorie, sans avoir à gérer des dizaines de curseurs complexes.

### 🔑 Key Features

- **Sélecteur de Profils (Presets) :**
  - Ajout d'un menu déroulant "Profil de Priorité" dans la barre latérale (au-dessus des sliders).
  - **Options proposées :**
    - _Équilibré_ (Défaut) : Tous les poids à 100.
    - _Famille_ (Stabilité & Education) : Logement (300), Education (300), Reste (100).
    - _Santé_ (Soins & Stabilité) : Santé (300), Logement (200), Reste (100).
    - _Economique_ (Emploi & Formation) : Emploi (300), Mobilité (200), Reste (100).
  - La sélection d'un profil met à jour automatiquement les sliders de catégorie existants.

- **Pondération Intra-Catégorie (Niveau Critère) :**
  - Refonte du moteur de scoring (`compute_category_scores`) pour passer d'une moyenne simple à une **moyenne pondérée** des critères.
  - **UI d'Affinage :** Dans les formulaires (onglets), ajout d'un contrôle "Importance" (ex: Toggle ou Sélecteur "Normal / Important") à côté des critères clés (Niveaux scolaires, Types de santé, Thématiques associatives).
  - **Impact Mathématique :** Un critère marqué "Important" reçoit un coefficient multiplicateur (ex: x3) dans le calcul du score de sa catégorie.

### 📊 Status

- Completed

## 🚀 Feature [F-16]: Optimisation et Refactoring ETL

### 📝 User Stories

- En tant que développeur, je veux que le pipeline ETL soit robuste, rapide et produise des fichiers optimisés pour l'application afin de réduire le temps de chargement et d'améliorer la réactivité.

### 🔑 Key Features

- **Pipeline Unifié :** Intégration de l'étape de déploiement (`deploy_data.py`) directement dans le CLI `etl.py`.
- **Pré-calcul des Scores :** Déplacement de la logique de normalisation (Min-Max Scaling) du temps d'exécution (`scoring.py`) vers le temps de construction (`build.py`) pour les indicateurs statiques.
- **Optimisation des Fichiers :** Suppression des colonnes inutilisées dans `odis_communes.parquet` et utilisation de formats verticaux (`bmo_vertical.parquet`) pour les données volumineuses (métiers).
- **Gestion des Erreurs Topologiques :** Correction robuste des géométries invalides lors de l'agrégation des Bassins de Vie.

### 📊 Status

- Completed

## 🚀 Feature [F-17]: Intégration Données Odace (Gares)

### 📝 User Stories

- En tant que travailleur social, je veux savoir si une commune dispose d'une gare ferroviaire pour évaluer la mobilité pendulaire et l'accès aux grands pôles urbains pour la famille accompagnée.

### 🔑 Key Features

- **Intégration API Odace :** Connexion à l'API Odace pour récupérer les données référentielles (`dim_commune` et `dim_gare`).
- **Indicateur Gare :** Création d'un indicateur binaire "Présence d'une gare" (`mob_gare_scaled`) pour chaque commune.
- **Scoring Mobilité :** Intégration de ce nouvel indicateur dans la catégorie "Mobilité", permettant de valoriser les communes desservies par le train.
- **Affichage :** Mention de la présence d'une gare dans les points forts de la localité.

### 📊 Status

- Completed

## 🚀 Feature [F-18]: Indicateurs de Déclin de Population

### 📝 User Stories

- En tant que travailleur social, je veux identifier les communes qui perdent des habitants, en particulier des jeunes, car elles pourraient être plus incitées à accueillir de nouvelles familles pour maintenir leurs écoles et services.

### 🔑 Key Features

- **Nouvelle Source de Données :** "Population Détails" (Insee) avec ventilation par âge (2011, 2016, 2022).
- **Indicateur Déclin Jeune :** `youth_decline_scaled`. Score élevé si la population des moins de 15 ans diminue. Intégré à la catégorie "Éducation".
- **Indicateur Déclin Actifs :** `workclass_decline_scaled`. Score élevé si la population des 25-54 ans diminue. Intégré à la catégorie "Emploi".
- **Scoring :** Plus le déclin est fort, plus le score est élevé (opportunité d'accueil).
- **Affichage :** Point fort "Besoin de familles" ou "Besoin d'actifs".

### 📊 Status

- Completed (Implemented as `youth_decline_scaled` and `workclass_decline_scaled`)

## 🚀 Feature [F-19]: Odis AI Agent (Assistant Virtuel)

### 📝 User Stories

- En tant que travailleur social, je veux interagir en langage naturel avec l'outil pour décrire la situation de la famille (récit de vie) et obtenir des recommandations sans avoir à remplir manuellement chaque champ du formulaire.
- En tant que travailleur social, je veux que l'assistant comprenne le jargon métier (codes ROME, besoins spécifiques) et trouve automatiquement les correspondances dans les référentiels.

### 🔑 Key Features

- **Moteur LLM :** Intégration de **Gemini 2.5-flash-lite** pour une compréhension contextuelle avancée et une capacité de "Tool Use" robuste.
- **Architecture MCP (Model Context Protocol) :**
  - Serveur MCP (`app/mcp_server.py`) exposant les données ODIS (Référentiels, Communes, Scoring) comme des outils standardisés.
  - Client Gemini (`app/gemini_client.py`) consommant ces outils.
- **Outils Intelligents :**
  - `search_commune(query)`: Trouve le code INSEE exact d'une ville.
  - `search_referentiels(query, domain)`: Recherche sémantique robuste in Referentials (Inclusion, Formations, ROME). [DEPRECATED] FAP search is no longer active.

  - `compute_top_cities(weight_profile, criterias)`: Lance le moteur de scoring ODIS avec des critères structurés.

- **Robustesse & Typage :** Utilisation de modèles **Pydantic** (`app/models.py`) pour garantir que l'IA génère des paramètres de recherche valides (schéma strict).
- **Prompt Engineering :** "System Instruction" externalisée (`AGENT_PROMPT.md`) définissant un persona "Assistant Expert" avec un protocole d'entretien strict en 4 phases (Ancrage, Famille, Besoins, Validation).

### 📊 Status

- Completed (Migrated to Multi-Agent Orchestration in Jan 2026)

### ⚙️ Multi-Agent Architecture

L'assistant est désormais composé de plusieurs agents spécialisés pilotés par un `Orchestrator` :

- **Interviewer** : Phase de découverte et collecte de critères.
- **Scorer** : Calcul et explication du Top villes.
- **Scout** : Recherche Google Maps/Places pour la "décoration" des résultats (infrastructures, trajets).
- **Web** : Expert News et Grounding Web via Google Search pour le contexte social.
- **JobHunter** : Recherche d'offres d'emploi ciblées via France Travail et consultation des détails d'une offre spécifique.

## 🚀 Feature [F-20]: Détails Territoire (Learn More)

### 📝 User Stories

- En tant qu'utilisateur (via UI classique ou Agent IA), je veux accéder à une fiche détaillée agrégeant toutes les informations disponibles sur un territoire (emploi, éducation, santé, inclusion, vie associative) pour approfondir ma compréhension au-delà du simple score.

### 🔑 Key Features

- **Nouveau Outil MCP :** `get_city_details(codgeo)` qui agrège les données de toutes les sources disponibles (ODIS, Annuaire Education/Santé/Inclusion, Associations, BMO).
- **Structure des Données :** Retourne un objet JSON structuré avec :
  - **Identité :** Nom, Code, Population, Bassin de Vie.
  - **Scores :** Détail des scores bruts et normalisés.
  - **Emploi :** Top secteurs recruteurs ([DEPRECATED] BMO, replaced by Live Jobs).

  - **Education :** Nombre d'établissements par niveau.
  - **Santé :** Dénombrement des services clés.
  - **Inclusion :** Liste des services disponibles.
  - **Associations :** Thématiques principales et volumétrie.

- **Intégration UI :** Bouton "En savoir plus" dans la liste des résultats ouvrant une vue détaillée.
- **Intégration Agent :** Le chatbot peut appeler cet outil pour répondre à des questions spécifiques comme "Quelles sont les associations sportives à X ?" ou "Y a-t-il un hôpital à Y ?".

## 🚀 Feature [F-21]: Sélecteur de Modèle (Chatbot)

### 📝 User Story

En tant que travailleur social, je veux pouvoir choisir entre différents modèles de langage (Gemini 2.5 Flash Lite ou Gemini 3.0 Flash) pour adapter la performance et le coût de l'assistant à mes besoins.

### 🔑 Key Features

- **Interface de Sélection :** Ajout d'un menu déroulant (selectbox) dans la barre latérale de la page chatbot.
- **Options de Modèles :**
  - Gemini 2.5 Flash Lite (Défaut)
  - Gemini 3.0 Flash
- **Persistance :** Le choix du modèle est conservé pendant la session.

### 📊 Status

- Completed (Integrated Dec 2025, Enhanced Jan 2026)

## 🚀 Feature [F-23]: Cartographie Administrative (DARES FAP-ROME)

### 📝 User Story

En tant qu'Agent Job Hunter, je veux utiliser une table de passage officielle pour traduire les profils ODIS (codes FAP) en codes métiers France Travail (codes ROME), afin de garantir une recherche d'emploi administrativement exacte et sans erreurs de "fuzzy matching".

### 🔑 Key Features

- **Pipeline ETL DARES :** Intégration de la table de passage officielle DARES (`Dares_Table_passage_ROME_Qualif_to_FAP2021_pour_programme.csv`) dans le processus de build.
- **Support Multi-Profils :** Le `JobHunterAgent` traite désormais séparément et exhaustivement chaque adulte du foyer (Adulte 1, Adulte 2).
- **Exactitude ROME :** Traduction directe FAP -> ROME via l'outil `get_rome_for_fap`, évitant les confusions de libellés.
- **Recherche V2 :** Utilisation du paramètre `codeRome` de l'API France Travail V2 pour des résultats plus ciblés.

### 📊 Status

- Completed (Jan 2026)

## 🚀 Feature [F-25]: Feedback Visuel de l'Agent (Toasts Humorisés)

### 📝 User Story

En tant que travailleur social, je veux être informé des actions en cours des différents agents (Scorer, Scout, Job Hunter, etc.) via des messages courts et humoristiques ("toasts"), afin de patienter agréablement pendant les temps de réflexion de l'IA.

### 🔑 Key Features

- **Notifications Dynamiques :** Utilisation de `st.toast` pour afficher les étapes clés de l'orchestration multi-agents.
- **Messages Humorisés :** Intégration de messages variés et drôles pour chaque expert (ex: "Interrogatoire des pigeons locaux" pour l'expert terrain).
- **Visibilité du Travail de l'Ombre :** Permet de comprendre quel agent est actif à quel moment dans la cascade de recherche.

### 📊 Status

- In Progress (Jan 2026)

## 🚀 Feature [F-26]: Référentiel des Associations Spécialisées Réfugiés

### 📝 User Story

- En tant que travailleur social, je veux pouvoir identifier et visualiser les associations spécialisées dans l'accueil des réfugiés (asile, migration, nouveaux arrivants) pour orienter au mieux les familles et valoriser les territoires qui disposent de ce soutien spécifique.

### 🔑 Key Features

- **DataSet Spécifique :** Création d'un dataset "Refugee Asso" filtré à partir du RNA (Codes WALDEC '003', '019', '020', '014' et mots-clés sémantiques 'asil', 'refug', 'migra', 'nouveaux arrivants').
- **Scoring Inclusion :** Intégration d'un nouveau score `inc_asso_refug_scaled` calculé par la densité d'associations spécialisées pour 1000 habitants.
- **UI WebForm (Results) :**
  - Affichage de ce critère dans la catégorie "Inclusion".
  - Dialogue "En savoir plus" enrichi avec la liste exhaustive des associations du territoire (nom, objet tronqué, lien vers assoce.fr).
- **Agent SCOUT (Chatbot) :** Capacité pour l'agent de recherche terrain d'interroger directement cette base pour identifier les acteurs locaux les plus pertinents.

### 📊 Status

- In Progress (Jan 2026)

## 🚀 Feature [F-27]: Unified Markdown Search Logging

### 📝 User Story

- En tant que développeur, je veux que les recherches effectuées via l'interface classique ET via le chatbot soient logguées dans un format Markdown lisible, afin de faciliter le débogage et l'analyse des recommandations de l'IA.

### 🔑 Key Features

- **Déplacement de la Logique :** La génération du log Markdown est déplacée de `app/pages/3_Resultats.py` vers le `ScoringEngine` (`app/core/scoring.py`).
- **Préfixes de Fichiers :** Utilisation de préfixes distincts (`classic_` vs `chatbot_`) pour identifier la source de la recherche dans le nom du fichier log.
- **Accessibilité :** Les logs sont stockés dans `.logs/` à la racine de `app/` pour une centralisation aisée.

### 📊 Status

- In Progress (Jan 2026)

## 🚀 Feature [F-28]: Live Jobs Integration (BMO Alternative)

### 📝 User Story

- En tant que travailleur social, je veux des données sur l'emploi mises à jour plus fréquemment que le BMO annuel, afin de refléter la réalité du marché du travail local en temps réel.
- En tant que développeur, je veux agréger les offres d'emploi live de France Travail pour créer des indicateurs de tension et de volume par commune plutôt que de dépendre uniquement des tendances déclaratives annuelles par bassin d'emploi.

### 🔑 Key Features

- **Automated ETL :** Pipeline robuste (`pipeline/ft_live_ingest.py`) capable de collecter ~500k offres en < 10 min.
- **Agrégation Multi-Niveaux :** Regroupement par Commune (INSEE), Code ROME et Domaine (3 digits).
- **Indicateurs Enrichis :** Calcul du nombre d'offres ET du nombre total de postes (`nombrePostes`).
- **Scoring Hybride :** Intégration dans le moteur de scoring pour utiliser ces données "live" en complément ou remplacement du BMO.
- **Décoration Temps Réel :** Utilisation de cette base pour guider les agents (JobHunter) vers les gisements d'emploi réels.

### 📊 Status

- In Progress (Jan 2026) - Branch `feat-ft-live-jobs`

## 🚀 Feature [F-29]: AI Agent Token Usage Logging

### 📝 User Story

- En tant que développeur, je veux voir l'utilisation des jetons (tokens) par modèle dans la console, afin de suivre la consommation et les coûts des différents agents IA.

### 🔑 Key Features

- **Logging INFO :** Restauration des logs de consommation de tokens (`in_tokens`, `out_tokens`) au niveau `INFO` pour qu'ils soient visibles par défaut dans la console.
- **Détail par Modèle :** Affichage explicite du `model_id` associé à chaque consommation.

### 📊 Status

- In Progress (Jan 2026)

## 🚀 Feature [F-32]: Mini Annuaire Associatif ODIS

### 📝 User Story

- En tant que travailleur social, je veux accéder à un annuaire simplifié des associations locales (nom, objet) directement dans l'application et via le chatbot, afin de proposer des solutions d'accompagnement concrètes à la famille.
- En tant que développeur, je veux une version "lite" du RNA intégrée au pipeline pour ne pas alourdir l'application tout en offrant des données qualitatives sur le tissu associatif.

### 🔑 Key Features

- **Pipeline "Lite" :** Filtrage intelligent du RNA (Répertoire National des Associations) pour ne garder que les associations actives et pertinentes pour l'intégration (Codes 003, 018, 019, 020, 032).
- **DataSet ODIS :** Création d'un fichier `odis_asso_mini.parquet` contenant l'ID, le code INSEE, le code Waldec, le titre court et l'objet (tronqué).
- **Micro-Annuaire MCP :** Nouvel outil `search_odis_associations` permettant aux agents (Scout notamment) de lister les acteurs locaux par thématique.
- **Décoration "En savoir plus" :** Intégration de cet annuaire dans les fiches détaillées des territoires.

### 📊 Status

- **Deprecated** (Superseeded by [F-38] RAG Search in Feb 2026)

## 🚀 Feature [F-34]: Orchestrator Prompt Refactoring

### 📝 User Story

- En tant que développeur, je veux que les prompts de synthèse de l'orchestrateur soient faciles à tuner en les plaçant en début de fichier et en utilisant des injections par remplacement de chaînes plutôt que des f-strings complexes.

### 🔑 Key Features

- **Centralisation :** Déplacement de `SYNTH_PROMPT` au début du fichier `app/agents/orchestrator.py`.
- **Templating :** Passage d'un format f-string à un format template (string statique) avec injection via `.replace()`.

### 📊 Status

- In Progress (Jan 2026)

## 🚀 Feature [F-35]: Deployment Preparation & Snapshot Update

### 📝 User Story

- En tant que développeur, je veux m'assurer que tous les tests passent et que les snapshots de référence sont à jour avant de procéder à un nouveau déploiement, en tenant compte des modifications architecturales récentes.

### 🔑 Key Features

- Exécution de la suite de tests complète (hors `test_graph_verification.py`).
- Mise à jour des snapshots de test pour refléter la nouvelle architecture.
- Vérification de l'intégrité des données après les changements structurels.

### 📊 Status

- In Progress (Jan 2026)

## 🚀 Feature [F-37]: Advanced ODIS Graph Architecture (v3)

### 📝 User Story

- En tant que développeur, je veux une architecture de graphe LangGraph plus robuste, déterministe et efficace pour éliminer la redondance des nœuds, garantir la fraîcheur des données via un système de hachage des critères, et centraliser le contrôle de l'exécution avec un pattern Dispatcher/Joiner.

### 🔑 Key Features

- **Versioning des Critères (Hashing) :** Implémentation d'un hachage MD5 des `search_criteria` pour détecter tout changement et invalider les données d'experts obsolètes.
- **Gestionnaire d'Artéfacts (`commune_artifacts`) :** Stockage structuré des résultats des experts par commune et par hash de critères, évitant les recalculs inutiles et les incohérences.
- **Pattern Dispatcher/Joiner :** Centralisation de la logique de routage parallèle et de convergence, supprimant le besoin de nœuds "\_solo" redondants.
- **Contrôle d'Exécution Explicite :** Utilisation de flags `pending_experts` et `execution_mode` (e.g., `full_analysis`, `specific_ask`) dans l'état global pour un pilotage précis du graphe.
- **Nettoyage Architectural :** Suppression des nœuds redondants (`scout_solo`, `web_solo`, etc.) au profit d'une logique unifiée.

### 📊 Status

- Planned (Jan 2026)

## 🚀 Feature [F-38]: RNA RAG Association Lookup

### 📝 User Story

- En tant que travailleur social, je veux effectuer une recherche sémantique (RAG) dans l'intégralité du Répertoire National des Associations (RNA) pour trouver les structures les plus pertinentes par rapport au projet de vie de la personne, même si les termes exacts ne correspondent pas aux catégories prédéfinies.

### 🔑 Key Features

- **Semantic Search & Vector Lookups (RAG):**
  - **Use-case 1 (Deep Analysis):** In-app vector similarity (dot product) for a specific commune/bdv, filtered by `is_inclusion_relevant`. Fetch 128-dim embeddings from BigQuery on demand.
  - **Use-case 2 (Scoring Aggregation):** BigQuery-side aggregation by `primary_category` for inclusion-relevant associations to replace/augment WALDEC-based scoring.
  - **Use-case 3 (General Discovery):** Semantic lookup for a specific `codgeo` for any topic (e.g., 'football') to decorate results.
- **Data Source:** BigQuery table `rna_rag.rna_rag_mini` (Project: `odis-stream2`, Region: `europe-west1`).
- **Embeddings:** Vertex AI `text-multilingual-embedding-002` (v128).

### 📊 Status

- Completed (Feb 2026)
