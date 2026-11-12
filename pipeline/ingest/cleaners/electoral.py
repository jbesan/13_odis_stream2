"""Political nuance and electoral history cleaners driven by domain contracts."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

import numpy as np
import pandas as pd
import polars as pl

from pipeline.common import PipelineLogger
from pipeline.ingest.config import get_domain_contracts
from pipeline.ingest import context
from pipeline.run_context import PipelineRunError


def clean_political(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Political Nuance and saves to parquet.

    Args:
        config: Pipeline configuration dictionary.
        logger: Pipeline logger instance.

    Raises:
        PipelineRunError: If ODAS fetch or cleaning fails.
    """
    import pipeline.ingest as ingest

    logger.log_step("clean_political", "STARTED")
    source = config.get("sources", {}).get("political_nuance") or config.get(
        "sources", {}
    ).get("political", {})
    domain_cfg = get_domain_contracts()
    electoral_cfg = domain_cfg.electoral

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            df_odace = client.fetch_table(
                source.get("odace_table", "fact_nuance_politique")
            )
            if not df_odace.empty:
                nuance_col = (
                    "code_nuance_politique"
                    if "code_nuance_politique" in df_odace.columns
                    else "nuance"
                )
                famille_col = (
                    "famille_nuance_politique"
                    if "famille_nuance_politique" in df_odace.columns
                    else "famille"
                )
                codgeo_col = (
                    "commune_insee_code"
                    if "commune_insee_code" in df_odace.columns
                    else "codgeo"
                )

                if codgeo_col in df_odace.columns:
                    nuance_val = (
                        df_odace[nuance_col].astype(str).str.strip().str.upper()
                        if nuance_col in df_odace.columns
                        else pd.Series("", index=df_odace.index)
                    )
                    famille_val = (
                        df_odace[famille_col].astype(str).str.strip()
                        if famille_col in df_odace.columns
                        else pd.Series("", index=df_odace.index)
                    )

                    far_right_nuances = set(electoral_cfg.far_right_nuances)
                    far_right_family = electoral_cfg.far_right_family

                    df_out = pd.DataFrame()
                    df_out["codgeo"] = ingest.normalize_code(df_odace[codgeo_col], 5)
                    df_out["maire_extreme_droite"] = (
                        famille_val == far_right_family
                    ) | (nuance_val.isin(far_right_nuances))
                    df_out["pol_num"] = np.where(
                        df_out["maire_extreme_droite"], 0.0, 1.0
                    )
                    df_out = (
                        df_out.dropna(subset=["codgeo"])
                        .sort_values(by="maire_extreme_droite", ascending=False)
                        .drop_duplicates(subset=["codgeo"], keep="first")
                    )
                    df_out = df_out[["codgeo", "pol_num", "maire_extreme_droite"]]

                    if not df_out.empty:
                        output_path = context.CLEAN_DIR / "political.parquet"
                        df_out.to_parquet(output_path)
                        logger.log_step(
                            "clean_political",
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
                    "Odace fetch returned empty data for fact_nuance_politique."
                )
        except Exception as e:
            logging.error(f"Failed to fetch fact_nuance_politique from Odace: {e}")
        raise PipelineRunError("Odace political did not produce a usable dataset")

    path = context.CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    df = ingest.load_dataset(path, source)
    df.columns = [c.strip() for c in df.columns]

    codgeo_col = next(
        (c for c in df.columns if "Code Insee" in c or "cog_commune" in c), None
    )
    nuance_col = next(
        (
            c
            for c in df.columns
            if ("Nuance" in c or "nuance_politique" in c) and "Libellé" not in c
        ),
        None,
    )
    famille_col = next(
        (c for c in df.columns if "famille" in c.lower() or "famille_nuance" in c), None
    )

    if codgeo_col:
        nuance_val = (
            df[nuance_col].astype(str).str.strip().str.upper()
            if nuance_col
            else pd.Series("", index=df.index)
        )
        famille_val = (
            df[famille_col].astype(str).str.strip()
            if famille_col
            else pd.Series("", index=df.index)
        )

        far_right_nuances = set(electoral_cfg.far_right_nuances)
        far_right_family = electoral_cfg.far_right_family

        df_out = pd.DataFrame()
        df_out["codgeo"] = ingest.normalize_code(df[codgeo_col], 5)
        df_out["maire_extreme_droite"] = (famille_val == far_right_family) | (
            nuance_val.isin(far_right_nuances)
        )
        df_out["pol_num"] = np.where(df_out["maire_extreme_droite"], 0.0, 1.0)
        df_out = (
            df_out.dropna(subset=["codgeo"])
            .sort_values(by="maire_extreme_droite", ascending=False)
            .drop_duplicates(subset=["codgeo"], keep="first")
        )
        df_out = df_out[["codgeo", "pol_num", "maire_extreme_droite"]]

        output_path = context.CLEAN_DIR / "political.parquet"
        df_out.to_parquet(output_path)
        logger.log_step("clean_political", "COMPLETED", {"path": str(output_path)})
    else:
        logging.warning(f"Political: Columns not found. Found: {df.columns}")


def clean_electoral_history(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Electoral History using Polars and saves to parquet.

    Supports both ODAS Silver table fact_resultat_election_candidat and legacy
    data.gouv candidats_results parquet.

    Args:
        config: Pipeline configuration dictionary.
        logger: Pipeline logger instance.
    """
    import pipeline.ingest as ingest

    logger.log_step("clean_electoral_history", "STARTED")
    source = config.get("sources", {}).get("electoral_history", {})
    path = None

    if source.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            odace_table = source.get("odace_table", "fact_resultat_election_candidat")
            ttl_days = source.get("ttl_days", 365)
            if hasattr(client, "fetch_table_path"):
                odace_path = client.fetch_table_path(odace_table, ttl_days=ttl_days)
            else:
                client.fetch_table(odace_table, ttl_days=ttl_days)
                odace_path = context.CACHE_DIR / f"odace_{odace_table}.parquet"
            if odace_path.exists():
                path = odace_path
        except Exception as e:
            logging.error(f"❌ [Electoral History] Failed to fetch from ODAS: {e}")
            raise PipelineRunError(
                f"Odace electoral_history did not produce a usable dataset: {e}"
            ) from e

    if path is None:
        local_name = source.get("local_name", "candidats_results.parquet")
        path = context.CACHE_DIR / local_name

    if not path.exists():
        raise PipelineRunError(f"Electoral history source file not found at: {path}")

    domain_cfg = get_domain_contracts()
    electoral_cfg = domain_cfg.electoral

    election_pattern = electoral_cfg.election_pattern
    nuance_labels = electoral_cfg.nuance_labels
    election_dates = electoral_cfg.election_dates
    election_labels = electoral_cfg.election_labels
    tour_labels = electoral_cfg.tour_labels
    max_hist = electoral_cfg.max_history_per_category

    # 1. Scan Parquet lazily with Polars
    lazy_df = pl.scan_parquet(path)
    schema = lazy_df.collect_schema()

    # Dynamic column mapping (ODAS vs legacy data.gouv)
    col_election = "code_election" if "code_election" in schema else "id_election"
    col_commune = (
        "commune_insee_code" if "commune_insee_code" in schema else "code_commune"
    )
    col_nuance = (
        "code_nuance_politique" if "code_nuance_politique" in schema else "nuance"
    )
    col_nom = "nom_candidat" if "nom_candidat" in schema else "nom"
    col_voix = "nb_voix" if "nb_voix" in schema else "voix"

    # 2. Filter relevant elections (municipales and présidentielles) and normalize codgeo early
    filtered = (
        lazy_df.filter(
            pl.col(col_election).cast(pl.Utf8).str.contains(election_pattern)
        )
        .select(
            [
                pl.col(col_commune)
                .cast(pl.Utf8)
                .str.strip_chars()
                .str.zfill(5)
                .alias("code_commune"),
                pl.col(col_election)
                .cast(pl.Utf8)
                .str.strip_chars()
                .alias("id_election"),
                pl.col(col_nuance)
                .fill_null("")
                .cast(pl.Utf8)
                .str.strip_chars()
                .alias("nuance"),
                pl.col("libelle_abrege_liste")
                .fill_null("")
                .cast(pl.Utf8)
                .str.strip_chars(),
                pl.col(col_nom)
                .fill_null("")
                .cast(pl.Utf8)
                .str.strip_chars()
                .alias("nom"),
                pl.col(col_voix).fill_null(0).cast(pl.Float64).alias("voix"),
            ]
        )
        .filter(
            pl.col("code_commune").is_not_null()
            & (pl.col("code_commune") != "")
            & (pl.col("code_commune") != "00000")
        )
    )

    # 3. Aggregate votes per candidate/list
    agg = filtered.group_by(
        ["code_commune", "id_election", "nuance", "libelle_abrege_liste", "nom"]
    ).agg(pl.col("voix").sum().alias("voix"))

    # 4. Compute total votes per commune+election and percentage
    agg = agg.with_columns(
        pl.col("voix").sum().over(["code_commune", "id_election"]).alias("total_voix")
    ).with_columns(
        pl.when(pl.col("total_voix") > 0)
        .then((pl.col("voix") / pl.col("total_voix") * 100).round(1))
        .otherwise(0.0)
        .alias("pct")
    )

    # 5. Pick winner per commune+election (row with max votes, deterministic tie-break)
    winners = (
        agg.sort(
            ["voix", "nuance", "libelle_abrege_liste", "nom"],
            descending=[True, False, False, False],
        )
        .group_by(["code_commune", "id_election"], maintain_order=True)
        .first()
    )

    # 6. Map winner labels, dates, tours, and formatted titles
    mapped_nuance = pl.col("nuance").replace_strict(
        nuance_labels, default=pl.col("nuance")
    )
    winner_label = (
        pl.when(pl.col("nuance") != "")
        .then(mapped_nuance)
        .when(pl.col("libelle_abrege_liste") != "")
        .then(pl.col("libelle_abrege_liste"))
        .when(pl.col("nom") != "")
        .then(pl.col("nom"))
        .otherwise(pl.lit("Inconnu"))
        .alias("winner_label")
    )

    id_el = pl.col("id_election")
    parts = id_el.str.split("_")
    year = parts.list.get(0, null_on_oob=True).fill_null("")
    el_type = parts.list.get(1, null_on_oob=True).fill_null("")
    tour_key = parts.list.get(2, null_on_oob=True).fill_null("")

    default_date = year + "-01-01"
    date_col = id_el.replace_strict(election_dates, default=default_date).alias("date")
    tour_col = tour_key.replace_strict(tour_labels, default=pl.lit("")).alias("tour")
    type_label = el_type.replace_strict(
        election_labels, default=el_type.str.to_titlecase()
    )
    formatted_election = (type_label + pl.lit(" ") + year).alias("election_formatted")

    winners_df = (
        winners.with_columns(
            [
                winner_label,
                el_type.alias("type"),
                date_col,
                tour_col,
                formatted_election,
            ]
        )
        .sort(["code_commune", "date", "id_election"], descending=[False, True, True])
        .collect()
    )

    # 7. Structure into JSON electoral history per commune
    commune_histories: Dict[str, Dict[str, list]] = {}

    muni_winners = (
        winners_df.filter(pl.col("type") == "muni")
        .group_by("code_commune", maintain_order=True)
        .head(max_hist)
    )
    pres_winners = (
        winners_df.filter(pl.col("type") == "pres")
        .group_by("code_commune", maintain_order=True)
        .head(max_hist)
    )

    for row in muni_winners.iter_rows(named=True):
        code = row["code_commune"]
        if code not in commune_histories:
            commune_histories[code] = {"municipales": [], "presidentielles": []}
        commune_histories[code]["municipales"].append(
            {
                "election": row["election_formatted"],
                "tour": row["tour"],
                "nuance": row["winner_label"],
                "percentage": float(row["pct"]),
            }
        )

    for row in pres_winners.iter_rows(named=True):
        code = row["code_commune"]
        if code not in commune_histories:
            commune_histories[code] = {"municipales": [], "presidentielles": []}
        commune_histories[code]["presidentielles"].append(
            {
                "election": row["election_formatted"],
                "tour": row["tour"],
                "nuance": row["winner_label"],
                "percentage": float(row["pct"]),
            }
        )

    df_out = pl.DataFrame(
        [
            {
                "codgeo": str(k).zfill(5),
                "electoral_history": json.dumps(v, ensure_ascii=False),
            }
            for k, v in commune_histories.items()
        ],
        schema={"codgeo": pl.Utf8, "electoral_history": pl.Utf8},
    )

    output_path = context.CLEAN_DIR / "electoral_history.parquet"
    df_out.write_parquet(output_path)
    logger.log_step(
        "clean_electoral_history",
        "COMPLETED",
        {"path": str(output_path), "rows": len(df_out)},
    )
