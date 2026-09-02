"""
High-performance Decoupled Vector Map Component for ODIS Stream 2.

Decouples static commune geometries (~10 MB, cached once in browser HTTP cache)
from dynamic scoring payloads (~250 KB JSON dictionary) using Deck.gl Standalone WebGL.
Renders 35,000 communes at 60 FPS with instant WebGL shader color updates.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Set

import pandas as pd
import streamlit as st

import config as cfg
from core.models import SearchCriterias
from core.maps_deck import _get_geom

logger = logging.getLogger(__name__)


def prepare_map_payload(
    gdf_scores: Optional[pd.DataFrame] = None,
    center: Optional[List[float]] = None,
    zoom: Optional[int] = None,
    search_results: Optional[Any] = None,
    config: Optional[SearchCriterias] = None,
    pois_df: Optional[pd.DataFrame] = None,
    selected_ids: Optional[Set[str]] = None,
    highlighted_rank: Optional[int] = None,
    show_top_5: bool = True,
    current_map_context: Optional[pd.DataFrame] = None,
    center_offset_lon: float = 0.0,
    inclusion_services_index: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Prepares a lightweight JSON payload containing scores, markers, and view state.

    Zero geometry data is included in this payload, ensuring instantaneous WebSocket transmission.

    Args:
        gdf_scores: DataFrame with commune scores indexed or keyed by codgeo.
        center: Initial map center [latitude, longitude].
        zoom: Initial map zoom level.
        search_results: SearchResultsData object containing top results and reference geo.
        config: User search criteria configuration.
        pois_df: GeoDataFrame of points of interest.
        selected_ids: Set of active POI category filter keys ('mairie', 'edu', 'sante', 'inc').
        highlighted_rank: Rank index of currently highlighted commune card (-1 for wished city).
        show_top_5: Whether to render Top 5 and wished city badges.
        current_map_context: Optional DataFrame context for centroid resolution.
        center_offset_lon: Longitudinal offset applied when sidebars are open.
        inclusion_services_index: Optional DataFrame mapping inclusion service slugs to human labels.

    Returns:
        Dictionary payload containing scores dictionary, marker arrays, and view state coordinates.
    """
    if selected_ids is None:
        selected_ids = set()

    gdf_ctx = current_map_context if current_map_context is not None else gdf_scores

    # 1. Extract minimal scores dictionary: {codgeo: score_float}
    scores_dict: Dict[str, float] = {}
    if gdf_scores is not None and not gdf_scores.empty:
        score_col = "weighted_score" if "weighted_score" in gdf_scores.columns else "score"
        if score_col in gdf_scores.columns:
            if "codgeo" in gdf_scores.columns:
                ids = gdf_scores["codgeo"].astype(str).values
            else:
                ids = gdf_scores.index.astype(str).values
            scores = gdf_scores[score_col].fillna(0.0).clip(0.0, 1.0).round(3).values
            scores_dict = dict(zip(ids, scores))

    # 2. ViewState configuration
    lat = center[0] if center and len(center) >= 2 else cfg.DEFAULT_MAP_CENTER[0]
    lon = center[1] if center and len(center) >= 2 else cfg.DEFAULT_MAP_CENTER[1]
    lon += center_offset_lon
    zoom_val = zoom if zoom is not None else cfg.DEFAULT_MAP_ZOOM

    # 3. Top 5 & Shortlisted City Markers
    top_markers: List[Dict[str, Any]] = []
    if search_results and show_top_5 and getattr(search_results, "results", None):
        for i, c in enumerate(search_results.results[:5]):
            centroid = _get_geom(c, "centroid", gdf_context=gdf_ctx)
            if centroid is not None:
                top_markers.append({
                    "rank": i + 1,
                    "name": getattr(c, "name", f"Top {i+1}"),
                    "codgeo": str(getattr(c, "codgeo", "")),
                    "score_pct": f"{getattr(c, 'global_score', 0.0) * 100:.0f}%",
                    "lat": float(centroid.y),
                    "lon": float(centroid.x),
                    "is_highlighted": (highlighted_rank == i),
                    "type": "top5",
                })

        # Shortlisted city (Ville pressentie)
        p_city = getattr(search_results, "commune_pressentie", None)
        if p_city is not None:
            p_centroid = _get_geom(p_city, "centroid", gdf_context=gdf_ctx)
            if p_centroid is not None:
                top_markers.append({
                    "rank": 0,
                    "name": getattr(p_city, "name", "Ville Souhaitée"),
                    "codgeo": str(getattr(p_city, "codgeo", "")),
                    "score_pct": f"{getattr(p_city, 'global_score', 0.0) * 100:.0f}%",
                    "lat": float(p_centroid.y),
                    "lon": float(p_centroid.x),
                    "is_highlighted": (highlighted_rank == -1),
                    "type": "pressentie",
                })

    # 4. Current reference location
    current_marker = None
    if search_results and getattr(search_results, "current_geo", None):
        c_geo = search_results.current_geo
        c_centroid = _get_geom(c_geo, "centroid", gdf_context=gdf_ctx)
        if c_centroid is not None:
            current_marker = {
                "name": getattr(c_geo, "name", "Commune Actuelle"),
                "codgeo": str(getattr(c_geo, "codgeo", "")),
                "lat": float(c_centroid.y),
                "lon": float(c_centroid.x),
                "type": "current",
            }

    # 5. POI Markers (Mairies, Écoles, Santé, Inclusion)
    poi_markers: List[Dict[str, Any]] = []
    if search_results and pois_df is not None and not pois_df.empty:
        target_codgeos = {str(c.codgeo) for c in getattr(search_results, "results", [])}
        if getattr(search_results, "commune_pressentie", None):
            target_codgeos.add(str(search_results.commune_pressentie.codgeo))

        pois_work = pois_df.copy()
        if "codgeo" in pois_work.columns:
            pois_work["codgeo_norm"] = pois_work["codgeo"].astype(str).str.strip().str.zfill(5)
            pois_filtered = pois_work[pois_work["codgeo_norm"].isin(target_codgeos)]

            # A. Mairies
            if "mairie" in selected_ids:
                mairies = pois_filtered[pois_filtered["category"] == "mairie"]
                for _, r in mairies.iterrows():
                    lat_val = r["geometry"].y if hasattr(r.get("geometry"), "y") else r.get("lat")
                    lon_val = r["geometry"].x if hasattr(r.get("geometry"), "x") else r.get("lon")
                    if pd.notna(lat_val) and pd.notna(lon_val):
                        poi_markers.append({
                            "name": str(r.get("name", "Mairie")),
                            "type": "Mairie",
                            "category": "mairie",
                            "color": "#F5D819",
                            "icon": "🏛️",
                            "lat": float(lat_val),
                            "lon": float(lon_val),
                        })

            # B. Écoles
            if "edu" in selected_ids:
                ecoles = pois_filtered[pois_filtered["category"] == "education"]
                for _, r in ecoles.iterrows():
                    lat_val = r["geometry"].y if hasattr(r.get("geometry"), "y") else r.get("lat")
                    lon_val = r["geometry"].x if hasattr(r.get("geometry"), "x") else r.get("lon")
                    if pd.notna(lat_val) and pd.notna(lon_val):
                        poi_markers.append({
                            "name": str(r.get("name", "Établissement")),
                            "type": str(r.get("type", "École")),
                            "category": "education",
                            "color": "#22C55E",
                            "icon": "🎓",
                            "lat": float(lat_val),
                            "lon": float(lon_val),
                        })

            # C. Santé
            if "sante" in selected_ids:
                sante = pois_filtered[pois_filtered["category"] == "sante"]
                for _, r in sante.iterrows():
                    lat_val = r["geometry"].y if hasattr(r.get("geometry"), "y") else r.get("lat")
                    lon_val = r["geometry"].x if hasattr(r.get("geometry"), "x") else r.get("lon")
                    if pd.notna(lat_val) and pd.notna(lon_val):
                        poi_markers.append({
                            "name": str(r.get("name", "Établissement")),
                            "type": str(r.get("type", "Santé")),
                            "category": "sante",
                            "color": "#3B82F6",
                            "icon": "🏥",
                            "lat": float(lat_val),
                            "lon": float(lon_val),
                        })

            # D. Inclusion
            if "inc" in selected_ids:
                inc = pois_filtered[
                    pois_filtered["category"].isin(["incl_services", "inclusion"])
                ]
                if not inc.empty:
                    inc_sel = (
                        getattr(config, "inc_services_selection", None)
                        if config
                        else None
                    )
                    if inc_sel:
                        if isinstance(inc_sel, list):
                            slugs = [
                                item.code if hasattr(item, "code") else str(item)
                                for item in inc_sel
                            ]
                            mask = inc["type"].isin(slugs)
                        elif isinstance(inc_sel, dict):
                            mask = inc["type"].isin(inc_sel.keys())
                        else:
                            mask = pd.Series(False, index=inc.index)
                        inc = inc[mask]

                    for _, r in inc.iterrows():
                        lat_val = (
                            r["geometry"].y
                            if hasattr(r.get("geometry"), "y")
                            else r.get("lat")
                        )
                        lon_val = (
                            r["geometry"].x
                            if hasattr(r.get("geometry"), "x")
                            else r.get("lon")
                        )
                        raw_type = str(r.get("type", "Inclusion"))
                        display_type = raw_type
                        if (
                            inclusion_services_index is not None
                            and not inclusion_services_index.empty
                            and raw_type in inclusion_services_index.index
                        ):
                            val = inclusion_services_index.loc[raw_type, "label"]
                            display_type = str(val if isinstance(val, str) else val.iloc[0])

                        if pd.notna(lat_val) and pd.notna(lon_val):
                            poi_markers.append({
                                "name": str(r.get("name", "Structure")),
                                "type": display_type,
                                "category": "incl_services",
                                "color": "#A855F7",
                                "icon": "🤝",
                                "lat": float(lat_val),
                                "lon": float(lon_val),
                            })

    return {
        "scores": scores_dict,
        "center": [lat, lon],
        "zoom": zoom_val,
        "top_markers": top_markers,
        "current_marker": current_marker,
        "poi_markers": poi_markers,
        "geojson_url": "/app/static/data/communes_france.geojson",
    }


def render_vector_map(
    gdf_scores: Optional[pd.DataFrame] = None,
    center: Optional[List[float]] = None,
    zoom: Optional[int] = None,
    search_results: Optional[Any] = None,
    config: Optional[SearchCriterias] = None,
    pois_df: Optional[pd.DataFrame] = None,
    selected_ids: Optional[Set[str]] = None,
    highlighted_rank: Optional[int] = None,
    show_top_5: bool = True,
    current_map_context: Optional[pd.DataFrame] = None,
    center_offset_lon: float = 0.0,
    inclusion_services_index: Optional[pd.DataFrame] = None,
    height: int = 1500,
) -> None:
    """Renders the decoupled Vector Choropleth Map using Deck.gl Standalone WebGL.

    Args:
        gdf_scores: DataFrame with commune scores indexed or keyed by codgeo.
        center: Initial map center [latitude, longitude].
        zoom: Initial map zoom level.
        search_results: SearchResultsData object containing top results and reference geo.
        config: User search criteria configuration.
        pois_df: GeoDataFrame of points of interest.
        selected_ids: Set of active POI category filter keys ('edu', 'sante', 'inc').
        highlighted_rank: Rank index of currently highlighted commune card.
        show_top_5: Whether to render Top 5 and wished city badges.
        current_map_context: Optional DataFrame context for centroid resolution.
        center_offset_lon: Longitudinal offset applied when sidebars are open.
        inclusion_services_index: Optional DataFrame mapping inclusion service slugs to human labels.
        height: Height in pixels for the map iframe component.
    """
    payload = prepare_map_payload(
        gdf_scores=gdf_scores,
        center=center,
        zoom=zoom,
        search_results=search_results,
        config=config,
        pois_df=pois_df,
        selected_ids=selected_ids,
        highlighted_rank=highlighted_rank,
        show_top_5=show_top_5,
        current_map_context=current_map_context,
        center_offset_lon=center_offset_lon,
        inclusion_services_index=inclusion_services_index,
    )

    payload_json = json.dumps(payload)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet" />
      <script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
      <script src="https://unpkg.com/deck.gl@8.9.36/dist.min.js"></script>
      <style>
        body, html {{
          margin: 0;
          padding: 0;
          width: 100%;
          height: 100%;
          overflow: hidden;
          background: #e5e7eb;
        }}
        #container {{
          width: 100%;
          height: 100vh;
          position: relative;
        }}
        #odis-tooltip {{
          position: absolute;
          display: none;
          pointer-events: none;
          background: rgba(27, 68, 41, 0.95);
          color: white;
          font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          border-radius: 8px;
          padding: 8px 12px;
          font-size: 13px;
          line-height: 1.4;
          box-shadow: 0 8px 20px rgba(0,0,0,0.35);
          border: 1px solid rgba(255,255,255,0.2);
          z-index: 10000;
          transform: translate(-50%, -120%);
        }}
      </style>
    </head>
    <body>
      <div id="container"></div>
      <div id="odis-tooltip"></div>
      <script>
        const payload = {payload_json};
        const tooltipEl = document.getElementById('odis-tooltip');

        // 9-stop YlGn palette in RGBA format [R, G, B, A]
        const ylgnRgb = [
          [255, 255, 229, 190],
          [247, 252, 185, 190],
          [217, 240, 163, 190],
          [173, 221, 142, 190],
          [120, 198, 121, 190],
          [65, 171, 93, 190],
          [35, 132, 67, 190],
          [0, 104, 55, 190],
          [0, 69, 41, 190]
        ];

        // OpenStreetMap Humanitarian (HOT) style definition
        const mapStyle = {{
          version: 8,
          sources: {{
            'osm-hot-tiles': {{
              type: 'raster',
              tiles: [
                'https://a.tile.openstreetmap.fr/hot/{{z}}/{{x}}/{{y}}.png',
                'https://b.tile.openstreetmap.fr/hot/{{z}}/{{x}}/{{y}}.png',
                'https://c.tile.openstreetmap.fr/hot/{{z}}/{{x}}/{{y}}.png'
              ],
              tileSize: 256,
              attribution: '&copy; OpenStreetMap contributors, Tiles by Humanitarian OpenStreetMap Team'
            }}
          }},
          layers: [
            {{
              id: 'osm-hot-tiles-layer',
              type: 'raster',
              source: 'osm-hot-tiles',
              minzoom: 0,
              maxzoom: 19
            }}
          ]
        }};

        const CACHE_NAME = 'odis-communes-v1';
        const CACHE_TTL_MS = 365 * 24 * 60 * 60 * 1000; // 1 an
        const CACHE_TIMESTAMP_KEY = 'odis_communes_geojson_cached_at';

        function getTopWindow() {{
          try {{
            if (window.parent && window.parent !== window) {{
              const test = window.parent.location.origin;
              return window.parent;
            }}
          }} catch (e) {{
            // Fallback en cas d'isolation cross-origin
          }}
          return window;
        }}

        async function fetchGeoJson() {{
          const topWin = getTopWindow();

          // 1. Tier 1 : Cache memoire sur window.parent (survit aux reruns d'iframe, 0 ms, 0 parsing)
          if (topWin.__communesGeoJsonMemoryCache) {{
            return topWin.__communesGeoJsonMemoryCache;
          }}
          if (window.__communesGeoJsonMemoryCache) {{
            return window.__communesGeoJsonMemoryCache;
          }}

          // Dedoublonnage des requetes simultanees
          if (topWin.__communesGeoJsonPromise) {{
            return await topWin.__communesGeoJsonPromise;
          }}

          topWin.__communesGeoJsonPromise = (async () => {{
            const origin = window.location.origin.startsWith('http')
              ? window.location.origin
              : (topWin.location.origin.startsWith('http') ? topWin.location.origin : '');
            const candidates = [
              '/app/static/data/communes_france.geojson',
              '/static/data/communes_france.geojson',
              origin + '/app/static/data/communes_france.geojson',
              origin + '/static/data/communes_france.geojson'
            ];

            // 2. Tier 2 : CacheStorage du navigateur (window.caches / topWin.caches) avec TTL de 1 an
            let cacheStorage = null;
            try {{
              cacheStorage = topWin.caches || window.caches || null;
            }} catch (e) {{
              cacheStorage = null;
            }}

            if (cacheStorage) {{
              try {{
                let cachedAt = 0;
                try {{
                  const storedTime = (topWin.localStorage || window.localStorage).getItem(CACHE_TIMESTAMP_KEY);
                  if (storedTime) {{
                    cachedAt = parseInt(storedTime, 10);
                  }}
                }} catch (e) {{
                  // localStorage inaccessible
                }}

                const now = Date.now();
                const isExpired = !cachedAt || (now - cachedAt > CACHE_TTL_MS);

                const cache = await cacheStorage.open(CACHE_NAME);
                if (!isExpired) {{
                  for (const url of candidates) {{
                    const cachedResponse = await cache.match(url);
                    if (cachedResponse) {{
                      const data = await cachedResponse.json();
                      topWin.__communesGeoJsonMemoryCache = data;
                      window.__communesGeoJsonMemoryCache = data;
                      return data;
                    }}
                  }}
                }} else {{
                  for (const url of candidates) {{
                    await cache.delete(url);
                  }}
                }}
              }} catch (e) {{
                console.warn('[ODIS-MAP] Erreur lecture CacheStorage:', e);
              }}
            }}

            // 3. Tier 3 : Telechargement reseau
            for (const url of candidates) {{
              try {{
                const res = await fetch(url);
                if (res.ok) {{
                  if (cacheStorage) {{
                    try {{
                      const resClone = res.clone();
                      const cache = await cacheStorage.open(CACHE_NAME);
                      await cache.put(url, resClone);
                      try {{
                        (topWin.localStorage || window.localStorage).setItem(CACHE_TIMESTAMP_KEY, Date.now().toString());
                      }} catch (e) {{}}
                    }} catch (e) {{
                      console.warn('[ODIS-MAP] Erreur ecriture CacheStorage:', e);
                    }}
                  }}

                  const data = await res.json();
                  topWin.__communesGeoJsonMemoryCache = data;
                  window.__communesGeoJsonMemoryCache = data;
                  return data;
                }}
              }} catch (e) {{
                // Essai du candidat suivant
              }}
            }}
            throw new Error('Impossible de charger le fichier communes_france.geojson');
          }})();

          try {{
            return await topWin.__communesGeoJsonPromise;
          }} finally {{
            topWin.__communesGeoJsonPromise = null;
          }}
        }}

        function hexToRgba(hex, alpha = 255) {{
          if (!hex || typeof hex !== 'string') return [100, 100, 100, alpha];
          let c = hex.replace('#', '');
          if (c.length === 3) c = c.split('').map(x => x + x).join('');
          const num = parseInt(c, 16);
          if (isNaN(num)) return [100, 100, 100, alpha];
          return [(num >> 16) & 255, (num >> 8) & 255, num & 255, alpha];
        }}

        async function init() {{
          try {{
            const baseData = await fetchGeoJson();
            const scores = payload.scores || {{}};
            const scoreColorMap = new Map();
            for (const [code, sc] of Object.entries(scores)) {{
              if (sc >= 0) {{
                const idx = Math.min(8, Math.max(0, Math.floor(sc * 8.99)));
                scoreColorMap.set(String(code), ylgnRgb[idx]);
              }}
            }}

            const scoredFeatures = [];
            for (let i = 0; i < baseData.features.length; i++) {{
              const f = baseData.features[i];
              const code = String(f.properties.codgeo);
              if (scoreColorMap.has(code)) {{
                scoredFeatures.push(f);
              }}
            }}

            const centerLon = (payload.center && payload.center.length >= 2) ? payload.center[1] : 1.888334;
            const centerLat = (payload.center && payload.center.length >= 2) ? payload.center[0] : 46.603354;

            // 1. Base Choropleth Layer (Level 0)
            const deckLayers = [
              new deck.GeoJsonLayer({{
                id: 'communes-geojson-layer',
                data: scoredFeatures.length > 0 ? scoredFeatures : baseData,
                filled: true,
                stroked: true,
                getFillColor: f => {{
                  const code = String(f.properties.codgeo);
                  return scoreColorMap.get(code) || [0, 0, 0, 0];
                }},
                getLineColor: [27, 68, 41, 120],
                getLineWidth: 1,
                lineWidthMinPixels: 0.8,
                pickable: true,
                autoHighlight: true,
                highlightColor: [255, 255, 255, 140],
                onHover: info => {{
                  if (info.object) {{
                    const code = String(info.object.properties.codgeo);
                    const name = info.object.properties.libgeo || 'Commune';
                    const sc = scores[code];
                    const scPct = sc !== undefined ? (sc * 100).toFixed(1) + '%' : 'N/A';
                    tooltipEl.innerHTML = `<strong>${{name}}</strong><br/><span style="color: #A3E635;">Score : <strong>${{scPct}}</strong></span>`;
                    tooltipEl.style.display = 'block';
                    tooltipEl.style.left = `${{info.x}}px`;
                    tooltipEl.style.top = `${{info.y}}px`;
                  }} else {{
                    tooltipEl.style.display = 'none';
                  }}
                }}
              }})
            ];

            // 2. Reference Starting Commune Layer (Level 1: Blue Polygon)
            if (payload.current_marker && payload.current_marker.codgeo) {{
              const currentFeat = baseData.features.find(f => String(f.properties.codgeo) === String(payload.current_marker.codgeo));
              if (currentFeat) {{
                deckLayers.push(
                  new deck.GeoJsonLayer({{
                    id: 'current-location-polygon-layer',
                    data: [currentFeat],
                    filled: true,
                    stroked: true,
                    getFillColor: [30, 100, 220, 90],
                    getLineColor: [30, 100, 220, 255],
                    getLineWidth: 3,
                    lineWidthMinPixels: 3,
                    pickable: true,
                    onHover: info => {{
                      if (info.object) {{
                        tooltipEl.innerHTML = `<strong>📍 Commune Actuelle (Référence)</strong><br/><span>${{payload.current_marker.name}}</span>`;
                        tooltipEl.style.display = 'block';
                        tooltipEl.style.left = `${{info.x}}px`;
                        tooltipEl.style.top = `${{info.y}}px`;
                      }} else {{
                        tooltipEl.style.display = 'none';
                      }}
                    }}
                  }})
                );
              }}
            }}

            // 3. POI Markers Layer (Level 2: Mairies, Écoles, Santé, Inclusion)
            if (payload.poi_markers && payload.poi_markers.length > 0) {{
              deckLayers.push(
                new deck.ScatterplotLayer({{
                  id: 'poi-markers-layer',
                  data: payload.poi_markers,
                  getPosition: d => [d.lon, d.lat],
                  getFillColor: d => hexToRgba(d.color, 240),
                  getLineColor: [255, 255, 255, 255],
                  lineWidthMinPixels: 1,
                  stroked: true,
                  getRadius: 7,
                  radiusUnits: 'pixels',
                  pickable: true,
                  onHover: info => {{
                    if (info.object) {{
                      const p = info.object;
                      tooltipEl.innerHTML = `<strong>${{p.icon}} ${{p.name}}</strong><br/><span>${{p.type}}</span>`;
                      tooltipEl.style.display = 'block';
                      tooltipEl.style.left = `${{info.x}}px`;
                      tooltipEl.style.top = `${{info.y}}px`;
                    }}
                  }}
                }})
              );
            }}

            // 4. Top 5 & Shortlisted Markers (Level 3: Red/Yellow badges with Rank numbers)
            if (payload.top_markers && payload.top_markers.length > 0) {{
              deckLayers.push(
                new deck.ScatterplotLayer({{
                  id: 'top5-circles-layer',
                  data: payload.top_markers,
                  getPosition: d => [d.lon, d.lat],
                  getFillColor: d => d.type === 'pressentie' ? [245, 216, 25, 255] : (d.is_highlighted ? [239, 68, 68, 255] : [214, 62, 42, 255]),
                  getLineColor: d => d.type === 'pressentie' ? [27, 68, 41, 255] : [255, 255, 255, 255],
                  lineWidthMinPixels: 2.5,
                  getRadius: d => d.is_highlighted ? 18 : 15,
                  radiusUnits: 'pixels',
                  pickable: true,
                  onHover: info => {{
                    if (info.object) {{
                      const m = info.object;
                      const title = m.type === 'pressentie' ? '📌 Ville Souhaitée' : `Top ${{m.rank}}`;
                      tooltipEl.innerHTML = `<strong>${{title}} : ${{m.name}}</strong><br/><span style="color: #A3E635;">Score : <strong>${{m.score_pct}}</strong></span>`;
                      tooltipEl.style.display = 'block';
                      tooltipEl.style.left = `${{info.x}}px`;
                      tooltipEl.style.top = `${{info.y}}px`;
                    }}
                  }}
                }}),
                new deck.TextLayer({{
                  id: 'top5-texts-layer',
                  data: payload.top_markers,
                  getPosition: d => [d.lon, d.lat],
                  getText: d => d.rank === 0 ? '📌' : String(d.rank),
                  getSize: 15,
                  getColor: d => d.type === 'pressentie' ? [27, 68, 41, 255] : [255, 255, 255, 255],
                  getTextAnchor: 'middle',
                  getAlignmentBaseline: 'center',
                  fontWeight: '800',
                  pickable: false
                }})
              );
            }}

            new deck.DeckGL({{
              container: 'container',
              mapLib: maplibregl,
              mapStyle: mapStyle,
              initialViewState: {{
                longitude: centerLon,
                latitude: centerLat,
                zoom: payload.zoom || 8,
                minZoom: 4,
                maxZoom: 18,
                pitch: 0,
                bearing: 0
              }},
              controller: true,
              layers: deckLayers,
              onViewStateChange: () => {{
                tooltipEl.style.display = 'none';
              }}
            }});

          }} catch (err) {{
            console.error('Erreur chargement carte:', err);
          }}
        }}

        init();
      </script>
    </body>
    </html>
    """

    st.iframe(
        html_content,
        height=height,
    )
