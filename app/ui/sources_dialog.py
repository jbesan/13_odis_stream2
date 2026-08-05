import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
import pandas as pd
import streamlit as st

from utils.data_loader import load_active_data_manifest
from services.service_outcomes import OutcomeStatus, ServiceOutcome

logger = logging.getLogger(__name__)

@st.cache_data(show_spinner=False, ttl=300)
def load_manifest() -> ServiceOutcome[Dict[str, Any]]:
    """Load the verified manifest belonging to the active dataset release."""
    try:
        return ServiceOutcome(
            status=OutcomeStatus.SUCCESS, value=load_active_data_manifest()
        )
    except Exception:
        logger.error(
            "Active data manifest could not be loaded",
            extra={
                "extra_data": {
                    "operation": "load_data_manifest",
                    "error_code": "DATA-MANIFEST-UNAVAILABLE",
                }
            },
            exc_info=True,
        )
        return ServiceOutcome(
            status=OutcomeStatus.UNAVAILABLE,
            error_code="DATA-MANIFEST-UNAVAILABLE",
        )




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
    manifest_outcome = load_manifest()

    if not manifest_outcome.is_success or manifest_outcome.value is None:
        st.warning(
            "⚠️ Le manifeste de données est temporairement indisponible "
            "(code : DATA-MANIFEST-UNAVAILABLE)."
        )
        return
    manifest = manifest_outcome.value

    manifest_version = manifest.get("manifest_version", "v1.0")
    release_version = manifest.get("active_release_version") or manifest.get(
        "pipeline_run_id", "-"
    )
    created_at = format_iso_date(manifest.get("created_at"))
    sources: List[Dict[str, Any]] = manifest.get("sources", [])

    st.markdown(
        f"""
        <div style="background-color: #f8f9fa; border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; border: 1px solid #e9ecef;">
            <span style="font-weight: 600; color: #212529;">📦 Version du jeu de données :</span> <code>{manifest_version}</code><br/>
            <span style="color: #6c757d; font-size: 0.85rem;">Release active : <code>{release_version}</code> | Compilation : {created_at} | <strong>{len(sources)}</strong> sources référencées</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


    table_rows = []
    for s in sources:
        artifact = s.get("artifact") or {}
        rows_val = artifact.get("row_count")
        formatted_rows = f"{rows_val:,}".replace(",", " ") if isinstance(rows_val, int) else "-"
        doc_url = s.get("doc_url")

        annee_ref = str(s.get("annee_reference")) if s.get("annee_reference") else "-"

        table_rows.append(
            {
                "Source": s.get("name") or s.get("source_key"),
                "Méthode": s.get("method") or "Open Data",
                "Année réf.": annee_ref,
                # "Statut": s.get("acquisition_status", "inconnu"),
                "Mise à jour constatée": format_iso_date(s.get("acquired_at")),
                "Âge / TTL": _format_age_and_ttl(s),
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
        "La date indiquée est celle observée lors du run de la release active. Une date ou un statut inconnu signifie que la fraîcheur n'a pas été prouvée pour cette source."
    )


def _format_age_and_ttl(source: Dict[str, Any]) -> str:
    age_days = source.get("age_days")
    ttl_days = source.get("ttl_days")
    age = f"{age_days:g} j" if isinstance(age_days, (int, float)) else "inconnu"
    ttl = f"{ttl_days} j" if isinstance(ttl_days, int) else "non défini"
    suffix = " (fallback)" if source.get("fallback_used") else ""
    return f"{age} / {ttl}{suffix}"
