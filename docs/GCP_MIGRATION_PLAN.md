# Plan de migration GCP — ODIS vers J'accueille

Établi le 31 juillet 2026 à partir d'un inventaire GCP en lecture seule. Aucune ressource n'a été modifiée.

## État validé

| Élément | Constat / décision |
| --- | --- |
| Projet source | `odis-stream2` (n° `277907345311`). `odis-stream2-eu` est le bucket GCS principal, pas un projet. |
| Projet cible | `odis-stream2-app` (n° `297204448527`), actif, sans compte de service ni Cloud Run/Artifact Registry. |
| Localisations | Cloud Run, Artifact Registry et GCS : `europe-west1`. BQ `rna_rag` : `europe-west1`; BQ `jaccueille` et `odis_logs` : `EU`. Les datasets cible doivent conserver ces localisations. |
| Compte actuel | L'app source utilise le compte Compute par défaut avec droits larges. À remplacer par un compte dédié. |
| Accès opérateur | `jbesancon@gmail.com` a `Editor` et `Secret Manager Admin` sur la cible. L'Owner `tech@jaccueille.fr` doit poser les bindings IAM initiaux. |
| Accès source | `jbesancon@gmail.com` est `Owner` de `odis-stream2` : les copies GCS/BigQuery source ne sont pas bloquées côté source. |
| Exposition | Cloud Run est public ; l'app applique sa propre auth OIDC. Conserver ce modèle pour la migration, sauf décision explicite IAP/IAM. |

## Architecture cible

Créer deux comptes de service sans clé JSON.

| Compte | Usage | Rôles minimaux |
| --- | --- | --- |
| `odis-run@odis-stream2-app.iam.gserviceaccount.com` | Identité Cloud Run | `roles/aiplatform.user`, `roles/bigquery.jobUser`; `roles/bigquery.dataViewer` sur `rna_rag` et `jaccueille`; `roles/bigquery.dataEditor` sur `odis_logs`; `roles/storage.objectViewer` sur le bucket de données; `roles/storage.objectUser` sur celui des recherches; `roles/secretmanager.secretAccessor` sur les seuls secrets montés. |
| `odis-ci@odis-stream2-app.iam.gserviceaccount.com` | Cloud Build et déploiement | `roles/artifactregistry.writer` (dépôt), `roles/run.admin` (service), `roles/iam.serviceAccountUser` (sur `odis-run`) et `roles/logging.logWriter`. |

L'Owner doit aussi donner à l'agent Cloud Run `service-297204448527@serverless-robot-prod.iam.gserviceaccount.com` le rôle `roles/iam.serviceAccountTokenCreator` sur `odis-run`.

Ressources proposées : dépôt Docker `odis-stream2-repo`, service `odis-app`, bucket `odis-stream2-app-data-euw1` (lecture seule pour l'app), bucket `odis-stream2-app-shares-euw1` (recherches partagées, lifecycle recommandé de 90 jours), et datasets BQ `rna_rag`, `jaccueille`, `odis_logs`.

## Inventaire et décision de backfill

| Source | Volume observé | Décision |
| --- | ---: | --- |
| `gs://odis-stream2-eu/datasets/**` | 116,6 Mo | **Copier.** L'app utilise `datasets/current.json` et les releases immuables. |
| `gs://odis-stream2-eu/searches/**` | 230 Ko, 5 objets | **Optionnel.** Copier seulement si les liens partagés existants doivent rester valides. |
| Racine de `odis-stream2-eu` | environ 1,37 Go | **Ne pas copier par défaut.** Exports historiques hors du mécanisme de release. |
| `gs://odis-stream2-eu_cloudbuild/source/**` | 12,4 Mo | **Ne pas copier.** Archives de build. |
| `gs://rna_rag_euw1/**` | 33,3 Go | **Ne pas copier pour le runtime.** Aucun chemin applicatif ne le référence; à migrer seulement avec le pipeline de régénération RAG. |
| `rna_rag.rna_rag` | 14,35 Go / 1 888 279 lignes | **Copier intégralement.** Table RAG interrogée à l'exécution. |
| `jaccueille.jaccueille_accueillants_bdv` | 2,7 Ko / 181 lignes | **Copier** ou régénérer juste avant cutover. |
| `jaccueille.jaccueille_prospects_bdv` | 22 Ko / 1 472 lignes | **Copier** ou régénérer juste avant cutover. |
| `odis_logs.search_events`, `usage_events`, `user_feedback`, `agent_state_logs` | 440 lignes max / 24,4 Mo max | **Créer vides, sans backfill.** Données d'usage et potentiellement personnelles. |
| `odis_logs.gcp_billing_export_*` | — | **Ne pas copier.** Le projet cible aura son propre export de facturation si souhaité. |
| `rna_rag.rna_rag_mini` (13,65 Go) et `rna_rag.odis_communes` | — | **Ne pas copier.** Aucun appel runtime identifié. |
| Images, révisions Cloud Run et historique Cloud Build | — | **Ne pas copier.** Premier build cible depuis un commit versionné. |

Les 12 secrets réellement injectés sont : `GOOGLE_MAPS_API_KEY`, `FRANCE_TRAVAIL_CLIENT_ID`, `FRANCE_TRAVAIL_CLIENT_SECRET`, `DATA_INCLUSION_API_KEY`, `LOGFIRE_TOKEN`, `ODIS_USERS_CONFIG`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_COOKIE_SECRET`, `OIDC_REDIRECT_URI`, `OIDC_ALLOWED_EMAILS_JSON`, `OIDC_EMAIL_ORG_MAPPING_JSON`.

`ADMIN_USERS_JSON` et `OIDC_DOMAIN_ORG_MAPPING` existent dans la source mais ne sont pas montés par le service actuel : ne pas les migrer sans corriger d'abord leur configuration.

## Correctifs de code bloquants

Le projet cible lirait encore la source sans ces adaptations :

1. Dans `app/services/rna_rag.py`, remplacer le projet et la table codés en dur `odis-stream2.rna_rag.rna_rag` par `ODIS_DATA_PROJECT`, avec `GOOGLE_CLOUD_PROJECT` comme défaut.
2. Faire la même chose dans `app/utils/data_loader.py` pour les deux tables `jaccueille.*`, puis dans `pipeline/ingest.py` pour les requêtes RAG.
3. Passer explicitement `GCS_DATASETS_BUCKET=odis-stream2-app-data-euw1` et `GCS_SHARED_SEARCHES_BUCKET=odis-stream2-app-shares-euw1`; les défauts actuels pointent sur le bucket source.
4. Rendre `cloudbuild.yaml` générique : retirer `_PROJECT_ID: odis-stream2` fixé dans le fichier, et renseigner `GOOGLE_CLOUD_PROJECT` / `ODIS_AGENT_GCP_PROJECT` avec le projet du build.
5. Mettre à jour `OIDC_REDIRECT_URI` chez le fournisseur OIDC avec l'URL de recette cible avant la bascule. Créer une nouvelle clé Maps dans la cible, restreinte aux APIs/origines réellement utilisées.

## Déroulé

### A. Pré-vol

1. Valider les comptes, rôles et la rétention des recherches partagées avec l'Owner J'accueille.
2. Préparer les valeurs des 12 secrets par un canal sûr : ni Git, ni `.env`, ni substitutions Cloud Build, ni logs.
3. Confirmer le transfert de la table `rna_rag.rna_rag` et l'inscription de l'URL cible chez le fournisseur OIDC.

### B. Bootstrap cible

1. Activer uniquement `run.googleapis.com`, `artifactregistry.googleapis.com`, `cloudbuild.googleapis.com`, `secretmanager.googleapis.com`, `storage.googleapis.com`, `bigquery.googleapis.com`, `aiplatform.googleapis.com`, `iam.googleapis.com` et `logging.googleapis.com`. BigQuery, Storage et Logging sont déjà actifs; Cloud Run et Artifact Registry ne le sont pas.
2. Créer les deux comptes et les bindings IAM; créer dépôt, buckets et datasets.

```bash
bq --location=europe-west1 mk --dataset odis-stream2-app:rna_rag
bq --location=EU mk --dataset odis-stream2-app:jaccueille
bq --location=EU mk --dataset odis-stream2-app:odis_logs
```

3. Créer les 12 secrets et donner leur lecture au seul compte `odis-run`.

### C. Backfill contrôlé

```bash
gcloud storage rsync --recursive \
  gs://odis-stream2-eu/datasets \
  gs://odis-stream2-app-data-euw1/datasets

bq --location=europe-west1 cp -f \
  odis-stream2:rna_rag.rna_rag \
  odis-stream2-app:rna_rag.rna_rag

bq --location=EU cp -f \
  odis-stream2:jaccueille.jaccueille_accueillants_bdv \
  odis-stream2-app:jaccueille.jaccueille_accueillants_bdv
bq --location=EU cp -f \
  odis-stream2:jaccueille.jaccueille_prospects_bdv \
  odis-stream2-app:jaccueille.jaccueille_prospects_bdv
```

Créer les quatre tables de télémétrie vides avec leur schéma source (`CREATE TABLE ... LIKE`), sans copier de lignes. Vérifier le manifest GCS, les comptes de lignes BQ et l'accès de `odis-run`.

### D. Recette et cutover

1. Appliquer les correctifs, passer `ruff`, `ty` et `pytest`, puis construire l'image cible du commit choisi avec un tag immuable `${SHORT_SHA}`.
2. Déployer cette révision avec `--tag=staging --no-traffic`. Tester : OIDC, recherche standard, carte, RAG, données J'accueille, partage, PDF, Analytics et absence de `403` dans Cloud Logging.
3. Promouvoir **la même révision** à 100 % du trafic; ne pas reconstruire entre recette et production.
4. Garder la source disponible pendant la fenêtre de réversibilité convenue. Ne supprimer aucune ressource source avant sa clôture.

## CI/CD simple après migration

1. Pull requests : qualité seulement.
2. Branche `staging` : image `${SHORT_SHA}`, révision Cloud Run `--tag=staging --no-traffic`, smoke test authentifié.
3. Tag Git `vX.Y.Z` ou approbation manuelle : promotion de la révision déjà testée; tracer tag Git, digest et révision.
4. Utiliser `odis-ci` explicitement et Cloud Logging pour les logs de build, plutôt qu'un bucket d'archives.

Un tag Cloud Run donne une URL stable de recette sans trafic utilisateur sur la nouvelle révision. Un service `odis-app-staging` séparé ne devient utile que si les secrets ou les données de recette doivent être isolés.

## FinOps et coûts dans l'Admin Dashboard

### Action item FINOPS-01 — budget et alertes

Le rôle Billing Account Administrator peut être utilisé pour créer un budget et des alertes
de coût ciblant `odis-stream2-app`. Cette action relève du compte de facturation, pas des
comptes de service applicatifs. Définir au minimum : budget mensuel, seuils 50/80/100 %,
destinataires des notifications et périmètre projet.

### Action item FINOPS-02 — export Cloud Billing vers BigQuery

Configurer depuis Cloud Billing un export dans un dataset dédié `odis_billing` du projet
`odis-stream2-app`, localisation `EU`, sans expiration de tables. Le choix pragmatique pour
le dashboard est l'export **Standard usage cost** ; l'export détaillé est à activer seulement
si l'analyse au niveau ressource devient nécessaire, car il augmente le volume et le coût
de stockage/requête.

L'export est au niveau du compte de facturation : les tables peuvent donc contenir plusieurs
projets. Toutes les requêtes du dashboard doivent filtrer `project.id = 'odis-stream2-app'`.
La table standard aura un nom de la forme
`gcp_billing_export_v1_<BILLING_ACCOUNT_ID>`. Google peut faire évoluer son schéma ; créer
une vue stable `odis_billing.v_daily_costs` et faire dépendre l'application de cette vue,
jamais de la table brute.

Vue recommandée (à adapter au nom réel de la table) :

```sql
CREATE OR REPLACE VIEW `odis-stream2-app.odis_billing.v_daily_costs` AS
SELECT
  DATE(usage_start_time) AS usage_date,
  service.description AS service,
  project.id AS project_id,
  currency,
  SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS net_cost
FROM `odis-stream2-app.odis_billing.gcp_billing_export_v1_*`
WHERE project.id = 'odis-stream2-app'
GROUP BY usage_date, service, project_id, currency;
```

Google crée et gère le compte de service d'export
`billing-export-bigquery@system.gserviceaccount.com` et lui attribue l'accès nécessaire au
dataset ; cette liaison ne doit pas être supprimée. L'utilisateur qui configure l'export
doit avoir Billing Account Administrator (déjà disponible) et `roles/bigquery.user` sur le
projet contenant le dataset. La configuration peut aussi nécessiter `roles/bigquery.admin`
pendant le bootstrap déjà prévu.

### Action item FINOPS-03 — affichage Streamlit

1. Accorder à `odis-run` `roles/bigquery.dataViewer` uniquement sur `odis_billing`.
2. Ajouter dans `app/services/analytics_data.py` une requête paramétrée sur
   `v_daily_costs` (période, total net, service, jour), avec cache de quelques minutes.
3. Ajouter dans `app/pages/4_Analytics.py` un onglet « Coûts GCP » réservé aux admins,
   affichant au minimum : coût de la période, courbe journalière, répartition par service
   et date de dernière donnée disponible.
4. Ne jamais afficher la table brute ni les autres projets du compte de facturation.
5. Ajouter un test de non-régression vérifiant le filtre `project_id` et le comportement
   lorsque l'export n'est pas encore alimenté.

L'export n'est pas instantané : le premier remplissage peut prendre plusieurs heures et,
en dataset multi-région, peut inclure rétroactivement une partie du mois précédent. Le
dashboard doit donc afficher « données en cours de propagation » lorsque la vue est vide.

Références : [configuration de l'export Cloud Billing vers BigQuery](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-setup),
[schéma de l'export standard](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/standard-usage).

## Conditions d'exécution assistée

L'automatisation est possible après validation de l'Owner pour les comptes/bindings IAM, autorisation de créer les ressources, fourniture sécurisée des secrets, décision sur les recherches partagées et modification OIDC/Maps si gérées hors GCP. Les opérations de lecture, contrôles et scripts de provisioning peuvent ensuite être exécutés de façon reproductible.
