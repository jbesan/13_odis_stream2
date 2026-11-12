"""Geographic referential cleaners (communes, departements, postal codes, gares)."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

import numpy as np
import pandas as pd
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
                epci_cfg = config.get("sources", {}).get("ref_epci", {})
                commune_epci_cfg = config.get("sources", {}).get("ref_commune_epci", {})
                epci_map = {}

                use_odace_epci = epci_cfg.get(
                    "use_odace", False
                ) or commune_epci_cfg.get("use_odace", False)
                if use_odace_epci:
                    try:
                        commune_epci_table = commune_epci_cfg.get(
                            "odace_table", "ref_commune_epci"
                        )
                        ttl = commune_epci_cfg.get(
                            "ttl_days", epci_cfg.get("ttl_days", 90)
                        )
                        df_commune_epci = client.fetch_table(
                            commune_epci_table, ttl_days=ttl
                        )
                        if (
                            not df_commune_epci.empty
                            and "commune_insee_code" in df_commune_epci.columns
                            and "code_epci" in df_commune_epci.columns
                        ):
                            epci_map = dict(
                                zip(
                                    df_commune_epci["commune_insee_code"]
                                    .astype(str)
                                    .str.zfill(5),
                                    df_commune_epci["code_epci"]
                                    .astype(str)
                                    .str.zfill(9),
                                )
                            )
                            # Also cache ref_epci for downstream consumers (CTAI, ANVITA)
                            ref_epci_table = epci_cfg.get("odace_table", "ref_epci")
                            client.fetch_table(
                                ref_epci_table,
                                ttl_days=epci_cfg.get("ttl_days", 90),
                            )
                    except Exception as e:
                        logging.warning(
                            f"Error fetching EPCI from ODAS: {e}. Attempting fallback..."
                        )

                if not epci_map:
                    fallback_epci_cfg = {**epci_cfg, "use_odace": False}
                    epci_path = ingest.fetch_source(
                        "ref_epci", fallback_epci_cfg, logger
                    )
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
                    if use_odace_epci and not epci_map:
                        raise ValueError(
                            "Failed to build EPCI mapping from ODAS and fallback"
                        )

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
    """Cleans Departements referential and saves to parquet.

    Args:
        config: Pipeline configuration dictionary.
        logger: Pipeline logger instance.

    Raises:
        PipelineRunError: If ODAS fetch or legacy clean fails.
    """
    import pipeline.ingest as ingest

    logger.log_step("clean_departements", "STARTED")
    source = config.get("sources", {}).get("departements_ref") or config.get(
        "sources", {}
    ).get("departements", {})

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(source.get("odace_table", "ref_departement"))
            if not df_odace.empty and "code_departement" in df_odace.columns:
                df_out = pd.DataFrame()
                df_out["code"] = ingest.normalize_code(df_odace["code_departement"], 2)
                df_out["label"] = (
                    df_odace["libelle_departement"].astype(str).str.strip()
                    if "libelle_departement" in df_odace.columns
                    else pd.Series("", index=df_odace.index)
                )
                if "code_region" in df_odace.columns:
                    df_out["reg_code"] = ingest.normalize_code(
                        df_odace["code_region"], 2
                    )

                df_out = df_out.dropna(subset=["code", "label"]).drop_duplicates(
                    subset=["code"]
                )
                if not df_out.empty:
                    output_path = context.CLEAN_DIR / "departements.parquet"
                    df_out.to_parquet(output_path)
                    logger.log_step(
                        "clean_departements",
                        "COMPLETED",
                        {
                            "path": str(output_path),
                            "rows": len(df_out),
                            "source": "odace",
                        },
                    )
                    return
            else:
                logging.warning(
                    "Odace fetch returned empty or invalid data for ref_departement."
                )
        except Exception as e:
            logging.error(f"Failed to fetch ref_departement from Odace: {e}")
        raise PipelineRunError("Odace departements did not produce a usable dataset")

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
        df_out["code"] = ingest.normalize_code(df_out["code"], 2)
        df_out["label"] = df_out["label"].astype(str).str.strip()
        if "reg_code" in df_out.columns:
            df_out["reg_code"] = ingest.normalize_code(df_out["reg_code"], 2)

        df_out = df_out.dropna(subset=["code", "label"]).drop_duplicates(
            subset=["code"]
        )
        output_path = context.CLEAN_DIR / "departements.parquet"
        df_out.to_parquet(output_path)
        logger.log_step(
            "clean_departements",
            "COMPLETED",
            {"path": str(output_path), "rows": len(df_out)},
        )
    else:
        logging.warning(f"Departements: Columns not found. Found: {df.columns}")
        logger.log_step("clean_departements", "FAILED", {"reason": "Columns not found"})


def clean_regions(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Regions referential and saves to parquet.

    Args:
        config: Pipeline configuration dictionary.
        logger: Pipeline logger instance.

    Raises:
        PipelineRunError: If ODAS fetch or legacy clean fails.
    """
    import pipeline.ingest as ingest

    logger.log_step("clean_regions", "STARTED")
    source = config.get("sources", {}).get("regions_ref") or config.get(
        "sources", {}
    ).get("regions", {})

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(source.get("odace_table", "ref_region"))
            if not df_odace.empty and "code_region" in df_odace.columns:
                df_out = pd.DataFrame()
                df_out["code"] = ingest.normalize_code(df_odace["code_region"], 2)
                df_out["label"] = (
                    df_odace["libelle_region"].astype(str).str.strip()
                    if "libelle_region" in df_odace.columns
                    else pd.Series("", index=df_odace.index)
                )

                df_out = df_out.dropna(subset=["code", "label"]).drop_duplicates(
                    subset=["code"]
                )
                if not df_out.empty:
                    output_path = context.CLEAN_DIR / "regions.parquet"
                    df_out.to_parquet(output_path)
                    logger.log_step(
                        "clean_regions",
                        "COMPLETED",
                        {
                            "path": str(output_path),
                            "rows": len(df_out),
                            "source": "odace",
                        },
                    )
                    return
            else:
                logging.warning(
                    "Odace fetch returned empty or invalid data for ref_region."
                )
        except Exception as e:
            logging.error(f"Failed to fetch ref_region from Odace: {e}")
        raise PipelineRunError("Odace regions did not produce a usable dataset")

    path = context.CACHE_DIR / source.get("local_name", "referentiel_regions.json")
    if not path.exists():
        return

    try:
        df = ingest.load_dataset(path, source)
        code_col = "REG" if "REG" in df.columns else "code"
        label_col = "LIBELLE" if "LIBELLE" in df.columns else "label"

        if code_col in df.columns and label_col in df.columns:
            df_out = pd.DataFrame()
            df_out["code"] = ingest.normalize_code(df[code_col], 2)
            df_out["label"] = df[label_col].astype(str).str.strip()
            df_out = df_out.dropna(subset=["code", "label"]).drop_duplicates(
                subset=["code"]
            )

            output_path = context.CLEAN_DIR / "regions.parquet"
            df_out.to_parquet(output_path)
            logger.log_step(
                "clean_regions",
                "COMPLETED",
                {"path": str(output_path), "rows": len(df_out)},
            )
        else:
            logging.warning(f"Regions: Columns not found. Found: {df.columns}")
            logger.log_step("clean_regions", "FAILED", {"reason": "Columns not found"})
            raise PipelineRunError("Regions: required columns not found in raw dataset")
    except Exception as e:
        logger.log_step("clean_regions", "ERROR", {"error": str(e)})
        logging.error(f"Regions clean failed: {e}")
        raise PipelineRunError(f"clean_regions failed: {e}") from e


def clean_codes_postaux(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Codes Postaux and saves to parquet.

    Args:
        config: Pipeline configuration dictionary.
        logger: Pipeline logger instance.

    Raises:
        PipelineRunError: If ODAS fetch or legacy clean fails.
    """
    import pipeline.ingest as ingest

    logger.log_step("clean_codes_postaux", "STARTED")
    source = config.get("sources", {}).get("codes_postaux", {})

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(source.get("odace_table", "ref_code_postal"))
            if (
                not df_odace.empty
                and "code_postal" in df_odace.columns
                and "commune_insee_code" in df_odace.columns
            ):
                df_out = pd.DataFrame()
                df_out["code_postal"] = ingest.normalize_code(
                    df_odace["code_postal"], 5
                )
                df_out["codgeo"] = ingest.normalize_code(
                    df_odace["commune_insee_code"], 5
                )
                df_out = df_out.dropna(
                    subset=["code_postal", "codgeo"]
                ).drop_duplicates()

                if not df_out.empty:
                    output_path = context.CLEAN_DIR / "codes_postaux.parquet"
                    df_out.to_parquet(output_path)
                    logger.log_step(
                        "clean_codes_postaux",
                        "COMPLETED",
                        {
                            "path": str(output_path),
                            "rows": len(df_out),
                            "source": "odace",
                        },
                    )
                    return
            else:
                logging.warning(
                    "Odace fetch returned empty or invalid data for ref_code_postal."
                )
        except Exception as e:
            logging.error(f"Failed to fetch ref_code_postal from Odace: {e}")
        raise PipelineRunError("Odace codes_postaux did not produce a usable dataset")

    try:
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
            df_out = pd.DataFrame()
            df_out["code_postal"] = ingest.normalize_code(df[cp_col], 5)
            df_out["codgeo"] = ingest.normalize_code(df[insee_col], 5)
            df_out = df_out.dropna(subset=["code_postal", "codgeo"]).drop_duplicates()

            output_path = context.CLEAN_DIR / "codes_postaux.parquet"
            df_out.to_parquet(output_path)
            logger.log_step(
                "clean_codes_postaux", "COMPLETED", {"path": str(output_path)}
            )
        else:
            logging.warning(f"Codes Postaux: Columns not found. Found: {df.columns}")
            logger.log_step(
                "clean_codes_postaux", "FAILED", {"reason": "Columns not found"}
            )

    except Exception as e:
        logging.error(f"Failed to clean codes_postaux: {e}")
        logger.log_step("clean_codes_postaux", "ERROR", {"error": str(e)})
        raise PipelineRunError(f"clean_codes_postaux failed: {e}") from e


def clean_odace_gares(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Fetches and cleans Gare data from Odace API (Bypassed in favor of BPE25)."""
    logger.log_step("clean_odace_gares", "SKIPPED")


def clean_bassins_de_vie(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Bassins de Vie referential and saves to parquet.

    Args:
        config: Pipeline configuration dictionary.
        logger: Pipeline logger instance.

    Raises:
        PipelineRunError: If Odace fetch or cleaning fails.
    """
    import pipeline.ingest as ingest

    logger.log_step("clean_bassins_de_vie", "STARTED")
    source = config["sources"]["bassins_de_vie"]
    output_path = context.CLEAN_DIR / "bassins_de_vie.parquet"

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "ref_bassin_vie"),
                ttl_days=source.get("ttl_days", 365),
            )
            if not df_odace.empty:
                cols_dict = {
                    "codgeo": ingest.normalize_code(df_odace["commune_insee_code"], 5),
                    "bassin_de_vie": ingest.normalize_code(
                        df_odace["code_bassin_vie"], 5
                    ),
                    "libelle_bassin_de_vie": df_odace["libelle_bassin_vie"]
                    .astype(str)
                    .str.strip(),
                }
                if "commune_sk" in df_odace.columns:
                    cols_dict["commune_sk"] = df_odace["commune_sk"]

                df_out = (
                    pd.DataFrame(cols_dict)
                    .dropna(subset=["codgeo", "bassin_de_vie"])
                    .drop_duplicates(subset=["codgeo"])
                )

                if df_out.empty:
                    raise PipelineRunError("Cleaned bassins_de_vie dataset is empty")

                df_out.to_parquet(output_path)
                logger.log_step(
                    "clean_bassins_de_vie",
                    "COMPLETED",
                    {"path": str(output_path), "rows": len(df_out), "source": "odace"},
                )
                return
            else:
                logging.warning("Odace fetch returned empty data for ref_bassin_vie.")
        except Exception as e:
            logging.error(f"Failed to fetch ref_bassin_vie from Odace: {e}")
            raise PipelineRunError(
                f"clean_bassins_de_vie failed with Odace: {e}"
            ) from e

    # Legacy raw fallback
    archive_file = source.get("archive_file")
    raw_path = context.CACHE_DIR / archive_file if archive_file else None
    if raw_path and raw_path.exists():
        try:
            df_raw = ingest.load_dataset(raw_path, source)
            df_raw = df_raw.rename(
                columns={
                    "Code géographique": "codgeo",
                    "Bassin de vie 2022": "bassin_de_vie",
                    "Libellé géographique du bassin de vie 2022": "libelle_bassin_de_vie",
                }
            )
            if "codgeo" in df_raw.columns and "bassin_de_vie" in df_raw.columns:
                df_out = (
                    pd.DataFrame(
                        {
                            "codgeo": ingest.normalize_code(df_raw["codgeo"], 5),
                            "bassin_de_vie": ingest.normalize_code(
                                df_raw["bassin_de_vie"], 5
                            ),
                            "libelle_bassin_de_vie": df_raw.get(
                                "libelle_bassin_de_vie",
                                pd.Series("", index=df_raw.index),
                            )
                            .astype(str)
                            .str.strip(),
                        }
                    )
                    .dropna(subset=["codgeo", "bassin_de_vie"])
                    .drop_duplicates(subset=["codgeo"])
                )

                if df_out.empty:
                    raise PipelineRunError("Raw bassins_de_vie produced no valid rows")

                df_out.to_parquet(output_path)
                logger.log_step(
                    "clean_bassins_de_vie",
                    "COMPLETED",
                    {"path": str(output_path), "rows": len(df_out), "source": "raw"},
                )
                return
        except Exception as e:
            logging.error(f"Failed to clean raw bassins_de_vie: {e}")
            raise PipelineRunError(
                f"clean_bassins_de_vie raw fallback failed: {e}"
            ) from e

    logging.warning("Bassins de vie source file not found and Odace disabled.")
    logger.log_step("clean_bassins_de_vie", "SKIPPED", {"reason": "Source not found"})
