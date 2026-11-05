"""Education cleaners for school effectifs and higher education/training directories."""

from __future__ import annotations

import logging
from typing import Any, Dict

import numpy as np
import pandas as pd

from pipeline.common import PipelineLogger
from pipeline.ingest.config import get_domain_contracts
from pipeline.ingest import context
from pipeline.run_context import PipelineRunError


def clean_school_effectifs(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans School Effectifs and saves to parquet.

    Args:
        config: Pipeline configuration dictionary.
        logger: Pipeline logger instance.

    Raises:
        PipelineRunError: If ODAS fetch fails or returns unusable data.
    """
    import pipeline.ingest as ingest

    logger.log_step("clean_school_effectifs", "STARTED")
    source = config.get("sources", {}).get("education_effectifs", {})

    domain_cfg = get_domain_contracts()
    edu_cfg = domain_cfg.education

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_effectif_scolaire")
            )
            if not df_odace.empty and "numero_ecole" in df_odace.columns:
                # Filter for latest year
                year_col = next(
                    (c for c in ["annee_rentree_scolaire", "rentree_scolaire"] if c in df_odace.columns),
                    None,
                )
                if year_col:
                    latest_year = df_odace[year_col].max()
                    df_eff = df_odace[df_odace[year_col] == latest_year].copy()
                else:
                    df_eff = df_odace.copy()

                # Fetch annuaire to resolve UAI -> codgeo
                annuaire_cfg = config.get("sources", {}).get("education_annuaire", {})
                annuaire_table = annuaire_cfg.get("odace_table", "dim_etablissement_scolaire")
                df_annuaire = client.fetch_table(
                    annuaire_table, ttl_days=annuaire_cfg.get("ttl_days", 30)
                )

                uai_col = next(
                    (c for c in df_annuaire.columns if c in set(edu_cfg.uai_column_candidates)),
                    None,
                )
                insee_col = next(
                    (c for c in df_annuaire.columns if c in set(edu_cfg.insee_column_candidates)),
                    None,
                )
                if not uai_col or not insee_col:
                    raise PipelineRunError("Odace education_annuaire is missing its UAI or commune code columns")

                df_ref = (
                    df_annuaire[[uai_col, insee_col]]
                    .drop_duplicates(subset=[uai_col])
                    .rename(columns={uai_col: "numero_ecole", insee_col: "codgeo"})
                )
                df_ref["codgeo"] = ingest.normalize_code(df_ref["codgeo"], 5)
                df_ref["numero_ecole"] = df_ref["numero_ecole"].astype(str).str.strip().str.upper()
                df_eff["numero_ecole"] = df_eff["numero_ecole"].astype(str).str.strip().str.upper()

                merged = df_eff.merge(df_ref, on="numero_ecole", how="left")
                valid = merged.dropna(subset=["codgeo"]).copy()

                effectif_col = "nombre_total_eleves"
                classes_col = "nombre_total_classes"
                if effectif_col in valid.columns and classes_col in valid.columns:
                    valid[effectif_col] = pd.to_numeric(valid[effectif_col], errors="coerce").fillna(0)
                    valid[classes_col] = pd.to_numeric(valid[classes_col], errors="coerce").fillna(0)

                    valid["students_per_class"] = np.where(
                        valid[classes_col] > 0,
                        valid[effectif_col] / valid[classes_col],
                        0.0,
                    )
                    threshold = edu_cfg.risky_school_class_threshold
                    valid["is_risky"] = (valid["students_per_class"] < threshold).astype(int)

                    df_agg = (
                        valid.groupby("codgeo")
                        .agg(
                            {
                                effectif_col: "sum",
                                classes_col: "sum",
                                "is_risky": "sum",
                                "numero_ecole": "nunique",
                            }
                        )
                        .reset_index()
                    )

                    df_agg.rename(
                        columns={
                            effectif_col: "total_eleves",
                            classes_col: "total_classes",
                            "is_risky": "risky_schools_count",
                            "numero_ecole": "ecoles_count",
                        },
                        inplace=True,
                    )

                    df_agg["avg_students_per_class_commune"] = np.where(
                        df_agg["total_classes"] > 0,
                        df_agg["total_eleves"] / df_agg["total_classes"],
                        0.0,
                    )

                    output_path = context.CLEAN_DIR / "school_effectifs.parquet"
                    df_agg.to_parquet(output_path)
                    bpe_eff_path = context.CLEAN_DIR / "bpe_education_effectifs.parquet"
                    df_agg.to_parquet(bpe_eff_path)
                    logger.log_step(
                        "clean_school_effectifs",
                        "COMPLETED",
                        {"path": str(output_path), "rows": len(df_agg), "source": "odace"},
                    )
                    return
            else:
                logging.warning(
                    "Odace fetch returned empty or invalid data for fact_effectif_scolaire."
                )
        except Exception as e:
            logging.error(f"Failed to fetch fact_effectif_scolaire from Odace: {e}")
        raise PipelineRunError("Odace education_effectifs did not produce a usable dataset")

    # Legacy fallback
    path = context.CACHE_DIR / source.get("local_name", "education_effectifs.csv")
    if not path.exists():
        logging.warning("Education Effectifs not found.")
        return

    df = ingest.load_dataset(path, source)

    # Filter for Latest Year
    if "rentree_scolaire" in df.columns:
        latest_year = df["rentree_scolaire"].max()
        logging.info(f"Education Effectifs: Using latest year {latest_year}")
        df = df[df["rentree_scolaire"] == latest_year].copy()
    else:
        logging.warning(
            "Education Effectifs: 'rentree_scolaire' column missing. Using full dataset (risk of duplication)."
        )

    # 2. Education Annuaire (Reference for UAI -> Commune)
    annuaire_cfg = config["sources"]["education_annuaire"]
    try:
        df_annuaire = ingest.get_odace_client(logger).fetch_table(
            annuaire_cfg["odace_table"]
        )
    except Exception as exc:
        raise PipelineRunError("Odace education_annuaire could not be loaded") from exc
    if df_annuaire.empty:
        raise PipelineRunError("Odace education_annuaire returned no rows")

    uai_col = next(
        (c for c in df_annuaire.columns if c in set(edu_cfg.uai_column_candidates)),
        None,
    )
    insee_col = next(
        (c for c in df_annuaire.columns if c in set(edu_cfg.insee_column_candidates)),
        None,
    )

    if not uai_col or not insee_col:
        logging.warning(
            f"Education Annuaire: Missing UAI ({uai_col}) or INSEE ({insee_col}) columns."
        )
        raise PipelineRunError(
            "Odace education_annuaire is missing its UAI or commune code columns"
        )

    df_ref = (
        df_annuaire[[uai_col, insee_col]]
        .drop_duplicates(subset=[uai_col])
        .rename(columns={uai_col: "numero_ecole", insee_col: "codgeo"})
    )
    df_ref["codgeo"] = df_ref["codgeo"].astype(str).str.zfill(5)

    merged = df.merge(df_ref, on="numero_ecole", how="left")
    mapped_count = merged["codgeo"].notna().sum()
    logging.info(
        f"Education Mapping (UAI): {mapped_count} / {len(merged)} ({mapped_count / len(merged):.1%}) mapped."
    )

    if len(merged) - mapped_count > 0:
        logging.warning(
            f"Education Effectifs: {len(merged) - mapped_count} rows failed to map via UAI."
        )

    valid = merged.dropna(subset=["codgeo"]).copy()

    effectif_col = "nombre_total_eleves"
    classes_col = "nombre_total_classes"

    if effectif_col not in valid.columns or classes_col not in valid.columns:
        logging.warning("Missing effectifs/classes columns.")
        return

    valid["students_per_class"] = np.where(
        valid[classes_col] > 0, valid[effectif_col] / valid[classes_col], 0.0
    )

    threshold = edu_cfg.risky_school_class_threshold
    valid["is_risky"] = (valid["students_per_class"] < threshold).astype(int)

    df_agg = (
        valid.groupby("codgeo")
        .agg(
            {
                effectif_col: "sum",
                classes_col: "sum",
                "is_risky": "sum",
                "numero_ecole": "nunique",
            }
        )
        .reset_index()
    )

    df_agg.rename(
        columns={
            effectif_col: "total_eleves",
            classes_col: "total_classes",
            "is_risky": "risky_schools_count",
            "numero_ecole": "ecoles_count",
        },
        inplace=True,
    )

    df_agg["avg_students_per_class_commune"] = np.where(
        df_agg["total_classes"] > 0,
        df_agg["total_eleves"] / df_agg["total_classes"],
        0.0,
    )

    output_path = context.CLEAN_DIR / "school_effectifs.parquet"
    df_agg.to_parquet(output_path)
    bpe_eff_path = context.CLEAN_DIR / "bpe_education_effectifs.parquet"
    df_agg.to_parquet(bpe_eff_path)
    logger.log_step(
        "clean_school_effectifs",
        "COMPLETED",
        {"path": str(output_path), "rows": len(df_agg)},
    )


def clean_formations(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Formations and saves to parquet.

    Args:
        config: Pipeline configuration dictionary.
        logger: Pipeline logger instance.

    Raises:
        PipelineRunError: If ODAS fetch fails or returns unusable data.
    """
    import pipeline.ingest as ingest

    logger.log_step("clean_formations", "STARTED")

    # 1. Formations Referentiel
    ref_cfg = config.get("sources", {}).get("formations_referentiel", {})
    if ref_cfg.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_ref = client.fetch_table(
                ref_cfg.get("odace_table", "ref_formation"),
                ttl_days=ref_cfg.get("ttl_days", 365),
            )
            code_col = next((c for c in ["code_formation", "code"] if c in df_ref.columns), None)
            label_col = next(
                (c for c in ["libelle_formation", "label", "libelle"] if c in df_ref.columns),
                None,
            )
            if not df_ref.empty and code_col and label_col:
                df_out_ref = pd.DataFrame()
                df_out_ref["code"] = ingest.normalize_code(df_ref[code_col], 3)
                df_out_ref["label"] = df_ref[label_col].astype(str).str.strip()
                df_out_ref = df_out_ref.dropna(subset=["code", "label"]).drop_duplicates()

                output_ref = context.CLEAN_DIR / "formations_referentiel.parquet"
                df_out_ref.to_parquet(output_ref)
                logger.log_step(
                    "clean_formations",
                    "REFERENTIEL",
                    {"path": str(output_ref), "rows": len(df_out_ref), "source": "odace"},
                )
            else:
                logging.warning(
                    "Odace fetch returned empty or invalid data for ref_formation."
                )
                raise PipelineRunError("Odace formations_referentiel returned empty or invalid data")
        except Exception as e:
            logging.error(f"Failed to fetch ref_formation from Odace: {e}")
            raise PipelineRunError("Odace formations_referentiel did not produce a usable dataset") from e
    else:
        # Legacy fallback
        ref_path = context.CACHE_DIR / ref_cfg.get("local_name", "formations_referentiel.xlsx")
        if ref_path.exists():
            df_ref = ingest.load_dataset(ref_path, ref_cfg, header=None, skiprows=2)
            if len(df_ref.columns) >= 2:
                df_ref = df_ref.iloc[:, :2]
                df_ref.columns = ["code", "label"]
                df_ref["code"] = (
                    df_ref["code"].astype(str).str.replace(".0", "", regex=False)
                )

                output_ref = context.CLEAN_DIR / "formations_referentiel.parquet"
                df_ref.to_parquet(output_ref)
                logger.log_step(
                    "clean_formations", "REFERENTIEL", {"path": str(output_ref)}
                )
            else:
                logging.warning("Formations Referentiel: Unexpected columns.")

    # 2. Formations Annuaire (resolves CP -> codgeo)
    cp_path = context.CLEAN_DIR / "codes_postaux.parquet"
    cp_df = None
    if cp_path.exists():
        cp_df = pd.read_parquet(cp_path)
    else:
        odace_cp_path = context.CACHE_DIR / "odace_ref_code_postal.parquet"
        if odace_cp_path.exists():
            try:
                cp_raw = pd.read_parquet(odace_cp_path)
                if "commune_insee_code" in cp_raw.columns and "code_postal" in cp_raw.columns:
                    cp_df = cp_raw.rename(columns={"commune_insee_code": "codgeo"})[
                        ["code_postal", "codgeo"]
                    ].drop_duplicates()
                    cp_df["codgeo"] = ingest.normalize_code(cp_df["codgeo"], 5)
                    cp_df["code_postal"] = ingest.normalize_code(cp_df["code_postal"], 5)
            except Exception as ex:
                logging.debug(f"Could not load fallback odace_ref_code_postal: {ex}")

    if cp_df is None or cp_df.empty:
        logging.warning("Codes Postaux file not found. Skipping formations annuaire.")
        return

    # 2. Formations Annuaire
    annuaire_cfg = config.get("sources", {}).get("formations_annuaire", {})
    if annuaire_cfg.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_annuaire = client.fetch_table(
                annuaire_cfg.get("odace_table", "dim_organisme_formation")
            )
            cp_col = next(
                (c for c in ["code_postal", "cp"] if c in df_annuaire.columns),
                None,
            )
            formation_cols = [
                c
                for c in df_annuaire.columns
                if c in ["code_specialite_1", "code_specialite_2", "code_specialite_3"]
                or "codeSpecialite" in c
            ]
            if not df_annuaire.empty and cp_col and formation_cols:
                df_melted = df_annuaire.melt(
                    id_vars=[cp_col],
                    value_vars=formation_cols,
                    value_name="formation_code",
                ).dropna(subset=["formation_code"])

                df_melted["formation_code"] = (
                    df_melted["formation_code"].astype(str).str.strip()
                )
                df_melted = df_melted[
                    ~df_melted["formation_code"].isin(["nan", "None", "", "<NA>"])
                ]
                df_melted["code_postal"] = ingest.normalize_code(df_melted[cp_col], 5)
                df_melted = df_melted.dropna(subset=["code_postal"])

                merged = df_melted.merge(cp_df, on="code_postal", how="inner")
                merged["formation_code"] = ingest.normalize_code(
                    merged["formation_code"], 3
                )
                merged = merged.dropna(subset=["codgeo", "formation_code"])
                df_out = merged[["codgeo", "formation_code"]].drop_duplicates()

                output_annuaire = context.CLEAN_DIR / "formations_annuaire.parquet"
                df_out.to_parquet(output_annuaire)
                logger.log_step(
                    "clean_formations",
                    "ANNUAIRE",
                    {"path": str(output_annuaire), "rows": len(df_out), "source": "odace"},
                )
            else:
                logging.warning(
                    "Odace fetch returned empty or invalid data for dim_organisme_formation."
                )
                raise PipelineRunError("Odace formations_annuaire returned empty or invalid data")
        except Exception as e:
            logging.error(f"Failed to fetch dim_organisme_formation from Odace: {e}")
            raise PipelineRunError("Odace formations_annuaire did not produce a usable dataset") from e
    else:
        # Legacy fallback
        annuaire_path = context.CACHE_DIR / annuaire_cfg.get("local_name", "formations_annuaire.csv")
        if annuaire_path.exists():
            try:
                df_annuaire = pd.read_csv(
                    annuaire_path, sep=";", on_bad_lines="skip", low_memory=False
                )
            except (pd.errors.ParserError, UnicodeDecodeError, ValueError) as exc:
                logging.debug(
                    f"Failed to read annuaire CSV with ';' separator ({exc}), retrying with ','."
                )
                df_annuaire = pd.read_csv(
                    annuaire_path, sep=",", on_bad_lines="skip", low_memory=False
                )

            df_annuaire.columns = [c.strip() for c in df_annuaire.columns]

            cp_col = next(
                (
                    c
                    for c in df_annuaire.columns
                    if "adressePhysiqueOrganismeFormation.codePostal" in c
                ),
                None,
            )
            if not cp_col:
                cp_col = next(
                    (
                        c
                        for c in df_annuaire.columns
                        if "code_postal" in c.lower() or "codepostal" in c.lower()
                    ),
                    None,
                )

            formation_cols = [c for c in df_annuaire.columns if "codeSpecialite" in c]

            if cp_col and formation_cols:
                df_melted = df_annuaire.melt(
                    id_vars=[cp_col], value_vars=formation_cols, value_name="formation_code"
                ).dropna(subset=["formation_code"])

                df_melted["code_postal"] = (
                    pd.to_numeric(df_melted[cp_col], errors="coerce")
                    .fillna(0)
                    .astype(int)
                    .astype(str)
                    .str.zfill(5)
                )

                merged = df_melted.merge(cp_df, on="code_postal", how="inner")
                merged["formation_code"] = (
                    merged["formation_code"]
                    .astype(str)
                    .str.replace(r"\.0$", "", regex=True)
                    .str.strip()
                )

                merged = merged[merged["formation_code"] != "nan"]
                df_out = merged[["codgeo", "formation_code"]].drop_duplicates()

                output_annuaire = context.CLEAN_DIR / "formations_annuaire.parquet"
                df_out.to_parquet(output_annuaire)
                logger.log_step(
                    "clean_formations",
                    "ANNUAIRE",
                    {"path": str(output_annuaire), "rows": len(df_out)},
                )
            else:
                logging.warning(
                    f"Formations Annuaire: Columns not found. CP: {cp_col}, Formations: {formation_cols}"
                )
