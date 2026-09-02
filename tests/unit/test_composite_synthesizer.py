import os
from unittest.mock import MagicMock, patch
import pytest
from pydantic_ai import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.models import (
    CommuneResult,
    CommuneScoreDetail,
    SearchResultsData,
    SearchCriterias,
    CriteriaItem,
    DomainReport,
    CityAnalysisReport,
)
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
        assert "🏛️ Contact du CCAS" in output
        assert "## ❓ Et ensuite ? (Pistes d'action)" in output

        # Verify section order: Executive overview < Expert fiches < Comparative analysis < Unverified < CCAS < Next steps
        idx_exec = output.index("## 🧭 Avis Global d'Orientation")
        idx_experts = output.index("# 🔬 Analyses Thématiques Détaillées")
        idx_comp = output.index("## ⚖️ Analyse Comparative Territoriale")
        idx_unverified = output.index("## ⚠️ Éléments Non Vérifiés & Vigilances")
        idx_ccas = output.index("🏛️ Contact du CCAS")
        idx_next_steps = output.index("## ❓ Et ensuite ? (Pistes d'action)")

        assert idx_exec < idx_experts < idx_comp < idx_unverified < idx_ccas < idx_next_steps

        # Verify that structured CityAnalysisReport is populated on the commune
        assert city.analysis_report is not None
        assert city.analysis_report.city_name == "Saint-Jean-d'Angély"
        assert city.analysis_report.city_code == "17347"
        assert "housing_expert" in city.analysis_report.domains
        assert city.analysis_report.domains["housing_expert"].content == "Disponibilité confirmée."
        assert city.analysis_report.analyse_comparative is not None
        assert city.analysis_report.to_flat_markdown() == output


def test_domain_report_instantiation():
    """Verify DomainReport fields and defaults."""
    domain = DomainReport(
        domain_key="housing_expert",
        label="🏠 Logement & Hébergement",
        short_label="🏠 Logement",
        content="Offre de logements sociaux suffisante.",
        sources=[{"source_tag": "INSEE", "source_url": "https://insee.fr"}],
        artifacts={"evidence": [], "gaps": []},
    )
    assert domain.domain_key == "housing_expert"
    assert domain.short_label == "🏠 Logement"
    assert len(domain.sources) == 1
    assert domain.artifacts is not None


def test_city_analysis_report_to_flat_markdown():
    """Verify that CityAnalysisReport assembles markdown sequentially in the exact intended order."""
    report = CityAnalysisReport(
        city_name="Bergerac",
        city_code="24037",
        avis_global="Bergerac présente une excellente adéquation.",
        domains={
            "housing_expert": DomainReport(
                domain_key="housing_expert",
                label="🏠 Logement & Hébergement",
                short_label="🏠 Logement",
                content="Loyers modérés et disponibilité de T3.",
            ),
            "mobility_expert": DomainReport(
                domain_key="mobility_expert",
                label="🚆 Mobilité & Transports",
                short_label="🚆 Mobilité",
                content="Ligne TER directe vers Bordeaux.",
            ),
        },
        analyse_comparative="| Critère | Bergerac | Réf |\n| Loyer | 8€ | 15€ |",
        elements_non_verifies="Places en crèche à vérifier auprès du RPE.",
        ccas_contact="# 🏛️ Contact du CCAS de Bergerac\n\n12 rue Neuve.",
        et_ensuite="- Contacter le CCAS\n- Prendre contact avec Action Logement",
    )

    flat_md = report.to_flat_markdown()

    # Verify all sections are present
    assert "## 🧭 Avis Global d'Orientation pour Bergerac" in flat_md
    assert "# 🔬 Analyses Thématiques Détaillées" in flat_md
    assert "### 🏠 Logement & Hébergement" in flat_md
    assert "### 🚆 Mobilité & Transports" in flat_md
    assert "## ⚖️ Analyse Comparative Territoriale" in flat_md
    assert "## ⚠️ Éléments Non Vérifiés & Vigilances" in flat_md
    assert "# 🏛️ Contact du CCAS de Bergerac" in flat_md
    assert "## ❓ Et ensuite ? (Pistes d'action)" in flat_md

    # Verify strict sequential order
    idx_avis = flat_md.index("## 🧭 Avis Global d'Orientation")
    idx_experts = flat_md.index("# 🔬 Analyses Thématiques Détaillées")
    idx_comp = flat_md.index("## ⚖️ Analyse Comparative Territoriale")
    idx_vigilances = flat_md.index("## ⚠️ Éléments Non Vérifiés")
    idx_ccas = flat_md.index("🏛️ Contact du CCAS")
    idx_actions = flat_md.index("## ❓ Et ensuite ?")

    assert idx_avis < idx_experts < idx_comp < idx_vigilances < idx_ccas < idx_actions


def test_commune_result_with_analysis_report():
    """Verify attaching CityAnalysisReport to CommuneResult and Pydantic serialization roundtrip."""
    report = CityAnalysisReport(
        city_name="Périgueux",
        city_code="24322",
        avis_global="Périgueux est une ville dynamique.",
    )
    commune = CommuneResult(
        codgeo="24322",
        name="Périgueux",
        global_score=0.82,
        analysis_report=report,
    )

    assert commune.analysis_report is not None
    assert commune.analysis_report.city_name == "Périgueux"

    # Serialization roundtrip
    dumped = commune.model_dump()
    reloaded = CommuneResult.model_validate(dumped)
    assert reloaded.analysis_report is not None
    assert reloaded.analysis_report.city_code == "24322"
    assert reloaded.analysis_report.avis_global == "Périgueux est une ville dynamique."


def test_render_initial_analysis_report_with_structured_report():
    """Verify that _render_initial_analysis_report creates st.tabs when analysis_report is present."""
    from ui.ai_analysis_dialog import _render_initial_analysis_report

    report = CityAnalysisReport(
        city_name="Bordeaux",
        city_code="33063",
        avis_global="Avis test",
        domains={
            "housing_expert": DomainReport(
                domain_key="housing_expert",
                label="🏠 Logement & Hébergement",
                short_label="🏠 Logement",
                content="Contenu logement",
                sources=[{"label": "INSEE", "source_url": "https://insee.fr"}],
            )
        },
    )
    commune = CommuneResult(codgeo="33063", name="Bordeaux", analysis_report=report)

    with (
        patch("ui.ai_analysis_dialog.st.markdown") as mock_md,
        patch("ui.ai_analysis_dialog.st.header") as mock_hdr,
        patch("ui.ai_analysis_dialog.st.tabs", return_value=[MagicMock()]) as mock_tabs,
        patch("ui.ai_analysis_dialog.st.divider"),
        patch("ui.ai_analysis_dialog.st.columns", return_value=(MagicMock(), MagicMock())),
        patch("ui.ai_analysis_dialog._render_sources_popover") as mock_sources,
    ):
        _render_initial_analysis_report(commune, "")

        mock_tabs.assert_called_once_with(["🏠 Logement"])
        mock_sources.assert_called_once_with(
            [{"label": "INSEE", "source_url": "https://insee.fr"}],
            "housing_expert",
        )


def test_render_initial_analysis_report_fallback_markdown():
    """Verify that _render_initial_analysis_report falls back to st.markdown when analysis_report is absent."""
    from ui.ai_analysis_dialog import _render_initial_analysis_report

    commune = CommuneResult(codgeo="33063", name="Bordeaux")

    with patch("ui.ai_analysis_dialog.st.markdown") as mock_md:
        _render_initial_analysis_report(commune, "Contenu brut de secours")
        mock_md.assert_called_once_with("Contenu brut de secours")


def test_interactive_chat_enabled_logic():
    """Verify that is_interactive_chat_enabled returns True for chat-enabled orgs and False by default."""
    import config as cfg
    from core.models import Org

    chat_org = Org(id="chat_org", name="Chat Org", enable_interactive_chat=True)
    no_chat_org = Org(id="no_chat_org", name="No Chat Org", enable_interactive_chat=False)

    with patch.dict(os.environ, {"ODIS_AI_FREE_MODE": "False"}, clear=False):
        assert cfg.is_interactive_chat_enabled(chat_org) is True
        assert cfg.is_interactive_chat_enabled(no_chat_org) is False
        assert cfg.is_interactive_chat_enabled(None) is False


def test_ia_analysis_content_chat_disabled_for_default_org():
    """Verify that ia_analysis_content does NOT render chat input if chat is disabled for the org."""
    from ui.ai_analysis_dialog import ia_analysis_content
    from core.models import SearchResultsData, Org

    commune = CommuneResult(
        codgeo="33063",
        name="Bordeaux",
        odis_synthesis=[{"role": "assistant", "content": "Rapport complet"}],
        analysis_report=CityAnalysisReport(
            city_name="Bordeaux",
            city_code="33063",
            avis_global="Avis test",
        ),
    )
    search_results = SearchResultsData(
        results=[commune], current_geo=commune, search_hash="test_h"
    )

    with (
        patch.dict(os.environ, {"ODIS_AI_FREE_MODE": "False"}, clear=False),
        patch("ui.ai_analysis_dialog.st.session_state", {
            "search_results": search_results,
            "active_search_hash": "test_h",
            "org": Org(id="no_chat_org", name="No Chat Org", enable_interactive_chat=False),
        }),
        patch("ui.ai_analysis_dialog._render_initial_analysis_report") as mock_render_report,
        patch("ui.ai_analysis_dialog.st.chat_input") as mock_chat_input,
    ):
        ia_analysis_content("Bordeaux", "33063", None)
        mock_render_report.assert_called_once()
        # chat_input must NOT be called when disabled
        mock_chat_input.assert_not_called()


def test_ia_analysis_content_chat_enabled_for_org():
    """Verify that ia_analysis_content renders chat input if chat is enabled for the Org."""
    from ui.ai_analysis_dialog import ia_analysis_content
    from core.models import SearchResultsData, Org

    commune = CommuneResult(
        codgeo="33063",
        name="Bordeaux",
        odis_synthesis=[
            {"role": "assistant", "content": "Rapport complet"},
            {"role": "user", "content": "Une question ?"},
            {"role": "assistant", "content": "Une réponse."},
        ],
        analysis_report=CityAnalysisReport(
            city_name="Bordeaux",
            city_code="33063",
            avis_global="Avis test",
        ),
    )
    search_results = SearchResultsData(
        results=[commune], current_geo=commune, search_hash="test_h"
    )

    with (
        patch.dict(os.environ, {"ODIS_AI_FREE_MODE": "False"}, clear=False),
        patch("ui.ai_analysis_dialog.st.session_state", {
            "search_results": search_results,
            "active_search_hash": "test_h",
            "org": Org(id="chat_org", name="Chat Org", enable_interactive_chat=True),
        }),
        patch("ui.ai_analysis_dialog._render_initial_analysis_report") as mock_render_report,
        patch("ui.ai_analysis_dialog.st.divider"),
        patch("ui.ai_analysis_dialog.st.subheader"),
        patch("ui.ai_analysis_dialog.st.chat_message") as mock_chat_msg,
        patch("ui.ai_analysis_dialog.st.markdown"),
        patch("ui.ai_analysis_dialog.st.chat_input") as mock_chat_input,
    ):
        ia_analysis_content("Bordeaux", "33063", None)
        mock_render_report.assert_called_once()
        mock_chat_input.assert_called_once()
        assert mock_chat_msg.call_count == 2


def test_render_sources_popover_list_and_sublist_format():
    """Verify that _render_sources_popover formats items as list with sublist captions."""
    from ui.ai_analysis_dialog import _render_sources_popover

    source_data = [
        {
            "label": "INSEE Logement",
            "source_url": "https://insee.fr/logement",
            "status": "donnée socle",
            "grounding_domain": "insee.fr",
            "grounding_queries": ["logement bordeaux"],
            "note": "Données 2024",
        }
    ]

    with (
        patch("ui.ai_analysis_dialog.st.popover") as mock_popover,
        patch("ui.ai_analysis_dialog.st.markdown") as mock_markdown,
    ):
        mock_popover.return_value.__enter__.return_value = MagicMock()
        _render_sources_popover(source_data, "housing_expert")

        # Verify markdown calls
        assert mock_markdown.call_count >= 2
        content_call = mock_markdown.call_args_list[1][0][0]
        assert "- [INSEE Logement](https://insee.fr/logement) — *donnée socle*" in content_call
        assert "  - <small style='color: gray;'>Domaine : insee.fr</small>" in content_call
        assert "  - <small style='color: gray;'>Mots clés : logement bordeaux</small>" in content_call
        assert "  - <small style='color: gray;'>Données 2024</small>" in content_call

