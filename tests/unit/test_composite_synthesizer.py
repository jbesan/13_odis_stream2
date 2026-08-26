import pytest
from unittest.mock import MagicMock, patch
from pydantic_ai import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.models import CommuneResult, CommuneScoreDetail, SearchResultsData, SearchCriterias, CriteriaItem
from agents.state import GraphState, ODISDeps
from agents.synthesizer import (
    synthesizer_agent,
    build_composite_synthesis_context,
)
from agents.graph import create_odis_graph


def test_build_composite_synthesis_context():
    """Verify that composite context incorporates pre-digested snippets without raw JSON."""
    marseille = CommuneResult(
        codgeo="13055",
        name="Marseille",
        global_score=0.85,
        expert_analysis={
            "city_comparator": "### ⚖️ Comparatif : Atout loyer +20%",
            "ccas_locator": "### 🏛️ CCAS : 4 rue de la République",
            "housing_expert": "Loyer moyen abordable et disponibilité F3.",
            "mobility_expert": "Réseau de métro et tram dense.",
        },
    )

    state = GraphState(
        odis_brief="Famille avec 2 enfants recherchant un logement et un accès rapide aux transports.",
        focus_city=marseille,
        search_results=SearchResultsData(search_hash="h1", results=[marseille], current_geo=marseille),
    )

    ctx_str = build_composite_synthesis_context(state)

    # 1. Contains pre-digested components
    assert "Profil & Besoins du Bénéficiaire" in ctx_str
    assert "Famille avec 2 enfants" in ctx_str
    assert "Comparatif : Atout loyer +20%" in ctx_str
    assert "CCAS : 4 rue de la République" in ctx_str
    assert "Expert Housing" in ctx_str
    assert "Loyer moyen abordable" in ctx_str
    assert "Expert Mobility" in ctx_str
    assert "Réseau de métro" in ctx_str

    # 2. Does NOT contain raw JSON metadata tags or schema dumps
    assert "json_schema_extra" not in ctx_str
    assert '"score_id":' not in ctx_str


@pytest.mark.asyncio
async def test_synthesizer_agent_offline_run():
    """Verify offline execution of composite synthesizer with FunctionModel."""
    marseille = CommuneResult(
        codgeo="13055",
        name="Marseille",
        global_score=0.85,
        expert_analysis={
            "city_comparator": "### ⚖️ Atout loyer",
            "ccas_locator": "### 🏛️ CCAS Marseille",
            "housing_expert": "Logement F3 disponible.",
        },
    )

    state = GraphState(
        odis_brief="Dossier relocalisation",
        focus_city=marseille,
        search_results=SearchResultsData(search_hash="h1", results=[marseille], current_geo=marseille),
    )
    deps = ODISDeps(state=state, client=MagicMock())

    expected_synthesis = (
        "## 🧭 Avis Global d'Orientation pour Marseille\n"
        "Marseille présente une excellente adéquation pour la famille avec une bonne offre de transports.\n\n"
        "## ⚖️ Analyse Comparative Territoriale\n"
        "| Critère | Marseille | Réf | Écart |\n"
        "| :--- | :---: | :---: | :---: |\n"
        "| Loyer m² | 12€ | 16€ | +25 pts |\n\n"
        "Le loyer moyen est 25% plus bas que la référence, offrant un vrai gain de pouvoir d'achat.\n\n"
        "## ❓ Et ensuite ? (Pistes d'action)\n"
        "- Contacter le CCAS pour le dossier logement.\n"
        "- Inscrire les enfants à l'école de quartier."
    )

    def call_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=expected_synthesis)])

    with synthesizer_agent.override(model=FunctionModel(call_model)):
        result = await synthesizer_agent.run("Synthèse demandée pour Marseille.", deps=deps)
        assert "Avis Global d'Orientation pour Marseille" in result.output
        assert "Analyse Comparative Territoriale" in result.output
        assert "Et ensuite ?" in result.output


@pytest.mark.asyncio
async def test_graph_end_to_end_with_local_nodes_offline():
    """Verify that create_odis_graph executes local nodes (comparator & CCAS) offline."""
    graph = create_odis_graph()

    focus = CommuneResult(
        codgeo="17347",
        name="Saint-Jean-d'Angély",
        global_score=0.80,
        scores={
            "logement": [
                CommuneScoreDetail(
                    score_id="log_01",
                    label="Loyer m²",
                    score_normalise=0.80,
                    valeur_kpi=9.0,
                    unit="€",
                    relative_weight=20.0,
                )
            ]
        },
    )
    ref = CommuneResult(
        codgeo="33063",
        name="Bordeaux",
        global_score=0.70,
        scores={
            "logement": [
                CommuneScoreDetail(
                    score_id="log_01",
                    label="Loyer m²",
                    score_normalise=0.50,
                    valeur_kpi=16.0,
                    unit="€",
                    relative_weight=20.0,
                )
            ]
        },
    )

    state = GraphState(
        odis_brief="Dossier test",
        search_criteria=SearchCriterias(commune_actuelle=CriteriaItem(code="33063", label="Bordeaux")),
        search_results=SearchResultsData(search_hash="h1", results=[focus], current_geo=ref),
        focus_city=focus,
        messages=[{"role": "user", "content": "Fais une analyse complète pour Saint-Jean-d'Angély."}],
    )
    deps = ODISDeps(state=state, client=MagicMock())

    # Mock ts_agent planning to full_analysis with housing_expert
    def mock_ts_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        from pydantic_ai import ToolCallPart
        tool_name = info.output_tools[0].name if info.output_tools else "final_result"
        args = {
            "swarm_mode": "full_analysis",
            "tasks": [{"expert": "housing_expert", "task_description": "Logement", "skill_cards": []}],
        }
        return ModelResponse(parts=[ToolCallPart(tool_name, args)])

    def mock_expert_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        from pydantic_ai import ToolCallPart
        tool_name = info.output_tools[0].name if info.output_tools else "final_result"
        args = {"result": "Disponibilité confirmée."}
        return ModelResponse(parts=[ToolCallPart(tool_name, args)])

    def mock_synth_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        from pydantic_ai import ToolCallPart
        tool_name = info.output_tools[0].name if info.output_tools else "final_result"
        args = {
            "avis_global": "Très favorable pour la famille.",
            "analyse_comparative": "| Critère | Cible | Réf | Écart |\n| :--- | :---: | :---: | :---: |\n| Loyer m² | 9€ | 16€ | +30 pts |\n\nLoyer beaucoup plus abordable, facilitant l'accès à un logement décent.",
            "elements_non_verifies": "Places en crèche à confirmer avec la mairie.",
            "et_ensuite": "- Contacter le CCAS\n- Déposer le dossier logement",
        }
        return ModelResponse(parts=[ToolCallPart(tool_name, args)])

    from agents.ts_agent import ts_agent
    from agents.housing_expert import housing_expert_agent

    with (
        patch("agents.ccas_worker.search_ccas", return_value=[{"nom": "CCAS Local", "codgeo": "17347"}]),
        patch("agents.graph.bq_logger.log_agent_state_to_bq", return_value=None),
        ts_agent.override(model=FunctionModel(mock_ts_model)),
        housing_expert_agent.override(model=FunctionModel(mock_expert_model)),
        synthesizer_agent.override(model=FunctionModel(mock_synth_model)),
    ):
        final_res = await graph.run(state=state, deps=deps)

        city = state.search_results.get_by_code("17347")
        assert city is not None
        # Verify that both local deterministic nodes were executed and saved in expert_analysis
        assert "city_comparator" in city.expert_analysis
        assert "ccas_locator" in city.expert_analysis
        assert "housing_expert" in city.expert_analysis
        assert "Comparatif territorial" in city.expert_analysis["city_comparator"]
        assert "Contact du CCAS" in city.expert_analysis["ccas_locator"]

        # Verify that Recherches effectuées was removed from expert artifact
        assert "Recherches effectuées" not in city.expert_analysis["housing_expert"]
        assert "Disponibilité confirmée." in city.expert_analysis["housing_expert"]

        # Verify that the final full report assembled all sections in the correct order
        output = final_res.data
        assert "## 🧭 Avis Global d'Orientation pour Saint-Jean-d'Angély" in output
        assert "## ⚖️ Analyse Comparative Territoriale" in output
        assert "Loyer beaucoup plus abordable" in output
        assert "# 🔬 Analyses Thématiques Détaillées" in output
        assert "### 🏠 Logement & Hébergement" in output
        assert "## ⚠️ Éléments Non Vérifiés & Vigilances" in output
        assert "Places en crèche à confirmer" in output
        assert "### 🏛️ Contact du CCAS" in output
        assert "## ❓ Et ensuite ? (Pistes d'action)" in output

        # Verify section order: Executive overview < Comparative analysis < Expert fiches < Unverified < CCAS < Next steps
        idx_exec = output.index("## 🧭 Avis Global d'Orientation")
        idx_comp = output.index("## ⚖️ Analyse Comparative Territoriale")
        idx_experts = output.index("# 🔬 Analyses Thématiques Détaillées")
        idx_unverified = output.index("## ⚠️ Éléments Non Vérifiés & Vigilances")
        idx_ccas = output.index("### 🏛️ Contact du CCAS")
        idx_next_steps = output.index("## ❓ Et ensuite ? (Pistes d'action)")

        assert idx_exec < idx_comp < idx_experts < idx_unverified < idx_ccas < idx_next_steps
