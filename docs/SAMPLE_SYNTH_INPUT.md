**Rôle** : Tu es le Synthétiseur ODIS. Ta mission est de fusionner les retours des experts pour donner une réponse unique, fluide et ultra-convaincante au travailleur social.

# CONTEXTE RÉSUMÉ :

### 📋 RÉSUMÉ DU DOSSIER (BRIEFING)

**PROJET DE VIE** :

- Localisation : Nantes (44109)
- Composition : 2 adulte(s), 1 enfant(s)
- Priorité : Famille
- Zone de recherche : region
- Métiers (ROME) : Mécanicien / Mécanicienne automobile (I1604), Agent / Agente de propreté de locaux (K2205), Secrétaire (M1607)
- Formations : Secrétariat, bureautique (324)
- Associations : Castans (11075), CULTURE, PRATIQUES D'ACTIVITÉS ARTISTIQUES, PRATIQUES CULTURELLES (6000)
- Besoins inclusion : Maîtriser le français (lecture-ecriture-calcul--maitriser-le-francais)

**MÉMOIRE DE L'ÉCHANGE** :
**Synthèse du Contexte : Famille Amir & Nour**

- **Bénéficiaires** : Amir, Nour et Ali (7 ans, scolarisé en élémentaire).
- **Localisation cible** : Région Occitanie.
- **Projet Professionnel** : Mécanique automobile (Amir) ; Entretien et secrétariat/bureautique (Nour).
- **Besoins spécifiques** : Apprentissage du français (FLE), club de football (Ali), réseaux associatifs culturels syriens.
- **Logement** : Recherche mixte (parc social et parc privé).
- **Villes identifiées (Top 5 ODIS)** :
  1.  **Carcassonne** (70.73%) : Priorité éducation et mobilité (gare).
  2.  **Decazeville** (70.55%) : Priorité logement social et intégration associative.
  3.  **Perpignan** (68.59%) : Dynamisme emploi et services administratifs.
  4.  **Castres** (68.05%) : Équilibre emploi/coût du logement et formation.
  5.  **Rodez** (66.56%) : Lien social et offre éducative.

Ville Analysée : Decazeville

# DONNÉES CHIFFRÉES (SCORER ODIS) :

```json
{
  \"identity\": {
    \"codgeo\": \"12089\",
    \"nom\": \"Decazeville\",
    \"population\": 5911,
    \"bassin_de_vie\": \"Decazeville\",
    \"score_global\": 0.7055383035808644
  },
  \"scores\": {
    \"logement\": [
      {
        \"label\": \"Taux Vacance Structurelle (> 2 ans)\",
        \"score_id\": \"log_vac_scaled\",
        \"valeur_kpi\": \"11.7\",
        \"score_normalise\": 1.0,
        \"unit\": \"Part des logements vacants depuis plus de 2 ans sur le parc total\"
      },
      {
        \"label\": \"Taux de Logements Sociaux Inoccupés (Vacants ou Vides)\",
        \"score_id\": \"log_soc_inoc_scaled\",
        \"valeur_kpi\": \"11.1\",
        \"score_normalise\": 1.0,
        \"unit\": \"Nombre de logements sociaux innocupés (vacants ou vide) sur le parc de logements sociaux\"
      },
      {
        \"label\": \"Taux d'Occupation (Sous-occupation)\",
        \"score_id\": \"log_occup_scaled\",
        \"valeur_kpi\": \"79.9\",
        \"unit\": \"Part des logements sous-occupés (Source: INSEE)\"
      },
      {
        \"label\": \"Loyer Abordable\",
        \"score_id\": \"loyer_abordable_scaled\",
        \"valeur_kpi\": \"8.01\",
        \"score_normalise\": 0.863547146320343,
        \"unit\": \"Loyer moyen d'annonce par m² pour les appartements (Source: Carte des Loyers 2023)\"
      }
    ],
    \"inclusion\": [
      {
        \"label\": \"Couleur Politique de la commmue\",
        \"score_id\": \"inc_pol_scaled\",
        \"valeur_kpi\": \"0.50\",
        \"score_normalise\": 0.5,
        \"unit\": \"Affiliation à un parti politique en faveur de l'accueil\"
      },
      {
        \"label\": \"Population de la commune\",
        \"score_id\": \"inc_population_scaled\",
        \"valeur_kpi\": \"5911\",
        \"score_normalise\": 0.2046249955892563,
        \"unit\": \"Population totale de la commune\"
      },
      {
        \"label\": \"Socle Administratif\",
        \"score_id\": \"inc_services_core_scaled\",
        \"valeur_kpi\": \"3\",
        \"score_normalise\": 1.0,
        \"unit\": \"Présence des services administratifs sélectionnés\"
      },
      {
        \"label\": \"Lien Social\",
        \"score_id\": \"inc_asso_core_scaled\",
        \"valeur_kpi\": \"44.32\",
        \"score_normalise\": 0.31543439626693726,
        \"unit\": \"Densité d'associations favorisant le lien social\"
      },
      {
        \"label\": \"Accompagnement Réfugiés\",
        \"score_id\": \"inc_asso_refug_scaled\",
        \"valeur_kpi\": \"0.34\",
        \"score_normalise\": 0.5841984152793884,
        \"unit\": \"Densité d'associations spécialisées réfugiés (Source RNA)\"
      },
      {
        \"label\": \"Affinités\",
        \"score_id\": \"inc_asso_add_scaled\",
        \"valeur_kpi\": \"0\",
        \"score_normalise\": 0.0,
        \"unit\": \"Densité d'associations correspondant aux affinités sélectionnées\"
      },
      {
        \"label\": \"Services Spécifiques\",
        \"score_id\": \"inc_services_add_scaled\",
        \"valeur_kpi\": \"N/A\",
        \"score_normalise\": 1.0,
        \"unit\": \"Présence des services spécifiques sélectionnés\"
      }
    ],
    \"emploi\": [
      {
        \"label\": \"Match besoins et Centres de formation\",
        \"score_id\": \"form_match_adult1_scaled\",
        \"valeur_kpi\": \"0\",
        \"score_normalise\": 0.0,
        \"unit\": \"Nombre centres de formations dans la commune\"
      },
      {
        \"label\": \"Déclin Démographique Actif\",
        \"score_id\": \"workclass_decline_scaled\",
        \"valeur_kpi\": \"-0.04\",
        \"score_normalise\": 0.6978892087936401,
        \"unit\": \"Evolution de la population des 25-54 ans entre 2016 et 2022\"
      },
      {
        \"label\": \"Opportunités Emploi (Ville)\",
        \"score_id\": \"met_live_commune_scaled\",
        \"valeur_kpi\": \"3\",
        \"score_normalise\": 0.3,
        \"unit\": \"Nombre d'offres d'emploi en direct pour les codes ROME recherchés (Source: France Travail)\"
      },
      {
        \"label\": \"Opportunités Emploi (Bassin de Vie)\",
        \"score_id\": \"met_live_bdv_scaled\",
        \"valeur_kpi\": \"6\",
        \"score_normalise\": 0.12,
        \"unit\": \"Nombre d'offres d'emploi dans le bassin de vie pour les codes ROME recherchés (Source: France Travail)\"
      },
      {
        \"label\": \"Tension de recrutement (Live)\",
        \"score_id\": \"met_live_tension_scaled\",
        \"valeur_kpi\": \"0\",
        \"score_normalise\": 0.0,
        \"unit\": \"Nombre d'offres signalées avec 'Manque de candidats' (Source: France Travail)\"
      }
    ],
    \"mobilité\": [
      {
        \"label\": \"Même agglomération que la localisation actuelle\",
        \"score_id\": \"mob_epci_scaled\",
        \"valeur_kpi\": \"N/A\",
        \"score_normalise\": 0.0,
        \"unit\": \"\"
      },
      {
        \"label\": \"Gare ferroviaire\",
        \"score_id\": \"mob_gare_scaled\",
        \"valeur_kpi\": \"0\",
        \"score_normalise\": 0.0,
        \"unit\": \"Présence d'une gare ferroviaire dans la commune (Source: Odace)\"
      }
    ],
    \"education\": [
      {
        \"label\": \"Accueil Petite Enfance\",
        \"score_id\": \"edu_petite_enfance_scaled\",
        \"valeur_kpi\": \"0\",
        \"unit\": \"Nombre de places (collectif + individuel) pour 100 enfants de moins de 3 ans (Source: CAF)\"
      },
      {
        \"label\": \"Ecole Maternelle\",
        \"score_id\": \"edu_maternelle_scaled\",
        \"valeur_kpi\": \"1\",
        \"unit\": \"Présence d'une école maternelle\"
      },
      {
        \"label\": \"Ecole Elémentaire\",
        \"score_id\": \"edu_elementaire_scaled\",
        \"valeur_kpi\": \"4\",
        \"score_normalise\": 1.0,
        \"unit\": \"Présence d'une école élémentaire\"
      },
      {
        \"label\": \"Collège\",
        \"score_id\": \"edu_college_scaled\",
        \"valeur_kpi\": \"2\",
        \"unit\": \"Présence d'un collège\"
      },
      {
        \"label\": \"Lycée\",
        \"score_id\": \"edu_lycee_scaled\",
        \"valeur_kpi\": \"0\",
        \"unit\": \"Présence d'un lycée\"
      },
      {
        \"label\": \"Nombre de classes à risque de fermeture\",
        \"score_id\": \"edu_classes_ferm_scaled\",
        \"valeur_kpi\": \"5\",
        \"score_normalise\": 0.7267441749572754,
        \"unit\": \"Nombre d'écoles avec des classes à faible effectif (< 20 élèves)\"
      },
      {
        \"label\": \"Déclin Démographique Jeune\",
        \"score_id\": \"youth_decline_scaled\",
        \"valeur_kpi\": \"-0.09\",
        \"score_normalise\": 0.9387186765670776,
        \"unit\": \"Evolution de la population des moins de 15 ans entre 2016 et 2022\"
      }
    ],
    \"santé\": []
  },
  \"education\": {
    \"counts\": {
      \"maternelle\": 1,
      \"elementaire\": 4,
      \"college\": 2,
      \"lycee\": 0
    },
    \"etablissements\": {}
  },
  \"emploi\": {
    \"top_metiers\": [
      \"Technicien / Technicienne méthodes (7 postes)\",
      \"Conducteur / Conductrice d'engins de chantier (6 postes)\",
      \"Conseiller / Conseillère immobilier (5 postes)\",
      \"Vendeur / Vendeuse en épicerie (4 postes)\",
      \"Opérateur / Opératrice sur machines automatisées en production électrique (4 postes)\",
      \"Ingénieur / Ingénieure d'affaires en industrie (3 postes)\",
      \"Agent / Agente d'entretien du bâtiment (3 postes)\",
      \"Facteur / Factrice (3 postes)\",
      \"Expert-comptable / Experte-comptable (3 postes)\",
      \"Employé familial / Employée familiale (3 postes)\"
    ],
    \"formations\": [
      \"999\",
      \"Commerce, vente\",
      \"Développement des capacités d'orientation, d'insertion ou de réinsertion sociales et professionnelles\",
      \"Enseignement, formation\",
      \"Informatique, traitement de l'information, réseaux de transmission des données\",
      \"Sécurité des biens et des personnes, police, surveillance (y compris hygiène et sécurité)\"
    ]
  }
}
```

# Expert Terrain (Scout) :

Decazeville dispose d'associations pour l'aide aux réfugiés : \"LA BOUSSOLE COLLECTIF D'ENTRAIDE AUX EXILÉS DU BASSIN\" et \"ASSOCIATION UKRAINIENNE DZYGA\".

Concernant les activités sportives, plusieurs clubs de football sont présents : \"Jeunesse Sport Bassin Aveyron\", \"Sporting-Club Decazeville\" et \"Sporting Club Decazevillois\".

Pour la formation, le \"Campus des Métiers et des Qualifications d'Excellence Industrie du futur\" et le \"GRETA Midi-Pyrénées Nord\" proposent des formations dans les métiers de la mécanique et de la bureautique. La \"CMA Formation Rodez-Onet\" est également une option.

Il n'y a pas d'association culturelle syrienne ou d'épicerie syrienne spécifiquement répertoriée à Decazeville.

Le trajet vers la préfecture de Rodez prend environ 1h54 en transports en commun, avec un changement de bus à Viviez et un trajet en train jusqu'à Rodez, suivi d'un dernier trajet en bus.

Souhaitez-vous que j'approfondisse la recherche sur une autre commune ?

# Expert News (Web) :

Voici les informations actualisées et contextuelles pour la ville de **Decazeville**, afin de compléter l'analyse de votre projet de réinstallation.

### 1. Actualité et Dynamisme Local

Decazeville est actuellement dans une phase de **transformation urbaine et sociale**.

- **Éducation et Inclusion** : L'école Jean-Macé vient d'inaugurer le projet « L'Orchestre à l'école », un dispositif qui permet aux enfants (du CP au CM2) de pratiquer gratuitement un instrument. C'est une opportunité d'intégration culturelle précieuse pour le jeune Ali (7 ans).
- **Revitalisation** : La ville mise sur son patrimoine industriel pour se réinventer, notamment via le label \"Territoire d'Industrie\", ce qui maintient une activité économique stable dans les secteurs de la maintenance et des services.

### 2. Climat Social et Accueil des Réfugiés

La ville possède une **longue tradition d'accueil** liée à son passé minier et industriel.

- **Structures dédiées** : Decazeville héberge un **CPH (Centre Provisoire d'Hébergement)** et un **CADA**, gérés par des acteurs comme France Horizon. Le climat social est marqué par une solidarité active.
- **Soutien associatif** : Le collectif **\"La Boussole\"** (Collectif d'Entraide aux Exilés du Bassin) est l'acteur central pour l'accompagnement des familles arrivantes. Il travaille en lien avec le Centre Social et la Croix-Rouge pour faciliter l'insertion.

### 3. Culture, Loisirs et Intérêts de la Famille

- **Street Art (Festival Mur/Murs)** : Pour Nour et Amir, intéressés par les pratiques artistiques, Decazeville est devenue une référence nationale du Street Art. Le parcours \"Mur/Murs\" transforme la ville en musée à ciel ouvert avec plus de 30 fresques monumentales.
- **Sport pour Ali** : Pour le football, il faudra se tourner vers la **Jeunesse Sportive Bassin Aveyron (JSBA)**, qui gère l'école de foot locale (à ne pas confondre avec le Sporting Club Decazevillois, qui est le club de rugby historique).
- **Réseaux Syriens** : Bien qu'il n'existe pas d'association spécifiquement syrienne, le brassage multiculturel de la ville facilite les rencontres via les ateliers du Centre Social.

### 4. Transports et Mobilité

Le réseau **TUB (Transports Urbains du Bassin)** dessert efficacement la zone :

- **Lignes A et B** : Elles relient Decazeville aux communes voisines (Aubin, Cransac, Viviez, Firmi). Le bus passe environ toutes les heures.
- **Gare de Viviez-Decazeville** : Située à proximité immédiate, elle permet de rejoindre Rodez, Figeac et Toulouse via le réseau TER Occitanie.

### 5. Inclusion et Apprentissage du Français (FLE)

Des solutions concrètes existent pour Amir et Nour :

- **Ateliers Sociolinguistiques** : Le **Centre Social de Decazeville Communauté** propose des cours de français gratuits tous les **mardis et jeudis de 9h30 à 11h00** (4 Place Cabrol).
- **Formation Professionnelle** : Des organismes comme **Village 12** ou le **GRETA** proposent des parcours de FLE à visée professionnelle, adaptés aux métiers de la mécanique ou du secrétariat.

**Synthèse pour la famille** : Decazeville offre un cadre rassurant et très structuré pour l'inclusion. La présence de dispositifs d'apprentissage du français en centre-ville et le dynamisme culturel (Street Art, Orchestre à l'école) correspondent parfaitement aux priorités de la famille Amir & Nour.

# Expert Emploi (Job Hunter) :

Voici les offres d'emploi correspondant aux métiers recherchés à Decazeville :

**Mécanicien / Mécanicienne automobile (ROME I1604)**
Il y a 4 offres d'emploi pour ce métier. Voici les 3 plus pertinentes :

- **Technicien / Technicienne service rapide en automobile (H/F)** (ID : 202JDNB) : Ce poste en CDI chez GROUPE CHALLENGE PNEUS correspond à une expérience de 3 ans et propose une rémunération annuelle entre 24000 et 25000 euros.
- **Mécanicien Automobile H/F** (ID : 7564389) : Offre en CDI chez Profil Plus, acceptant les débutants, ce qui peut être une bonne opportunité pour commencer dans le métier.
- **Technicien Service Rapide H/F** (ID : 7564388) : Également une offre en CDI chez Profil Plus, acceptant les débutants, similaire à la précédente.

**Agent / Agente de propreté de locaux (ROME K2205)**
Il y a 8 offres d'emploi pour ce métier. Voici les 3 plus pertinentes :

- **Agent / Agente d'entretien/propreté de locaux (H/F)** (ID : 202QXRR) : Ce poste en CDD de 5 jours chez GUY SCHLOSSER NETTOYAGE est à temps partiel (15h/semaine) et propose une rémunération horaire de 12.38 euros. Il s'agit d'un remplacement.
- **Agent de propreté H/F - ABER PROPRETE AGENCE MENDE** (ID : 7874507) : Offre en CDD de 10 jours, à temps partiel (14h/semaine), pour un poste basé à Decazeville même.
- **Agent / Agente de propreté de locaux (H/F)** (ID : 202NGZN) : Ce poste en CDI est à temps partiel (3h/semaine) et concerne le secteur de Montbazens, avec une rémunération horaire de 12.48 euros.

**Secrétaire (ROME M1607)**
Il n'y a pas d'offres d'emploi correspondant à ce métier à Decazeville pour le moment.

Souhaitez-vous obtenir plus de détails sur l'une de ces offres ?

# Instructions :

1. Fais une synthèse argumentée des éléments ci-dessus qui soit factuelle et convaincante en FRANÇAIS.
2. Utilise les **DONNÉES CHIFFRÉES** (scores, points forts ODIS) pour asseoir ta démonstration. Lorsque pertinent présente les scores sous forme de pourcentage (63%) plutot que décimal (0.63).
3. S'il y a des points noirs dis-le clairement.
4. Ne répète pas les titres. Structure la réponse par thématiques (Vie Quotidienne, Opportunités Emploi, etc).
5. Fais le lien avec le projet de vie (Profil: Famille) et les indices de vie.
6. Termine par une question ouverte pour analyser une autre ville du top 5 ou approfondir l'analyse. N'écris JAMAIS tout en majuscule (CAPSLOCK)
