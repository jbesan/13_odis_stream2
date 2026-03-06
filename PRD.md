# PRD (Product Requirements Document) - OD&IS "Stream 2"

**Version :** 1.5
**Projet :** Prototype de Recherche Inversée (Aide à la Localisation)
**Auteur :** D4G: OD&IS (revu le 26/02/2026)

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
- [ ] [F-43] Upgrade to Gemini 3.1 Flash-Lite for all agents.
- [ ] [F-44] Standardize all agents to return Pydantic structured outputs instead of raw strings.
- [ ] AI Bot: ajouter le contact CCAS (passer dans get_city_details? )

## 5. Features

_(Most features omitted for brevity. Appending F-42)_

## 🚀 Feature [F-42]: Refinements Hébergement (Court Terme)

### 📝 User Story

- En tant que travailleur social, je veux des options d'hébergement court-terme plus réalistes et alignées sur les dispositifs existants (IML/Solibail, pensions de famille, foyers spécifiques, hébergement citoyen).
- Je veux pouvoir sélectionner plusieurs options d'hébergement simultanément pour élargir le champ des recherches.

### 🔑 Key Features

- **Transformation UI :** Passage de "radio" (choix unique) à "checkboxes" (choix multiples) pour les différentes options d'hébergement.
- **Location avec Intermédiation :** Remplace l'option "Location" (dans Hébergement). Recherche via RNA des associations proposant des services IML/Solibail ("intermédiation locative", "solibail"). Active le score de vacance et de loyer moyen (`log_loyer_moyen_appt_all_scaled`).
- **Centres d'Hébergement :** Remplace l'option "Foyers". Intègre les sources BPE pour D703 (CHRS) et D704 (CPH) et évalue les scores basé sur `sum(CAPACITE)` d'accueil par habitant de la commune.
- **Foyers & Pensions de Famille :** Utilise la classification BPE D710 et le nom ("fjt", "pension", "migrant") pour le décompte de places dans les Foyers de Jeunes Travailleurs, Pensions de famille et Foyers de Travailleurs Migrants.
- **Chez l'habitant :** Recherche enrichie via RNAG pour "hébergement citoyen". Intégration de la base "J'Accueille" pour identifier les bassins de vie disposant d'accueillants actifs, valorisé via le score binaire `heb_jaccueille_score`. **Note :** Les données d'accueillants sont considérées comme non-publiques et sont stockées sur BigQuery (`jaccueille_accueillants_bdv`), puis récupérées dynamiquement par l'application pour plus de sécurité.

### 📊 Status

- Planned (Feb 2026)
