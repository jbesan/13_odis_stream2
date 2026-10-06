"""Demographics data cleaning routines (population, active population, age breakdown)."""

from __future__ import annotations

import logging
from typing import Any, Dict

import numpy as np
import pandas as pd

from pipeline.common import PipelineLogger
from pipeline.ingest.config import get_domain_contracts
from pipeline.ingest import context
from pipeline.run_context import PipelineRunError


def clean_population(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Population and saves to parquet."""
    import pipeline.ingest as ingest

    logger.log_step("clean_population", "STARTED")
    source = config["sources"]["population"]

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_population_municipale")
            )
            if not df_odace.empty:
                df_out = df_odace[["geo_code", "pop_2023"]].rename(
                    columns={"geo_code": "codgeo", "pop_2023": "population"}
                )
                df_out["codgeo"] = df_out["codgeo"].astype(str).str.zfill(5)
                df_out["population"] = pd.to_numeric(
                    df_out["population"], errors="coerce"
                )

                output_path = context.CLEAN_DIR / "population.parquet"
                df_out.to_parquet(output_path)
                logger.log_step(
                    "clean_population",
                    "COMPLETED",
                    {"rows": len(df_out), "source": "odace"},
                )
                return
            else:
                logging.warning("Odace fetch returned empty data for population.")
        except Exception as e:
            logging.error(f"Failed to fetch population from Odace: {e}")
        raise PipelineRunError("Odace population did not produce a usable dataset")


def clean_population_active(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Population Active and saves to parquet."""
    import pipeline.ingest as ingest

    logger.log_step("clean_population_active", "STARTED")
    source = config["sources"]["population_active"]

    domain_cfg = get_domain_contracts()
    active_cfg = domain_cfg.demographics.active_population

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_population_active")
            )
            if not df_odace.empty:
                df_mapped = df_odace.rename(
                    columns={
                        "annee": "TIME_PERIOD",
                        "commune_insee_code": "GEO",
                        "code_pcs": "PCS",
                        "statut_emploi": "EMPSTA_ENQ",
                        "valeur": "OBS_VALUE",
                    }
                )

                max_year = df_mapped["TIME_PERIOD"].max()
                actif_2022 = df_mapped[
                    (df_mapped.TIME_PERIOD == max_year)
                    & (df_mapped.PCS == active_cfg.pcs_filter)
                    & (
                        df_mapped.EMPSTA_ENQ.astype(str).isin(
                            active_cfg.employment_status_codes
                        )
                    )
                ].pivot_table(
                    index="GEO", columns="EMPSTA_ENQ", values="OBS_VALUE", aggfunc="sum"
                )

                actif_2022.columns = [str(c) for c in actif_2022.columns]

                code_active = active_cfg.code_active
                code_employed = active_cfg.code_employed

                if (
                    code_active in actif_2022.columns
                    and code_employed in actif_2022.columns
                ):
                    actif_2022["pop_chomeurs"] = (
                        actif_2022[code_active] - actif_2022[code_employed]
                    )
                    actif_2022.rename(
                        columns={
                            code_active: "pop_active",
                            code_employed: "pop_employes",
                        },
                        inplace=True,
                    )
                    actif_2022 = actif_2022[
                        ["pop_active", "pop_employes", "pop_chomeurs"]
                    ]
                    actif_2022.index.name = "codgeo"
                    actif_2022 = actif_2022.reset_index()
                    actif_2022["codgeo"] = actif_2022["codgeo"].astype(str).str.zfill(5)

                    output_path = context.CLEAN_DIR / "population_active.parquet"
                    actif_2022.to_parquet(output_path)
                    logger.log_step(
                        "clean_population_active",
                        "COMPLETED",
                        {
                            "path": str(output_path),
                            "rows": len(actif_2022),
                            "source": "odace",
                        },
                    )
                    return
                else:
                    logging.warning("Population Active pivot failed on Odace data.")
            else:
                logging.warning(
                    "Odace fetch returned empty data for population_active."
                )
        except Exception as e:
            logging.error(f"Failed to fetch population_active from Odace: {e}")
        raise PipelineRunError(
            "Odace population_active did not produce a usable dataset"
        )


def clean_population_details(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Population Details (Age Breakdown) and saves to parquet."""
    import pipeline.ingest as ingest

    logger.log_step("clean_population_details", "STARTED")
    source = config["sources"]["population_details"]

    domain_cfg = get_domain_contracts()
    details_cfg = domain_cfg.demographics.population_details

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_population_age_detail")
            )
            if not df_odace.empty:
                df_mapped = df_odace.rename(
                    columns={
                        "annee": "TIME_PERIOD",
                        "commune_insee_code": "GEO",
                        "tranche_age": "AGE",
                        "sexe": "SEX",
                        "valeur": "OBS_VALUE",
                    }
                )

                df_filtered = df_mapped[
                    (df_mapped["SEX"] == details_cfg.sex_filter)
                    & (df_mapped["AGE"].isin(details_cfg.target_age_codes))
                ]

                df_filtered = df_filtered.rename(columns={"GEO": "codgeo"})
                df_filtered["codgeo"] = df_filtered["codgeo"].astype(str).str.zfill(5)
                df_filtered["year"] = df_filtered["TIME_PERIOD"].astype(str)
                df_filtered["count"] = pd.to_numeric(
                    df_filtered["OBS_VALUE"], errors="coerce"
                )

                age_mapping = details_cfg.age_group_mapping
                df_filtered["age_group"] = df_filtered["AGE"].map(age_mapping)

                df_pivot = (
                    df_filtered.groupby(["codgeo", "age_group", "year"], observed=True)[
                        "count"
                    ]
                    .sum(min_count=1)
                    .unstack(["age_group", "year"])
                )
                df_pivot.columns = [f"pop_{c[0]}_{c[1]}" for c in df_pivot.columns]
                df_pivot = df_pivot.reset_index()

                for col in details_cfg.expected_columns:
                    if col not in df_pivot.columns:
                        df_pivot[col] = np.nan

                output_path = context.CLEAN_DIR / "population_details.parquet"
                df_pivot.to_parquet(output_path)
                logger.log_step(
                    "clean_population_details",
                    "COMPLETED",
                    {"rows": len(df_pivot), "source": "odace"},
                )
                return
            else:
                logging.warning(
                    "Odace fetch returned empty data for population_details."
                )
        except Exception as e:
            logging.error(f"Failed to fetch population_details from Odace: {e}")
        raise PipelineRunError(
            "Odace population_details did not produce a usable dataset"
        )
