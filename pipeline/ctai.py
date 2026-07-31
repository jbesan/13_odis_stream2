import json
import logging
from pathlib import Path
import pandas as pd

from pipeline.anvita import (
    clean_and_normalize,
    clean_dept_code,
    resolve_entity,
    CHILD_TO_PARENT
)

logger = logging.getLogger(__name__)

# Manual corrections specifically for CTAI data typo(s)
MANUAL_EPCI_CORRECTIONS = {
    "sijon": "dijon",
    "le clunisois": "du clunisois"
}

def compute_ctai_scores(
    communes_df: pd.DataFrame,
    cache_raw_dir: Path,
    json_path: Path
) -> pd.Series:
    """
    Computes territory score boosts (1.0, 0.5, or 0.0) for CTAI signatories.
    Reads CTAI signatories JSON and matches them using raw cached referential files.
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
        logger.warning("No commune name column found in communes_df. Matching may fail.")
    
    # 1. Soft fail check for JSON file
    if not json_path.exists():
        logger.warning(f"CTAI signatories JSON file not found at: {json_path}. Falling back to 0.0 for all communes.")
        return default_scores
        
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            ctai_data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read CTAI signatories JSON file: {e}. Falling back to 0.0.")
        return default_scores

    # 2. Load reference JSON files from cache
    region_name_to_code = {}
    dept_name_to_code = {}
    epci_lookup = {}
    epci_to_communes = {}

    regions_file = cache_raw_dir / "referentiel_regions.json"
    if regions_file.exists():
        try:
            with open(regions_file, "r") as f:
                regs = json.load(f)
                region_name_to_code = {clean_and_normalize(r['LIBELLE']): r['REG'] for r in regs if 'LIBELLE' in r}
                # Add aliases
                region_name_to_code[clean_and_normalize("Bourgogne-Franche-Comté")] = "27"
                region_name_to_code[clean_and_normalize("Centre-Val de Loire")] = "24"
                region_name_to_code[clean_and_normalize("Occitanie")] = "76"
                region_name_to_code[clean_and_normalize("Auvergne Rhône-Alpes")] = "84"
                region_name_to_code[clean_and_normalize("Île-de-France")] = "11"
        except Exception as e:
            logger.warning(f"Failed to parse referentiel_regions.json: {e}")

    depts_file = cache_raw_dir / "referentiel_departements.json"
    if depts_file.exists():
        try:
            with open(depts_file, "r") as f:
                depts = json.load(f)
                dept_name_to_code = {clean_and_normalize(d['LIBELLE']): clean_dept_code(d['DEP']) for d in depts if 'LIBELLE' in d}
        except Exception as e:
            logger.warning(f"Failed to parse referentiel_departements.json: {e}")

    epci_file = cache_raw_dir / "ref_epci.json"
    if epci_file.exists():
        try:
            with open(epci_file, "r") as f:
                epcis = json.load(f)
                epci_lookup = {clean_and_normalize(e['nom']): e['code'] for e in epcis if 'nom' in e}
                epci_to_communes = {e['code']: [m['code'] for m in e.get('membres', [])] for e in epcis if 'code' in e}
        except Exception as e:
            logger.warning(f"Failed to parse ref_epci.json: {e}")

    # 3. Index communes for fast exact/global lookup
    commune_lookup = {}
    commune_global_lookup = {}
    
    if "commune_name" in communes_df.columns:
        for idx, row in communes_df.iterrows():
            c_name = row["commune_name"]
            if pd.isna(c_name):
                continue
            norm_name = clean_and_normalize(str(c_name))
            dep = clean_dept_code(row.get('dep_code', ''))
            cod = str(row['codgeo'])
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
        "communes": ("Commune", 1.0)
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
                communes_df=communes_df
            )
            
            if res['resolved']:
                for cod in res['codgeo_list']:
                    parent_cod = CHILD_TO_PARENT.get(cod, cod)
                    codgeo_scores[parent_cod] = max(codgeo_scores.get(parent_cod, 0.0), score_to_assign)
            else:
                logger.warning(f"Failed to resolve CTAI entity: {name} (type: {type_hint})")

    # 5. Build final Series matching communes_df codgeo values
    final_scores = []
    for idx, row in communes_df.iterrows():
        cod = str(row['codgeo'])
        lookup_cod = CHILD_TO_PARENT.get(cod, cod)
        final_scores.append(codgeo_scores.get(lookup_cod, 0.0))
        
    return pd.Series(final_scores, index=communes_df.index)
