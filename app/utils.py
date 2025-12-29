import unicodedata
from typing import Set, Tuple, List, Optional, Any
import pandas as pd
import numpy as np
import os
import base64
import logging
from pathlib import Path
import config as cfg

logger = logging.getLogger(__name__)

def normalize_text(text: str) -> str:
    """
    Normalizes text by removing accents and lowercasing.
    """
    if not isinstance(text, str):
        return str(text)
    return ''.join(c for c in unicodedata.normalize('NFD', text)
                   if unicodedata.category(c) != 'Mn').lower()

def calculate_token_overlap(
    query_tokens: Set[str], 
    target_tokens: Set[str],
    stop_words: Optional[Set[str]] = None
) -> int:
    """
    Calculates the number of overlapping tokens between query and target.
    """
    if stop_words:
        q_tokens = query_tokens - stop_words
        # If query was ONLY stop words, use original query
        if not q_tokens and query_tokens:
            q_tokens = query_tokens
    else:
        q_tokens = query_tokens

    return len(q_tokens.intersection(target_tokens))

def calculate_fuzzy_match_score(
    query_norm: str,
    target_norm: str,
    query_tokens: Set[str],
    target_tokens: Set[str],
    stop_words: Optional[Set[str]] = None,
    weights: dict = None
) -> int:
    """
    Generic fuzzy match scoring helper.
    Default weights can be overridden.
    """
    if weights is None:
        weights = {
            'exact': 100,
            'starts_with': 50,
            'contains': 20,
            'token_overlap': 10,
            'substring_match': 0
        }

    score = 0
    
    # A. Exact Match
    if query_norm == target_norm:
        score += weights.get('exact', 0)
    # B. Starts With
    elif target_norm.startswith(query_norm):
        score += weights.get('starts_with', 0)
    # C. Contains
    elif query_norm in target_norm:
        score += weights.get('contains', 0)
        
    # D. Token Overlap
    overlap = calculate_token_overlap(query_tokens, target_tokens, stop_words)
    score += overlap * weights.get('token_overlap', 0)
    
    return score

def sanitize_for_json(obj: Any) -> Any:
    """
    Recursively sanitizes the object for JSON serialization.
    - Dicts: Recursively sanitize values. Keys with None/NaN values are REMOVED.
    - Lists: Recursively sanitize items.
    - Floats: NaNs become None.
    """
    if isinstance(obj, dict):
        clean = {}
        for k, v in obj.items():
            sanitized_val = sanitize_for_json(v)
            if sanitized_val is not None:
                clean[k] = sanitized_val
        return clean
    elif isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    elif isinstance(obj, float):
        if np.isnan(obj) or pd.isna(obj):
            return None
        return obj
    elif isinstance(obj, (np.integer, np.floating)):
        if pd.isna(obj):
             return None
        return obj.item()
    elif pd.isna(obj): # Catch-all for other NA types
        return None
    return obj

def get_asset_path(filename: str) -> str:
    """Returns the absolute path to an asset file."""
    return os.path.join(cfg.ASSETS_DIR, filename)

def get_base64_image(image_path: str) -> str:
    """Encodes an image to base64 for embedding in Markdown."""
    if not image_path: return ""
    
    p = Path(image_path)
    if not p.exists():
        logger.warning(f"Image not found: {p}")
        return ""
    
    try:
        with open(p, "rb") as f:
            data = f.read()
        return base64.b64encode(data).decode()
    except Exception as e:
        logger.error(f"Error encoding image {image_path}: {e}")
        return ""
from pyproj import Transformer

def project_point(lon: float, lat: float, from_crs: str = "EPSG:4326", to_crs: str = "EPSG:2154") -> Tuple[float, float]:
    """
    Transforms a single coordinate point using scalars to avoid NumPy 1.25+ DeprecationWarning.
    """
    transformer = Transformer.from_crs(from_crs, to_crs, always_xy=True)
    x, y = transformer.transform(lon, lat)
    return x, y
