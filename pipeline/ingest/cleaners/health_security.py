"""Health and territory insecurity cleaners (APL doctors, SSMSI delinquency)."""

from __future__ import annotations

import logging
from typing import Any, Dict

import pandas as pd

from pipeline.common import PipelineLogger
from pipeline.ingest import context
from pipeline.run_context import PipelineRunError


def clean_sante_apl(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans DREES APL and saves to parquet."""
    import pipeline.ingest as ingest

    logger.log_step("clean_sante_apl", "STARTED")
    source = config["sources"]["sante_apl"]
    output_path = context.CLEAN_DIR / "sante_apl.parquet"

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(source.get("odace_table", "fact_apl_medecin"))
            if not df_odace.empty:
                if "annee" in df_odace.columns:
                    latest = df_odace["annee"].max()
                    df_odace = df_odace[df_odace["annee"] == latest]

                df_out = df_odace[
                    ["commune_insee_code", "apl_medecin_generaliste"]
                ].rename(
                    columns={
                        "commune_insee_code": "codgeo",
                        "apl_medecin_generaliste": "sante_apl",
                    }
                )
                df_out["codgeo"] = df_out["codgeo"].astype(str).str.zfill(5)
                df_out["sante_apl"] = pd.to_numeric(
                    df_out["sante_apl"], errors="coerce"
                )

                output_path.parent.mkdir(parents=True, exist_ok=True)
                df_out.to_parquet(output_path)
                logger.log_step(
                    "clean_sante_apl",
                    "COMPLETED",
                    {"rows": len(df_out), "source": "odace"},
                )
                return
            else:
                logging.warning("Odace fetch returned empty data for sante_apl.")
        except Exception as e:
            logging.error(f"Failed to fetch sante_apl from Odace: {e}")
        raise PipelineRunError("Odace sante_apl did not produce a usable dataset")


def clean_ter_insecurite(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans SSMSI Insecurity and saves to parquet."""
    import pipeline.ingest as ingest

    logger.log_step("clean_ter_insecurite", "STARTED")
    source = config["sources"]["ter_insecurite"]
    output_path = context.CLEAN_DIR / "ter_insecurite.parquet"

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_insecurite_commune")
            )
            if not df_odace.empty:
                latest = df_odace["annee"].max()
                df_odace = df_odace[df_odace["annee"] == latest]

                df_agg = (
                    df_odace.groupby("commune_insee_code")["taux_pour_mille"]
                    .sum(min_count=1)
                    .reset_index()
                )
                df_agg.rename(
                    columns={
                        "commune_insee_code": "codgeo",
                        "taux_pour_mille": "ter_insecurite",
                    },
                    inplace=True,
                )
                df_agg["codgeo"] = df_agg["codgeo"].astype(str).str.zfill(5)

                output_path.parent.mkdir(parents=True, exist_ok=True)
                df_agg.to_parquet(output_path)
                logger.log_step(
                    "clean_ter_insecurite",
                    "COMPLETED",
                    {"rows": len(df_agg), "source": "odace"},
                )
                return
            else:
                logging.warning("Odace fetch returned empty data for ter_insecurite.")
        except Exception as e:
            logging.error(f"Failed to fetch ter_insecurite from Odace: {e}")
        raise PipelineRunError("Odace ter_insecurite did not produce a usable dataset")
