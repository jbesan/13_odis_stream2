import logging
from typing import List, Optional, Any, Tuple
import pandas as pd
import streamlit as st

import config as cfg
from core.models import (
    CommuneResult,
    CommuneScoreDetail,
    AssociationDetail,
    InclusionServiceDetail,
    JobOfferDetail,
)
from core.scoring import _format_kpi_value
from core.enrichment_status import EnrichmentStatus
from utils.data_loader import fetch_salesforce_jaccueille_bdv
from agents.utils import odis_get_bg_result
from services import telemetry

logger = logging.getLogger("ui.details_dialog")


def _on_details_dialog_dismiss():
    st.session_state.active_details_index = None


def _enrichment_status_for_city(
    h: Optional[str], status_key: str, codgeo: str
) -> Optional[str]:
    if not h:
        return None
    bg_res = odis_get_bg_result(h)
    if not isinstance(bg_res, dict):
        return None
    status_data = bg_res.get(status_key, {}).get(str(codgeo))
    return status_data.get("status") if isinstance(status_data, dict) else None


def _should_poll_enrichment(h: Optional[str], status_key: str, codgeo: str) -> bool:
    """Only run a timed fragment while a task is genuinely non-terminal."""
    if st.session_state.get("immutable_shared_snapshot"):
        return False
    status = _enrichment_status_for_city(h, status_key, codgeo)
    return status is None or status == EnrichmentStatus.PENDING.value


def sync_background_data(commune: CommuneResult, h: Optional[str]):
    """
    Syncs both enrichment (associations) and pitches from the background store
    back into the CommuneResult model for persistence.
    """
    if not h:
        return

    bg_res = odis_get_bg_result(h)
    if not isinstance(bg_res, dict):
        return

    # 1. Sync Enrichment (Associations)
    if "enrichment" in bg_res:
        enrich_data = bg_res["enrichment"].get(str(commune.codgeo))
        if enrich_data and not commune.inclusion.asso_inclusion_list_by_cat:
            logging.debug(f"✨ [SYNC] Associations sync for {commune.codgeo}")
            inc_data = commune.inclusion
            inc_data.asso_refugee_list = [
                AssociationDetail.model_validate(a)
                for a in enrich_data.get("refugee", [])
            ]
            inc_data.asso_refugee_count = len(inc_data.asso_refugee_list)

            raw_inclusion = enrich_data.get("inclusion", {})
            inc_data.asso_inclusion_list_by_cat = {
                cat: [AssociationDetail.model_validate(a) for a in asso_list]
                for cat, asso_list in raw_inclusion.items()
            }
            inc_data.asso_inclusion_count = sum(
                len(l) for l in inc_data.asso_inclusion_list_by_cat.values()
            )

    # 1b. Sync Enrichment (Job Offers)
    if "jobs_enrichment" in bg_res:
        jobs_city_data = bg_res["jobs_enrichment"].get(str(commune.codgeo))
        if (
            jobs_city_data
            and jobs_city_data.get("status")
            in {
                EnrichmentStatus.SUCCESS_NONEMPTY.value,
                EnrichmentStatus.SUCCESS_EMPTY.value,
                EnrichmentStatus.PARTIAL.value,
            }
            and not commune.employment.matching_job_offers
        ):
            logging.debug(f"✨ [SYNC] Jobs sync for {commune.codgeo}")
            emp_data = commune.employment

            raw_jobs = jobs_city_data.get("jobs", [])
            emp_data.matching_job_offers = [
                [JobOfferDetail.model_validate(o) for o in adult_list]
                for adult_list in raw_jobs
            ]
            if "total" in jobs_city_data:
                emp_data.standard_jobs_matching_total = jobs_city_data["total"]

    # 1c. Sync Enrichment (Inclusion Services)
    if "inclusion_services_enrichment" in bg_res:
        incl_services_data = bg_res["inclusion_services_enrichment"].get(
            str(commune.codgeo)
        )
        if incl_services_data and not commune.inclusion.services_detailed:
            logging.debug(f"✨ [SYNC] Inclusion services sync for {commune.codgeo}")
            inc_data = commune.inclusion
            inc_data.services_detailed = {
                cat: [InclusionServiceDetail.model_validate(s) for s in svc_list]
                for cat, svc_list in incl_services_data.items()
            }

    # 2. Sync Pitches (AI analysis)
    if "pitches" in bg_res:
        pitches_data = bg_res["pitches"]
        if isinstance(pitches_data, dict):
            # A. City-specific pitch
            if "pitches" in pitches_data:
                city_pitches = pitches_data["pitches"]
                if isinstance(city_pitches, dict):
                    cg = str(commune.codgeo).strip()
                    cname = commune.name.lower().strip() if commune.name else ""
                    pitch_for_city = (
                        city_pitches.get(cg)
                        or city_pitches.get(cg.zfill(5))
                        or city_pitches.get(cg.lstrip("0"))
                        or city_pitches.get(cname)
                        or next(
                            (
                                v
                                for k, v in city_pitches.items()
                                if k.lower().strip() == cname
                            ),
                            None,
                        )
                    )
                    if pitch_for_city and not commune.refiner_pitch:
                        logging.debug(f"✨ [SYNC] Pitch sync for {commune.codgeo}")
                        commune.refiner_pitch = pitch_for_city

            # B. Global introduction (Global Pitch)
            if "global" in pitches_data and "search_results" in st.session_state:
                if not st.session_state.search_results.global_pitch:
                    st.session_state.search_results.global_pitch = pitches_data[
                        "global"
                    ]

    # 3. Sync Unified Briefing (Profile Summary)
    if "odis_brief" in bg_res and st.session_state.get("config"):
        brief_val = bg_res["odis_brief"]
        if brief_val and st.session_state.config.odis_brief != brief_val:
            logging.debug("✨ [SYNC] Unified Briefing sync")
            st.session_state.config.odis_brief = brief_val


def _get_jaccueille_salesforce_urls(
    commune: CommuneResult,
) -> Tuple[Optional[str], Optional[str]]:
    """Returns (acc_url, prosp_url) if org_context == 'jaccueille', else (None, None)."""
    org_is_jaccueille = False
    org = st.session_state.get("org")
    if org:
        if isinstance(org, dict):
            org_id = org.get("id")
        elif hasattr(org, "id"):
            org_id = org.id
        else:
            org_id = str(org)
        if str(org_id).lower() == "jaccueille":
            org_is_jaccueille = True

    cfg_obj = st.session_state.get("config")
    if cfg_obj and getattr(cfg_obj, "org_context", None) == "jaccueille":
        org_is_jaccueille = True

    if not org_is_jaccueille:
        return None, None

    df_bdv = fetch_salesforce_jaccueille_bdv()
    bdv_code = (
        commune.codgeo_bdv
        or getattr(commune.territoire, "bassin_de_vie", None)
        or commune.codgeo
    )

    codes_postaux = []
    if not df_bdv.empty and "bassin_de_vie" in df_bdv.columns:
        row = df_bdv[df_bdv["bassin_de_vie"] == str(bdv_code)]
        if not row.empty:
            r = row.iloc[0]
            cp_json = r.get("codes_postaux")
            if cp_json:
                try:
                    import json

                    codes_postaux = (
                        json.loads(cp_json) if isinstance(cp_json, str) else cp_json
                    )
                except Exception as e:
                    logger.warning("Error parsing codes_postaux JSON for J'Accueille SF link: %s", e)

    cp_param = ",".join(str(cp) for cp in codes_postaux) if codes_postaux else ""
    acc_report_base = cfg.SF_REPORT_ACCUEILLANTS_URL
    prosp_report_base = cfg.SF_REPORT_PROSPECTS_URL

    acc_url = f"{acc_report_base}?fv0={cp_param}" if cp_param else acc_report_base
    prosp_url = (
        f"{prosp_report_base}?fv0={cp_param}" if cp_param else prosp_report_base
    )
    return acc_url, prosp_url


def render_jaccueille_housing_info(commune: CommuneResult):
    """
    Renders J'Accueille hosts & prospects counts with report links if org_context == 'jaccueille'.
    """
    acc_url, prosp_url = _get_jaccueille_salesforce_urls(commune)
    if not acc_url and not prosp_url:
        return

    housing_data = commune.housing
    j_count = (
        int(housing_data.host_count) if housing_data and housing_data.host_count else 0
    )

    df_bdv = fetch_salesforce_jaccueille_bdv()
    bdv_code = (
        commune.codgeo_bdv
        or getattr(commune.territoire, "bassin_de_vie", None)
        or commune.codgeo
    )
    lead_count = 0
    contact_count = j_count
    if not df_bdv.empty and "bassin_de_vie" in df_bdv.columns:
        row = df_bdv[df_bdv["bassin_de_vie"] == str(bdv_code)]
        if not row.empty:
            r = row.iloc[0]
            if "contact_count" in r and pd.notna(r["contact_count"]):
                contact_count = int(r["contact_count"])
            if "lead_count" in r and pd.notna(r["lead_count"]):
                lead_count = int(r["lead_count"])

    if contact_count == 0 and lead_count == 0:
        return

    st.info(
        f"**{contact_count} accueillants** J'Accueille dans le bassin de vie ([voir la liste sur Salesforce]({acc_url}))  \n"
        f"**{lead_count} prospects** J'Accueille dans le bassin de vie ([voir la liste sur Salesforce]({prosp_url}))"
    )


def render_associations_enrichment(commune: CommuneResult, h: Optional[str]):
    """Render association enrichment once; polling is controlled by the caller."""
    inc_data = commune.inclusion
    if h and not inc_data.asso_inclusion_list_by_cat:
        bg_res = odis_get_bg_result(h)
        if isinstance(bg_res, dict) and "enrichment" in bg_res:
            enrich_data = bg_res["enrichment"].get(str(commune.codgeo))
            if enrich_data:
                # We use the new AssociationDetail model instead of raw strings
                inc_data.asso_refugee_list = [
                    AssociationDetail.model_validate(a)
                    for a in enrich_data.get("refugee", [])
                ]
                inc_data.asso_refugee_count = len(inc_data.asso_refugee_list)
                raw_inclusion = enrich_data.get("inclusion", {})
                inc_data.asso_inclusion_list_by_cat = {
                    cat: [AssociationDetail.model_validate(a) for a in asso_list]
                    for cat, asso_list in raw_inclusion.items()
                }
                inc_data.asso_inclusion_count = sum(
                    len(l) for l in inc_data.asso_inclusion_list_by_cat.values()
                )
                st.rerun()  # Trigger dialog rerun to reveal content

    total_assos = (inc_data.asso_refugee_count or 0) + (
        inc_data.asso_inclusion_count or 0
    )
    if total_assos > 0:
        categories_to_show = {}
        if inc_data.asso_refugee_list:
            categories_to_show["Intégration des réfugiés & migrants"] = (
                inc_data.asso_refugee_list
            )
        if inc_data.asso_inclusion_list_by_cat:
            for cat, asso_list in inc_data.asso_inclusion_list_by_cat.items():
                categories_to_show[cat] = asso_list

        for cat, asso_list in sorted(categories_to_show.items()):
            with st.expander(f"**{cat}** ({len(asso_list)})", expanded=False):
                for asso in asso_list:
                    url = f"https://www.assoce.fr/waldec/{asso.id}" if asso.id else "#"
                    st.markdown(
                        f"**{asso.name}**: {asso.description or ''} [Détails]({url})"
                    )
    else:
        bg_res = odis_get_bg_result(h) if h else None
        status_data = (
            bg_res.get("association_enrichment_status", {}).get(str(commune.codgeo))
            if isinstance(bg_res, dict)
            else None
        )
        status = status_data.get("status") if isinstance(status_data, dict) else None
        if st.session_state.get("immutable_shared_snapshot"):
            st.caption("Associations non incluses dans cet instantané partagé.")
        elif status in {
            EnrichmentStatus.ERROR.value,
            EnrichmentStatus.TIMEOUT.value,
            EnrichmentStatus.NOT_CONFIGURED.value,
        }:
            st.info("Associations temporairement indisponibles.")
        elif status == EnrichmentStatus.PARTIAL.value:
            st.warning(
                "Liste d'associations partielle : certaines données sont temporairement indisponibles."
            )
        elif status == EnrichmentStatus.PENDING.value:
            st.caption("Traitement des associations locales en cours…")
        elif h and (
            not isinstance(bg_res, dict)
            or "association_enrichment_status" not in bg_res
        ):
            st.caption("Traitement des associations locales en cours…")
        else:
            st.info("Aucune association répertoriée.")


def render_inclusion_services_enrichment(commune: CommuneResult, h: Optional[str]):
    """Render structured inclusion services with Data Inclusion details, or fallback to grouped services."""
    inc_data = commune.inclusion
    if h and not inc_data.services_detailed:
        bg_res = odis_get_bg_result(h)
        if isinstance(bg_res, dict) and "inclusion_services_enrichment" in bg_res:
            incl_services_data = bg_res["inclusion_services_enrichment"].get(
                str(commune.codgeo)
            )
            if incl_services_data:
                inc_data.services_detailed = {
                    cat: [InclusionServiceDetail.model_validate(s) for s in svc_list]
                    for cat, svc_list in incl_services_data.items()
                }
                st.rerun()

    bg_res = odis_get_bg_result(h) if h else None
    status_data = (
        bg_res.get("inclusion_services_status", {}).get(str(commune.codgeo))
        if isinstance(bg_res, dict)
        else None
    )
    status = status_data.get("status") if isinstance(status_data, dict) else None
    is_loading = (
        bool(h)
        and not st.session_state.get("immutable_shared_snapshot")
        and (status is None or status == EnrichmentStatus.PENDING.value)
    )

    if inc_data.services_detailed:
        # Global deduplication: each structure appears in at most one expander.
        seen_struct_keys: set[str] = set()
        for thematique, services in sorted(inc_data.services_detailed.items()):
            if not services:
                continue
            struct_map: dict[str, dict] = {}
            for srv in services:
                struct_key = srv.structure_id or srv.nom_structure or srv.name
                if struct_key in seen_struct_keys:
                    continue
                if struct_key not in struct_map:
                    struct_map[struct_key] = {
                        "nom": srv.nom_structure.title() or srv.name.title(),
                        "presentation_structure": getattr(
                            srv, "presentation_structure", None
                        )
                        or "",
                        "lien_source": srv.lien_source,
                        "distance_km": getattr(srv, "distance_km", None),
                        "commune_nom": getattr(srv, "commune_nom", "") or "",
                        "services": [],
                    }
                elif getattr(srv, "distance_km", None) is not None:
                    curr_dist = struct_map[struct_key]["distance_km"]
                    if curr_dist is None or srv.distance_km < curr_dist:
                        struct_map[struct_key]["distance_km"] = srv.distance_km

                svc_label = srv.name
                if svc_label and not any(
                    item["name"] == svc_label.capitalize()
                    for item in struct_map[struct_key]["services"]
                ):
                    struct_map[struct_key]["services"].append(
                        {
                            "name": svc_label.capitalize(),
                            "description": srv.description or "",
                        }
                    )

            if not struct_map:
                continue
            seen_struct_keys.update(struct_map.keys())

            with st.expander(f"{thematique} ({len(struct_map)})", expanded=False):
                sorted_structs = sorted(
                    struct_map.values(),
                    key=lambda item: (
                        item["distance_km"]
                        if item.get("distance_km") is not None
                        else 999,
                        item["nom"],
                    ),
                )
                for struct_data in sorted_structs:
                    url_part = (
                        f" [↗ Fiche]({struct_data['lien_source']})"
                        if struct_data["lien_source"]
                        else ""
                    )
                    dist_val = struct_data.get("distance_km")
                    commune_val = struct_data.get("commune_nom")
                    if dist_val == 0:
                        pill = " :blue-badge[Sur place]"
                    elif dist_val is not None:
                        if commune_val:
                            pill = f" :gray-badge[{commune_val} · {dist_val} km]"
                        else:
                            pill = f" :gray-badge[{dist_val} km]"
                    else:
                        pill = ""

                    header_text = f"• **{struct_data['nom']}**{pill}{url_part}"
                    if struct_data["presentation_structure"]:
                        st.markdown(
                            header_text,
                            help=struct_data["presentation_structure"],
                        )
                    else:
                        st.markdown(header_text)

                    for service in struct_data["services"]:
                        if service["description"]:
                            st.caption(
                                f"&nbsp;&nbsp;&nbsp;&nbsp;└ {service['name']}",
                                help=service["description"],
                            )
                        else:
                            st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;└ {service['name']}")
    else:
        services_grouped = inc_data.services_grouped
        if services_grouped:
            for thematique, names in sorted(services_grouped.items()):
                items = sorted({name for name in names if pd.notna(name)})
                if items:
                    with st.expander(f"{thematique} ({len(items)})", expanded=False):
                        for name in items:
                            st.write(f"• {name}")
        elif not is_loading:
            st.info("Aucun service spécifique référencé.")

        if is_loading:
            st.caption("⌛ _Chargement des détails des services depuis Data Inclusion..._")

    if not h:
        return

    if st.session_state.get("immutable_shared_snapshot"):
        st.caption(
            "Détails des services Data Inclusion non inclus dans cet instantané partagé."
        )
    elif status in {
        EnrichmentStatus.ERROR.value,
        EnrichmentStatus.TIMEOUT.value,
        EnrichmentStatus.NOT_CONFIGURED.value,
    }:
        st.caption("Détails des services Data Inclusion temporairement indisponibles.")
    elif status == EnrichmentStatus.PARTIAL.value:
        st.caption("Détails des services Data Inclusion partiels.")


def render_jobs_enrichment(commune: CommuneResult, h: Optional[str]):
    """Render job enrichment once; polling is controlled by the caller."""
    emp_data = commune.employment

    if h and not emp_data.matching_job_offers:
        bg_res = odis_get_bg_result(h)
        if isinstance(bg_res, dict) and "jobs_enrichment" in bg_res:
            jobs_city_data = bg_res["jobs_enrichment"].get(str(commune.codgeo))
            if jobs_city_data:
                if jobs_city_data.get("status") in {
                    EnrichmentStatus.SUCCESS_NONEMPTY.value,
                    EnrichmentStatus.SUCCESS_EMPTY.value,
                    EnrichmentStatus.PARTIAL.value,
                }:
                    raw_jobs = jobs_city_data.get("jobs", [])
                    emp_data.matching_job_offers = [
                        [JobOfferDetail.model_validate(o) for o in adult_list]
                        for adult_list in raw_jobs
                    ]
                    if "total" in jobs_city_data:
                        emp_data.standard_jobs_matching_total = jobs_city_data["total"]
                    st.rerun()  # Trigger dialog rerun to reveal content

    bg_res = odis_get_bg_result(h) if h else None
    jobs_city_data = (
        bg_res.get("jobs_enrichment", {}).get(str(commune.codgeo))
        if isinstance(bg_res, dict) and "jobs_enrichment" in bg_res
        else None
    )

    # Helper function to render a single job offer in the premium style
    def render_job_card(offer):
        company_display = f" chez **{offer.company}**" if offer.company else ""
        st.markdown(f"**{offer.title}**{company_display}")

        # Badges line
        badges = []
        if offer.contract_label or offer.contract_type:
            badges.append(f"💼 {offer.contract_label or offer.contract_type}")
        if offer.location:
            badges.append(f"📍 {offer.location}")
        loc_insee = getattr(offer, "location_insee", None)
        if loc_insee and str(loc_insee) == str(commune.codgeo):
            badges.append("🟢 **Même commune**")
        if offer.salary:
            badges.append(f"💰 {offer.salary}")

        # New context badges (experience and work duration)
        experience = getattr(offer, "experience", None)
        if experience:
            badges.append(f"🎓 {experience}")
        work_duration = getattr(offer, "work_duration", None)
        if work_duration:
            badges.append(f"⏱️ {work_duration}")

        if badges:
            st.markdown(" | ".join(badges))

        brief = getattr(offer, "job_brief", None)
        if brief:
            st.markdown(f"{brief}")
        elif offer.description:
            st.caption(offer.description)

        # Display publication date
        date_creation = getattr(offer, "date_creation", None)
        if date_creation:
            date_str = (
                date_creation.split("T")[0] if "T" in date_creation else date_creation
            )
            st.caption(f"Publiée le : {date_str}")

        if offer.url:
            st.link_button("Voir l'Offre", offer.url, type="secondary")
        st.divider()

    if emp_data.matching_job_offers and any(emp_data.matching_job_offers):
        for i, adult_jobs in enumerate(emp_data.matching_job_offers):
            if not adult_jobs:
                continue
            title = f"Meilleures offres pour l'Adulte {i + 1}"
            with st.expander(title, expanded=True):
                # Group offers by ROME label
                from collections import defaultdict

                grouped = defaultdict(list)
                for offer in adult_jobs:
                    r_label = getattr(offer, "rome_label", None) or "Autre"
                    grouped[r_label].append(offer)

                # If we have results for more than 1 ROME code, group in nested expanders
                if len(grouped) > 1:
                    for rome_label, rome_offers in grouped.items():
                        with st.expander(
                            f"💼 {rome_label} ({len(rome_offers)})", expanded=False
                        ):
                            for offer in rome_offers:
                                render_job_card(offer)
                else:
                    # Otherwise, list them directly
                    for offer in adult_jobs:
                        render_job_card(offer)

    elif jobs_city_data and jobs_city_data.get("status") in {
        EnrichmentStatus.ERROR.value,
        EnrichmentStatus.TIMEOUT.value,
        EnrichmentStatus.NOT_CONFIGURED.value,
    }:
        with st.expander("💼 Offres d'emploi directes", expanded=True):
            st.info("⚠️ Offres d'emploi temporairement indisponibles.")
    elif (
        jobs_city_data
        and jobs_city_data.get("status") == EnrichmentStatus.PARTIAL.value
    ):
        with st.expander("💼 Offres d'emploi directes", expanded=True):
            st.warning(
                "⚠️ Offres d'emploi partielles : certaines recherches sont temporairement indisponibles."
            )
    elif (
        jobs_city_data
        and jobs_city_data.get("status") == EnrichmentStatus.PENDING.value
    ):
        st.caption("Traitement des offres d'emploi locales en cours…")
    elif st.session_state.get("immutable_shared_snapshot"):
        st.caption(
            "Offres d'emploi en direct non incluses dans cet instantané partagé."
        )
    elif h and (not bg_res or "jobs_enrichment" not in bg_res or not jobs_city_data):
        st.caption("Traitement des offres d'emploi locales en cours…")
    else:
        st.info("Aucune offre d'emploi directe répertoriée dans le rayon de recherche.")


@st.fragment(run_every=3.0)
def polling_associations_fragment(commune: CommuneResult, h: Optional[str]):
    """Poll associations only until their provider state becomes terminal."""
    render_associations_enrichment(commune, h)
    if not _should_poll_enrichment(h, "association_enrichment_status", commune.codgeo):
        st.rerun()


@st.fragment(run_every=3.0)
def polling_inclusion_services_fragment(commune: CommuneResult, h: Optional[str]):
    """Poll inclusion services only until their provider state becomes terminal."""
    render_inclusion_services_enrichment(commune, h)
    if not _should_poll_enrichment(h, "inclusion_services_status", commune.codgeo):
        st.rerun()


@st.fragment(run_every=3.0)
def polling_jobs_fragment(commune: CommuneResult, h: Optional[str]):
    """Poll job offers only until their provider state becomes terminal."""
    render_jobs_enrichment(commune, h)
    if not _should_poll_enrichment(h, "jobs_enrichment", commune.codgeo):
        st.rerun()


def render_scores_for_category(
    commune: CommuneResult,
    category_key: str,
    metric_filter: Optional[str] = None,  # "discrete", "continuous", or None
    scores_list: Optional[List[CommuneScoreDetail]] = None,
    acc_url: Optional[str] = None,
    prosp_url: Optional[str] = None,
):
    """Renders normalized indicator scores and discrete badges for a specific category."""
    # category_key: emploi, logement, education, sante, inclusion, mobilite, territoire
    scores: List[CommuneScoreDetail] = (
        scores_list
        if scores_list is not None
        else commune.scores.get(category_key, [])
    )
    if metric_filter == "discrete":
        scores = [s for s in scores if s.metric_type == "discrete"]
    elif metric_filter == "continuous":
        scores = [s for s in scores if s.metric_type != "discrete"]

    if not scores:
        if metric_filter == "continuous":
            st.info("Aucun indicateur continu pour cette catégorie.")
        return

    # Filter out redundant education presence scores if we have the counts tab
    if category_key == "education" and scores_list is None:
        scores = [s for s in scores if not s.label.startswith("Présence")]

    # Always sort discrete metrics first (if mixed), then alphabetical by score_id
    scores = sorted(
        scores,
        key=lambda x: (
            0 if getattr(x, "metric_type", "continuous") == "discrete" else 1,
            x.score_id,
        ),
    )

    with st.container(border=False):
        for s in scores:
            p_val_raw = s.score_normalise
            p_val = (
                float(max(0.0, min(1.0, p_val_raw)))
                if p_val_raw is not None and pd.notna(p_val_raw)
                else 0.0
            )

            if s.metric_type == "discrete":
                c_label, c_val = st.columns([3, 2], vertical_alignment="center")
                status_c = s.status_label or (
                    str(s.valeur_kpi_commune or s.valeur_kpi)
                    if (s.valeur_kpi_commune or s.valeur_kpi) is not None
                    else "Donnée indisponible"
                )
                if p_val < 0.35:
                    badge_color = "gray"
                    badge_icon = ":material/cancel:"
                elif p_val < 0.65:
                    badge_color = "orange"
                    badge_icon = ":material/info:"
                else:
                    badge_color = "green"
                    badge_icon = ":material/check_circle:"

                with c_label:
                    st.markdown(f"**{s.label}**")
                    if s.score_id == "heb_jaccueille_accueillants_score" and acc_url:
                        st.caption(f"[:material/open_in_new: Voir la liste sur Salesforce]({acc_url})")
                    elif s.score_id == "heb_jaccueille_prospects_score" and prosp_url:
                        st.caption(f"[:material/open_in_new: Voir la liste sur Salesforce]({prosp_url})")
                with c_val:
                    st.badge(status_c, icon=badge_icon, color=badge_color)

            else:
                c_label, c_val = st.columns([2.8, 1.2], vertical_alignment="center")
                if p_val < 0.05:
                    p_val_bar = 0.05
                    bar_color = "linear-gradient(90deg, #505050, #000000)"
                elif p_val < 0.35:
                    p_val_bar = p_val
                    bar_color = "linear-gradient(90deg, #f87171, #ef4444)"
                elif p_val < 0.65:
                    p_val_bar = p_val
                    bar_color = "linear-gradient(90deg, #fbbf24, #f59e0b)"
                else:
                    p_val_bar = p_val
                    bar_color = "linear-gradient(90deg, #34d399, #10b981)"

                with c_label:
                    st.markdown(f"**{s.label}**")
                    st.markdown(
                        f"""
                        <div style="width: 100%; background-color: rgba(128, 128, 128, 0.15); border-radius: 4px; height: 8px; margin-top: 2px; overflow: hidden;">
                            <div style="width: {p_val_bar * 100}%; background: {bar_color}; height: 100%; border-radius: 4px;"></div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    val_kpi_c = (
                        s.valeur_kpi_commune
                        if s.valeur_kpi_commune is not None
                        else s.valeur_kpi
                    )
                    c_val_fmt = _format_kpi_value(
                        val_kpi_c,
                        s.unit,
                        s.score_id,
                        s.score_normalise_commune or s.score_normalise,
                    )
                    unit_str = f" {s.unit}" if s.unit and s.unit != "None" else ""
                    c_txt = (
                        f"{c_val_fmt}"
                        if c_val_fmt is not None
                        else "Donnée indisponible"
                    )

                    if getattr(s, "bdv_applied", False):
                        b_val_fmt = _format_kpi_value(
                            s.valeur_kpi_bdv, s.unit, s.score_id, s.score_normalise_bdv
                        )
                        b_txt = (
                            f"{b_val_fmt}"
                            if b_val_fmt is not None
                            else "Donnée indisponible"
                        )
                        st.caption(
                            f"Commune : {c_txt} | Bassin de Vie : {b_txt}{unit_str}"
                        )
                    else:
                        st.caption(f"Commune : {c_txt}{unit_str}")

                with c_val:
                    if p_val_raw is not None and pd.notna(p_val_raw):
                        score_pct_str = f"{p_val * 100:.0f}/100"
                    else:
                        score_pct_str = "N/A"
                    st.markdown(
                        f"<div style='text-align: right; font-weight: 600; font-size: 1.05rem;'>{score_pct_str}</div>",
                        unsafe_allow_html=True,
                    )
        st.markdown("<br>", unsafe_allow_html=True)


@st.dialog(
    title="Détails du Territoire", width="large", on_dismiss=_on_details_dialog_dismiss
)
def show_details_dialog(index: Any):
    """Displays thematic details for a city in a large modal."""
    if (
        "search_results" not in st.session_state
        or not st.session_state.search_results
        or not st.session_state.search_results.get_by_code(index)
    ):
        st.error("Données de la ville introuvables.")
        return

    commune = st.session_state.search_results.get_by_code(index)

    if not commune:
        st.error("Détails non disponibles.")
        return

    telemetry.log_usage_event(
        "view_commune_details", {"codgeo": commune.codgeo, "name": commune.name}
    )

    # --- Header ---
    st.markdown(f"## 📍 {commune.name} (code INSEE: {commune.codgeo})")

    # Active search hash for background enrichment (SOTA Pattern)
    h = st.session_state.get("active_search_hash")

    # Sync background results into model if available
    sync_background_data(commune, h)

    # Salesforce J'Accueille report links (org == jaccueille)
    acc_url, prosp_url = _get_jaccueille_salesforce_urls(commune)

    with st.container(border=False):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric(
                "Population",
                f"{commune.population:,}".replace(",", " "),
                help="Population totale de la commune",
            )
        with col2:
            pop_coeff_display = getattr(commune, "coeff_population_gauss", 1.0)
            pop_coeff_val = (
                float(pop_coeff_display)
                if pop_coeff_display is not None and pd.notna(pop_coeff_display)
                else 1.0
            )
            st.metric(
                "Adéquation démographique",
                f"{pop_coeff_val * 100:.0f}%",
                help="Correspondance avec la taille de ville ciblée, calculée sur la population du Bassin de Vie pour prendre en compte le cadre de vie réel et les services du quotidien.",
            )
        with col3:
            st.metric(
                "Bassin de Vie",
                commune.name_bdv,
                help="Territoire d'influence économique et sociale",
            )
        with col4:
            st.metric(
                "Score",
                f"{commune.global_score * 100:.0f}/100",
                help="Score = Adéquation besoins × Adéquation démographique.",
            )

    def _render_cat_scores(cat_key: str, metric_filter: Optional[str] = None, scores_list: Optional[List[CommuneScoreDetail]] = None):
        render_scores_for_category(
            commune=commune,
            category_key=cat_key,
            metric_filter=metric_filter,
            scores_list=scores_list,
            acc_url=acc_url,
            prosp_url=prosp_url,
        )

    # --- Tabs ---
    tab_emploi, tab_logement, tab_edu, tab_sante, tab_vie, tab_mob, tab_ter = st.tabs(
        [
            "💼 Emploi & Formation",
            "🏠 Logement",
            "🎓 Éducation",
            "🏥 Santé",
            "🤝 Vie Sociale & Inclusion",
            "🚉 Mobilité",
            "🛡️ Contexte Territorial",
        ]
    )

    with tab_emploi:
        employment_data = commune.employment
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            with st.container(border=False):
                st.markdown("#### :material/work: Opportunités")

                live_total = employment_data.standard_jobs_total
                matching_total = employment_data.standard_jobs_matching_total
                availability = employment_data.source_availability

                if availability.get("france_travail") == "unavailable":
                    st.info(
                        "Données France Travail indisponibles pour cette release : elles ne sont pas comptées dans le score."
                    )
                if availability.get("emplois_inclusion") == "unavailable":
                    st.info(
                        "Données Emplois de l'inclusion indisponibles pour cette release : elles ne sont pas comptées dans le score."
                    )

                if live_total > 0:
                    st.info(
                        f"**{matching_total} postes** correspondent à votre recherche sur cette zone."
                    )

                # 1. Hydrated live France Travail job offers first
                if _should_poll_enrichment(h, "jobs_enrichment", commune.codgeo):
                    polling_jobs_fragment(commune, h)
                else:
                    render_jobs_enrichment(commune, h)

                # 2. SIAE matching or local listings second
                matching_siae = employment_data.inclusive_jobs_matching_summary
                if matching_siae:
                    with st.expander(
                        f"Offres par les SIAE correspondant au projet ({employment_data.inclusive_jobs_matching_total})",
                        expanded=True,
                    ):
                        for label, count in matching_siae.items():
                            st.write(
                                f"• **{label}** : {count} offre{'s' if count > 1 else ''}"
                            )
                elif employment_data.inclusive_jobs_total > 0:
                    with st.expander(
                        f"Toutes les offres par les SIAE locales ({employment_data.inclusive_jobs_total})",
                        expanded=False,
                    ):
                        for (
                            label,
                            count,
                        ) in employment_data.inclusive_jobs_summary.items():
                            st.write(
                                f"• **{label}** : {count} offre{'s' if count > 1 else ''}"
                            )

                # 3. Métiers recherchés at the bottom
                with st.expander("Métiers les plus recherchés", expanded=False):
                    top_professions = employment_data.top_professions
                    if top_professions:
                        for m in top_professions:
                            st.write(f"• {m}")
                    else:
                        st.write("Pas de données détaillées.")

                # 4. Formations proposées at the bottom
                with st.expander("Formations proposées", expanded=False):
                    training_programs = employment_data.training_programs
                    if training_programs:
                        pref_forms = []
                        for k in st.session_state:
                            if k.startswith("ui_formations_adult"):
                                val = st.session_state[k]
                                if isinstance(val, list):
                                    pref_forms.extend(val)
                                elif isinstance(val, str) and val:
                                    pref_forms.append(val)
                        unique_prefs = set(str(p).lower() for p in pref_forms)
                        for label in training_programs:
                            is_pref = any(p in label.lower() for p in unique_prefs)
                            icon = "⭐ " if is_pref else ""
                            st.write(f"• {icon}{label}")
                    else:
                        st.info(
                            "Aucune formation spécifique listée pour ce territoire."
                        )

                # Discrete indicators for employment (if configured)
                _render_cat_scores("emploi", metric_filter="discrete")

        with c2:
            c_h_title, c_h_score = st.columns([2.8, 1.2], vertical_alignment="bottom")
            with c_h_title:
                st.markdown("#### :material/monitoring: Indicateurs Emploi")
            with c_h_score:
                with st.container(border=False, width="stretch", horizontal=True, horizontal_alignment="right"):
                    st.text("Score", help="Score relatif aux scores des autres territoires de la recherche")
            _render_cat_scores("emploi", metric_filter="continuous")

    with tab_logement:
        c1, c2 = st.columns([1, 1], gap="medium")

        with c1:
            st.markdown("#### :material/check_circle: Statuts & Partenariats")
            _render_cat_scores("logement", metric_filter="discrete")

        with c2:
            c_h_title, c_h_score = st.columns([2.8, 1.2], vertical_alignment="bottom")
            with c_h_title:
                st.markdown("#### :material/home: Indicateurs Logement")
            with c_h_score:
                with st.container(border=False, width="stretch", horizontal=True, horizontal_alignment="right"):
                    st.text("Score", help="Score relatif aux scores des autres territoires de la recherche")
            _render_cat_scores("logement", metric_filter="continuous")

    with tab_edu:
        education_data = commune.education
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            with st.container(border=False):
                st.markdown("#### :material/school: Établissements")
                facility_details = education_data.facility_details
                if facility_details:
                    for cat, names in sorted(facility_details.items()):
                        items = sorted(list(set([n for n in names if pd.notna(n)])))
                        if items:
                            with st.expander(f"{cat} ({len(items)})", expanded=False):
                                for name in items:
                                    st.write(f"• {name}")
                else:
                    st.info("Aucune information détaillée sur les établissements.")

                _render_cat_scores("education", metric_filter="discrete")
        with c2:
            c_h_title, c_h_score = st.columns([2.8, 1.2], vertical_alignment="bottom")
            with c_h_title:
                st.markdown("#### :material/analytics: Indicateurs Éducation")
            with c_h_score:
                with st.container(border=False, width="stretch", horizontal=True, horizontal_alignment="right"):
                    st.text("Score", help="Score relatif aux scores des autres territoires de la recherche")
            _render_cat_scores("education", metric_filter="continuous")

    with tab_sante:
        health_data = commune.health
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            with st.container(border=False):
                st.markdown("#### :material/medical_services: Structures & Professionnels")
                facility_details = health_data.facility_details
                if facility_details:
                    for cat, names in sorted(facility_details.items()):
                        items = sorted(list(set([n for n in names if pd.notna(n)])))
                        if items:
                            with st.expander(f"{cat} ({len(items)})", expanded=False):
                                for name in items:
                                    st.write(f"• {name}")
                else:
                    st.info("Aucune information détaillée sur les structures de santé.")

                _render_cat_scores("sante", metric_filter="discrete")
        with c2:
            c_h_title, c_h_score = st.columns([2.8, 1.2], vertical_alignment="bottom")
            with c_h_title:
                st.markdown("#### :material/medical_services: Indicateurs Santé")
            with c_h_score:
                with st.container(border=False, width="stretch", horizontal=True, horizontal_alignment="right"):
                    st.text("Score", help="Score relatif aux scores des autres territoires de la recherche")
            _render_cat_scores("sante", metric_filter="continuous")

    with tab_vie:
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            with st.container(border=False):
                st.markdown("#### :material/volunteer_activism: Services d'Inclusion à moins de 10km")
                if _should_poll_enrichment(
                    h, "inclusion_services_status", commune.codgeo
                ):
                    polling_inclusion_services_fragment(commune, h)
                else:
                    render_inclusion_services_enrichment(commune, h)

                st.markdown("#### :material/groups: Associations de l'inclusion")

                if _should_poll_enrichment(
                    h, "association_enrichment_status", commune.codgeo
                ):
                    polling_associations_fragment(commune, h)
                else:
                    render_associations_enrichment(commune, h)

                _render_cat_scores("inclusion", metric_filter="discrete")

        with c2:
            c_h_title, c_h_score = st.columns([2.8, 1.2], vertical_alignment="bottom")
            with c_h_title:
                st.markdown("#### :material/diversity_3: Indicateurs Inclusion")
            with c_h_score:
                with st.container(border=False, width="stretch", horizontal=True, horizontal_alignment="right"):
                    st.text("Score", help="Score relatif aux scores des autres territoires de la recherche")
            _render_cat_scores("inclusion", metric_filter="continuous")

    with tab_mob:
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            st.markdown("#### :material/commute: Réseaux & Connexions")
            _render_cat_scores("mobilite", metric_filter="discrete")
        with c2:
            c_h_title, c_h_score = st.columns([2.8, 1.2], vertical_alignment="bottom")
            with c_h_title:
                st.markdown("#### :material/commute: Indicateurs Mobilité")
            with c_h_score:
                with st.container(border=False, width="stretch", horizontal=True, horizontal_alignment="right"):
                    st.text("Score", help="Score relatif aux scores des autres territoires de la recherche")
            _render_cat_scores("mobilite", metric_filter="continuous")

    with tab_ter:
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            st.markdown("#### :material/security: Contexte Territorial")
            if commune.territoire.ter_insecurite:
                st.info(
                    f"🚨 **Sécurité** : {commune.territoire.ter_insecurite:.1f} crimes+délits pour 1000 hab. (Moyenne départementale)."
                )

            if commune.territoire.maire_extreme_droite:
                st.warning(
                    "⚠️ **Municipalité** : Le maire actuel est classé à l'extrême droite."
                )

            _render_cat_scores("territoire", metric_filter="discrete")

            if commune.territoire.electoral_history:
                try:
                    import json

                    history = json.loads(commune.territoire.electoral_history)
                    if isinstance(history, dict):
                        muni_list = history.get("municipales", [])
                        pres_list = history.get("presidentielles", [])

                        if muni_list:
                            with st.expander("🗳️ Élections Municipales"):
                                rows_muni = [
                                    {
                                        "Scrutin": item.get("election", ""),
                                        "Tour": item.get("tour", ""),
                                        "Nuance Majoritaire": item.get("nuance", ""),
                                        "Score": f"{item.get('percentage', 0):.1f}%",
                                    }
                                    for item in muni_list
                                ]
                                st.dataframe(
                                    pd.DataFrame(rows_muni),
                                    hide_index=True,
                                    width="stretch",
                                )

                        if pres_list:
                            with st.expander("🗳️ Élections Présidentielles"):
                                rows_pres = [
                                    {
                                        "Scrutin": item.get("election", ""),
                                        "Tour": item.get("tour", ""),
                                        "Nuance Majoritaire": item.get("nuance", ""),
                                        "Score": f"{item.get('percentage', 0):.1f}%",
                                    }
                                    for item in pres_list
                                ]
                                st.dataframe(
                                    pd.DataFrame(rows_pres),
                                    hide_index=True,
                                    width="stretch",
                                )
                    elif isinstance(history, list) and history:
                        with st.expander("🗳️ Historique Électoral"):
                            table_rows = [
                                {
                                    "Scrutin": item.get("election", ""),
                                    "Tour": item.get("tour", "-"),
                                    "Nuance Majoritaire": item.get("nuance", ""),
                                    "Score": f"{item.get('percentage', 0):.1f}%",
                                }
                                for item in history
                            ]
                            st.dataframe(
                                pd.DataFrame(table_rows), hide_index=True, width="stretch"
                            )
                except Exception as e:
                    st.caption("Erreur lors du chargement de l'historique électoral.")

            
        with c2:
            c_h_title, c_h_score = st.columns([2.8, 1.2], vertical_alignment="bottom")
            with c_h_title:
                st.markdown("#### :material/security: Indicateurs Territoriaux")
            with c_h_score:
                with st.container(border=False, width="stretch", horizontal=True, horizontal_alignment="right"):
                    st.text("Score", help="Score relatif aux scores des autres territoires de la recherche")
            _render_cat_scores("territoire", metric_filter="continuous")
