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

    Note: Purged of 33 lines of unused codes_postaux and normalize_city dead code.
    """
    import pipeline.ingest as ingest

    logger.log_step("clean_school_effectifs", "STARTED")
    source = config["sources"]["education_effectifs"]
    path = context.CACHE_DIR / source["local_name"]

    if not path.exists():
        logging.warning("Education Effectifs not found.")
        return

    domain_cfg = get_domain_contracts()
    edu_cfg = domain_cfg.education

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
    logger.log_step(
        "clean_school_effectifs",
        "COMPLETED",
        {"path": str(output_path), "rows": len(df_agg)},
    )


def clean_formations(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Formations and saves to parquet."""
    logger.log_step("clean_formations", "STARTED")

    cp_path = context.CLEAN_DIR / "codes_postaux.parquet"
    if not cp_path.exists():
        logging.warning("Codes Postaux clean file not found. Skipping formations.")
        return

    cp_df = pd.read_parquet(cp_path)

    # 2. Formations Referentiel (XLSX)
    ref_cfg = config["sources"]["formations_referentiel"]
    ref_path = context.CACHE_DIR / ref_cfg["local_name"]

    if ref_path.exists():
        import pipeline.ingest as ingest

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

    # 3. Formations Annuaire (CSV)
    annuaire_cfg = config["sources"]["formations_annuaire"]
    annuaire_path = context.CACHE_DIR / annuaire_cfg["local_name"]

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
