import math
import unicodedata
from typing import Set, Tuple, Optional, Any
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
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    ).lower()


def calculate_token_overlap(
    query_tokens: Set[str],
    target_tokens: Set[str],
    stop_words: Optional[Set[str]] = None,
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
    weights: Optional[dict] = None,
) -> int:
    """
    Generic fuzzy match scoring helper.
    Default weights can be overridden.
    """
    if weights is None:
        weights = {
            "exact": 100,
            "starts_with": 50,
            "contains": 20,
            "token_overlap": 10,
            "substring_match": 0,
        }

    score = 0

    # A. Exact Match
    if query_norm == target_norm:
        score += weights.get("exact", 0)
    # B. Starts With
    elif target_norm.startswith(query_norm):
        score += weights.get("starts_with", 0)
    # C. Contains
    elif query_norm in target_norm:
        score += weights.get("contains", 0)

    # D. Token Overlap
    overlap = calculate_token_overlap(query_tokens, target_tokens, stop_words)
    score += overlap * weights.get("token_overlap", 0)

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
    elif pd.isna(obj):  # Catch-all for other NA types
        return None
    return obj


def get_asset_path(filename: str) -> str:
    """Returns the absolute path to an asset file."""
    return os.path.join(cfg.ASSETS_DIR, filename)


def get_base64_image(image_path: str) -> str:
    """Encodes an image to base64 for embedding in Markdown."""
    if not image_path:
        return ""

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


# Official IGN Lambert-93 (EPSG:2154) / GRS80 ellipsoidal parameters
_L93_A = 6378137.0
_L93_E = 0.08181919106
_L93_C = 11754255.426096
_L93_N = 0.7256077650532473
_L93_XS = 700000.0
_L93_YS = 12655612.0499
_L93_LON0 = 3.0 * math.pi / 180.0


def _lambert93_to_wgs84(x: float, y: float) -> Tuple[float, float]:
    """Converts Lambert-93 (EPSG:2154) meters (x, y) to WGS84 (EPSG:4326) degrees (lon, lat)."""
    dx = x - _L93_XS
    dy = _L93_YS - y
    r = math.hypot(dx, dy)
    gamma = math.atan2(dx, dy)
    lon_rad = _L93_LON0 + gamma / _L93_N

    l = -(1.0 / _L93_N) * math.log(abs(r / _L93_C))
    phi = 2.0 * math.atan(math.exp(l)) - math.pi / 2.0
    for _ in range(5):
        esphi = _L93_E * math.sin(phi)
        phi = 2.0 * math.atan(math.pow((1.0 + esphi) / (1.0 - esphi), _L93_E / 2.0) * math.exp(l)) - math.pi / 2.0

    return math.degrees(lon_rad), math.degrees(phi)


def _wgs84_to_lambert93(lon: float, lat: float) -> Tuple[float, float]:
    """Converts WGS84 (EPSG:4326) degrees (lon, lat) to Lambert-93 (EPSG:2154) meters (x, y)."""
    phi = math.radians(lat)
    lam = math.radians(lon)
    esphi = _L93_E * math.sin(phi)
    l = math.log(math.tan(math.pi / 4.0 + phi / 2.0) * math.pow((1.0 - esphi) / (1.0 + esphi), _L93_E / 2.0))
    gamma = (lam - _L93_LON0) * _L93_N
    r = _L93_C * math.exp(-_L93_N * l)
    x = _L93_XS + r * math.sin(gamma)
    y = _L93_YS - r * math.cos(gamma)
    return x, y


def project_point(
    lon: float, lat: float, from_crs: str = "EPSG:4326", to_crs: str = "EPSG:2154"
) -> Tuple[float, float]:
    """Transforms a coordinate pair between Lambert-93 (EPSG:2154) and WGS84 (EPSG:4326).

    Uses the closed-form official IGN projection formulas in pure Python,
    removing the heavy C/C++ PROJ/pyproj dependency.

    Args:
        lon: First coordinate (X/easting in meters if Lambert-93, longitude in degrees if WGS84).
        lat: Second coordinate (Y/northing in meters if Lambert-93, latitude in degrees if WGS84).
        from_crs: Source CRS identifier (default: "EPSG:4326").
        to_crs: Destination CRS identifier (default: "EPSG:2154").

    Returns:
        Tuple[float, float]: Transformed coordinate pair (first, second).
    """
    from_norm = str(from_crs).upper().replace("EPSG:", "").strip()
    to_norm = str(to_crs).upper().replace("EPSG:", "").strip()

    if from_norm == to_norm:
        return float(lon), float(lat)

    if from_norm == "2154" and to_norm == "4326":
        return _lambert93_to_wgs84(float(lon), float(lat))

    if from_norm == "4326" and to_norm == "2154":
        return _wgs84_to_lambert93(float(lon), float(lat))

    raise ValueError(f"Unsupported CRS transformation: {from_crs} -> {to_crs}")
