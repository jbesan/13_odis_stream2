import pandas as pd
import json
import re
import unicodedata
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Constants
PLM_PARENTS = {
    "75056": [str(x) for x in range(75101, 75121)],  # Paris
    "13055": [str(x) for x in range(13201, 13217)],  # Marseille
    "69123": [str(x) for x in range(69381, 69390)],  # Lyon
}

# Reverse mapping: child -> parent
CHILD_TO_PARENT = {}
for parent, children in PLM_PARENTS.items():
    for child in children:
        CHILD_TO_PARENT[child] = parent

def clean_and_normalize(name: str) -> str:
    """Normalize names for matching by removing accents, parentheses, and common prefixes."""
    if not isinstance(name, str):
        return ""
    name = name.strip()
    
    # Lowercase
    name = name.lower()
    
    # Handle parentheses
    # E.g. "Percy (Le)" -> "le percy"
    # E.g. "Vigan (Le)" -> "le vigan"
    # E.g. "Diois (Communauté de Communes)" -> "diois"
    m = re.search(r'\((.*?)\)', name)
    if m:
        content = m.group(1).strip()
        if content in ['le', 'la', 'les', 'l\'', "l"]:
            name = re.sub(r'\s*\(.*?\)', '', name)
            name = f"{content} {name}"
        else:
            name = re.sub(r'\s*\(.*?\)', '', name)
            
    # Replace all types of apostrophes with space
    name = name.replace("'", " ").replace("’", " ")
    
    # Remove accents
    name = "".join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn')
    
    # Strip common prefixes/suffixes after type detection
    name = re.sub(r'^(cr|cd|cc|ca|cu|ville de|mairie de|communaute de communes du|communaute de communes|metropole|region de la|region de l\'|region de|region|departement de la|departement de l\'|departement de|departement)\s+', '', name)
    name = re.sub(r'\s+(metropole)$', '', name)
    
    # Replace dashes and punctuation with space
    name = re.sub(r'[-_/,.]', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def clean_dept_code(d: Any) -> str:
    """Normalize department codes to standard 2-digit strings (or 3 for DOM-TOM)."""
    if pd.isna(d):
        return ""
    try:
        val = str(int(float(d)))
    except ValueError:
        val = str(d).strip()
    if len(val) == 1:
        val = "0" + val
    return val

def get_dept_from_pc(pc: Any) -> str:
    """Extract department code from postal code."""
    if not pc or pd.isna(pc):
        return ""
    pc = str(pc).strip().zfill(5)
    if len(pc) >= 5:
        # Check DOM-TOM
        if pc.startswith(('97', '98')):
            return pc[:3]
        return pc[:2]
    return ""

def resolve_entity(
    name: str,
    dept_code_input: Any,
    pc_input: Any = None,
    type_hint: Optional[str] = None,
    region_name_to_code: Dict[str, str] = None,
    dept_name_to_code: Dict[str, str] = None,
    epci_lookup: Dict[str, str] = None,
    epci_to_communes: Dict[str, List[str]] = None,
    commune_lookup: Dict[tuple, str] = None,
    commune_global_lookup: Dict[str, List[tuple]] = None,
    communes_df: pd.DataFrame = None
) -> Dict[str, Any]:
    """Resolves an ANVITA member entity name to its type and list of constituent codgeo values."""
    orig_name_lower = name.lower().strip()
    
    # Strip accents for keyword detection
    orig_name_clean = "".join(c for c in unicodedata.normalize('NFD', orig_name_lower) if unicodedata.category(c) != 'Mn')
    
    # 1. Determine type using the original name structure before normalization
    entity_type = "Commune"
    if type_hint:
        entity_type = type_hint
    else:
        if orig_name_clean.startswith("cd ") or "departement" in orig_name_clean:
            entity_type = "Département"
        elif orig_name_clean.startswith("cr ") or "region" in orig_name_clean or clean_and_normalize(name) in region_name_to_code:
            entity_type = "Région"
        elif (orig_name_clean.startswith("cc ") or orig_name_clean.startswith("ca ") or orig_name_clean.startswith("cu ") or
              "metropole" in orig_name_clean or "communaute" in orig_name_clean or "vallees" in orig_name_clean or "pays basque" in orig_name_clean):
            entity_type = "Intercommunalité"
            
    # 2. Get department code, cleaning and aligning with postal code if available
    dept_code = clean_dept_code(dept_code_input)
    dept_from_pc = get_dept_from_pc(pc_input)
    
    if dept_from_pc:
        dept_code = dept_from_pc
        
    norm_name = clean_and_normalize(name)
    
    # Intercommunalité self-correction fallback:
    # If it was classified as a Commune, but is not in commune_lookup and is an exact match in epci_lookup, reclassify!
    if entity_type == "Commune" and commune_lookup and (norm_name, dept_code) not in commune_lookup and epci_lookup and norm_name in epci_lookup:
        entity_type = "Intercommunalité"
        
    resolved = False
    codgeo_list = []
    match_method = ""
    resolved_id = ""
    
    # Manual fallback for associated/merged communes
    manual_communes = {
        ("lomme", "59"): "59350",  # Lomme is associated with Lille
    }
    
    if entity_type == "Région":
        reg_code = region_name_to_code.get(norm_name, '') if region_name_to_code else ''
        if reg_code:
            coms = communes_df[communes_df['reg_code'] == reg_code]['codgeo'].tolist()
            codgeo_list = coms
            resolved_id = reg_code
            match_method = "region_code"
            resolved = True
            
    elif entity_type == "Département":
        dept_val = dept_code if dept_code else (dept_name_to_code.get(norm_name, '') if dept_name_to_code else '')
        if dept_val:
            coms = communes_df[communes_df['dep_code'] == dept_val]['codgeo'].tolist()
            codgeo_list = coms
            resolved_id = dept_val
            match_method = "dept_code"
            resolved = True
            
    elif entity_type == "Intercommunalité":
        epci_code = epci_lookup.get(norm_name, '') if epci_lookup else ''
        if not epci_code and epci_lookup:
            # Fuzzy match (substring)
            for epci_n, code in epci_lookup.items():
                if norm_name in epci_n or epci_n in norm_name:
                    epci_code = code
                    break
        if epci_code and epci_to_communes:
            coms = epci_to_communes.get(epci_code, [])
            codgeo_list = coms
            resolved_id = epci_code
            match_method = "epci_siren"
            resolved = True
            
    else:  # Commune
        # Check manual alias first
        if (norm_name, dept_code) in manual_communes:
            codgeo_list = [manual_communes[(norm_name, dept_code)]]
            resolved_id = codgeo_list[0]
            match_method = "manual_alias"
            resolved = True
        # Exact match
        elif commune_lookup and (norm_name, dept_code) in commune_lookup:
            codgeo_list = [commune_lookup[(norm_name, dept_code)]]
            resolved_id = codgeo_list[0]
            match_method = "exact_name_dept"
            resolved = True
        else:
            # Try fuzzy / substring match in the department first
            dept_coms = communes_df[communes_df['dep_code'] == dept_code] if communes_df is not None else pd.DataFrame()
            matches = []
            for idx, row in dept_coms.iterrows():
                if pd.isna(row['commune_name']):
                    continue
                c_norm = clean_and_normalize(row['commune_name'])
                if norm_name in c_norm or c_norm in norm_name:
                    matches.append(row['codgeo'])
            
            if len(matches) == 1:
                codgeo_list = [matches[0]]
                resolved_id = matches[0]
                match_method = "fuzzy_dept_match"
                resolved = True
            elif len(matches) > 1:
                codgeo_list = matches
                resolved_id = ",".join(matches)
                match_method = "fuzzy_dept_ambiguous"
                resolved = True
            else:
                # Fallback to global match
                if commune_global_lookup and norm_name in commune_global_lookup:
                    global_matches = commune_global_lookup[norm_name]
                    if len(global_matches) == 1:
                        codgeo_list = [global_matches[0][1]]
                        resolved_id = global_matches[0][1]
                        match_method = "global_name_unique_fallback"
                        resolved = True
                    else:
                        # Ambiguous globally
                        codgeo_list = [m[1] for m in global_matches]
                        resolved_id = ",".join(codgeo_list)
                        match_method = "global_name_ambiguous_fallback"
                        resolved = True
                else:
                    # Fuzzy match globally as last resort
                    matches_global = []
                    if communes_df is not None:
                        for idx, row in communes_df.iterrows():
                            if pd.isna(row['commune_name']):
                                continue
                            c_norm = clean_and_normalize(row['commune_name'])
                            if norm_name in c_norm or c_norm in norm_name:
                                matches_global.append((row['dep_code'], row['codgeo']))
                    if len(matches_global) == 1:
                        codgeo_list = [matches_global[0][1]]
                        resolved_id = matches_global[0][1]
                        match_method = "fuzzy_global_unique_fallback"
                        resolved = True

    return {
        'resolved': resolved,
        'type': entity_type,
        'resolved_id': resolved_id,
        'codgeo_list': codgeo_list,
        'match_method': match_method
    }

def compute_anvita_scores(
    communes_df: pd.DataFrame,
    cache_raw_dir: Path,
    excel_path: Path
) -> pd.Series:
    """
    Computes territory score boosts (1.0, 0.5, or 0.0) for ANVITA membership.
    Reads private Excel trackers and matches them using raw cached referential files.
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
    
    # 1. Soft fail check for Excel file
    if not excel_path.exists():
        logger.warning(f"ANVITA Excel file not found at: {excel_path}. Falling back to 0.0 for all communes.")
        return default_scores
        
    try:
        excel_df = pd.read_excel(excel_path, sheet_name='CT Membres ANVITA V2')
    except Exception as e:
        logger.error(f"Failed to read ANVITA Excel file: {e}. Falling back to 0.0.")
        return default_scores

    # Clean empty rows
    excel_members = excel_df[excel_df['Collectivité'].notna()]
    if excel_members.empty:
        logger.warning("ANVITA Excel sheet has no members listed. Returning 0.0.")
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
    # Map (normalized_name, dep_code) -> codgeo
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

    # 4. Map each Excel member
    codgeo_scores = {}
    for idx, row in excel_members.iterrows():
        name = str(row['Collectivité']).strip()
        dept = clean_dept_code(row.get('Département', ''))
        
        res = resolve_entity(
            name=name,
            dept_code_input=dept,
            pc_input=None,
            type_hint=None,
            region_name_to_code=region_name_to_code,
            dept_name_to_code=dept_name_to_code,
            epci_lookup=epci_lookup,
            epci_to_communes=epci_to_communes,
            commune_lookup=commune_lookup,
            commune_global_lookup=commune_global_lookup,
            communes_df=communes_df
        )
        
        if res['resolved']:
            # Communes and EPCIs get 1.0, Departments and Regions get 0.5
            score_to_assign = 1.0 if res['type'] in ['Commune', 'Intercommunalité'] else 0.5
            for cod in res['codgeo_list']:
                # Remap child ARR to parent PLM code (Paris, Lyon, Marseille)
                parent_cod = CHILD_TO_PARENT.get(cod, cod)
                # Keep maximum score if duplicate
                codgeo_scores[parent_cod] = max(codgeo_scores.get(parent_cod, 0.0), score_to_assign)

    # 5. Build final Series matching communes_df codgeo values
    # Also handle remapping of children in the input Series to assign their score from parent
    final_scores = []
    for idx, row in communes_df.iterrows():
        cod = str(row['codgeo'])
        # If it is a child arrondissement, we look up the score of its parent PLM code
        lookup_cod = CHILD_TO_PARENT.get(cod, cod)
        final_scores.append(codgeo_scores.get(lookup_cod, 0.0))
        
    return pd.Series(final_scores, index=communes_df.index)
