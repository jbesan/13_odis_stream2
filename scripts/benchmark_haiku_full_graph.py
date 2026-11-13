"""Run the complete ODIS graph with Claude Haiku 5.5 for all nodes EXCEPT web search.

Usage:
  uv run python scripts/benchmark_haiku_full_graph.py --cities 42218
  uv run python scripts/benchmark_haiku_full_graph.py --cities all
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv("app/.env")

sys.path.insert(0, "app")

# Initialize Logfire & logging before importing agents
import utils.logger  # noqa: E402, F401
import logfire  # noqa: E402

# Monkey-patch PydanticAI 2.37 BetaMessageParam Union bug in-memory ONLY for this script
import pydantic_ai.models.anthropic as pai_anth  # noqa: E402

pai_anth.BetaMessageParam = dict

from anthropic import AsyncAnthropic  # noqa: E402
import pandas as pd  # noqa: E402
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings  # noqa: E402
from pydantic_ai.profiles.anthropic import anthropic_model_profile  # noqa: E402
from pydantic_ai.providers.anthropic import AnthropicProvider  # noqa: E402

import config as cfg  # noqa: E402
from agents import agent_config  # noqa: E402
from agents import graph as odis_graph  # noqa: E402
from agents.utils import run_logic  # noqa: E402
from core.models import SearchCriterias, SearchResultsData  # noqa: E402
from core.postscoring import launch_hydrations, sync_search_results_data  # noqa: E402
from core.scoring import ScoringEngine  # noqa: E402
from utils.data_loader import load_app_data_raw  # noqa: E402

TARGET_CITIES = [
    ("42218", "Saint-Étienne", "01_saint_etienne.md"),
    ("38416", "Saint-Marcellin", "02_saint_marcellin.md"),
    ("63430", "Thiers", "03_thiers.md"),
    ("26281", "Romans-sur-Isère", "04_romans_sur_isere.md"),
    ("69264", "Villefranche-sur-Saône", "05_villefranche_sur_saone.md"),
]


def install_haiku_patches() -> None:
    """Route all graph LLM nodes to claude-haiku-5-5 with thinking disabled.

    search_web_batch_tool continues using ctx.deps.client (Gemini 3.1 on Vertex EU).
    """
    api_key = os.getenv("ODIS_ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ODIS_ANTHROPIC_API_KEY in app/.env")

    haiku_profile = {
        **anthropic_model_profile("claude-haiku-5-5"),
        "anthropic_disallows_sampling_settings": True,
    }

    def haiku_get_model(agent_name: str) -> str:
        return "anthropic:claude-haiku-5-5"

    def haiku_get_p_model(agent_name: str, client: Any = None) -> AnthropicModel:
        # Create a fresh AsyncAnthropic bound to the current running event loop
        anth_client = AsyncAnthropic(api_key=api_key)
        provider = AnthropicProvider(anthropic_client=anth_client)
        return AnthropicModel(
            "claude-haiku-5-5",
            provider=provider,
            profile=haiku_profile,
        )

    def haiku_get_model_settings(agent_name: str) -> AnthropicModelSettings:
        return AnthropicModelSettings(
            anthropic_thinking={"type": "disabled"},
            thinking=False,
            max_tokens=4096,
            timeout=30.0,
        )

    agent_config.get_model = haiku_get_model
    agent_config.get_p_model = haiku_get_p_model
    agent_config.get_model_settings = haiku_get_model_settings

    odis_graph.get_model = haiku_get_model
    odis_graph.get_p_model = haiku_get_p_model
    odis_graph.get_model_settings = haiku_get_model_settings


def build_benchmark_criteria() -> SearchCriterias:
    preset = cfg.DEMO_SCENARIOS["lancement"]
    return SearchCriterias(
        commune_actuelle={"code": "69123", "label": "Lyon"},
        loc_search_area="region",
        loc_search_code=["84"],
        nb_adultes=1,
        nb_enfants=2,
        classe_enfants=["Maternelle", "Elémentaire"],
        codes_metiers=[
            [{"code": "I1604", "label": "Mécanicien / Mécanicienne automobile"}]
        ],
        codes_formations=[[]],
        recherche_siae=True,
        inc_services_selection=[
            {
                "code": "difficultes-administratives-ou-juridiques--accompagnement-aux-demarches-administratives",
                "label": "Accompagnement aux démarches administratives",
            },
            {
                "code": "lecture-ecriture-calcul--maitriser-le-francais",
                "label": "Maîtriser le français",
            },
            {
                "code": "logement-hebergement--sinformer-sur-les-demarches-liees-a-lacces-au-logement",
                "label": "S’informer sur les démarches liées à l’accès au logement",
            },
            {
                "code": "preparer-sa-candidature--organiser-ses-demarches-de-recherche-demploi",
                "label": "Organiser ses démarches de recherche d’emploi",
            },
        ],
        inc_asso_add_selection=[
            {"code": "011075", "label": "football  (football, futsal)"}
        ],
        hebergement_cible=["Location avec Intermédiation", "Chez l'habitant"],
        logement="Logement Social",
        type_logement={"code": "appt_all", "label": "Appartement (Tous types)"},
        besoin_sante=["Soutien Psychologique", "Addictologie"],
        freq_retour="1 fois/an",
        notes_qualitatives=[
            "Souhaite passer son permis, cherche la proximité d'une église évangélique."
        ],
        weight_profile="Profil personnalisé",
        poids_emploi=1.0,
        poids_logement=1.0,
        poids_education=0.5,
        poids_inclusion=0.5,
        poids_sante=0.25,
        poids_mobilite=0.25,
        poids_territoire=1.0,
        org_context="jaccueille",
        org_strategic_locations=[
            "01",
            "13",
            "22",
            "26",
            "30",
            "31",
            "33",
            "34",
            "35",
            "37",
            "38",
            "40",
            "42",
            "44",
            "64",
            "69",
            "72",
            "75",
            "76",
            "77",
            "78",
            "81",
            "91",
            "92",
            "93",
            "94",
            "95",
        ],
        org_strategic_locations_type="departement",
        org_strategic_locations_filter=True,
        target_city_size=preset["target_city_size"],
        target_population_a=preset["target_population_a"],
        target_population_b=preset["target_population_b"],
        target_population_c=preset["target_population_c"],
        target_population_d=preset["target_population_d"],
    )


def prepare_hydrated_results(
    criteria: SearchCriterias, target_codes: list[str]
) -> tuple[SearchResultsData, str]:
    app_data = load_app_data_raw()
    engine = ScoringEngine(
        df_all_communes=app_data["odis"],
        df_bv_geo=app_data["bv_geo"],
        scores_cat=app_data["scores_cat"],
        incl_index=app_data["incl_index"],
        associations_data=app_data["associations_data"],
        formations_data=app_data["formations_data"],
        codformations_index=app_data["codformations_index"],
        waldec_index=app_data["waldec_index"],
        global_stats={},
        refugee_associations_data=app_data["refugee_associations_data"],
        live_jobs_data=app_data["live_jobs_data"],
        siae_jobs_data=app_data["siae_jobs_data"],
        annuaire_ecoles=app_data.get("annuaire_ecoles", pd.DataFrame()),
        annuaire_sante=app_data.get("annuaire_sante", pd.DataFrame()),
        annuaire_inclusion=app_data.get("annuaire_inclusion", pd.DataFrame()),
        inclusion_services_index=app_data.get(
            "inclusion_services_index", pd.DataFrame()
        ),
        rome_index=app_data.get("rome_index", pd.DataFrame()),
        bv_data=app_data.get("bv_data"),
        pois=app_data.get("pois", pd.DataFrame()),
    )
    results_raw = engine.run(criteria, log_prefix="haiku_bench")
    search_results = engine.create_search_results(results_raw, criteria)

    # Keep only the requested benchmark target cities in search_results.results
    # (preserving current_geo so comparator works identically); build any city outside Top 5 from results_raw
    by_code = {c.codgeo: c for c in search_results.results}
    for code in target_codes:
        if code not in by_code and code in results_raw.index:
            by_code[code] = engine.format_city_details(results_raw.loc[code], criteria)
    filtered = [by_code[code] for code in target_codes if code in by_code]
    # Reorder according to target_codes
    order_map = {code: idx for idx, code in enumerate(target_codes)}
    filtered.sort(key=lambda c: order_map.get(c.codgeo, 999))
    search_results.results = filtered

    h = f"haiku_bench_{uuid.uuid4().hex[:8]}"
    print(f"Hydrating {len(filtered)} cities: {[c.name for c in filtered]}...")
    hydration_run = launch_hydrations(
        engine, criteria, search_results, h, is_ai_free=False
    )
    hydration_run.start()
    while not all(hydration_run.ready(c.codgeo) for c in filtered):
        time.sleep(0.25)

    from agents.utils import get_odis_bg_store

    bg = get_odis_bg_store().get(h, {})
    sync_search_results_data(search_results, bg, criteria)
    print("Hydration complete.")
    return search_results, h


async def run_city_graph(
    codgeo: str,
    city_name: str,
    criteria: SearchCriterias,
    search_results: SearchResultsData,
    h: str,
    interaction_id: str,
) -> dict[str, Any]:
    commune = next(c for c in search_results.results if c.codgeo == codgeo)
    input_data = {
        "search_criteria": criteria.model_dump(mode="json"),
        "search_results": search_results.model_dump(mode="json"),
        "focus_city": commune.model_dump(mode="json"),
        "criteria_hash": h,
        "execution_mode": "full_analysis",
        "messages": [
            {"role": "user", "content": f"Fais une analyse complète pour {city_name}."}
        ],
        "interaction_id": interaction_id,
        "run_id": uuid.uuid4().hex[:8],
        "run_attempt": 1,
        "trigger": "benchmark_haiku_full_graph",
    }

    t0 = time.perf_counter()
    res = await run_logic(input_data)
    dur = time.perf_counter() - t0

    updated_sr: SearchResultsData = res["search_results"]
    updated_city = next(c for c in updated_sr.results if c.codgeo == codgeo)
    analysis_md = ""
    if updated_city.analysis_report is not None:
        analysis_md = updated_city.analysis_report.to_flat_markdown()
    elif updated_city.odis_synthesis:
        analysis_md = "\n\n".join(str(x) for x in updated_city.odis_synthesis)
    print(
        f"[{city_name}] Finished in {dur:.2f}s | Analysis len: {len(analysis_md)} chars"
    )
    return {
        "codgeo": codgeo,
        "city_name": city_name,
        "duration": dur,
        "analysis": analysis_md,
        "messages": res.get("messages", []),
    }


async def main_async(target_codes: list[str]) -> None:
    install_haiku_patches()
    criteria = build_benchmark_criteria()
    search_results, h = prepare_hydrated_results(criteria, target_codes)

    interaction_id = f"hk{uuid.uuid4().hex[:6]}"
    print(f"\n🚀 Running Haiku 5.5 Full Graph Benchmark | interaction_id={interaction_id}")
    print(f"   All nodes: claude-haiku-5-5 (thinking=disabled)")
    print(f"   search_web_batch_tool: gemini-3.1-flash-lite (Vertex EU)")

    tasks = []
    for code, name, _ in TARGET_CITIES:
        if code in target_codes:
            tasks.append(
                run_city_graph(code, name, criteria, search_results, h, interaction_id)
            )

    results = await asyncio.gather(*tasks)

    out_dir = Path("docs/audit/syntheses_haiku_5.5_full_2026-11-12")
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        file_name = next(fn for c, _, fn in TARGET_CITIES if c == r["codgeo"])
        content = f"""# Synthèse Graphe Complet : {r['city_name']} ({r['codgeo']}) - Claude Haiku 5.5

- **Interaction ID Logfire** : `{interaction_id}`
- **Modèle Graphe (Triage + 6 Experts + Synthesizer)** : `claude-haiku-5-5` (`thinking=disabled`)
- **Modèle Web Search (`search_web_batch_tool`)** : `gemini-3.1-flash-lite` (Vertex EU)
- **Durée Totale Graphe** : {r['duration']:.2f} s

---

{r['analysis'] or '*Aucune analyse générée*'}
"""
        (out_dir / file_name).write_text(content, encoding="utf-8")

    # Flush Logfire spans cleanly before process exit
    logfire.force_flush()
    print(f"\n✅ Done! interaction_id={interaction_id} flushed to Logfire.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cities",
        default="42218",
        help="Comma-separated INSEE codes or 'all' for the 5 cities",
    )
    args = parser.parse_args()
    if args.cities == "all":
        codes = [c for c, _, _ in TARGET_CITIES]
    else:
        codes = [c.strip() for c in args.cities.split(",") if c.strip()]

    asyncio.run(main_async(codes))


if __name__ == "__main__":
    main()
