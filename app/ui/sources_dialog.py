import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime
import pandas as pd
import streamlit as st

import config

logger = logging.getLogger(__name__)

MANIFEST_PATH = Path(config.LOCAL_DATA_PATH) / "data_manifest.json"


@st.cache_data(show_spinner=False, ttl=300)
def load_manifest() -> Optional[Dict[str, Any]]:
    """Loads the Data Manifest JSON from app/data/data_manifest.json."""
    if MANIFEST_PATH.exists():
        try:
            content = MANIFEST_PATH.read_text(encoding="utf-8")
            return json.loads(content)
        except Exception as e:
            logger.warning(f"Failed to load manifest at {MANIFEST_PATH}: {e}")
    return None




def format_iso_date(iso_str: Optional[str]) -> str:
    """Formats ISO date string to DD/MM/YYYY HH:MM."""
    if not iso_str:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(iso_str)[:10]


@st.dialog("À propos des données utilisées par l'application", width="large")
def show_sources_dialog():
    """Renders the Streamlit dialog modal showing the Data Manifest sources table."""
    manifest = load_manifest()

    if not manifest:
        st.warning("⚠️ Aucun fichier de Manifest de données n'est disponible actuellement.")
        return

    manifest_version = manifest.get("manifest_version", "v1.0")
    created_at = format_iso_date(manifest.get("created_at"))
    sources: List[Dict[str, Any]] = manifest.get("sources", [])

    st.markdown(
        f"""
        <div style="background-color: #f8f9fa; border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; border: 1px solid #e9ecef;">
            <span style="font-weight: 600; color: #212529;">📦 Version du jeu de données :</span> <code>{manifest_version}</code><br/>
            <span style="color: #6c757d; font-size: 0.85rem;">Dernière compilation des jeu de données : {created_at} | <strong>{len(sources)}</strong> sources référencées</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


    table_rows = []
    for s in sources:
        rows_val = s.get("row_count")
        formatted_rows = f"{rows_val:,}".replace(",", " ") if isinstance(rows_val, int) else "-"
        doc_url = s.get("doc_url")

        annee_ref = str(s.get("annee_reference")) if s.get("annee_reference") else "-"

        table_rows.append(
            {
                "Source": s.get("name") or s.get("source_key"),
                "Méthode": s.get("method") or "Open Data",
                "Année réf.": annee_ref,
                "Dernière maj.": format_iso_date(s.get("last_updated")),
                "Volumétrie": formatted_rows,
                "Documentation": doc_url if doc_url else None,
            }
        )

    df = pd.DataFrame(table_rows)


    st.dataframe(
        df,
        column_config={
            "Documentation": st.column_config.LinkColumn("Documentation", display_text="Consulter"),
            "Volumétrie": st.column_config.TextColumn("Volumétrie"),
        },
        width="stretch",
        hide_index=True,
    )

    st.caption(
        "Les sources Odace sont synchronisées via la Data Platform. Les autres jeux de données sont extraits en Open Data certifié (INSEE, Data.gouv.fr, Data Inclusion)."
    )
