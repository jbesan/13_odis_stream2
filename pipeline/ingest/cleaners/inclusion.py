"""Inclusion cleaners (services, structures, associations, WALDEC, CAF)."""

from __future__ import annotations

import ast
import logging
import re
from typing import Any, Dict

import numpy as np
import pandas as pd

from pipeline.common import PipelineLogger
from pipeline.ingest.config import get_domain_contracts
from pipeline.ingest import context
from pipeline.run_context import PipelineRunError


def clean_caf(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans CAF and saves to parquet."""
    import pipeline.ingest as ingest

    logger.log_step("clean_caf", "STARTED")
    source = config["sources"]["caf"]

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_couverture_petite_enfance")
            )
            if not df_odace.empty:
                df_odace = df_odace.rename(
                    columns={
                        "commune_insee_code": "codgeo",
                        "taux_couverture_commune": "taux_couverture",
                    }
                )
                df_odace["codgeo"] = df_odace["codgeo"].astype(str).str.zfill(5)

                if "annee" in df_odace.columns:
                    max_year = df_odace["annee"].max()
                    df_odace = df_odace[df_odace["annee"] == max_year]

                df_out = df_odace[["codgeo", "taux_couverture"]].copy()
                df_out["taux_couverture"] = pd.to_numeric(
                    df_out["taux_couverture"], errors="coerce"
                )

                output_path = context.CLEAN_DIR / "caf.parquet"
                df_out.to_parquet(output_path)
                logger.log_step(
                    "clean_caf",
                    "COMPLETED",
                    {"path": str(output_path), "rows": len(df_out), "source": "odace"},
                )
                return
            else:
                logging.warning(
                    "Odace fetch returned empty data for fact_couverture_petite_enfance."
                )
        except Exception as e:
            logging.error(
                f"Failed to fetch fact_couverture_petite_enfance from Odace: {e}"
            )
        raise PipelineRunError("Odace caf did not produce a usable dataset")


def parse_thematiques(val: Any) -> list[str]:
    """Parses thematiques string or list into flat list of slugs."""
    try:
        raw_extracted = []
        if isinstance(val, str):
            val = val.strip()
            if val.startswith("[") and val.endswith("]"):
                if re.search(r"'\s+'", val) and "," not in val:
                    raw_extracted = re.findall(r"'([^']*)'", val)
                else:
                    try:
                        raw_extracted = ast.literal_eval(val)
                    except (ValueError, SyntaxError):
                        raw_extracted = re.findall(r"'([^']*)'", val) or [val]
            else:
                raw_extracted = [val]
        elif isinstance(val, list):
            raw_extracted = val
        elif hasattr(val, "tolist"):
            raw_extracted = val.tolist()

        flat_list: list[str] = []

        def flatten(x: Any) -> None:
            if isinstance(x, (list, tuple, np.ndarray)):
                for item in x:
                    flatten(item)
            elif x is not None:
                flat_list.append(str(x))

        flatten(raw_extracted)
        return flat_list
    except Exception as e:
        logging.warning(f"Error parsing thematiques: {val} -> {e}")
        return []


def clean_slug(val: Any) -> str:
    """Strips outer bracket quotes if present."""
    val = str(val).strip()
    if val.startswith("['") and val.endswith("']"):
        return val[2:-2]
    return val


def parse_reseaux(val: Any) -> list[str]:
    """Parses reseaux_porteurs string or list."""
    try:
        if hasattr(val, "tolist"):
            val = val.tolist()
        if pd.isna(val):
            return []
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            val = val.strip()
            if val.lower() == "none" or not val:
                return []
            if val.startswith("[") and val.endswith("]"):
                if re.search(r"'\s+'", val) and "," not in val:
                    return re.findall(r"'([^']*)'", val)
                try:
                    return ast.literal_eval(val)
                except (ValueError, SyntaxError):
                    return re.findall(r"'([^']*)'", val) or [val]
            return [val]
        return []
    except Exception as exc:
        logging.debug(f"Failed to parse reseaux_porteurs '{val}': {exc}")
        return []


def clean_services_inclusion(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Inclusion (Services) and saves to parquet (one row per service).

    Args:
        config: Pipeline configuration dictionary.
        logger: Pipeline logger instance.

    Raises:
        PipelineRunError: If ODAS fetch fails or returns unusable data.
    """
    import pipeline.ingest as ingest

    logger.log_step("clean_services_inclusion", "STARTED")
    source = config.get("sources", {}).get("services_inclusion", {})

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "dim_service_inclusion")
            )
            id_col = next((c for c in ["service_id", "id"] if c in df_odace.columns), None)
            nom_col = next((c for c in ["service_nom", "nom"] if c in df_odace.columns), None)
            commune_col = next(
                (c for c in ["commune_insee_code", "code_insee", "codgeo"] if c in df_odace.columns),
                None,
            )

            if (
                not df_odace.empty
                and id_col
                and nom_col
                and commune_col
                and "thematiques" in df_odace.columns
                and "latitude" in df_odace.columns
                and "longitude" in df_odace.columns
            ):
                df_clean = df_odace.dropna(subset=["latitude", "longitude", "thematiques"]).copy()
                df_clean["thematique_list"] = df_clean["thematiques"].apply(parse_thematiques)
                df_exploded = df_clean.explode("thematique_list").dropna(subset=["thematique_list"])
                df_exploded["service_slug"] = df_exploded["thematique_list"].apply(clean_slug)

                df_out = pd.DataFrame(
                    {
                        "id_structure": df_exploded[id_col],
                        "nom": df_exploded[nom_col],
                        "service_slug": df_exploded["service_slug"],
                        "latitude": df_exploded["latitude"],
                        "longitude": df_exploded["longitude"],
                        "codgeo": ingest.normalize_code(df_exploded[commune_col], 5),
                    }
                ).dropna(subset=["codgeo", "service_slug"])

                if not df_out.empty:
                    output_path = context.CLEAN_DIR / "services_inclusion.parquet"
                    df_out.to_parquet(output_path)
                    logger.log_step(
                        "clean_services_inclusion",
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
                    "Odace fetch returned empty or invalid data for dim_service_inclusion."
                )
        except Exception as e:
            logging.error(f"Failed to fetch dim_service_inclusion from Odace: {e}")
        raise PipelineRunError("Odace services_inclusion did not produce a usable dataset")

    # Legacy fallback
    path = context.CACHE_DIR / source.get("local_name", "services_inclusion.parquet")
    if not path.exists():
        return

    df = ingest.load_dataset(path, source)
    df.columns = [c.strip() for c in df.columns]

    required_cols = ["id", "nom", "thematiques", "latitude", "longitude", "code_insee"]
    if not all(col in df.columns for col in required_cols):
        logging.warning(f"Services Inclusion: Missing columns. Found: {df.columns}")
        return

    df = df.dropna(subset=["latitude", "longitude", "thematiques"])

    df["thematique_list"] = df["thematiques"].apply(parse_thematiques)

    df_exploded = df.explode("thematique_list")
    df_exploded = df_exploded.dropna(subset=["thematique_list"])

    df_exploded["service_slug"] = df_exploded["thematique_list"].apply(clean_slug)

    df_out = df_exploded[
        ["id", "nom", "service_slug", "latitude", "longitude", "code_insee"]
    ].rename(columns={"id": "id_structure", "code_insee": "codgeo"})

    df_out["codgeo_numeric"] = pd.to_numeric(df_out["codgeo"], errors="coerce")
    df_out = df_out.dropna(subset=["codgeo_numeric"])
    df_out["codgeo"] = df_out["codgeo_numeric"].astype(int).astype(str).str.zfill(5)
    df_out = df_out.drop(columns=["codgeo_numeric"])

    output_path = context.CLEAN_DIR / "services_inclusion.parquet"
    df_out.to_parquet(output_path)
    logger.log_step(
        "clean_services_inclusion",
        "COMPLETED",
        {"path": str(output_path), "rows": len(df_out)},
    )


def clean_structures_inclusion(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Inclusion (Structures) and saves to parquet (one row per structure).

    Args:
        config: Pipeline configuration dictionary.
        logger: Pipeline logger instance.

    Raises:
        PipelineRunError: If ODAS fetch fails or returns unusable data.
    """
    import pipeline.ingest as ingest

    logger.log_step("clean_structures_inclusion", "STARTED")
    source = config.get("local_files", {}).get("structures_inclusion")
    if not source:
        source = config.get("sources", {}).get("structures_inclusion", {})

    domain_cfg = get_domain_contracts()
    inclusion_cfg = domain_cfg.inclusion

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "dim_structure_inclusion")
            )
            if not df_odace.empty and "reseaux_porteurs" in df_odace.columns:
                df_odace["reseaux_parsed"] = df_odace["reseaux_porteurs"].apply(parse_reseaux)
                ccas_keywords = inclusion_cfg.ccas_keywords

                def has_ccas(lst: list[str]) -> bool:
                    if not lst:
                        return False
                    for item in lst:
                        if isinstance(item, str):
                            s = item.lower()
                            if any(kw in s for kw in ccas_keywords):
                                return True
                    return False

                df_filtered = df_odace[df_odace["reseaux_parsed"].apply(has_ccas)].copy()

                for field in inclusion_cfg.required_contact_fields:
                    if field in df_filtered.columns:
                        df_filtered[field] = (
                            df_filtered[field]
                            .replace("", np.nan)
                            .replace("None", np.nan)
                        )
                    else:
                        df_filtered[field] = np.nan

                df_filtered = df_filtered.dropna(
                    subset=inclusion_cfg.required_contact_fields, how="all"
                )

                codgeo_col = next(
                    (c for c in ["commune_insee_code", "code_insee", "codgeo"] if c in df_filtered.columns),
                    None,
                )
                if codgeo_col:
                    df_filtered["codgeo"] = ingest.normalize_code(df_filtered[codgeo_col], 5)
                    df_filtered = df_filtered.dropna(subset=["codgeo"])

                    col_map = {
                        "structure_id": "id",
                        "structure_nom": "nom",
                        "commune_label": "commune",
                    }
                    for old_c, new_c in col_map.items():
                        if old_c in df_filtered.columns and new_c not in df_filtered.columns:
                            df_filtered[new_c] = df_filtered[old_c]

                    cols_to_keep = [
                        "id",
                        "nom",
                        "codgeo",
                        "courriel",
                        "telephone",
                        "site_web",
                        "adresse",
                        "commune",
                    ]
                    existing_cols = [c for c in cols_to_keep if c in df_filtered.columns]
                    df_out = df_filtered[existing_cols]

                    if not df_out.empty:
                        output_path = context.CLEAN_DIR / "structures_inclusion.parquet"
                        df_out.to_parquet(output_path)
                        ccas_path = context.CLEAN_DIR / "odis_ccas.parquet"
                        df_out.to_parquet(ccas_path)
                        logger.log_step(
                            "clean_structures_inclusion",
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
                    "Odace fetch returned empty or invalid data for dim_structure_inclusion."
                )
        except Exception as e:
            logging.error(f"Failed to fetch dim_structure_inclusion from Odace: {e}")
        raise PipelineRunError("Odace structures_inclusion did not produce a usable dataset")

    # Legacy fallback
    path = context.CACHE_DIR / source.get("local_name", "structures_inclusion.parquet")
    if not path.exists():
        return

    df = ingest.load_dataset(path, source)
    df.columns = [c.strip() for c in df.columns]

    if "reseaux_porteurs" not in df.columns:
        logging.warning(
            "clean_structures_inclusion: 'reseaux_porteurs' column missing."
        )
        return

    df["reseaux_parsed"] = df["reseaux_porteurs"].apply(parse_reseaux)

    ccas_keywords = inclusion_cfg.ccas_keywords

    def has_ccas_legacy(lst: list[str]) -> bool:
        if not lst:
            return False
        for item in lst:
            if isinstance(item, str):
                s = item.lower()
                if any(kw in s for kw in ccas_keywords):
                    return True
        return False

    df_filtered = df[df["reseaux_parsed"].apply(has_ccas_legacy)].copy()

    for field in inclusion_cfg.required_contact_fields:
        if field in df_filtered.columns:
            df_filtered[field] = df_filtered[field].replace("", np.nan)
        else:
            df_filtered[field] = np.nan

    count_before_contact_filter = len(df_filtered)
    df_filtered = df_filtered.dropna(
        subset=inclusion_cfg.required_contact_fields, how="all"
    )
    logging.info(
        f"filtered structures: CCAS match={count_before_contact_filter}, +Contact match={len(df_filtered)}"
    )

    if df_filtered.empty:
        logging.warning(
            "clean_structures_inclusion: No structures found after filtering."
        )
        return

    if "code_insee" in df_filtered.columns:
        df_filtered["codgeo_numeric"] = pd.to_numeric(
            df_filtered["code_insee"], errors="coerce"
        )
        df_filtered = df_filtered.dropna(subset=["codgeo_numeric"])
        df_filtered["codgeo"] = (
            df_filtered["codgeo_numeric"].astype(int).astype(str).str.zfill(5)
        )
    else:
        logging.warning("clean_structures_inclusion: 'code_insee' missing.")
        return

    cols_to_keep = [
        "id",
        "nom",
        "codgeo",
        "courriel",
        "telephone",
        "site_web",
        "adresse",
        "commune",
    ]
    existing_cols = [c for c in cols_to_keep if c in df_filtered.columns]
    df_out = df_filtered[existing_cols]

    output_path = context.CLEAN_DIR / "structures_inclusion.parquet"
    df_out.to_parquet(output_path)
    ccas_path = context.CLEAN_DIR / "odis_ccas.parquet"
    df_out.to_parquet(ccas_path)
    logger.log_step(
        "clean_structures_inclusion",
        "COMPLETED",
        {"path": str(output_path), "rows": len(df_out)},
    )


def clean_associations(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Associations and saves to parquet."""
    import pipeline.ingest as ingest

    logger.log_step("clean_associations", "STARTED")
    source = config["sources"]["associations"]
    output_path = context.CLEAN_DIR / "associations_vertical.parquet"

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(source.get("odace_table", "dim_association"))
            if not df_odace.empty:
                df_odace["id_waldec"] = (
                    df_odace["objet_social_code"].astype(str).str.zfill(6)
                )
                df_odace["codgeo"] = (
                    df_odace["commune_insee_code"].astype(str).str.zfill(5)
                )

                df_out = (
                    df_odace.groupby(["codgeo", "id_waldec"])
                    .size()
                    .rename("count")
                    .reset_index()
                )

                output_path.parent.mkdir(parents=True, exist_ok=True)
                df_out.to_parquet(output_path)
                logger.log_step(
                    "clean_associations",
                    "COMPLETED",
                    {"path": str(output_path), "rows": len(df_out), "source": "odace"},
                )
                return
            else:
                logging.warning("Odace fetch returned empty data for dim_association.")
        except Exception as e:
            logging.error(f"Failed to fetch associations from Odace: {e}")
        raise PipelineRunError("Odace associations did not produce a usable dataset")


def clean_nomenclature_waldec(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans WALDEC Nomenclature and saves to parquet.

    Args:
        config: Pipeline configuration dictionary.
        logger: Pipeline logger instance.

    Raises:
        PipelineRunError: If ODAS fetch or cleaning fails.
    """
    import pipeline.ingest as ingest

    logger.log_step("clean_nomenclature_waldec", "STARTED")
    source = config.get("sources", {}).get("nomenclature_waldec", {})

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "ref_waldec")
            )
            if not df_odace.empty and "code_waldec" in df_odace.columns:
                df_out = pd.DataFrame()
                df_out["code"] = ingest.normalize_code(df_odace["code_waldec"], 6)
                df_out["label"] = (
                    df_odace["libelle"].astype(str).str.strip()
                    if "libelle" in df_odace.columns
                    else pd.Series("", index=df_odace.index)
                )

                df_out = df_out.dropna(subset=["code", "label"]).drop_duplicates(
                    subset=["code"]
                )
                if not df_out.empty:
                    output_path = context.CLEAN_DIR / "referentiel_waldec.parquet"
                    df_out.to_parquet(output_path)
                    logger.log_step(
                        "clean_nomenclature_waldec",
                        "COMPLETED",
                        {
                            "path": str(output_path),
                            "rows": len(df_out),
                            "source": "odace",
                        },
                    )
                    return
            else:
                logging.warning("Odace fetch returned empty or invalid data for ref_waldec.")
        except Exception as e:
            logging.error(f"Failed to fetch ref_waldec from Odace: {e}")
        raise PipelineRunError("Odace nomenclature_waldec did not produce a usable dataset")

    path = context.CACHE_DIR / source["local_name"]

    if not path.exists():
        logging.warning("WALDEC Nomenclature file not found.")
        return

    try:
        df = pd.read_json(path)
        df.columns = [c.strip().lower() for c in df.columns]

        code_col = next(
            (
                c
                for c in df.columns
                if c in ["code", "id", "id_waldec", "objet_social_id"]
            ),
            None,
        )
        label_col = next(
            (
                c
                for c in df.columns
                if c in ["libelle", "label", "titre", "lib", "objet_social_lib"]
            ),
            None,
        )

        if code_col and label_col:
            df_out = pd.DataFrame()
            df_out["code"] = ingest.normalize_code(df[code_col], 6)
            df_out["label"] = df[label_col].astype(str).str.strip()
            df_out = df_out.dropna(subset=["code", "label"]).drop_duplicates(
                subset=["code"]
            )

            output_path = context.CLEAN_DIR / "referentiel_waldec.parquet"
            df_out.to_parquet(output_path)
            logger.log_step(
                "clean_nomenclature_waldec",
                "COMPLETED",
                {"path": str(output_path), "rows": len(df_out)},
            )
        else:
            logging.warning(
                f"WALDEC: Could not identify code/label columns. Found: {df.columns}"
            )
            logger.log_step(
                "clean_nomenclature_waldec", "FAILED", {"reason": "Columns not found"}
            )
            raise PipelineRunError("WALDEC: required columns not found in raw dataset")

    except Exception as e:
        logger.log_step("clean_nomenclature_waldec", "ERROR", {"error": str(e)})
        logging.error(f"WALDEC clean failed: {e}")
        raise PipelineRunError(f"WALDEC clean failed: {e}") from e
