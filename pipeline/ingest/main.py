"""Command-line entry point and execution coordinator for the ingest pipeline."""

from __future__ import annotations

import argparse
import logging
from typing import Sequence

from pipeline.common import PipelineLogger, load_config
from pipeline.ingest.cleaners import (
    clean_associations,
    clean_bpe,
    clean_caf,
    clean_codes_postaux,
    clean_communes,
    clean_departements,
    clean_electoral_history,
    clean_formations,
    clean_hebergement_rna,
    clean_housing_occupation,
    clean_inclusion_jobs,
    clean_live_jobs,
    clean_log_soc_delay,
    clean_lovac,
    clean_loyers,
    clean_mob_durable,
    clean_mob_transports_pub,
    clean_nomenclature_waldec,
    clean_odace_gares,
    clean_odace_rent,
    clean_political,
    clean_population,
    clean_population_active,
    clean_population_details,
    clean_refugee_associations,
    clean_rpls,
    clean_salesforce_jaccueille,
    clean_sante_apl,
    clean_school_effectifs,
    clean_services_inclusion,
    clean_structures_inclusion,
    clean_ter_insecurite,
)
from pipeline.ingest import context
from pipeline.ingest.fetcher import fetch_rome_referential, fetch_source
from pipeline.ingest.cleaners.rna_rag import fetch_rna_rag_stats
from pipeline.ingest.runner import run_clean_step_safely


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entry point for executing the offline data ingestion pipeline."""
    logging.getLogger().setLevel(logging.INFO)
    parser = argparse.ArgumentParser(description="ODIS Ingest Pipeline")
    parser.add_argument(
        "--steps",
        type=str,
        help="Comma-separated list of steps to run (e.g. communes,inclusion)",
    )
    parser.add_argument(
        "--skip-live-jobs",
        action="store_true",
        help="Skip France Travail Live Jobs fetch",
    )
    parser.add_argument(
        "--skip-inclusion-jobs", action="store_true", help="Skip Inclusion Jobs fetch"
    )
    args = parser.parse_args(argv)

    logger = PipelineLogger(context.STATUS_FILE)
    config = load_config(context.CONFIG_FILE)

    context.CLEAN_DIR.mkdir(parents=True, exist_ok=True)

    logger.log_step("ingest_all", "STARTED")

    steps_map = {
        "communes": clean_communes,
        "services_inclusion": clean_services_inclusion,
        "structures_inclusion": clean_structures_inclusion,
        "population": clean_population,
        "population_active": clean_population_active,
        "lovac": clean_lovac,
        "rpls": clean_rpls,
        "caf": clean_caf,
        "associations": clean_associations,
        "refugee_associations": clean_refugee_associations,
        "political": clean_political,
        "electoral_history": clean_electoral_history,
        "housing_occupation": clean_housing_occupation,
        "school_effectifs": clean_school_effectifs,
        "bpe": clean_bpe,
        "codes_postaux": clean_codes_postaux,
        "formations": clean_formations,
        "gares": clean_odace_gares,
        "odace_rent": clean_odace_rent,
        "loyers": clean_loyers,
        "population_details": clean_population_details,
        "nomenclature_waldec": clean_nomenclature_waldec,
        "departements": clean_departements,
        "live_jobs": clean_live_jobs,
        "inclusion_jobs": clean_inclusion_jobs,
        "mob_transports_pub": clean_mob_transports_pub,
        "hebergement_rna": clean_hebergement_rna,
        "log_soc_delay": clean_log_soc_delay,
        "sante_apl": clean_sante_apl,
        "mob_durable": clean_mob_durable,
        "ter_insecurite": clean_ter_insecurite,
        "salesforce_jaccueille": clean_salesforce_jaccueille,
    }

    selected_steps = args.steps.split(",") if args.steps else list(steps_map.keys())

    if not args.steps or "rome" in selected_steps:
        fetch_rome_referential(config, logger)

    if not args.steps or any(
        s in selected_steps for s in ["associations", "refugee_associations", "rna"]
    ):
        fetch_rna_rag_stats(logger, config)

    for name, source_cfg in config["sources"].items():
        if args.steps and name not in selected_steps:
            continue
        if name == "rna_rag":
            continue
        fetch_source(name, source_cfg, logger)

    for step_name in selected_steps:
        if step_name in steps_map:
            try:
                if step_name == "live_jobs":
                    skip_live = getattr(args, "skip_live_jobs", False)
                    run_clean_step_safely(
                        step_name, steps_map[step_name], config, logger, skip=skip_live
                    )
                elif step_name == "inclusion_jobs":
                    skip_inc = getattr(args, "skip_inclusion_jobs", False)
                    run_clean_step_safely(
                        step_name, steps_map[step_name], config, logger, skip=skip_inc
                    )
                else:
                    run_clean_step_safely(
                        step_name, steps_map[step_name], config, logger
                    )
            except Exception as e:
                logging.exception(
                    f"❌ [INGEST FAILURE] Error running step '{step_name}'"
                )
                logger.log_step("ingest_all", "ERROR", {"failed_step": step_name})
                raise
        else:
            logging.warning(f"Unknown step: {step_name}")

    logger.log_step("ingest_all", "COMPLETED")


if __name__ == "__main__":
    main()
