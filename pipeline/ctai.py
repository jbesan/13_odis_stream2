import json
import logging
from pathlib import Path
import pandas as pd

from pipeline.anvita import (
    CHILD_TO_PARENT,
    clean_and_normalize,
    clean_dept_code,
    load_territory_referentials,
    resolve_entity,
)

logger = logging.getLogger(__name__)

# Manual corrections specifically for CTAI data typo(s)
MANUAL_EPCI_CORRECTIONS = {
    "sijon": "dijon",
    "le clunisois": "du clunisois",
    "saint-nazaire": "region nazairienne",
    "saint nazaire": "region nazairienne",
    "libourne": "libournais",
}


def compute_ctai_scores(
    communes_df: pd.DataFrame, cache_raw_dir: Path, json_path: Path
) -> pd.Series:
    """Computes territory score boosts (1.0, 0.5, or 0.0) for CTAI signatories.

    Args:
        communes_df: DataFrame of communes containing at least codgeo, dep_code, reg_code.
        cache_raw_dir: Path to raw cache directory containing geographic referentials.
        json_path: Path to the CTAI signatories JSON file.

    Returns:
        A float Series indexed like communes_df with values in {0.0, 0.5, 1.0}.
    """
    default_scores = pd.Series(0.0, index=communes_df.index)

    # Standardize name column to 'commune_name' for internal matches
    communes_df = communes_df.copy()
    name_col = None
    for col in ["commune_name", "nom", "libgeo", "commune"]:
        if col in communes_df.columns:
            name_col = col
            break
    if name_col:
        communes_df["commune_name"] = communes_df[name_col]
    else:
        logger.warning(
            "No commune name column found in communes_df. Matching may fail."
        )

    # 1. Soft fail check for JSON file
    if not json_path.exists():
        logger.warning(
            f"CTAI signatories JSON file not found at: {json_path}. Falling back to 0.0 for all communes."
        )
        return default_scores

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            ctai_data = json.load(f)
    except Exception as e:
        logger.error(
            f"Failed to read CTAI signatories JSON file: {e}. Falling back to 0.0."
        )
        return default_scores

    # 2. Load reference Parquet / JSON files from cache
    (
        region_name_to_code,
        dept_name_to_code,
        epci_lookup,
        epci_to_communes,
    ) = load_territory_referentials(cache_raw_dir)

    # 3. Index communes for fast exact/global lookup
    commune_lookup = {}
    commune_global_lookup = {}

    if "commune_name" in communes_df.columns:
        for idx, row in communes_df.iterrows():
            c_name = row["commune_name"]
            if pd.isna(c_name):
                continue
            norm_name = clean_and_normalize(str(c_name))
            dep = clean_dept_code(row.get("dep_code", ""))
            cod = str(row["codgeo"])
            commune_lookup[(norm_name, dep)] = cod

            if norm_name not in commune_global_lookup:
                commune_global_lookup[norm_name] = []
            commune_global_lookup[norm_name].append((dep, cod))

    # 4. Resolve each entity type from CTAI data
    codgeo_scores = {}

    # Define mapping of CTAI categories to resolve_entity type_hints and scores
    category_mapping = {
        "regions": ("Région", 0.5),
        "departements": ("Département", 0.5),
        "epcis": ("Intercommunalité", 1.0),
        "communes": ("Commune", 1.0),
    }

    for cat_name, (type_hint, score_to_assign) in category_mapping.items():
        entities = ctai_data.get(cat_name, [])
        for name in entities:
            name_to_resolve = name.strip()
            # Apply manual corrections (e.g. Sijon -> dijon)
            norm_n = clean_and_normalize(name_to_resolve)
            if norm_n in MANUAL_EPCI_CORRECTIONS:
                name_to_resolve = MANUAL_EPCI_CORRECTIONS[norm_n]

            res = resolve_entity(
                name=name_to_resolve,
                dept_code_input="",
                pc_input=None,
                type_hint=type_hint,
                region_name_to_code=region_name_to_code,
                dept_name_to_code=dept_name_to_code,
                epci_lookup=epci_lookup,
                epci_to_communes=epci_to_communes,
                commune_lookup=commune_lookup,
                commune_global_lookup=commune_global_lookup,
                communes_df=communes_df,
            )

            if res["resolved"]:
                for cod in res["codgeo_list"]:
                    parent_cod = CHILD_TO_PARENT.get(cod, cod)
                    codgeo_scores[parent_cod] = max(
                        codgeo_scores.get(parent_cod, 0.0), score_to_assign
                    )
            else:
                logger.warning(
                    f"Failed to resolve CTAI entity: {name} (type: {type_hint})"
                )

    # 5. Build final Series matching communes_df codgeo values
    final_scores = []
    for idx, row in communes_df.iterrows():
        cod = str(row["codgeo"])
        lookup_cod = CHILD_TO_PARENT.get(cod, cod)
        final_scores.append(codgeo_scores.get(lookup_cod, 0.0))

    return pd.Series(final_scores, index=communes_df.index)
