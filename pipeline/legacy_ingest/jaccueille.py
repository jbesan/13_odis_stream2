"""Archived manual J'Accueille prospect-export cleaner.

The active source of J'Accueille hosts and prospects is Salesforce. This
function is intentionally detached from the normal ingest step map.
"""

from pathlib import Path
import re

import pandas as pd


def clean_legacy_jaccueille_prospects(
    source_path: Path,
    postal_code_mapping_path: Path,
    bassins_path: Path,
    output_path: Path,
) -> None:
    """Reproduce the retired XLSX-to-BdV transformation on explicit inputs."""
    df_raw = pd.read_excel(source_path, header=13)
    cp_col = next(
        (
            c
            for c in df_raw.columns
            if "Code postal" in str(c) or "code_postal" in str(c).lower()
        ),
        None,
    )
    value_col = next(
        (
            c
            for c in df_raw.columns
            if "Nombre d'enregistrements" in str(c)
            or "prospects" in str(c).lower()
            or "count" in str(c).lower()
            or "enregistrements" in str(c).lower()
        ),
        None,
    )
    if not cp_col or not value_col:
        raise ValueError("Legacy J'Accueille export does not contain expected columns")

    df = df_raw[[cp_col, value_col]].rename(
        columns={cp_col: "code_postal_raw", value_col: "prospects_count"}
    )

    def postal_code(value: object) -> str | None:
        if pd.isna(value):
            return None
        match = re.search(r"\d{5}", str(value).replace(" ", "").replace(",", ""))
        return match.group(0) if match else None

    df["code_postal"] = df["code_postal_raw"].map(postal_code)
    df = df.dropna(subset=["code_postal"])
    df["prospects_count"] = pd.to_numeric(
        df["prospects_count"], errors="coerce"
    ).fillna(0)

    postal_codes = pd.read_parquet(postal_code_mapping_path).drop_duplicates(
        subset=["code_postal"], keep="first"
    )
    bassins = pd.read_parquet(bassins_path)
    required_bassins = {"codgeo", "bassin_de_vie"}
    if not required_bassins.issubset(bassins.columns):
        raise ValueError("Bassin reference does not contain codgeo and bassin_de_vie")

    merged = df.merge(postal_codes[["code_postal", "codgeo"]], on="code_postal")
    merged = merged.merge(bassins[["codgeo", "bassin_de_vie"]], on="codgeo")
    result = (
        merged.groupby("bassin_de_vie", as_index=False)["prospects_count"]
        .sum()
        .sort_values("bassin_de_vie")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(output_path, index=False)
