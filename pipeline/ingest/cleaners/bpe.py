"""BPE equipment cleaning and aggregation routines driven by domain contracts."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, Dict

import numpy as np
import pandas as pd

from pipeline.common import PipelineLogger, _lambert93_to_wgs84
from pipeline.ingest.config import get_domain_contracts
from pipeline.ingest import context
from pipeline.run_context import PipelineRunError


def clean_bpe(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Extracts points of interest and aggregated counts from BPE.

    Args:
        config: Pipeline sources configuration dictionary.
        logger: Pipeline logger instance.

    Raises:
        PipelineRunError: If Odace fetch fails or produces invalid data.
    """
    import pipeline.ingest as ingest

    domain_cfg = get_domain_contracts()
    bpe_cfg = domain_cfg.bpe

    output_edu_cols = context.CLEAN_DIR / "bpe_education_cols.parquet"
    output_sante_cols = context.CLEAN_DIR / "bpe_sante_cols.parquet"
    output_heb_cols = context.CLEAN_DIR / "bpe_hebergement_cols.parquet"
    output_act_cols = context.CLEAN_DIR / "bpe_action_sociale_cols.parquet"
    output_gares_cols = context.CLEAN_DIR / "bpe_gares_cols.parquet"
    output_pois = context.CLEAN_DIR / "bpe_pois.parquet"
    source = config["sources"]["bpe"]

    needs_refresh = True
    if (
        output_edu_cols.exists()
        and output_sante_cols.exists()
        and output_heb_cols.exists()
        and output_act_cols.exists()
        and output_gares_cols.exists()
        and output_pois.exists()
    ):
        mtime = datetime.fromtimestamp(output_edu_cols.stat().st_mtime)
        age_days = (datetime.now() - mtime).days
        if age_days < source["ttl_days"]:
            logging.info(
                "[BPE] BPE stats are %s days old. Using cache (TTL=%s days).",
                age_days,
                source["ttl_days"],
            )
            needs_refresh = False

    if not needs_refresh:
        return

    logger.log_step("clean_bpe", "STARTED")
    df = None
    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "dim_equipement_territoire"),
                ttl_days=source["ttl_days"],
            )
            if not df_odace.empty:
                rename_dict = {
                    "commune_insee_code": "codgeo",
                    "type_equipement_code": "TYPEQU",
                    "equipement_label": "NOMRS",
                    "coord_x_lambert": "LAMBERT_X",
                    "coord_y_lambert": "LAMBERT_Y",
                    "departement_code": "DEP",
                }
                df = df_odace.rename(columns=rename_dict).copy()
                df["longitude"] = pd.to_numeric(
                    df.get("longitude", np.nan), errors="coerce"
                )
                df["latitude"] = pd.to_numeric(
                    df.get("latitude", np.nan), errors="coerce"
                )
                logging.info(f"[BPE] Loaded {len(df)} rows from Odace API.")
        except Exception as e:
            logging.error(f"BPE: Odace fetch failed: {e}")

    if df is None:
        if source.get("use_odace", False):
            raise PipelineRunError("Odace bpe did not produce a usable dataset")

    # Construct CODGEO
    if "codgeo" not in df.columns:
        for col in ["commune_insee_code", "DEPCOM", "CODGEO"]:
            if col in df.columns:
                df["codgeo"] = df[col].astype(str).str.zfill(5)
                break
        else:
            if "DEP" in df.columns and "COM" in df.columns:
                df["codgeo"] = df["DEP"].astype(str).str.zfill(2) + df["COM"].astype(
                    str
                ).str.zfill(3)

    if "TYPEQU" not in df.columns:
        logging.warning("BPE: TYPEQU column not found.")
        return

    # Normalize/ensure standard naming
    if "longitude" not in df.columns and "LONGITUDE" in df.columns:
        df["longitude"] = pd.to_numeric(df["LONGITUDE"], errors="coerce")
    else:
        df["longitude"] = pd.to_numeric(df.get("longitude", np.nan), errors="coerce")

    if "latitude" not in df.columns and "LATITUDE" in df.columns:
        df["latitude"] = pd.to_numeric(df["LATITUDE"], errors="coerce")
    else:
        df["latitude"] = pd.to_numeric(df.get("latitude", np.nan), errors="coerce")

    target_types = set(bpe_cfg.target_types)
    is_edu = df["TYPEQU"].str.startswith("C", na=False)
    is_other_target = df["TYPEQU"].isin(target_types)
    df = df[is_edu | is_other_target].copy()

    # Project coords if latitude/longitude are missing but LAMBERT_X/LAMBERT_Y are present
    missing_coords = df["longitude"].isna() | df["latitude"].isna()
    valid_lambert = (
        ~df["LAMBERT_X"].isna()
        & ~df["LAMBERT_Y"].isna()
        & (df["LAMBERT_X"] != 0)
        & (df["LAMBERT_Y"] != 0)
    )

    reproject_mask = missing_coords & valid_lambert
    if reproject_mask.any():
        try:
            df_to_reproj = df[reproject_mask].copy()
            coords = [
                _lambert93_to_wgs84(float(x), float(y))
                for x, y in zip(df_to_reproj["LAMBERT_X"], df_to_reproj["LAMBERT_Y"])
            ]
            df.loc[reproject_mask, "longitude"] = [c[0] for c in coords]
            df.loc[reproject_mask, "latitude"] = [c[1] for c in coords]
            logging.info(
                f"BPE: Reprojected {reproject_mask.sum()} coords from Lambert EPSG:2154"
            )
        except Exception as e:
            logging.warning(f"BPE: Reprojection failed: {e}")

    # --- 1. Education & Early Childhood (Éducation & Petite Enfance) ---
    is_edu = df["TYPEQU"].str.startswith("C", na=False)
    all_edu_codes = {code for cat in bpe_cfg.education.categories for code in cat.codes}
    df_edu_all = df[is_edu | df["TYPEQU"].isin(all_edu_codes)].copy()

    agg_edu_dict = {}
    rename_edu_dict = {}
    for cat in bpe_cfg.education.categories:
        df_edu_all[cat.name] = df_edu_all["TYPEQU"].isin(cat.codes).astype(int)
        agg_edu_dict[cat.name] = "sum"
        rename_edu_dict[cat.name] = cat.output_col

    df_edu_cols = (
        df_edu_all.groupby("codgeo")
        .agg(agg_edu_dict)
        .rename(columns=rename_edu_dict)
        .reset_index()
    )
    df_edu_cols.to_parquet(output_edu_cols)

    # --- 2. Housing & Accommodation (Logement & Hébergement) ---
    heb_target_codes = set(bpe_cfg.hebergement.exact_types.keys()) | {"D710"}
    df_heb = df[df["TYPEQU"].isin(heb_target_codes)].copy()
    df_heb["sub_type"] = None

    for code, stype in bpe_cfg.hebergement.exact_types.items():
        df_heb.loc[df_heb["TYPEQU"] == code, "sub_type"] = stype

    for pat in bpe_cfg.hebergement.d710_patterns:
        mask = (df_heb["TYPEQU"] == "D710") & df_heb["NOMRS"].str.contains(
            pat.regex, case=False, na=False, regex=True
        )
        df_heb.loc[mask, "sub_type"] = pat.sub_type

    df_heb = df_heb.dropna(subset=["sub_type"]).copy()

    agg_heb_dict = {}
    rename_heb_dict = {}
    for stype, col_mapping in bpe_cfg.hebergement.columns.items():
        indicator = col_mapping.indicator
        df_heb[indicator] = (df_heb["sub_type"] == stype).astype(int)
        agg_heb_dict[indicator] = "sum"
        rename_heb_dict[indicator] = col_mapping.output_col

    df_heb_cols = (
        df_heb.groupby("codgeo")
        .agg(agg_heb_dict)
        .rename(columns=rename_heb_dict)
        .reset_index()
    )
    df_heb_cols.to_parquet(output_heb_cols)

    # --- 3. Health (Santé) ---
    sante_indicators = bpe_cfg.sante.indicators
    sante_codes = [ind.code for ind in sante_indicators]
    df_sante = df[df["TYPEQU"].isin(sante_codes)].copy()

    agg_sante_dict = {}
    rename_sante_dict = {}
    for ind in sante_indicators:
        df_sante[ind.indicator] = (df_sante["TYPEQU"] == ind.code).astype(int)
        agg_sante_dict[ind.indicator] = "sum"
        rename_sante_dict[ind.indicator] = ind.output_col

    df_sante_cols = (
        df_sante.groupby("codgeo")
        .agg(agg_sante_dict)
        .rename(columns=rename_sante_dict)
        .reset_index()
    )
    df_sante_cols.to_parquet(output_sante_cols)

    # --- 4. Action Sociale ---
    act_indicators = bpe_cfg.action_sociale.indicators
    act_codes = [ind.code for ind in act_indicators]
    df_act = df[df["TYPEQU"].isin(act_codes)].copy()

    agg_act_dict = {}
    rename_act_dict = {}
    for ind in act_indicators:
        df_act[ind.indicator] = (df_act["TYPEQU"] == ind.code).astype(int)
        agg_act_dict[ind.indicator] = "sum"
        rename_act_dict[ind.indicator] = ind.output_col

    df_act_cols = (
        df_act.groupby("codgeo")
        .agg(agg_act_dict)
        .rename(columns=rename_act_dict)
        .reset_index()
    )
    df_act_cols.to_parquet(output_act_cols)

    # --- 5. Gares ---
    gares_cfg = bpe_cfg.gares
    df_gares = df[df["TYPEQU"].isin(gares_cfg.types)].copy()
    df_gares["is_gare"] = 1
    df_gares_cols = (
        df_gares.groupby("codgeo")
        .agg({"is_gare": "sum"})
        .rename(columns={"is_gare": gares_cfg.output_count_col})
        .reset_index()
    )
    df_gares_cols[gares_cfg.output_flag_col] = (
        df_gares_cols[gares_cfg.output_count_col] > 0
    ).astype(int)
    df_gares_cols.to_parquet(output_gares_cols)

    # --- 6. Unified POIs output ---
    pois_parts = []

    # 1. Education POIs
    df_edu_pois = df_edu_all.dropna(subset=["longitude", "latitude"]).copy()
    if not df_edu_pois.empty:
        type_lbls = bpe_cfg.education.labels
        pois_parts.append(
            pd.DataFrame(
                {
                    "id": df_edu_pois.index.astype(str) + "_edu",
                    "name": df_edu_pois["NOMRS"].astype(str),
                    "type": df_edu_pois["TYPEQU"].map(type_lbls),
                    "category": "education",
                    "lat": df_edu_pois["latitude"],
                    "lon": df_edu_pois["longitude"],
                    "codgeo": df_edu_pois["codgeo"],
                }
            )
        )

    # 2. Housing POIs
    df_heb_pois = df_heb.dropna(subset=["longitude", "latitude"]).copy()
    if not df_heb_pois.empty:
        pois_parts.append(
            pd.DataFrame(
                {
                    "id": df_heb_pois.index.astype(str) + "_heb",
                    "name": df_heb_pois["NOMRS"].astype(str),
                    "type": df_heb_pois["sub_type"],
                    "category": "hebergement",
                    "lat": df_heb_pois["latitude"],
                    "lon": df_heb_pois["longitude"],
                    "codgeo": df_heb_pois["codgeo"],
                }
            )
        )

    # 3. Health POIs
    df_sante_pois = df_sante.dropna(subset=["longitude", "latitude"]).copy()
    if not df_sante_pois.empty:
        type_lbls = {ind.code: ind.label for ind in sante_indicators}
        pois_parts.append(
            pd.DataFrame(
                {
                    "id": df_sante_pois.index.astype(str) + "_sante",
                    "name": df_sante_pois["NOMRS"].astype(str),
                    "type": df_sante_pois["TYPEQU"].map(type_lbls),
                    "category": "sante",
                    "lat": df_sante_pois["latitude"],
                    "lon": df_sante_pois["longitude"],
                    "codgeo": df_sante_pois["codgeo"],
                }
            )
        )

    # 4. Action Sociale & Mairie POIs
    df_act_pois = df_act.dropna(subset=["longitude", "latitude"]).copy()
    if not df_act_pois.empty:
        type_lbls = {ind.code: ind.label for ind in act_indicators}
        cat_map = {ind.code: ind.category for ind in act_indicators}
        pois_parts.append(
            pd.DataFrame(
                {
                    "id": df_act_pois.index.astype(str) + "_act",
                    "name": df_act_pois["NOMRS"].astype(str),
                    "type": df_act_pois["TYPEQU"].map(type_lbls),
                    "category": df_act_pois["TYPEQU"].map(cat_map),
                    "lat": df_act_pois["latitude"],
                    "lon": df_act_pois["longitude"],
                    "codgeo": df_act_pois["codgeo"],
                }
            )
        )

    # 5. Gares POIs
    df_gares_pois = df_gares.dropna(subset=["longitude", "latitude"]).copy()
    if not df_gares_pois.empty:
        pois_parts.append(
            pd.DataFrame(
                {
                    "id": df_gares_pois.index.astype(str) + "_gare",
                    "name": df_gares_pois["NOMRS"].astype(str),
                    "type": gares_cfg.poi_label,
                    "category": gares_cfg.poi_category,
                    "lat": df_gares_pois["latitude"],
                    "lon": df_gares_pois["longitude"],
                    "codgeo": df_gares_pois["codgeo"],
                }
            )
        )

    if pois_parts:
        pois_df = pd.concat(pois_parts, ignore_index=True)
        pois_df.to_parquet(output_pois)
        logger.log_step(
            "clean_bpe",
            "COMPLETED",
            {
                "edu": str(output_edu_cols),
                "sante": str(output_sante_cols),
                "heb": str(output_heb_cols),
                "act": str(output_act_cols),
                "gares": str(output_gares_cols),
                "pois": str(output_pois),
            },
        )
    else:
        logger.log_step("clean_bpe", "PARTIAL", {"msg": "No POIs generated"})
