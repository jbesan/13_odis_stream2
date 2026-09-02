import logging
import streamlit as st
import pandas as pd
import plotly.express as px

from ui import page_shell
from services import analytics_data

logger = logging.getLogger("pages.analytics")

# --- Page Config ---
st.set_page_config(page_title="OD&IS - Analytics", page_icon="📊", layout="wide")

page_shell.enter_page("Analytics", admin_only=True)

# --- Sidebar ---
with st.sidebar:
    page_shell.render_sidebar_logo()
    st.header("📊 Administration")
    st.write(
        "Bienvenue sur le tableau de bord d'analyse de l'usage et des recommandations d'OD&IS."
    )
    st.divider()
    page_shell.render_primary_sidebar_actions(show_home=True)
    page_shell.render_account_sidebar_actions(show_admin=False)


# --- BigQuery Helper ---
client = analytics_data.get_bq_client()

st.title("📊 Dashboard Analytics & BI Métier")

if client is None:
    st.error(
        "❌ **Connexion à BigQuery indisponible**.\n\n"
        "Pour afficher ce tableau de bord, assurez-vous d'exécuter l'application avec un projet GCP actif "
        "(`GOOGLE_CLOUD_PROJECT` ou `GCP_PROJECT`) et des identifiants valides."
    )
    st.stop()

dataset_id = "odis_logs"

# --- Date & Action Filters ---
col_filter1, col_filter2, col_filter3 = st.columns([2, 3, 1])

with col_filter1:
    period_days = st.selectbox(
        "Période d'analyse",
        options=[7, 30, 90, 365],
        index=1,
        format_func=lambda x: f"Derniers {x} jours",
    )

with col_filter3:
    st.write("")  # Vertical spacing for alignment with selectbox
    st.write("")
    if st.button(
        "🔄 Rafraîchir", help="Forcer le rafraîchissement des données depuis BigQuery"
    ):
        analytics_data.clear_analytics_cache()
        st.rerun()


with st.spinner("Chargement des données BigQuery..."):
    analytics_result = analytics_data.fetch_analytics_data(client, period_days)
    billing_outcome = analytics_data.fetch_gcp_billing_data(client, period_days)
    agent_costs_outcome = analytics_data.fetch_agent_costs_data(client, period_days)

if analytics_result.status == analytics_data.OutcomeStatus.UNAUTHORIZED:
    st.error(
        "❌ **Accès aux données Analytics indisponible**. "
        "Réessayez plus tard ou contactez le support (code : ANALYTICS-BQ-UNAUTHORIZED)."
    )
    st.stop()
if analytics_result.status == analytics_data.OutcomeStatus.UNAVAILABLE:
    st.error(
        "❌ **Données Analytics temporairement indisponibles**. "
        "Réessayez dans quelques instants (code : ANALYTICS-BQ-UNAVAILABLE)."
    )
    st.stop()
if analytics_result.status == analytics_data.OutcomeStatus.PARTIAL:
    failed_tables = []
    if not analytics_result.searches.is_success:
        failed_tables.append("recherches")
    if not analytics_result.usage.is_success:
        failed_tables.append("événements d'usage")
    st.warning(
        "⚠️ Affichage partiel : les données suivantes sont temporairement indisponibles : "
        f"{', '.join(failed_tables)} (code : ANALYTICS-BQ-UNAVAILABLE)."
    )

df_searches = analytics_result.searches.value
df_usage = analytics_result.usage.value
if df_searches is None:
    df_searches = pd.DataFrame()
if df_usage is None:
    df_usage = pd.DataFrame()

# Filter by Org if data exists
all_orgs = sorted(
    list(
        set(
            (df_searches["org_id"].dropna().tolist() if not df_searches.empty else [])
            + (df_usage["org_id"].dropna().tolist() if not df_usage.empty else [])
        )
    )
)

with col_filter2:
    selected_orgs = st.multiselect(
        "Filtrer par Organisation",
        options=all_orgs,
        default=all_orgs,
        placeholder="Toutes les organisations",
    )

if selected_orgs:
    if not df_searches.empty and "org_id" in df_searches.columns:
        df_searches = df_searches[df_searches["org_id"].isin(selected_orgs)]
    if not df_usage.empty and "org_id" in df_usage.columns:
        df_usage = df_usage[df_usage["org_id"].isin(selected_orgs)]

# --- Tabs ---
tab_global, tab_recommandations, tab_profiles, tab_finops = st.tabs(
    [
        "📈 Activité Globale",
        "🥇 Résultats & Recommandations",
        "🔎 Profil de Recherches",
        "💰 Coûts & FinOps GCP",
    ]
)

# ==========================================
# TAB 1: ACTIVITÉ GLOBALE
# ==========================================
with tab_global:
    st.markdown("##### Indicateurs Clés de Performance (KPIs)")

    kpi1, kpi2, kpi3, kpi4, kpi5, kpi6 = st.columns(6)

    total_searches = len(df_searches) if not df_searches.empty else 0
    total_users = (
        df_searches["username"].nunique()
        if not df_searches.empty
        else (df_usage["username"].nunique() if not df_usage.empty else 0)
    )

    auto_detect_count = 0
    ia_analyses_count = 0
    details_view_count = 0
    pdf_export_count = 0

    if not df_usage.empty:
        auto_detect_count = len(
            df_usage[df_usage["event_name"] == "auto_detect_criteria"]
        )
        ia_analyses_count = len(df_usage[df_usage["event_name"] == "run_ia_analysis"])
        details_view_count = len(
            df_usage[df_usage["event_name"] == "view_commune_details"]
        )
        pdf_export_count = len(df_usage[df_usage["event_name"] == "export_pdf"])

    with kpi1:
        st.metric("Recherches Exécutées", total_searches)
    with kpi2:
        st.metric("Utilisateurs Actifs", total_users)
    with kpi3:
        st.metric("Détections IA (Accueil)", auto_detect_count)
    with kpi4:
        st.metric("Analyses IA Générées", ia_analyses_count)
    with kpi5:
        st.metric("Détails Consultés", details_view_count)
    with kpi6:
        st.metric("Exports PDF", pdf_export_count)

    st.divider()

    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        if not df_searches.empty:
            df_searches["date"] = pd.to_datetime(df_searches["timestamp"]).dt.date
            searches_by_date = (
                df_searches.groupby("date").size().reset_index(name="Recherches")
            )
            fig_searches = px.bar(
                searches_by_date,
                x="date",
                y="Recherches",
                title="Nombre de recherches par jour",
                color_discrete_sequence=["#1B4429"],
            )
            fig_searches.update_layout(xaxis_title="Date", yaxis_title="Recherches")
            st.plotly_chart(fig_searches, width="content")
        else:
            st.info("Aucune donnée de recherche enregistrée pour cette période.")

    with col_chart2:
        if not df_searches.empty and "org_id" in df_searches.columns:
            org_counts = (
                df_searches.groupby("org_id").size().reset_index(name="Recherches")
            )
            fig_org = px.pie(
                org_counts,
                names="org_id",
                values="Recherches",
                title="Répartition des recherches par Organisation",
                color_discrete_sequence=px.colors.qualitative.Dark2,
            )
            st.plotly_chart(fig_org, width="content")
        else:
            st.info("Aucune donnée d'organisation disponible.")

    st.divider()

    col_top_users, col_event_dist = st.columns(2)

    with col_top_users:
        if not df_searches.empty:
            top_users = (
                df_searches.groupby(["username", "org_id"])
                .size()
                .reset_index(name="Recherches")
                .sort_values(by="Recherches", ascending=False)
                .head(5)
            )
            fig_top_users = px.bar(
                top_users,
                x="Recherches",
                y="username",
                color="org_id",
                orientation="h",
                title="Top 5 Utilisateurs (+ Organisation)",
                color_discrete_sequence=px.colors.qualitative.Set3,
            )
            fig_top_users.update_layout(yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig_top_users, width="content")
        else:
            st.info("Aucune donnée utilisateur disponible.")

    with col_event_dist:
        if not df_usage.empty:
            event_counts = df_usage["event_name"].value_counts().reset_index()
            event_counts.columns = ["Action", "Nombre"]
            fig_events = px.bar(
                event_counts,
                x="Action",
                y="Nombre",
                title="Fréquence des événements applicatifs",
                color="Action",
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            st.plotly_chart(fig_events, width="content")
        else:
            st.info("Aucun événement d'usage enregistré pour l'instant.")


# ==========================================
# TAB 2: RÉSULTATS & RECOMMANDATIONS
# ==========================================
with tab_recommandations:
    # st.subheader("🥇 Analyse des Recommandations et Consultations")

    col_rec1, col_rec2 = st.columns(2)

    with col_rec1:
        if not df_searches.empty and "top_results" in df_searches.columns:
            city_records = []
            top_results_stats = analytics_data.ParseStats()
            for idx, row in df_searches.iterrows():
                top_res_str = row.get("top_results")
                if top_res_str:
                    top_list = analytics_data.parse_json_payload(
                        top_res_str, top_results_stats, expected_type=list
                    )
                    if top_list is None:
                        continue
                    for rank, city in enumerate(top_list, start=1):
                        if not isinstance(city, dict):
                            top_results_stats.invalid_rows += 1
                            continue
                        city_records.append(
                            {
                                "codgeo": city.get("codgeo"),
                                "libgeo": city.get("libgeo"),
                                "score": city.get("score", 0.0),
                                "rank": rank,
                                "org_id": row.get("org_id"),
                            }
                        )

            analytics_data.log_invalid_payload_summary(
                "top_recommended_cities", top_results_stats
            )
            if top_results_stats.invalid_rows:
                st.caption(
                    f"{top_results_stats.invalid_rows} résultat(s) invalide(s) ont été écartés."
                )

            if city_records:
                df_cities = pd.DataFrame(city_records)
                summary_cities = (
                    df_cities.groupby(["codgeo", "libgeo"])
                    .agg(
                        recommandations=("score", "count"),
                        score_moyen=("score", "mean"),
                        rang_moyen=("rank", "mean"),
                    )
                    .reset_index()
                    .sort_values(by="recommandations", ascending=False)
                )

                top_15 = summary_cities.head(15)
                fig_top_cities = px.bar(
                    top_15,
                    x="recommandations",
                    y="libgeo",
                    orientation="h",
                    title="Top 15 des villes les plus recommandées",
                    labels={"recommandations": "Occurrences", "libgeo": "Commune"},
                    color="score_moyen",
                    color_continuous_scale="Viridis",
                )
                fig_top_cities.update_layout(yaxis={"categoryorder": "total ascending"})
                st.plotly_chart(fig_top_cities, width="content")
            else:
                if top_results_stats.invalid_rows:
                    logger.error(
                        "Analytics recommendations widget has no usable rows",
                        extra={
                            "extra_data": {
                                "operation": "analytics_payload_parse",
                                "widget": "top_recommended_cities",
                                "error_code": "ANALYTICS-PAYLOAD-INVALID",
                            }
                        },
                    )
                    st.warning(
                        "Données insuffisantes ou invalides pour afficher les villes recommandées."
                    )
                else:
                    st.info(
                        "Aucun détail de ville disponible dans les résultats de recherche."
                    )
        else:
            st.info("Aucune donnée de recherche à analyser.")

    with col_rec2:
        # st.markdown("##### Top 15 des Communes les plus Consultées (Détails)")
        if not df_usage.empty:
            df_details = df_usage[df_usage["event_name"] == "view_commune_details"]
            if not df_details.empty:
                consult_records = []
                consult_stats = analytics_data.ParseStats()
                for _, row in df_details.iterrows():
                    payload_raw = row.get("payload")
                    if payload_raw:
                        p = analytics_data.parse_json_payload(
                            payload_raw, consult_stats, expected_type=dict
                        )
                        if p is None:
                            continue
                        consult_records.append(
                            {
                                "codgeo": p.get("codgeo"),
                                "name": p.get("name", p.get("codgeo")),
                            }
                        )
                analytics_data.log_invalid_payload_summary(
                    "most_consulted_cities", consult_stats
                )
                if consult_stats.invalid_rows:
                    st.caption(
                        f"{consult_stats.invalid_rows} événement(s) invalide(s) ont été écartés."
                    )
                if consult_records:
                    df_consult = pd.DataFrame(consult_records)
                    top_consult = df_consult["name"].value_counts().reset_index()
                    top_consult.columns = ["Commune", "Consultations"]
                    top_consult_15 = top_consult.head(15)

                    fig_consult = px.bar(
                        top_consult_15,
                        x="Consultations",
                        y="Commune",
                        orientation="h",
                        title="Top 15 des villes les plus consultées ('En savoir plus')",
                        color="Consultations",
                        color_continuous_scale="Plasma",
                    )
                    fig_consult.update_layout(
                        yaxis={"categoryorder": "total ascending"}
                    )
                    st.plotly_chart(fig_consult, width="content")
                else:
                    if consult_stats.invalid_rows:
                        logger.error(
                            "Analytics consultations widget has no usable rows",
                            extra={
                                "extra_data": {
                                    "operation": "analytics_payload_parse",
                                    "widget": "most_consulted_cities",
                                    "error_code": "ANALYTICS-PAYLOAD-INVALID",
                                }
                            },
                        )
                        st.warning(
                            "Données insuffisantes ou invalides pour afficher les consultations."
                        )
                    else:
                        st.info("Aucune consultation de commune enregistrée.")
            else:
                st.info(
                    "Aucun événement de consultation 'En savoir plus' trouvé pour cette période."
                )
        else:
            st.info("Aucun événement d'usage disponible.")

    st.divider()

    # Spider / Radar Chart of Category Scores
    # st.subheader("🕸️ Profil des Scores Moyens par Catégorie (Radar Chart)")
    if not df_searches.empty and "detailed_breakdown" in df_searches.columns:
        cat_scores_accumulator = {
            "Logement": [],
            "Emploi": [],
            "Santé": [],
            "Éducation": [],
            "Inclusion": [],
            "Mobilité": [],
            "Territoire": [],
        }

        cat_mapping = {
            "logement": "Logement",
            "emploi": "Emploi",
            "sante": "Santé",
            "education": "Éducation",
            "inclusion": "Inclusion",
            "mobilite": "Mobilité",
            "territoire": "Territoire",
        }

        breakdown_stats = analytics_data.ParseStats()
        for _, row in df_searches.iterrows():
            db_raw = row.get("detailed_breakdown")
            if db_raw:
                db = analytics_data.parse_json_payload(
                    db_raw, breakdown_stats, expected_type=dict
                )
                if db is None:
                    continue
                for city_data in db.values():
                    if not isinstance(city_data, dict):
                        breakdown_stats.invalid_rows += 1
                        continue
                    scores_dict = city_data.get("scores", {})
                    if not isinstance(scores_dict, dict):
                        breakdown_stats.invalid_rows += 1
                        continue
                    for cat_key, cat_name in cat_mapping.items():
                        items = scores_dict.get(cat_key, [])
                        if not isinstance(items, list):
                            breakdown_stats.invalid_rows += 1
                            continue
                        for item in items:
                            if isinstance(item, dict):
                                val = item.get("score_normalise", item.get("score"))
                            else:
                                val = getattr(
                                    item,
                                    "score_normalise",
                                    getattr(item, "score", None),
                                )
                            if val is None:
                                continue
                            try:
                                cat_scores_accumulator[cat_name].append(float(val))
                            except (TypeError, ValueError):
                                breakdown_stats.invalid_rows += 1

        analytics_data.log_invalid_payload_summary(
            "category_score_radar", breakdown_stats
        )
        if breakdown_stats.invalid_rows:
            st.caption(
                f"{breakdown_stats.invalid_rows} donnée(s) invalide(s) ont été écartées du radar."
            )

        radar_data = []
        for cat_name, scores in cat_scores_accumulator.items():
            if scores:
                mean_val = (sum(scores) / len(scores)) * 100
                radar_data.append(
                    {"Catégorie": cat_name, "Score Moyen (%)": round(mean_val, 1)}
                )

        df_radar = pd.DataFrame(radar_data)

        if not df_radar.empty and df_radar["Score Moyen (%)"].sum() > 0:
            fig_radar = px.line_polar(
                df_radar,
                r="Score Moyen (%)",
                theta="Catégorie",
                line_close=True,
                title="Moyenne des scores par thématique sur tous les résultats recommandés",
            )
            fig_radar.update_traces(
                fill="toself", fillcolor="rgba(27, 68, 41, 0.4)", line_color="#1B4429"
            )
            st.plotly_chart(fig_radar, width="content")
        else:
            st.warning(
                "Données insuffisantes ou invalides pour générer le graphique radar."
            )


# ==========================================
# TAB 3: PROFIL DE RECHERCHES
# ==========================================
with tab_profiles:
    # st.subheader("📋 Analyse des Critères et Besoins Saisis")

    if not df_searches.empty and "search_criteria" in df_searches.columns:
        parsed_criteria = []
        parsed_weights_by_criteria = []
        criteria_stats = analytics_data.ParseStats()
        weights_stats = analytics_data.ParseStats()

        for _, row in df_searches.iterrows():
            sc_raw = row.get("search_criteria")
            w_raw = row.get("weights")
            c_dict = analytics_data.parse_json_payload(
                sc_raw, criteria_stats, expected_type=dict
            )
            w_dict = analytics_data.parse_json_payload(
                w_raw, weights_stats, expected_type=dict
            )
            if c_dict is not None:
                parsed_criteria.append(c_dict)
                # Keep weights aligned with their own criteria record; the old
                # independent lists could pair two different searches.
                parsed_weights_by_criteria.append(w_dict or {})

        analytics_data.log_invalid_payload_summary("search_criteria", criteria_stats)
        analytics_data.log_invalid_payload_summary("search_weights", weights_stats)
        invalid_profile_rows = criteria_stats.invalid_rows + weights_stats.invalid_rows
        if invalid_profile_rows:
            st.caption(
                f"{invalid_profile_rows} donnée(s) de profil invalide(s) ont été écartées."
            )

        df_crit = pd.DataFrame(parsed_criteria)

        # 1. Type de Lieux de Recherche (Camembert)
        col_prof1, col_prof2 = st.columns(2)

        with col_prof1:
            if not df_crit.empty and "loc_search_area" in df_crit.columns:
                area_mapping = {
                    "departement": "Département",
                    "region": "Région",
                    "france": "France Métropolitaine",
                }
                areas = (
                    df_crit["loc_search_area"].map(area_mapping).fillna("Département")
                )
                area_counts = areas.value_counts().reset_index()
                area_counts.columns = ["Aire Géo", "Nombre"]
                fig_area = px.pie(
                    area_counts,
                    names="Aire Géo",
                    values="Nombre",
                    title="Répartition par type de zone de recherche",
                    color_discrete_sequence=px.colors.qualitative.Bold,
                )
                st.plotly_chart(fig_area, width="content")
            else:
                st.info("Données d'aire géographique non disponibles.")

        # 2. Profils de Pondérations les plus utilisés (Bar chart)
        with col_prof2:
            # st.markdown("##### Top 5 Profils de Pondération")
            profiles = []
            for idx, c in enumerate(parsed_criteria):
                p = c.get("weight_profile")
                if not p or p == "None" or not str(p).strip():
                    # Infer from weights if available
                    w = parsed_weights_by_criteria[idx]
                    if isinstance(w, dict) and w:
                        vals = [float(v) for v in w.values() if v is not None]
                        if vals and all(v == 1.0 for v in vals):
                            p = "Équilibré"
                        elif vals and len(set(vals)) > 1:
                            p = "Profil personnalisé"
                        else:
                            p = "Équilibré"
                    else:
                        p = "Équilibré"
                profiles.append(str(p).strip())

            if profiles:
                df_prof_counts = pd.Series(profiles).value_counts().reset_index()
                df_prof_counts.columns = ["Profil", "Recherches"]
                top_5_prof = df_prof_counts.head(5)

                fig_profiles = px.bar(
                    top_5_prof,
                    x="Recherches",
                    y="Profil",
                    orientation="h",
                    title="Profils de pondération les plus fréquemment utilisés",
                    color="Profil",
                    color_discrete_sequence=px.colors.qualitative.Dark2,
                )
                fig_profiles.update_layout(yaxis={"categoryorder": "total ascending"})
                st.plotly_chart(fig_profiles, width="content")
            else:
                st.info("Aucun profil de pondération trouvé.")

        st.divider()

        # 3. Composition Familiale (Enfants)
        col_enf1, col_enf2 = st.columns(2)

        with col_enf1:
            # st.markdown("##### Proportion de recherches avec enfants")
            if not df_crit.empty and "nb_enfants" in df_crit.columns:
                has_kids = df_crit["nb_enfants"].apply(
                    lambda x: "Avec enfants" if (x or 0) > 0 else "Sans enfant"
                )
                kids_counts = has_kids.value_counts().reset_index()
                kids_counts.columns = ["Statut", "Nombre"]
                fig_kids_pie = px.pie(
                    kids_counts,
                    names="Statut",
                    values="Nombre",
                    title="Présence d'enfants dans le projet",
                    color_discrete_sequence=["#2E7D32", "#A5D6A7"],
                )
                st.plotly_chart(fig_kids_pie, width="content")

        with col_enf2:
            # st.markdown("##### Top Nombre d'Enfants Renseigné")
            if not df_crit.empty and "nb_enfants" in df_crit.columns:
                nb_kids_counts = df_crit["nb_enfants"].value_counts().reset_index()
                nb_kids_counts.columns = ["Nombre d'enfants", "Recherches"]
                fig_kids_bar = px.bar(
                    nb_kids_counts.head(5),
                    x="Nombre d'enfants",
                    y="Recherches",
                    title="Répartition du nombre d'enfants",
                    color_discrete_sequence=["#1B4429"],
                )
                st.plotly_chart(fig_kids_bar, width="content")

        st.divider()

        # 4. Métiers & Familles ROME
        col_job1, col_job2 = st.columns(2)

        def extract_rome_item(item):
            if isinstance(item, dict):
                code = item.get("code") or item.get("id") or ""
                label = item.get("label") or item.get("name") or ""
                if code and label:
                    return f"{label} ({code})"
                return label or code
            elif isinstance(item, str) and item.strip():
                return item.strip()
            return ""

        all_rome_codes = []
        for c in parsed_criteria:
            metiers_list = c.get("codes_metiers", [])
            if isinstance(metiers_list, list):
                for adult in metiers_list:
                    if isinstance(adult, list):
                        for m in adult:
                            val = extract_rome_item(m)
                            if val:
                                all_rome_codes.append(val)
                    elif isinstance(adult, (dict, str)):
                        val = extract_rome_item(adult)
                        if val:
                            all_rome_codes.append(val)

        with col_job1:
            # st.markdown("##### Recherches incluant des Métiers (ROME)")
            has_jobs_count = len(all_rome_codes)
            status_labels = ["Avec Métier(s)", "Sans Métier"]
            if has_jobs_count > 0:
                counts = [has_jobs_count, max(0, len(parsed_criteria) - has_jobs_count)]
            else:
                counts = [0, len(parsed_criteria)]
            df_job_status = pd.DataFrame({"Statut": status_labels, "Nombre": counts})
            fig_job_pie = px.pie(
                df_job_status,
                names="Statut",
                values="Nombre",
                title="Recherches ciblant des métiers spécifiques",
                color_discrete_sequence=["#1565C0", "#90CAF9"],
            )
            st.plotly_chart(fig_job_pie, width="content")

        with col_job2:
            # st.markdown("##### Top 5 Familles / Codes ROME demandés")
            if all_rome_codes:
                df_rome = pd.Series(all_rome_codes).value_counts().reset_index()
                df_rome.columns = ["Code ROME", "Fréquence"]
                fig_rome = px.bar(
                    df_rome.head(5),
                    x="Fréquence",
                    y="Code ROME",
                    orientation="h",
                    title="Codes ROME les plus fréquents dans les demandes",
                    color="Code ROME",
                    color_discrete_sequence=px.colors.qualitative.Dark24,
                )
                fig_rome.update_layout(yaxis={"categoryorder": "total ascending"})
                st.plotly_chart(fig_rome, width="content")
            else:
                st.info(
                    "Aucun métier/code ROME spécifique n'a encore été saisi dans les recherches enregistrées."
                )

        st.divider()

        # 5. Besoins de Santé
        col_sante1, col_sante2 = st.columns(2)

        with col_sante1:
            # st.markdown("##### Recherches incluant des Besoins en Santé")
            if not df_crit.empty and "besoin_sante" in df_crit.columns:
                has_sante = df_crit["besoin_sante"].apply(
                    lambda x: (
                        "Avec besoin(s) santé"
                        if (isinstance(x, list) and len(x) > 0)
                        else "Sans besoin spécifique"
                    )
                )
                sante_counts = has_sante.value_counts().reset_index()
                sante_counts.columns = ["Statut", "Nombre"]
                fig_sante_pie = px.pie(
                    sante_counts,
                    names="Statut",
                    values="Nombre",
                    title="Proportion de recherches avec besoins médicaux",
                    color_discrete_sequence=["#C62828", "#EF9A9A"],
                )
                st.plotly_chart(fig_sante_pie, width="content")

        with col_sante2:
            # st.markdown("##### Top 5 Besoins en Santé les plus demandés")
            if not df_crit.empty and "besoin_sante" in df_crit.columns:
                all_sante_needs = []
                for c in parsed_criteria:
                    s_list = c.get("besoin_sante", [])
                    if isinstance(s_list, list):
                        all_sante_needs.extend(s_list)

                if all_sante_needs:
                    df_sante = pd.Series(all_sante_needs).value_counts().reset_index()
                    df_sante.columns = ["Besoin Santé", "Nombre"]
                    fig_sante_bar = px.bar(
                        df_sante.head(5),
                        x="Nombre",
                        y="Besoin Santé",
                        orientation="h",
                        title="Structures / Besoins de santé prioritaires",
                        color="Besoin Santé",
                        color_discrete_sequence=px.colors.qualitative.Set1,
                    )
                    fig_sante_bar.update_layout(
                        yaxis={"categoryorder": "total ascending"}
                    )
                    st.plotly_chart(fig_sante_bar, width="content")
                else:
                    st.info("Aucun besoin en santé spécifique renseigné.")
    else:
        st.info("Aucune donnée de critères enregistrée pour cette période.")


# ==========================================
# TAB 4: COÛTS & FINOPS GCP
# ==========================================
with tab_finops:
    st.markdown("##### 💰 Suivi des Dépenses Réelles GCP & FinOps")
    st.caption(
        "Données consolidées depuis l'export Cloud Billing BigQuery pour le projet `odis-stream2-app`."
    )

    if billing_outcome.status == analytics_data.OutcomeStatus.UNAUTHORIZED:
        st.error(
            "❌ **Accès aux données de facturation GCP non autorisé**. "
            "Assurez-vous que le compte de service dispose du rôle de lecture BigQuery "
            "sur la table d'export de facturation (code : BILLING-BQ-UNAUTHORIZED)."
        )
    elif billing_outcome.status == analytics_data.OutcomeStatus.UNAVAILABLE:
        st.warning(
            "⚠️ **Données de facturation temporairement indisponibles**. "
            "Réessayez dans quelques instants (code : BILLING-BQ-UNAVAILABLE)."
        )
    elif billing_outcome.is_success:
        df_billing = billing_outcome.value
        if df_billing is None or df_billing.empty:
            st.info(
                "ℹ️ **Table Cloud Billing connectée avec succès, en attente du premier lot Google Cloud**.\n\n"
                "La table d'export `odis-stream2-app.odis_logs.gcp_billing_export_v1_011680_B35255_2DA84B` est accessible. "
                "Google Cloud Billing injecte les données par lots toutes les quelques heures (délai habituel de 2 à 6 heures après l'activation, non rétroactif).\n\n"
                "En attendant la consolidation de la première facture GCP, voici la consommation enregistrée en temps réel par l'application :"
            )
            if (
                agent_costs_outcome.is_success
                and agent_costs_outcome.value is not None
                and not agent_costs_outcome.value.empty
            ):
                df_agent = agent_costs_outcome.value
                agent_estimated_cost = float(
                    df_agent["total_estimated_cost_eur"].dropna().sum()
                )
                agent_runs_count = int(df_agent["run_count"].dropna().sum())
                st.markdown("###### 🤖 Métriques IA Temps Réel (`agent_state_logs`)")
                m_c1, m_c2 = st.columns(2)
                with m_c1:
                    st.metric(
                        "Coût Estimé Agents IA (Tokens + Grounding)",
                        f"{agent_estimated_cost:.4f} €",
                    )
                with m_c2:
                    st.metric("Exécutions Swarm IA", f"{agent_runs_count}")
        else:
            df_finops = df_billing.copy()

            # --- Financial KPIs ---
            st.markdown("###### Indicateurs Financiers Clés (`odis-stream2-app`)")
            kpi_net, kpi_gross, kpi_cred, kpi_avg_day, kpi_per_search = st.columns(5)

            total_net_cost = (
                float(df_finops["cost_net"].sum()) if not df_finops.empty else 0.0
            )
            total_gross_cost = (
                float(df_finops["cost_gross"].sum()) if not df_finops.empty else 0.0
            )
            total_credits_val = (
                abs(float(df_finops["credits"].sum())) if not df_finops.empty else 0.0
            )
            avg_daily_cost = total_net_cost / max(period_days, 1)
            cost_per_search_val = (
                (total_net_cost / total_searches) if total_searches > 0 else 0.0
            )

            with kpi_net:
                st.metric("Coût Net Facturé", f"{total_net_cost:.2f} €")
            with kpi_gross:
                st.metric("Coût Brut (Avant Remises)", f"{total_gross_cost:.2f} €")
            with kpi_cred:
                st.metric("Crédits & Remises GCP", f"{total_credits_val:.2f} €")
            with kpi_avg_day:
                st.metric("Dépense Moyenne / Jour", f"{avg_daily_cost:.2f} €/j")
            with kpi_per_search:
                st.metric("Coût Infra / Recherche", f"{cost_per_search_val:.3f} €")

            st.divider()

            # --- Charts: Daily Evolution & Service Distribution ---
            col_chart_evol, col_chart_pie = st.columns(2)

            with col_chart_evol:
                if not df_finops.empty and "usage_date" in df_finops.columns:
                    df_daily_costs = (
                        df_finops.groupby(["usage_date", "service_name"])["cost_net"]
                        .sum()
                        .reset_index()
                    )
                    fig_daily_costs = px.bar(
                        df_daily_costs,
                        x="usage_date",
                        y="cost_net",
                        color="service_name",
                        title="Évolution des coûts quotidiens par service (€)",
                        labels={
                            "usage_date": "Date",
                            "cost_net": "Coût Net (€)",
                            "service_name": "Service GCP",
                        },
                        color_discrete_sequence=px.colors.qualitative.Safe,
                    )
                    fig_daily_costs.update_layout(
                        xaxis_title="Date",
                        yaxis_title="Coût Net (€)",
                        barmode="stack",
                    )
                    st.plotly_chart(fig_daily_costs, width="content")
                else:
                    st.info("Aucune donnée temporelle de coût disponible.")

            with col_chart_pie:
                if not df_finops.empty and "service_name" in df_finops.columns:
                    df_by_service = (
                        df_finops.groupby("service_name")["cost_net"]
                        .sum()
                        .reset_index()
                        .sort_values("cost_net", ascending=False)
                    )
                    # Filter out zero-cost items for cleaner pie chart
                    df_pie_data = df_by_service[df_by_service["cost_net"] > 0]
                    if df_pie_data.empty:
                        df_pie_data = df_by_service

                    fig_service_pie = px.pie(
                        df_pie_data,
                        names="service_name",
                        values="cost_net",
                        hole=0.4,
                        title="Répartition des dépenses nettes par service GCP",
                        color_discrete_sequence=px.colors.qualitative.Prism,
                    )
                    st.plotly_chart(fig_service_pie, width="content")
                else:
                    st.info("Aucune répartition par service disponible.")

            st.divider()

            # --- Row 2: Top SKUs Table & AI FinOps Reconciliation ---
            col_skus, col_ai_finops = st.columns(2)

            with col_skus:
                st.markdown("##### 🏆 Top 10 Postes de Coûts (SKUs)")
                if not df_finops.empty:
                    df_top_skus = (
                        df_finops.groupby(["service_name", "sku_description"])[
                            ["cost_gross", "cost_net"]
                        ]
                        .sum()
                        .reset_index()
                        .sort_values("cost_net", ascending=False)
                        .head(10)
                    )
                    df_top_skus_display = df_top_skus.copy()
                    df_top_skus_display["cost_net"] = df_top_skus_display[
                        "cost_net"
                    ].apply(lambda x: f"{x:.4f} €")
                    df_top_skus_display["cost_gross"] = df_top_skus_display[
                        "cost_gross"
                    ].apply(lambda x: f"{x:.4f} €")
                    df_top_skus_display.columns = [
                        "Service",
                        "Description SKU",
                        "Coût Brut",
                        "Coût Net",
                    ]
                    st.dataframe(
                        df_top_skus_display, use_container_width=True, hide_index=True
                    )
                else:
                    st.info("Aucun détail SKU disponible.")

            with col_ai_finops:
                st.markdown("##### 🤖 Rapprochement FinOps IA (Tokens vs Factures)")
                actual_vertex_cost = (
                    float(
                        df_finops[df_finops["service_name"] == "Vertex AI"][
                            "cost_net"
                        ].sum()
                    )
                    if not df_finops.empty
                    and "Vertex AI" in df_finops["service_name"].values
                    else 0.0
                )

                actual_places_cost = (
                    float(
                        df_finops[
                            df_finops["service_name"].str.contains(
                                "Places", case=False, na=False
                            )
                        ]["cost_net"].sum()
                    )
                    if not df_finops.empty
                    else 0.0
                )

                agent_estimated_cost = 0.0
                agent_runs_count = 0
                if (
                    agent_costs_outcome.is_success
                    and agent_costs_outcome.value is not None
                ):
                    df_agent = agent_costs_outcome.value
                    if not df_agent.empty:
                        agent_estimated_cost = float(
                            df_agent["total_estimated_cost_eur"].dropna().sum()
                        )
                        agent_runs_count = int(df_agent["run_count"].dropna().sum())

                recon_col1, recon_col2 = st.columns(2)
                with recon_col1:
                    st.metric("Facture Vertex AI (Réel)", f"{actual_vertex_cost:.2f} €")
                    st.metric(
                        "Facture Places API (Réel)", f"{actual_places_cost:.2f} €"
                    )
                with recon_col2:
                    st.metric(
                        "Estim. Tokens Agents (App)", f"{agent_estimated_cost:.2f} €"
                    )
                    st.metric("Exécutions Swarm IA", f"{agent_runs_count}")

                st.info(
                    "ℹ️ **Note FinOps** : L'estimation applicative se base sur le barème tarifaire Gemini "
                    "(EU) par token. La facture réelle GCP intègre en plus les mécanismes de prompt-caching, "
                    "le grounding Vertex AI Search et les arrondis de facturation à la seconde/au SKU."
                )
