"""Political nuance and electoral history cleaners driven by domain contracts."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

import numpy as np
import pandas as pd

from pipeline.common import PipelineLogger
from pipeline.ingest.config import get_domain_contracts
from pipeline.ingest import context


def clean_political(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Political Nuance and saves to parquet."""
    logger.log_step("clean_political", "STARTED")
    source = config["sources"]["political_nuance"]
    path = context.CACHE_DIR / source["local_name"]
    if not path.exists():
        return

    domain_cfg = get_domain_contracts()
    electoral_cfg = domain_cfg.electoral

    import pipeline.ingest as ingest

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

        df["maire_extreme_droite"] = (famille_val == far_right_family) | (
            nuance_val.isin(far_right_nuances)
        )
        df["pol_num"] = np.where(df["maire_extreme_droite"], 0.0, 1.0)
        df["codgeo"] = df[codgeo_col].astype(str).str.zfill(5)

        df_out = df[["codgeo", "pol_num", "maire_extreme_droite"]]
        output_path = context.CLEAN_DIR / "political.parquet"
        df_out.to_parquet(output_path)
        logger.log_step("clean_political", "COMPLETED", {"path": str(output_path)})
    else:
        logging.warning(f"Political: Columns not found. Found: {df.columns}")


def clean_electoral_history(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans Electoral History from candidats_results.parquet and saves to parquet."""
    logger.log_step("clean_electoral_history", "STARTED")
    source = config["sources"]["electoral_history"]
    path = context.CACHE_DIR / source["local_name"]
    if not path.exists():
        logging.warning(f"Electoral history source file not found at: {path}")
        return

    domain_cfg = get_domain_contracts()
    electoral_cfg = domain_cfg.electoral

    cols = [
        "id_election",
        "code_commune",
        "nuance",
        "libelle_abrege_liste",
        "nom",
        "voix",
    ]

    election_pattern = electoral_cfg.election_pattern
    try:
        all_elections = pd.read_parquet(
            path, columns=["id_election"], engine="pyarrow"
        )["id_election"].unique()
        allowed_elections = [
            str(el) for el in all_elections if re.search(election_pattern, str(el))
        ]
    except Exception as e:
        logging.warning(f"Error inspecting election IDs from parquet: {e}")
        allowed_elections = None

    if allowed_elections is not None:
        df = pd.read_parquet(
            path,
            columns=cols,
            filters=[("id_election", "in", allowed_elections)],
            engine="pyarrow",
        )
    else:
        df = pd.read_parquet(path, columns=cols, engine="pyarrow")
        df = df[
            df["id_election"].astype(str).str.contains(election_pattern, regex=True)
        ]

    df = df.copy()
    df["nuance"] = df["nuance"].fillna("").astype(str)
    df["libelle_abrege_liste"] = df["libelle_abrege_liste"].fillna("").astype(str)
    df["nom"] = df["nom"].fillna("").astype(str)

    agg = df.groupby(
        ["code_commune", "id_election", "nuance", "libelle_abrege_liste", "nom"],
        as_index=False,
    )["voix"].sum()

    totals = agg.groupby(["code_commune", "id_election"])["voix"].transform("sum")
    agg["total_voix"] = totals
    agg["pct"] = np.where(
        agg["total_voix"] > 0, (agg["voix"] / agg["total_voix"] * 100).round(1), 0.0
    )

    idx = agg.groupby(["code_commune", "id_election"])["voix"].idxmax()
    winners = agg.loc[idx].copy()

    nuance_labels = electoral_cfg.nuance_labels
    election_dates = electoral_cfg.election_dates
    election_labels = electoral_cfg.election_labels
    tour_labels = electoral_cfg.tour_labels

    nuance_s = winners["nuance"].str.strip()
    list_s = winners["libelle_abrege_liste"].str.strip()
    nom_s = winners["nom"].str.strip()

    nuance_mapped = nuance_s.map(nuance_labels).fillna(nuance_s)

    winners["winner_label"] = np.where(
        nuance_s != "",
        nuance_mapped,
        np.where(list_s != "", list_s, np.where(nom_s != "", nom_s, "Inconnu")),
    )

    id_el_s = winners["id_election"].astype(str)
    year_extracted = id_el_s.str.extract(r"^(\d{4})")[0].fillna("1900")
    default_date = year_extracted + "-01-01"
    winners["date"] = id_el_s.map(election_dates).fillna(default_date)

    parts = id_el_s.str.split("_", expand=True)
    winners["type"] = parts[1]
    winners["tour"] = parts[2].map(tour_labels).fillna("")
    label_col = (
        winners["type"].map(election_labels).fillna(winners["type"].str.capitalize())
    )
    winners["election_formatted"] = label_col + " " + parts[0]

    winners = winners.sort_values(by=["code_commune", "date"], ascending=[True, False])

    muni_df = winners[winners["type"] == "muni"]
    pres_df = winners[winners["type"] == "pres"]

    max_hist = electoral_cfg.max_history_per_category
    top_muni = muni_df.groupby("code_commune").head(max_hist)
    top_pres = pres_df.groupby("code_commune").head(max_hist)

    commune_histories = {}

    muni_records = top_muni[
        ["code_commune", "election_formatted", "tour", "winner_label", "pct"]
    ].to_dict("records")
    for r in muni_records:
        code = r["code_commune"]
        if code not in commune_histories:
            commune_histories[code] = {"municipales": [], "presidentielles": []}
        commune_histories[code]["municipales"].append(
            {
                "election": r["election_formatted"],
                "tour": r["tour"],
                "nuance": r["winner_label"],
                "percentage": float(r["pct"]),
            }
        )

    pres_records = top_pres[
        ["code_commune", "election_formatted", "tour", "winner_label", "pct"]
    ].to_dict("records")
    for r in pres_records:
        code = r["code_commune"]
        if code not in commune_histories:
            commune_histories[code] = {"municipales": [], "presidentielles": []}
        commune_histories[code]["presidentielles"].append(
            {
                "election": r["election_formatted"],
                "tour": r["tour"],
                "nuance": r["winner_label"],
                "percentage": float(r["pct"]),
            }
        )

    df_out = pd.DataFrame(
        [
            {
                "codgeo": str(k).zfill(5),
                "electoral_history": json.dumps(v, ensure_ascii=False),
            }
            for k, v in commune_histories.items()
        ]
    )

    output_path = context.CLEAN_DIR / "electoral_history.parquet"
    df_out.to_parquet(output_path)
    logger.log_step("clean_electoral_history", "COMPLETED", {"path": str(output_path)})
