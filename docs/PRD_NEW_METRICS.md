# PRD: Extension du Moteur de Scoring - Nouveaux Indicateurs 2026

## 1. Contexte & Objectifs
L'objectif est d'enrichir le moteur de scoring ODIS avec 4 nouveaux indicateurs clés pour améliorer la précision de la recommandation territoriale, notamment sur les aspects de tension (logement/santé), de comportement (mobilité durable) et de cadre de vie (sécurité).

## 2. Indicateurs Cibles

### A. Tension sur le Logement Social (Logement)
- **ID** : `log_soc_delay_scaled`
- **Source** : USH (Union Sociale pour l'Habitat) via [union-habitat.org](https://www.union-habitat.org/sites/default/files/articles/documents/2025-09/donnees_ush_stats_1_demande_attribution.xlsx).
- **Format** : Excel (`range A3:B1263`).
- **Statut API** : ⬇️ Download direct.
- **Logique** : Basé sur le délai moyen d'attribution (en mois) à l'échelle de l'EPCI. Plus le délai est long, plus le score est "pénalisé" (ou valorisé si l'utilisateur cherche de la disponibilité).

### B. Accessibilité aux Soins - APL (Santé)
- **ID** : `sante_rdv_delay_scaled`
- **Source** : DREES - APL (Accessibilité Potentielle Localisée) via [data.drees.solidarites-sante.gouv.fr](https://data.drees.solidarites-sante.gouv.fr/explore/dataset/530_l-accessibilite-potentielle-localisee-apl/).
- **Format** : Excel (URL directe : `https://data.drees.solidarites-sante.gouv.fr/api/datasets/1.0/530_l-accessibilite-potentielle-localisee-apl/attachments/indicateur_d_accessibilite_potentielle_localisee_apl_aux_medecins_generalistes_xlsx/`).
- **Range** : `A9:H34974` (Feuille "APL 2023").
- **Logique** : Nombre de consultations accessibles par habitant standardisé. Un score < 2.5 définit une zone sous-dotée.

### C. Part des trajets Domicile-Travail durables (Mobilité)
- **ID** : `mob_dur_share_scaled`
- **Source** : Ecolab via [data.gouv.fr](https://www.data.gouv.fr/fr/datasets/flux-domicile-travail-selon-le-mode-de-transport-principal-utilise/).
- **Resource ID (RID)** : `f624e1db-8f22-4a96-9f5a-9f9ee2aae53e`.
- **Statut API** : ✅ Tabular API.
- **Logique** : Somme des parts (%) du vélo et des transports en commun dans les déplacements domicile-travail.

### D. Indice d'Insécurité Communal (Territoire)
- **ID** : `ter_insecurite_scaled`
- **Source** : SSMSI via [data.gouv.fr](https://www.data.gouv.fr/fr/datasets/bases-statistiques-communale-departementale-et-regionale-de-la-delinquance-enregistree-par-la-police-et-la-gendarmerie-nationales/).
- **Resource ID (RID)** : `604d71b8-337d-4869-9226-49e01bae87df`.
- **Statut API** : ✅ Tabular API.
- **Logique** : Taux d'infractions (coups et blessures volontaires, cambriolages) pour 1000 habitants. Inversion du score pour qu'une valeur élevée = territoire plus sûr.

## 3. Stratégie d'Ingestion (Tabular API)
Plutôt que des liens statiques vers des CSV, le pipeline migrera vers l'usage de la **Tabular API** de `data.gouv.fr` :
- **Discovery** : Utilisation des Resource IDs (RID) pour garantir la récupération de la dernière version.
- **Filtering** : Utilisation des capacités de filtrage de l'API (`/api/resources/{rid}/data/`) pour ne récupérer que les colonnes nécessaires (réduction du volume de transfert).
- **Automation** : Intégration dans `pipeline/sources.yaml` via un nouveau type de source `tabapi`.

### Modèles de Données (`app/core/models.py`)
- Mise à jour de `HousingMetrics`, `HealthMetrics`, `MobilityMetrics`, `TerritoryMetrics`.
- Ajout des champs dans `SearchCriterias` pour permettre le filtrage/pondération.

### Configuration (`app/scores_config.yaml`)
- Déclaration des 4 indicateurs avec leurs métadonnées (tooltip, unité, poids par défaut).

### Pipeline ETL (`pipeline/prescoring.py`)
- Ces indicateurs étant `precomputed`, ils devront être intégrés dans la phase de scaling du parquet final.

### UI (`app/ui/forms.py`)
- Ajout de sliders ou toggles pour activer ces critères dans le formulaire de recherche.

## 4. Plan de Validation
- Vérification de la présence des colonnes dans le Parquet.
- Audit de l'explication du Scorer Agent pour vérifier qu'il cite ces nouveaux délais.
- Test de bout en bout : Vérifier qu'une ville avec un faible délai de logement social remonte effectivement dans les résultats "Priorité Logement".
