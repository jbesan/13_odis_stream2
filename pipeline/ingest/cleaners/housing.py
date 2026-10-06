"""Housing and rent cleaners (LOVAC, RPLS, housing occupation, rents, social delay)."""

from __future__ import annotations

import logging
from typing import Any, Dict

import numpy as np
import pandas as pd

from pipeline.common import PipelineLogger
from pipeline.ingest.config import get_domain_contracts
from pipeline.ingest import context
from pipeline.run_context import PipelineRunError


def clean_lovac(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans LOVAC and saves to parquet."""
    import pipeline.ingest as ingest

    logger.log_step("clean_lovac", "STARTED")
    source = config["sources"]["logement_vacant"]

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_logement_vacant")
            )
            if not df_odace.empty:
                rename_dict = {
                    "commune_insee_code": "codgeo",
                    "nb_logements_vacants_2ans": "pp_vacant_plus_2ans_25",
                    "nb_logements_prives_total": "log_priv_total_24",
                }
                df_out = df_odace.rename(columns=rename_dict)
                if "codgeo" not in df_out.columns:
                    raise ValueError("LOVAC source is missing commune_insee_code")
                for col in ["pp_vacant_plus_2ans_25", "log_priv_total_24"]:
                    if col not in df_out.columns:
                        df_out[col] = np.nan
                df_out = df_out[
                    ["codgeo", "pp_vacant_plus_2ans_25", "log_priv_total_24"]
                ].copy()
                df_out["codgeo"] = df_out["codgeo"].astype(str).str.zfill(5)
                df_out["pp_vacant_plus_2ans_25"] = pd.to_numeric(
                    df_out["pp_vacant_plus_2ans_25"], errors="coerce"
                )
                df_out["log_priv_total_24"] = pd.to_numeric(
                    df_out["log_priv_total_24"], errors="coerce"
                )

                output_path = context.CLEAN_DIR / "lovac.parquet"
                df_out.to_parquet(output_path)
                logger.log_step(
                    "clean_lovac", "COMPLETED", {"rows": len(df_out), "source": "odace"}
                )
                return
            else:
                logging.warning("Odace fetch returned empty data for logement_vacant.")
        except Exception as e:
            logging.error(f"Failed to fetch logement_vacant from Odace: {e}")
        raise PipelineRunError("Odace logement_vacant did not produce a usable dataset")


def clean_rpls(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans RPLS and saves to parquet."""
    import pipeline.ingest as ingest

    logger.log_step("clean_rpls", "STARTED")
    source = config["sources"]["logement_social"]

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_logement_social_rpls")
            )
            if not df_odace.empty:
                if "annee" in df_odace.columns:
                    max_year = df_odace["annee"].max()
                    logging.info(f"Odace RPLS: Filtering for max year {max_year}")
                    df_odace = df_odace[df_odace["annee"] == max_year]
                df_commune = client.fetch_dim_commune(source["ttl_days"])
                if not df_commune.empty:
                    merged = df_odace.merge(
                        df_commune[["commune_sk", "commune_insee_code"]],
                        on="commune_sk",
                        how="inner",
                    )
                    rename_dict = {
                        "commune_insee_code": "codgeo",
                        "nb_logements_total_sociaux": "log_soc_total",
                        "nb_logements_vacants": "log_soc_inoccupes",
                    }
                    df_out = merged.rename(columns=rename_dict)
                    if "codgeo" not in df_out.columns:
                        raise ValueError("RPLS source is missing commune_insee_code")
                    for col in ["log_soc_total", "log_soc_inoccupes"]:
                        if col not in df_out.columns:
                            df_out[col] = np.nan
                    df_out = df_out[
                        ["codgeo", "log_soc_total", "log_soc_inoccupes"]
                    ].copy()
                    df_out["codgeo"] = df_out["codgeo"].astype(str).str.zfill(5)
                    df_out["log_soc_total"] = pd.to_numeric(
                        df_out["log_soc_total"], errors="coerce"
                    )
                    df_out["log_soc_inoccupes"] = pd.to_numeric(
                        df_out["log_soc_inoccupes"], errors="coerce"
                    )

                    output_path = context.CLEAN_DIR / "rpls.parquet"
                    df_out.to_parquet(output_path)
                    logger.log_step(
                        "clean_rpls",
                        "COMPLETED",
                        {"rows": len(df_out), "source": "odace"},
                    )
                    return
                else:
                    logging.warning("Odace dim_commune is empty for RPLS.")
            else:
                logging.warning("Odace fetch returned empty data for RPLS.")
        except Exception as e:
            logging.error(f"Failed to fetch RPLS from Odace: {e}")
        raise PipelineRunError("Odace logement_social did not produce a usable dataset")


def transform_housing_occupation_odace(
    df_odace: pd.DataFrame, contract: Dict[str, Any]
) -> pd.DataFrame:
    """Validate and pivot the canonical Odace housing-occupation fact table."""
    required_columns = contract["required_columns"]
    missing_columns = sorted(set(required_columns) - set(df_odace.columns))
    if missing_columns:
        raise PipelineRunError(
            "Odace fact_occupation_logement is missing required columns: "
            f"{missing_columns}"
        )

    df = df_odace.copy()
    primary_key = contract["primary_key"]
    for column in primary_key:
        if df[column].isna().any():
            raise PipelineRunError(
                f"Odace fact_occupation_logement has null primary-key values in '{column}'"
            )

    commune_codes = df["commune_insee_code"].astype(str).str.strip()
    df["annee"] = df["annee"].astype(str).str.strip()
    df["indicateur_occupation"] = df["indicateur_occupation"].astype(str).str.strip()
    if (commune_codes == "").any() or (df["indicateur_occupation"] == "").any():
        raise PipelineRunError(
            "Odace fact_occupation_logement has blank commune or indicator identifiers"
        )
    df["commune_insee_code"] = commune_codes.str.zfill(5)
    if df.duplicated(primary_key).any():
        duplicate_count = int(df.duplicated(primary_key, keep=False).sum())
        raise PipelineRunError(
            "Odace fact_occupation_logement violates its primary key "
            f"{primary_key} ({duplicate_count} duplicate rows)"
        )

    df["valeur"] = pd.to_numeric(df["valeur"], errors="coerce")
    value_contract = contract["value"]
    if not value_contract.get("nullable", True) and df["valeur"].isna().any():
        raise PipelineRunError(
            "Odace fact_occupation_logement has null or invalid values"
        )
    minimum_value = value_contract.get("minimum")
    if minimum_value is not None and (df["valeur"] < minimum_value).any():
        raise PipelineRunError(
            "Odace fact_occupation_logement has values below its contractual minimum "
            f"({minimum_value})"
        )

    reference_year = str(contract["reference_year"])
    required_indicators = contract["required_indicators"]
    reference = df.loc[
        (df["annee"] == reference_year)
        & df["indicateur_occupation"].isin(required_indicators)
    ].copy()
    if reference.empty:
        raise PipelineRunError(
            "Odace fact_occupation_logement has no required observations for "
            f"reference year {reference_year}"
        )

    observed_indicators = set(reference["indicateur_occupation"])
    missing_indicators = sorted(set(required_indicators) - observed_indicators)
    if missing_indicators:
        raise PipelineRunError(
            "Odace fact_occupation_logement is missing required indicators for "
            f"{reference_year}: {missing_indicators}"
        )

    minimum_per_indicator = contract["minimum_communes_per_indicator"]
    indicator_coverage = reference.groupby("indicateur_occupation")[
        "commune_insee_code"
    ].nunique()
    insufficient_indicators = {
        indicator: int(indicator_coverage.get(indicator, 0))
        for indicator in required_indicators
        if indicator_coverage.get(indicator, 0) < minimum_per_indicator
    }
    if insufficient_indicators:
        raise PipelineRunError(
            "Odace fact_occupation_logement has insufficient commune coverage per "
            f"indicator: {insufficient_indicators}"
        )

    df_out = (
        reference.pivot(
            index="commune_insee_code",
            columns="indicateur_occupation",
            values="valeur",
        )
        .reindex(columns=required_indicators)
        .reset_index()
        .rename(columns={"commune_insee_code": "codgeo"})
    )
    if len(df_out) < contract["minimum_communes"]:
        raise PipelineRunError(
            "Odace fact_occupation_logement has insufficient commune coverage: "
            f"{len(df_out)} < {contract['minimum_communes']}"
        )
    if (
        contract.get("require_complete_indicator_set_per_commune", False)
        and df_out[required_indicators].isna().any().any()
    ):
        incomplete_count = int(df_out[required_indicators].isna().any(axis=1).sum())
        raise PipelineRunError(
            "Odace fact_occupation_logement has incomplete indicator families for "
            f"{incomplete_count} communes"
        )
    if df_out["codgeo"].duplicated().any():
        raise PipelineRunError("Housing occupation output has duplicate codgeo values")
    return df_out


def clean_housing_occupation(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Build the canonical occupancy-count dataset from the Odace fact table."""
    import pipeline.ingest as ingest

    logger.log_step("clean_housing_occupation", "STARTED")
    source = config["sources"]["housing_occupation"]
    if not source.get("use_odace", False):
        raise PipelineRunError("housing_occupation must be served by Odace")

    try:
        contract = ingest._load_source_contract("housing_occupation")
        if source.get("odace_table") != contract["odace_table"]:
            raise PipelineRunError(
                "housing_occupation source and contract disagree on the Odace table"
            )
        client = ingest.get_odace_client(logger)
        df_odace = client.fetch_table(contract["odace_table"])
        if df_odace.empty:
            raise PipelineRunError("Odace fact_occupation_logement returned no rows")
        df_out = transform_housing_occupation_odace(df_odace, contract)

        output_path = context.CLEAN_DIR / "housing_occupation.parquet"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df_out.to_parquet(output_path)
        logger.log_step(
            "clean_housing_occupation",
            "COMPLETED",
            {
                "path": str(output_path),
                "rows": len(df_out),
                "source": "odace",
                "contract_version": contract["version"],
                "reference_year": contract["reference_year"],
            },
        )
    except Exception as exc:
        logging.error("Failed to build housing_occupation from Odace: %s", exc)
        logger.log_step("clean_housing_occupation", "ERROR", {"error": str(exc)})
        raise PipelineRunError(
            "Odace housing_occupation did not produce a contract-compliant dataset"
        ) from exc


def clean_loyers(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Loyers data (Appartements) and saves to parquet."""
    logger.log_step("clean_loyers", "STARTED")
    source = config["sources"]["loyers_apparts"]
    path = context.CACHE_DIR / source["local_name"]

    if not path.exists():
        return

    sep = source.get("sep", ";")
    encoding = source.get("encoding", "utf-8")

    df = pd.read_csv(path, sep=sep, encoding=encoding, dtype={"INSEE_C": str})

    if "INSEE_C" in df.columns:
        df.rename(columns={"INSEE_C": "codgeo"}, inplace=True)

    if "codgeo" not in df.columns:
        codgeo_col = next((c for c in df.columns if "INSEE" in c or "COD" in c), None)
        if codgeo_col:
            df.rename(columns={codgeo_col: "codgeo"}, inplace=True)

    if "codgeo" not in df.columns:
        logging.warning(f"Loyers: CODGEO not found. Found: {df.columns}")
        return

    df["codgeo"] = df["codgeo"].astype(str).str.zfill(5)

    val_col = "loypredm2"
    if val_col not in df.columns:
        logging.warning(f"Loyers: {val_col} not found. Found: {df.columns}")
        return

    df[val_col] = pd.to_numeric(
        df[val_col].astype(str).str.replace(",", "."), errors="coerce"
    )

    df_out = df[["codgeo", val_col]].rename(columns={val_col: "loyer_app_m2"})
    df_out = df_out.groupby("codgeo")["loyer_app_m2"].mean().reset_index()

    output_path = context.CLEAN_DIR / "loyers.parquet"
    df_out.to_parquet(output_path)
    logger.log_step("clean_loyers", "COMPLETED", {"path": str(output_path)})


def clean_odace_rent(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Fetches and cleans Rent data from Odace API."""
    import pipeline.ingest as ingest

    logger.log_step("clean_odace_rent", "STARTED")

    client = ingest.get_odace_client(logger)

    odace_tables = config["sources"]["loyers_apparts"]["odace_tables"]
    df_rent = client.fetch_fact_loyer_annonce(
        odace_tables["fact_loyer_annonce"]["ttl_days"]
    )
    df_profil = client.fetch_ref_logement_profil(
        odace_tables["ref_logement_profil"]["ttl_days"]
    )

    if df_rent.empty or df_profil.empty:
        error_msg = (
            f"CRITICAL ERROR: Odace API returned empty data for rent facts (empty={df_rent.empty}) "
            f"or housing profiles (empty={df_profil.empty})!"
        )
        logging.error(error_msg)
        print(f"ERROR [clean_odace_rent]: {error_msg}")
        logger.log_step("clean_odace_rent", "ERROR", error_msg)
        raise PipelineRunError(error_msg)

    domain_cfg = get_domain_contracts()
    if "maille_observation" in df_rent.columns:
        priority_map = domain_cfg.housing.odace_rent_priority
        default_priority = domain_cfg.housing.default_rent_priority
        df_rent["maille_priority"] = (
            df_rent["maille_observation"].map(priority_map).fillna(default_priority)
        )

        df_rent = df_rent.sort_values(
            ["commune_sk", "logement_profil_sk", "maille_priority"]
        )
        df_rent = df_rent.drop_duplicates(subset=["commune_sk", "logement_profil_sk"])

        logging.info(
            f"Odace Rent: Filtered and deduplicated. {len(df_rent)} rows remaining."
        )
        df_rent = df_rent.drop(columns=["maille_priority"])

    df_rent.to_parquet(context.CLEAN_DIR / "odace_loyer_annonce.parquet", index=False)
    df_profil.to_parquet(
        context.CLEAN_DIR / "odace_logement_profil.parquet", index=False
    )

    logger.log_step(
        "clean_odace_rent",
        "COMPLETED",
        {"rent_rows": len(df_rent), "profil_rows": len(df_profil)},
    )


def clean_log_soc_delay(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans USH Housing Delay and saves to parquet."""
    import pipeline.ingest as ingest

    logger.log_step("clean_log_soc_delay", "STARTED")
    source = config["sources"]["logement_social_delay"]
    output_path = context.CLEAN_DIR / "log_soc_delay.parquet"

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_delai_attribution_logement")
            )
            if not df_odace.empty:
                df_odace = df_odace.dropna(subset=["delai_attribution_moyen_mois"])

                df_odace = df_odace[["siret", "delai_attribution_moyen_mois"]].rename(
                    columns={
                        "siret": "epci_code",
                        "delai_attribution_moyen_mois": "log_soc_delay",
                    }
                )
                df_odace["epci_code"] = (
                    df_odace["epci_code"].astype(str).str.strip().str.zfill(9)
                )
                df_odace["log_soc_delay"] = pd.to_numeric(
                    df_odace["log_soc_delay"], errors="coerce"
                )

                df_clean = (
                    df_odace.groupby("epci_code")["log_soc_delay"].mean().reset_index()
                )

                output_path.parent.mkdir(parents=True, exist_ok=True)
                df_clean.to_parquet(output_path)
                logger.log_step(
                    "clean_log_soc_delay",
                    "COMPLETED",
                    {"rows": len(df_clean), "source": "odace"},
                )
                return
            else:
                logging.warning(
                    "Odace fetch returned empty data for logement_social_delay."
                )
        except Exception as e:
            logging.error(f"Failed to fetch logement_social_delay from Odace: {e}")
        raise PipelineRunError(
            "Odace logement_social_delay did not produce a usable dataset"
        )
