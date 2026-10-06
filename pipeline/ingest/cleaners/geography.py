"""Geographic referential cleaners (communes, departements, postal codes, gares)."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

import numpy as np
import shapely
from shapely.geometry import shape

from pipeline.common import PipelineLogger
from pipeline.ingest.config import get_domain_contracts
from pipeline.ingest import context
from pipeline.run_context import PipelineRunError


def clean_communes(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Communes and saves to parquet."""
    import pipeline.ingest as ingest

    logger.log_step("clean_communes", "STARTED")
    domain_cfg = get_domain_contracts()
    source = config["sources"]["communes"]
    output_path = context.CLEAN_DIR / "communes.parquet"

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(source.get("odace_table", "ref_commune_geo"))
            if not df_odace.empty:
                # 1. Fetch and build EPCI mapping
                epci_cfg = config["sources"].get(
                    "ref_epci",
                    {
                        "datagouv_resource_id": "4f02ce39-2a91-4a8b-85cb-c6a0f912516b",
                        "local_name": "ref_epci.json",
                        "ttl_days": 90,
                    },
                )
                epci_path = ingest.fetch_source("ref_epci", epci_cfg, logger)
                epci_map = {}
                if epci_path and epci_path.exists():
                    try:
                        with open(epci_path, "r", encoding="utf-8") as f:
                            epci_data = json.load(f)
                        for epci in epci_data:
                            epci_code = epci.get("code")
                            for m in epci.get("membres", []):
                                if m.get("code"):
                                    epci_map[str(m["code"]).zfill(5)] = str(
                                        epci_code
                                    ).zfill(9)
                    except Exception as e:
                        logging.error(f"Error parsing ref_epci mapping: {e}")

                # 2. Fetch dim_commune for labels, departement, and region
                df_commune = client.fetch_dim_commune(source["ttl_days"])

                df_odace.rename(columns={"commune_insee_code": "codgeo"}, inplace=True)
                df_odace["codgeo"] = df_odace["codgeo"].astype(str).str.zfill(5)

                if not df_commune.empty:
                    df_commune.rename(
                        columns={"commune_insee_code": "codgeo"}, inplace=True
                    )
                    df_commune["codgeo"] = df_commune["codgeo"].astype(str).str.zfill(5)
                    df_odace = df_odace.merge(
                        df_commune[
                            [
                                "codgeo",
                                "commune_sk",
                                "commune_label",
                                "departement_code",
                                "region_code",
                            ]
                        ],
                        on="codgeo",
                        how="left",
                    )
                    df_odace.rename(
                        columns={
                            "commune_label": "nom",
                            "departement_code": "departement",
                            "region_code": "region",
                        },
                        inplace=True,
                    )
                else:
                    raise ValueError("Odace dim_commune is empty")

                df_odace["commune"] = df_odace["nom"]
                df_odace["plm"] = np.where(
                    df_odace["codgeo"].isin(domain_cfg.geography.plm_communes),
                    1.0,
                    np.nan,
                )

                df_odace["epci"] = df_odace["codgeo"].map(epci_map)

                geoms = df_odace["geometrie_geojson"].apply(
                    lambda x: shape(json.loads(x)) if x else None
                )
                df_odace["polygon"] = shapely.to_wkb(geoms)

                out_cols = [
                    "codgeo",
                    "commune_sk",
                    "nom",
                    "departement",
                    "region",
                    "commune",
                    "plm",
                    "epci",
                    "polygon",
                ]
                df_final = df_odace[
                    [c for c in out_cols if c in df_odace.columns]
                ].copy()

                df_final.to_parquet(output_path)
                logger.log_step(
                    "clean_communes",
                    "COMPLETED",
                    {
                        "path": str(output_path),
                        "rows": len(df_final),
                        "source": "odace",
                    },
                )
                return
            else:
                logging.warning("Odace fetch returned empty data for ref_commune_geo.")
        except Exception as e:
            logging.error(f"Failed to fetch ref_commune_geo from Odace: {e}")
        raise PipelineRunError("Odace communes did not produce a usable dataset")


def clean_departements(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Departements referential and saves to parquet."""
    import pipeline.ingest as ingest

    logger.log_step("clean_departements", "STARTED")
    source = config["sources"]["departements_ref"]
    path = context.CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    df = ingest.load_dataset(path, source)

    if "DEP" in df.columns and "LIBELLE" in df.columns:
        cols_to_keep = ["DEP", "LIBELLE"]
        if "REG" in df.columns:
            cols_to_keep.append("REG")

        df_out = df[cols_to_keep].rename(
            columns={"DEP": "code", "LIBELLE": "label", "REG": "reg_code"}
        )
        df_out["code"] = df_out["code"].astype(str).str.zfill(2)
        if "reg_code" in df_out.columns:
            df_out["reg_code"] = df_out["reg_code"].astype(str).str.zfill(2)

        output_path = context.CLEAN_DIR / "departements.parquet"
        df_out.to_parquet(output_path)
        logger.log_step(
            "clean_departements",
            "COMPLETED",
            {"path": str(output_path), "rows": len(df_out)},
        )
    else:
        logging.warning(f"Departements: Columns not found. Found: {df.columns}")


def clean_codes_postaux(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Codes Postaux and saves to parquet."""
    import pipeline.ingest as ingest

    logger.log_step("clean_codes_postaux", "STARTED")
    try:
        source = config["sources"]["codes_postaux"]
        path = context.CACHE_DIR / source["local_name"]
        if not path.exists():
            return

        df = ingest.load_dataset(path, source)
        df.columns = [c.strip() for c in df.columns]

        cp_col = next(
            (c for c in df.columns if "Code_postal" in c or "code_postal" in c), None
        )
        insee_col = next(
            (
                c
                for c in df.columns
                if "Code_commune_INSEE" in c or "code_commune_insee" in c
            ),
            None,
        )

        if cp_col and insee_col:
            df = df[[cp_col, insee_col]].copy()
            df["code_postal"] = df[cp_col].astype(str).str.zfill(5)
            df["codgeo"] = df[insee_col].astype(str).str.zfill(5)

            df_out = df[["code_postal", "codgeo"]].drop_duplicates()

            output_path = context.CLEAN_DIR / "codes_postaux.parquet"
            df_out.to_parquet(output_path)
            logger.log_step(
                "clean_codes_postaux", "COMPLETED", {"path": str(output_path)}
            )
        else:
            logging.warning(f"Codes Postaux: Columns not found. Found: {df.columns}")

    except Exception as e:
        logging.error(f"Failed to clean codes_postaux: {e}")
        logger.log_step("clean_codes_postaux", "ERROR", {"error": str(e)})
        raise PipelineRunError(f"clean_codes_postaux failed: {e}") from e


def clean_odace_gares(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Fetches and cleans Gare data from Odace API (Bypassed in favor of BPE25)."""
    logger.log_step("clean_odace_gares", "SKIPPED")
