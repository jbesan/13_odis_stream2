# Legacy ingestion archive

This package preserves retired manual-download ingestion paths. It is not part
of the default ETL, release manifest, or application runtime.

The J'Accueille CSV/XLSX exports and their former BigQuery uploader were
superseded by the Salesforce extract. Run an archived path only for a documented
historical-reproduction or recovery exercise, supplying the input path
explicitly. Do not re-add it as an implicit fallback to the active pipeline.

## Odace-backed manual-download archive

For every enabled Odace source, the active pipeline now fails closed if Odace
does not provide usable data. It will not silently serve an old local download.
The retired direct-download implementations are preserved in
`ingest_manual_fallbacks.py`, a non-imported source-level snapshot of the
pre-cleanup pipeline at commit `3104b64`.

The archived `housing_occupation` cleaner reads the direct INSEE/Data.gouv
CSV/ZIP export. The active equivalent reads Odace
`fact_occupation_logement` and validates its versioned source contract before
writing a candidate artifact.

| Active source | Odace table | Archived direct-download cleaner |
| --- | --- | --- |
| communes | `ref_commune_geo` | `clean_communes` |
| population | `fact_population_municipale` | `clean_population` |
| population_active | `fact_population_active` | `clean_population_active` |
| logement_vacant | `fact_logement_vacant` | `clean_lovac` |
| logement_social | `fact_logement_social_rpls` | `clean_rpls` |
| housing_occupation | `fact_occupation_logement` | `clean_housing_occupation` |
| caf | `fact_couverture_petite_enfance` | `clean_caf` |
| associations | `dim_association` | `clean_associations` |
| bpe | `dim_equipement_territoire` | `clean_bpe` |
| population_details | `fact_demographie` | `clean_population_details` |
| mob_transports_pub | `fact_transport_commun` | `clean_mob_transports_pub` |
| logement_social_delay | `fact_delai_attribution_logement` | `clean_log_soc_delay` |
| sante_apl | `fact_apl_medecin` | `clean_sante_apl` |
| mob_durable_share | `fact_mobilite_durable` | `clean_mob_durable` |
| ter_insecurite | `fact_insecurite_commune` | `clean_ter_insecurite` |

Do not import this snapshot into the active pipeline. It exists only for
historical reproduction and as a source-level recovery reference.

`maternites`, `education_annuaire`, and `finess_national` have been removed
from the active source catalogue: the BPE-based POI flow is their replacement.
