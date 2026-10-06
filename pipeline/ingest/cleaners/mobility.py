"""Mobility cleaners (public transit stations and sustainable mobility mode shares)."""

from __future__ import annotations

import logging
from typing import Any, Dict

import numpy as np

from pipeline.common import PipelineLogger
from pipeline.ingest.config import get_domain_contracts
from pipeline.ingest import context
from pipeline.run_context import PipelineRunError


def clean_mob_transports_pub(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Public Transport Stations data and saves to parquet."""
    import pipeline.ingest as ingest

    logger.log_step("clean_mob_transports_pub", "STARTED")
    source = config["sources"]["mob_transports_pub"]

    domain_cfg = get_domain_contracts()
    col_mapping = domain_cfg.mobility.public_transport_modes.copy()
    col_mapping["commune_insee_code"] = "codgeo"

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_transport_commun")
            )
            if not df_odace.empty:
                df_odace["type_transport"] = (
                    df_odace["type_transport"].astype(str).str.lower().str.strip()
                )
                df_pivot = (
                    df_odace.pivot_table(
                        index="commune_insee_code",
                        columns="type_transport",
                        values="nb_stations",
                        aggfunc="sum",
                    )
                    .reset_index()
                    .fillna(0)
                )

                df_pivot.rename(columns=col_mapping, inplace=True)

                expected_stop_cols = list(
                    domain_cfg.mobility.public_transport_modes.values()
                )
                for col in expected_stop_cols:
                    if col not in df_pivot.columns:
                        df_pivot[col] = 0.0

                df_pivot["codgeo"] = df_pivot["codgeo"].astype(str).str.zfill(5)
                df_pivot["nb_stops_total"] = df_pivot[expected_stop_cols].sum(axis=1)

                output_path = context.CLEAN_DIR / "mob_transports_pub.parquet"
                df_pivot.to_parquet(output_path)
                logger.log_step(
                    "clean_mob_transports_pub",
                    "COMPLETED",
                    {"rows": len(df_pivot), "source": "odace"},
                )
                return
            else:
                logging.warning("Odace fetch returned empty data for transports.")
        except Exception as e:
            logging.error(f"Failed to fetch transports from Odace: {e}")
        raise PipelineRunError(
            "Odace mob_transports_pub did not produce a usable dataset"
        )


def clean_mob_durable(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Ecolab Mobility and saves to parquet."""
    import pipeline.ingest as ingest

    logger.log_step("clean_mob_durable", "STARTED")
    source = config["sources"]["mob_durable_share"]
    output_path = context.CLEAN_DIR / "mob_durable.parquet"

    domain_cfg = get_domain_contracts()
    durable_modes = domain_cfg.mobility.sustainable_modes

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_mobilite_durable")
            )
            if not df_odace.empty:
                df_pivot = df_odace.pivot_table(
                    index="commune_insee_code",
                    columns="mode_transport",
                    values="part_modale",
                    aggfunc="sum",
                ).reset_index()
                df_pivot.rename(columns={"commune_insee_code": "codgeo"}, inplace=True)
                df_pivot["codgeo"] = df_pivot["codgeo"].astype(str).str.zfill(5)

                present_durable = [m for m in durable_modes if m in df_pivot.columns]

                mode_cols = [c for c in df_pivot.columns if c != "codgeo"]
                df_pivot["total_valeur"] = df_pivot[mode_cols].sum(axis=1)

                df_pivot["mob_dur_share"] = np.where(
                    df_pivot["total_valeur"] > 0,
                    df_pivot[present_durable].sum(axis=1) / df_pivot["total_valeur"],
                    np.nan,
                )

                df_out = df_pivot[["codgeo", "mob_dur_share"]]
                output_path.parent.mkdir(parents=True, exist_ok=True)
                df_out.to_parquet(output_path)
                logger.log_step(
                    "clean_mob_durable",
                    "COMPLETED",
                    {"rows": len(df_out), "source": "odace"},
                )
                return
            else:
                logging.warning(
                    "Odace fetch returned empty data for fact_mobilite_durable."
                )
        except Exception as e:
            logging.error(f"Failed to fetch mob_durable_share from Odace: {e}")
        raise PipelineRunError(
            "Odace mob_durable_share did not produce a usable dataset"
        )
