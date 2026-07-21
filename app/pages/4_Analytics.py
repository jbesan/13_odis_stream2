import os
import json
import logging
import streamlit as st
import pandas as pd
import plotly.express as px
from google.cloud import bigquery

from utils import auth
from ui import components as ui_comp
from utils import common as utils
from services import telemetry

logger = logging.getLogger("pages.analytics")

# --- Page Config ---
st.set_page_config(page_title="OD&IS - Analytics", page_icon="📊", layout="wide")

# --- Auth Guard ---
if not auth.check_password():
    st.stop()

if not auth.is_admin():
    st.error("🔒 Accès refusé : Cette page est réservée aux administrateurs.")
    st.stop()

telemetry.log_page_view("Analytics")

# --- Sidebar ---
with st.sidebar:
    logo_path = utils.get_asset_path("logo-jaccueille-singa.png")
    logo_b64 = utils.get_base64_image(logo_path)
    if logo_b64:
        st.markdown(
            f'<img src="data:image/png;base64,{logo_b64}" width="150" style="margin-bottom: 20px;">',
            unsafe_allow_html=True,
        )
    st.header("📊 Administration")
    st.write("Bienvenue sur le tableau de bord d'analyse de l'usage et des recommandations d'OD&IS.")
    st.divider()
    ui_comp.start_over()


# --- BigQuery Helper ---
@st.cache_resource(ttl=300)
def get_bq_client():
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GCP_PROJECT"):
        return None
    try:
        return bigquery.Client()
    except Exception as e:
        logger.error(f"Failed to initialize BQ client: {e}")
        return None


client = get_bq_client()

st.title("📊 Dashboard Analytics & BI Métier")

if client is None:
    st.error(
        "❌ **Connexion à BigQuery indisponible**.\n\n"
        "Pour afficher ce tableau de bord, assurez-vous d'exécuter l'application avec un projet GCP actif "
        "(`GOOGLE_CLOUD_PROJECT` ou `GCP_PROJECT`) et des identifiants valides."
    )
    st.stop()

dataset_id = "odis_logs"

# --- Date Filters ---
col_filter1, col_filter2 = st.columns([1, 2])
with col_filter1:
    period_days = st.selectbox(
        "Période d'analyse",
        options=[7, 30, 90, 365],
        index=1,
        format_func=lambda x: f"Derniers {x} jours",
    )


# Fetch data cached functions
@st.cache_data(ttl=60)
def fetch_analytics_data(days: int):
    query_searches = f"""
        SELECT 
            interaction_id,
            timestamp,
            username,
            IFNULL(org_id, 'défaut') AS org_id,
            IFNULL(search_hash, '') AS search_hash,
            source_flow,
            search_criteria,
            weights,
            top_results,
            detailed_breakdown
        FROM `{client.project}.{dataset_id}.search_events`
        WHERE TIMESTAMP(timestamp) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        ORDER BY timestamp DESC
    """

    query_usage = f"""
        SELECT 
            interaction_id,
            login_session_id,
            timestamp,
            username,
            IFNULL(org_id, 'défaut') AS org_id,
            event_name,
            payload
        FROM `{client.project}.{dataset_id}.usage_events`
        WHERE TIMESTAMP(timestamp) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        ORDER BY timestamp DESC
    """

    try:
        df_searches = client.query(query_searches).to_dataframe(create_bqstorage_client=False)
    except Exception as e:
        logger.warning(f"Failed to query search_events: {e}")
        df_searches = pd.DataFrame()

    try:
        df_usage = client.query(query_usage).to_dataframe(create_bqstorage_client=False)
    except Exception as e:
        logger.warning(f"Failed to query usage_events: {e}")
        df_usage = pd.DataFrame()

    return df_searches, df_usage


with st.spinner("Chargement des données BigQuery..."):
    df_searches, df_usage = fetch_analytics_data(period_days)

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
tab_global, tab_recommandations, tab_profiles = st.tabs(
    ["📈 Activité Globale", "🥇 Résultats & Recommandations", "🔎 Profil de Recherches"]
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
        auto_detect_count = len(df_usage[df_usage["event_name"] == "auto_detect_criteria"])
        ia_analyses_count = len(df_usage[df_usage["event_name"] == "run_ia_analysis"])
        details_view_count = len(df_usage[df_usage["event_name"] == "view_commune_details"])
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
            for idx, row in df_searches.iterrows():
                top_res_str = row.get("top_results")
                if top_res_str:
                    try:
                        top_list = (
                            json.loads(top_res_str)
                            if isinstance(top_res_str, str)
                            else top_res_str
                        )
                        for rank, city in enumerate(top_list, start=1):
                            city_records.append(
                                {
                                    "codgeo": city.get("codgeo"),
                                    "libgeo": city.get("libgeo"),
                                    "score": city.get("score", 0.0),
                                    "rank": rank,
                                    "org_id": row.get("org_id"),
                                }
                            )
                    except Exception:
                        pass

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
                st.info("Aucun détail de ville disponible dans les résultats de recherche.")
        else:
            st.info("Aucune donnée de recherche à analyser.")

    with col_rec2:
        # st.markdown("##### Top 15 des Communes les plus Consultées (Détails)")
        if not df_usage.empty:
            df_details = df_usage[df_usage["event_name"] == "view_commune_details"]
            if not df_details.empty:
                consult_records = []
                for _, row in df_details.iterrows():
                    payload_raw = row.get("payload")
                    if payload_raw:
                        try:
                            p = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                            consult_records.append({
                                "codgeo": p.get("codgeo"),
                                "name": p.get("name", p.get("codgeo")),
                            })
                        except Exception:
                            pass
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
                    fig_consult.update_layout(yaxis={"categoryorder": "total ascending"})
                    st.plotly_chart(fig_consult, width="content")
                else:
                    st.info("Aucune consultation de commune enregistrée.")
            else:
                st.info("Aucun événement de consultation 'En savoir plus' trouvé pour cette période.")
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

        for _, row in df_searches.iterrows():
            db_raw = row.get("detailed_breakdown")
            if db_raw:
                try:
                    db = json.loads(db_raw) if isinstance(db_raw, str) else db_raw
                    for codgeo, city_data in db.items():
                        scores_dict = city_data.get("scores", {})
                        for cat_key, cat_name in cat_mapping.items():
                            items = scores_dict.get(cat_key, [])
                            for item in items:
                                val = None
                                if isinstance(item, dict):
                                    val = item.get("score_normalise")
                                    if val is None:
                                        val = item.get("score", 0.0)
                                else:
                                    val = getattr(item, "score_normalise", getattr(item, "score", 0.0))
                                if val is not None:
                                    cat_scores_accumulator[cat_name].append(float(val))
                except Exception:
                    pass

        radar_data = []
        for cat_name, scores in cat_scores_accumulator.items():
            mean_val = (sum(scores) / len(scores)) * 100 if scores else 50.0
            radar_data.append({"Catégorie": cat_name, "Score Moyen (%)": round(mean_val, 1)})

        df_radar = pd.DataFrame(radar_data)

        if not df_radar.empty and df_radar["Score Moyen (%)"].sum() > 0:
            fig_radar = px.line_polar(
                df_radar,
                r="Score Moyen (%)",
                theta="Catégorie",
                line_close=True,
                title="Moyenne des scores par thématique sur tous les résultats recommandés",
            )
            fig_radar.update_traces(fill="toself", fillcolor="rgba(27, 68, 41, 0.4)", line_color="#1B4429")
            st.plotly_chart(fig_radar, width="content")
        else:
            st.info("Données insuffisantes pour générer le graphique radar.")


# ==========================================
# TAB 3: PROFIL DE RECHERCHES
# ==========================================
with tab_profiles:
    # st.subheader("📋 Analyse des Critères et Besoins Saisis")

    if not df_searches.empty and "search_criteria" in df_searches.columns:
        parsed_criteria = []
        parsed_weights = []

        for _, row in df_searches.iterrows():
            sc_raw = row.get("search_criteria")
            w_raw = row.get("weights")
            if sc_raw:
                try:
                    c_dict = json.loads(sc_raw) if isinstance(sc_raw, str) else sc_raw
                    parsed_criteria.append(c_dict)
                except Exception:
                    pass
            if w_raw:
                try:
                    w_dict = json.loads(w_raw) if isinstance(w_raw, str) else w_raw
                    parsed_weights.append(w_dict)
                except Exception:
                    pass

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
                areas = df_crit["loc_search_area"].map(area_mapping).fillna("Département")
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
                    w = parsed_weights[idx] if idx < len(parsed_weights) else {}
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
                has_kids = df_crit["nb_enfants"].apply(lambda x: "Avec enfants" if (x or 0) > 0 else "Sans enfant")
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
                st.info("Aucun métier/code ROME spécifique n'a encore été saisi dans les recherches enregistrées.")

        st.divider()

        # 5. Besoins de Santé
        col_sante1, col_sante2 = st.columns(2)

        with col_sante1:
            # st.markdown("##### Recherches incluant des Besoins en Santé")
            if not df_crit.empty and "besoin_sante" in df_crit.columns:
                has_sante = df_crit["besoin_sante"].apply(
                    lambda x: "Avec besoin(s) santé" if (isinstance(x, list) and len(x) > 0) else "Sans besoin spécifique"
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
                    fig_sante_bar.update_layout(yaxis={"categoryorder": "total ascending"})
                    st.plotly_chart(fig_sante_bar, width="content")
                else:
                    st.info("Aucun besoin en santé spécifique renseigné.")
    else:
        st.info("Aucune donnée de critères enregistrée pour cette période.")

