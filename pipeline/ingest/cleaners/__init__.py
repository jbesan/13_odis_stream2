"""Domain-specific cleaner modules for the ODIS ingestion pipeline."""

from __future__ import annotations

from pipeline.ingest.cleaners.bpe import clean_bpe
from pipeline.ingest.cleaners.demographics import (
    clean_population,
    clean_population_active,
    clean_population_details,
)
from pipeline.ingest.cleaners.education import (
    clean_formations,
    clean_school_effectifs,
)
from pipeline.ingest.cleaners.electoral import (
    clean_electoral_history,
    clean_political,
)
from pipeline.ingest.cleaners.geography import (
    clean_codes_postaux,
    clean_communes,
    clean_departements,
    clean_odace_gares,
)
from pipeline.ingest.cleaners.health_security import (
    clean_sante_apl,
    clean_ter_insecurite,
)
from pipeline.ingest.cleaners.housing import (
    clean_housing_occupation,
    clean_log_soc_delay,
    clean_lovac,
    clean_loyers,
    clean_odace_rent,
    clean_rpls,
    transform_housing_occupation_odace,
)
from pipeline.ingest.cleaners.inclusion import (
    clean_associations,
    clean_caf,
    clean_nomenclature_waldec,
    clean_services_inclusion,
    clean_structures_inclusion,
)
from pipeline.ingest.cleaners.jobs import (
    clean_inclusion_jobs,
    clean_live_jobs,
    get_inclusion_jobs_status,
    get_live_jobs_status,
)
from pipeline.ingest.cleaners.mobility import (
    clean_mob_durable,
    clean_mob_transports_pub,
)
from pipeline.ingest.cleaners.rna_rag import (
    clean_hebergement_rna,
    clean_refugee_associations,
    compute_rna_rag_counts,
    fetch_rna_rag_stats,
)
from pipeline.ingest.cleaners.salesforce import clean_salesforce_jaccueille

__all__ = [
    "clean_associations",
    "clean_bpe",
    "clean_caf",
    "clean_codes_postaux",
    "clean_communes",
    "clean_departements",
    "clean_electoral_history",
    "clean_formations",
    "clean_hebergement_rna",
    "clean_housing_occupation",
    "clean_inclusion_jobs",
    "clean_live_jobs",
    "clean_log_soc_delay",
    "clean_lovac",
    "clean_loyers",
    "clean_mob_durable",
    "clean_mob_transports_pub",
    "clean_nomenclature_waldec",
    "clean_odace_gares",
    "clean_odace_rent",
    "clean_political",
    "clean_population",
    "clean_population_active",
    "clean_population_details",
    "clean_refugee_associations",
    "clean_rpls",
    "clean_salesforce_jaccueille",
    "clean_sante_apl",
    "clean_school_effectifs",
    "clean_services_inclusion",
    "clean_structures_inclusion",
    "clean_ter_insecurite",
    "compute_rna_rag_counts",
    "fetch_rna_rag_stats",
    "get_inclusion_jobs_status",
    "get_live_jobs_status",
    "transform_housing_occupation_odace",
]
