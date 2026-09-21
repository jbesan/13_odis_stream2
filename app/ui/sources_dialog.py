import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from core.models import ScoresConfigFileSchema
from services.service_outcomes import OutcomeStatus, ServiceOutcome
from ui.dialog_state import clear_dialog
from utils.data_loader import load_active_data_manifest

logger = logging.getLogger(__name__)

CATEGORY_METADATA: Dict[str, tuple[str, str]] = {
    "logement": (
        "🏠 Logement",
        "Offre locative, loyers au m², parc social, vacance et hébergements citoyens.",
    ),
    "emploi": (
        "💼 Emploi & Formations",
        "Offres d'emploi ROME, structures d'insertion (SIAE), formations professionnelles et dynamisme.",
    ),
    "inclusion": (
        "🤝 Inclusion & Solidarité",
        "Services DORA de l'inclusion, tissu associatif, CCAS, épiceries solidaires et FLE.",
    ),
    "education": (
        "🎓 Éducation & Famille",
        "Petite enfance, crèches, écoles maternelles et élémentaires, collèges et lycées du bassin de vie.",
    ),
    "sante": (
        "🩺 Santé",
        "Accessibilité aux médecins généralistes (APL), hôpitaux, maternités, santé mentale et maisons de santé.",
    ),
    "mobilite": (
        "🧭 Mobilité",
        "Part des mobilités durables, transports en commun, gares SNCF et proximité géographique.",
    ),
    "territoire": (
        "🌳 Territoire & Cadre de vie",
        "Sécurité, équipements du quotidien (BPE), offre culturelle et sportive, espaces verts.",
    ),
}


@st.cache_data(show_spinner=False, ttl=300)
def load_manifest() -> ServiceOutcome[Dict[str, Any]]:
    """Load the verified manifest belonging to the active dataset release."""
    try:
        manifest = load_active_data_manifest()
        # Fallback in local development if sources were omitted from active data_manifest
        if not manifest.get("sources"):
            run_id = manifest.get("pipeline_run_id")
            if run_id:
                candidate_path = (
                    Path(__file__).resolve().parents[2]
                    / "pipeline"
                    / "cache"
                    / "runs"
                    / run_id
                    / "output"
                    / "data_manifest.json"
                )
                if candidate_path.is_file():
                    try:
                        with open(candidate_path, "r", encoding="utf-8") as handle:
                            cached_manifest = json.load(handle)
                            if cached_manifest.get("sources"):
                                manifest["sources"] = cached_manifest["sources"]
                    except Exception as fallback_exc:
                        logger.warning(
                            "Could not load candidate manifest fallback: %s",
                            fallback_exc,
                        )
        return ServiceOutcome(
            status=OutcomeStatus.SUCCESS, value=manifest
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


def _calculate_age_days(date_str: Optional[str]) -> Optional[int]:
    """Calculates elapsed days between now (UTC) and the provided ISO timestamp."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        return max(0, int(delta.total_seconds() // 86400))
    except Exception:
        return None


def _format_age_and_ttl(source: Dict[str, Any]) -> str:
    """Formats age vs TTL based on the actual date acquired/updated relative to now.

    Displays an alert icon ⚠️ if the calculated age exceeds the declared TTL.
    """
    date_str = source.get("acquired_at") or source.get("last_updated")
    age_days = _calculate_age_days(date_str)
    ttl_days = source.get("ttl_days")

    if age_days is not None:
        age_str = f"{age_days} j"
    else:
        # Fallback to precalculated age if date parsing was impossible
        raw_age = source.get("age_days")
        age_str = f"{raw_age:g} j" if isinstance(raw_age, (int, float)) else "inconnu"

    ttl_str = f"{ttl_days} j" if isinstance(ttl_days, int) else "non défini"

    prefix = ""
    if age_days is not None and isinstance(ttl_days, int) and age_days > ttl_days:
        prefix = "⚠️ "

    suffix = " (fallback)" if source.get("fallback_used") else ""
    return f"{prefix}{age_str} / {ttl_str}{suffix}"


def _render_sources_tab(manifest: Dict[str, Any]) -> None:
    """Renders the data sources table and provenance metadata."""
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

    if not sources:
        st.info("ℹ️ Aucune source détaillée n'est référencée dans le manifeste actuel.")
        return

    table_rows = []
    for s in sources:
        artifact = s.get("artifact") or {}
        rows_val = artifact.get("row_count") or s.get("row_count")
        formatted_rows = (
            f"{rows_val:,}".replace(",", " ") if isinstance(rows_val, int) else "-"
        )
        doc_url = s.get("doc_url")
        annee_ref = str(s.get("annee_reference")) if s.get("annee_reference") else "-"

        table_rows.append(
            {
                "Source": s.get("name") or s.get("source_key"),
                "Méthode": s.get("method") or "Open Data",
                "Année réf.": annee_ref,
                "Mise à jour constatée": format_iso_date(
                    s.get("acquired_at") or s.get("last_updated")
                ),
                "Âge / TTL": _format_age_and_ttl(s),
                "Volumétrie": formatted_rows,
                "Documentation": doc_url if doc_url else None,
            }
        )

    df = pd.DataFrame(table_rows)

    st.dataframe(
        df,
        column_config={
            "Documentation": st.column_config.LinkColumn(
                "Documentation", display_text="Consulter"
            ),
            "Volumétrie": st.column_config.TextColumn("Volumétrie"),
        },
        width="stretch",
        hide_index=True,
    )

    st.caption(
        "La date indiquée est celle observée lors du run de la release active. "
        "Une date ou un statut inconnu signifie que la fraîcheur n'a pas été prouvée pour cette source."
    )


def _render_criteria_catalog() -> None:
    """Renders the catalog of scoring indicators grouped by category."""
    try:
        catalog = ScoresConfigFileSchema.load_default()
    except Exception as exc:
        logger.error("Could not load scores catalog schema: %s", exc, exc_info=True)
        st.warning("Le catalogue des critères n'a pas pu être chargé.")
        return

    scores_by_cat: Dict[str, List[Any]] = {}
    for score in catalog.scores:
        scores_by_cat.setdefault(score.category, []).append(score)

    for cat_id, (cat_label, cat_desc) in CATEGORY_METADATA.items():
        cat_scores = scores_by_cat.get(cat_id, [])
        if not cat_scores:
            continue

        with st.expander(f"{cat_label} — {len(cat_scores)} critères"):
            st.caption(cat_desc)
            rows = []
            for s in cat_scores:
                calc_label = "En direct" if s.computation == "live" else "Pré-calculé"
                baseline_str = "Oui" if s.baseline else "Non"
                bdv_str = f"{int(s.bdv_factor * 100)}%" if s.bdv_factor > 0 else "-"
                description = s.display.tooltip or s.display.strong_point_text or "-"
                rows.append(
                    {
                        "Critère": s.display.name,
                        "Description": description,
                        "Calcul": calc_label,
                        "Toujours évalué ?": baseline_str,
                        # "Boost Bassin de Vie": bdv_str,
                    }
                )
            df_cat = pd.DataFrame(rows)
            st.dataframe(df_cat, width="stretch", hide_index=True)


def _render_scoring_methodology_tab() -> None:
    """Renders the scoring methodology, formulas, and criteria catalog."""
    st.markdown("### 🎯 Philosophie du scoring")
    st.markdown(
        """
        Contrairement aux moteurs de recherche classiques basés sur des **filtres éliminatoires stricts** 
        (risquant de ne retourner aucun résultat), ODIS calcule un **score de compatibilité continue (de 0 à 100%)** 
        entre chaque commune du territoire et le projet de vie de la personne accompagnée.
        """
    )

    st.markdown("### 📐 Formule du Score Global")
    st.latex(r"S_{\text{global}} = S_{\text{besoins}} \times C_{\text{pop}}")
    st.markdown(
        """
        - **$S_{\t{besoins}}$ (Score Besoins)** : Moyenne pondérée des 7 catégories thématiques selon les critères choisis et les curseurs de priorité définis dans le formulaire.
        - **$C_{\t{pop}}$ (Modulateur Démographique)** : Coefficient d'adéquation territoriale ($0.0 \\le C_{\text{pop}} \\le 1.0$) calculé sur la population du **Bassin de Vie (INSEE)**.
        """
    )

    st.markdown("### 🏘️ Pourquoi le Bassin de Vie (INSEE) ?")
    st.markdown(
        """
        Une commune ne vit pas en autarcie :
        - **Éviter les faux négatifs** : Une petite commune rurale bénéficie directement des commerces, des écoles, des professionnels de santé et des emplois du bourg ou de la ville voisine.
        - **Distinguer la banlieue d'une ville autonome** : Une commune de 35 000 habitants en périphérie immédiate d'une métropole appartient à un bassin de vie de plus d'un million d'habitants. L'évaluation au niveau du bassin de vie évite de la confondre avec une ville moyenne autonome.
        """
    )

    # st.markdown("#### 🎯 Courbe d'adéquation en trapèze avec plancher résiduel (15%)")
    # st.markdown(
    #     """
    #     Pour chaque typologie ciblée (*Commune rurale*, *Bourg*, *Petite ville*, *Ville moyenne*), une fonction trapézoïdale définit un **plateau idéal à 100% d'adéquation**, encadré de pentes de tolérance progressive.
        
    #     > **Plancher résiduel à 15%** : Même si une commune ou métropole s'éloigne du gabarit cible, son coefficient ne tombe jamais à 0 mais à un plancher de 15%. Cela évite les effets de bord arbitraires et préserve la lisibilité de la carte tout en garantissant son exclusion du Top 5.
    #     """
    # )

    st.markdown("### 🚀 Effet de rayonnement du Bassin de Vie")
    st.markdown(
        """
        Pour les critères d'emploi, de santé ou de formation, un **mécanisme de boost non pénalisant** valorise les opportunités du bassin de vie :
        """
    )
    st.latex(
        r"Score = S_{\text{commune}} + (1 - S_{\text{commune}}) \times (S_{\text{BassinDeVie}} \times factor)"
    )
    st.markdown(
        """
        Si un équipement ou une offre n'est pas présent sur la commune mais se trouve dans le bassin de vie immédiat, 
        le score local est augmenté selon un facteur d'accessibilité ($factor$). Si le bassin de vie est également dépourvu, 
        le score local n'est **jamais pénalisé**.
        """
    )

    st.markdown("### 🛡️ Critères Socles (Baselines)")
    st.markdown(
        """
        Certains indicateurs fondamentaux (délai d'accès aux soins de santé de base, sécurité publique, transports du quotidien, dynamisme de l'emploi) 
        sont des **critères socles** : ils sont systématiquement pris en compte pour chaque commune, même si aucun besoin spécifique 
        n'a été coché à leur sujet, afin d'assurer un cadre d'accueil sécurisant et viable.
        """
    )

    st.divider()
    st.markdown("### 📋 Catalogue interactif des critères")
    _render_criteria_catalog()


def _render_legal_terms_tab() -> None:
    """Renders the terms of use and legal disclaimers."""
    st.markdown("### 📜 Conditions d'usage & Cadre d'utilisation")
    st.markdown(
        """
        OD&IS (*Outils d’Aide au Diagnostic et à l’Implantation Solidaire*) permet aux travailleurs sociaux de guider et d'implanter les personnes réfugiées vers des territoires adaptés à leurs besoins réels et d'optimiser leur parcours d'intégration

        L'utilisation de cette plateforme implique l'acceptation et la prise en compte des principes ci-dessous.
        """
    )

    with st.expander(
        "🎯 Nature du service & Aide à la décision (Non prescriptif)",
        expanded=True,
    ):
        st.markdown(
            """
            - **Outil d'aide à la décision** : ODIS est un outil de simulation et d'aide à l'orientation à visée 
              purement informative. Il propose un classement de communes par affinité continue avec les besoins exprimés.
            - **Absence de décision administrative automatisée** : Conformément à l'article L. 311-3-1 du Code des relations 
              entre le public et l'administration (CRPA) et à l'article 22 du Règlement Général sur la Protection des Données (RGPD), 
              **aucune décision produisant des effets juridiques ou affectant de manière significative une personne n'est prise 
              de manière automatisée** par la plateforme.
            - **Primauté de l'évaluation humaine** : Les résultats constituent un support de discussion. Le choix final d'un 
              territoire ou d'une démarche d'installation appartient souverainement à la personne accompagnée, en concertation 
              avec ses accompagnateurs.
            """
        )

    with st.expander(
        "📊 Réutilisation des données publiques & Absence de garantie de résultat"
    ):
        st.markdown(
            """
            - **Sources Open Data tierces** : Les indicateurs proviennent de bases de données publiques officielles 
              (INSEE, France Travail, Data Inclusion / DORA, CAF, SSMSI, USH, etc.) réutilisées sous Licence Ouverte (Etalab) ou ODbL.
            - **Fourniture « en l'état »** : Malgré les protocoles de validation et de contrôle qualité du pipeline ETL, 
              les données peuvent présenter un décalage temporel ou des lacunes territoriales inhérentes aux publications des producteurs.
            - **Absence de garantie d'attribution** : L'affichage d'un score favorable sur une commune ne garantit en aucun cas 
              l'attribution effective ou prioritaire d'un logement social, l'obtention d'un contrat de travail, 
              d'une place en crèche ou d'un hébergement. Les démarches de droit commun auprès des administrations 
              et organismes compétents demeurent indispensables.
            """
        )

    with st.expander(
        "🔒 Protection des données personnelles & Confidentialité (RGPD)"
    ):
        st.markdown(
            """
            - **Minimisation des données (Privacy by Design)** : Aucun nom, prénom, numéro de dossier ou donnée directement 
              identifiante relative aux personnes accompagnées n'est collecté, saisi ou conservé dans la base de données. 
              Les simulations reposent exclusivement sur des critères de besoins anonymisés.
            - **Comptes et habilitations** : L'accès à la plateforme est réservé aux professionnels et intervenants dûment habilités 
              des organisations partenaires (via authentification sécurisée Google Workspace / OIDC).
            - **Traçabilité & Télémétrie** : Les journaux techniques et statistiques d'usage sont traités sur une infrastructure 
              Cloud européenne (Cloud Run, BigQuery, région europe-west1) dans le strict respect de la réglementation européenne.
            """
        )

    with st.expander(
        "🤖 Avertissement relatif à l'Intelligence Artificielle (AI Act)"
    ):
        st.markdown(
            """
            - **Assistance par modèle de langage** : Les argumentaires territoriaux, fiches de synthèse et analyses comparatives 
              sont générés à l'aide de modèles d'intelligence artificielle générative (Google Gemini / Vertex AI hébergés en Union Européenne).
            - **Vigilance face aux hallucinations** : Ces contenus automatisés sont des aides à la rédaction fournies à titre indicatif. 
              Ils peuvent comporter des approximations, omissions ou inexactitudes.
            - **Obligation de contrôle** : Il est impératif pour le professionnel de vérifier et valider systématiquement 
              les informations clés (coordonnées, dispositifs locaux, conditions d'accès) avant toute transmission ou prise d'engagement.
            """
        )


def _on_about_dialog_dismiss() -> None:
    """Clear the pending about dialog request when the modal is dismissed."""
    clear_dialog(st.session_state, "active_about_dialog")


@st.dialog("À propos d'ODIS", width="large", on_dismiss=_on_about_dialog_dismiss)
def show_about_dialog() -> None:
    """Renders the Streamlit dialog modal with tabs for terms of use, data sources, and scoring methodology."""
    tab_terms, tab_sources, tab_scoring = st.tabs(
        [
            "📜 Conditions d'usage",
            "📦 Sources des données",
            "⚙️ Scoring & Méthodologie",
        ]
    )

    with tab_terms:
        _render_legal_terms_tab()

    with tab_sources:
        manifest_outcome = load_manifest()
        if not manifest_outcome.is_success or manifest_outcome.value is None:
            st.warning(
                "⚠️ Le manifeste de données est temporairement indisponible "
                "(code : DATA-MANIFEST-UNAVAILABLE)."
            )
        else:
            _render_sources_tab(manifest_outcome.value)

    with tab_scoring:
        _render_scoring_methodology_tab()


# Backward-compatible alias
show_sources_dialog = show_about_dialog
