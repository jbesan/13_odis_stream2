import os
import sys
import json

# Ensure 'app' directory is in python path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "app"))

from agents.state import ODISContextBuilder, GraphState, SearchResultsData
from core.models import (
    CriteriaItem,
    SearchCriterias,
    CommuneResult,
    EducationMetrics,
    HousingMetrics,
    EmploymentMetrics,
    InclusionMetrics,
    TerritoryMetrics,
    JobOfferDetail,
    AssociationDetail,
    InclusionServiceDetail,
    CommuneScoreDetail,
)


def test_education_expert_context_object_level():
    """Verify education_expert receives its full EducationMetrics with facility_details and zero empty dicts."""
    criteria = SearchCriterias(
        nb_adultes=1,
        nb_enfants=2,
        classe_enfants=["maternelle", "elementaire"],
        odis_brief="Mère célibataire avec 2 enfants cherchant une école primaire.",
    )
    edu_metrics = EducationMetrics(
        cat_score=0.85,
        facility_counts={"ecoles_maternelles": 4, "ecoles_elementaires": 6},
        facility_details={
            "ecoles_elementaires": ["École Pasteur", "École Jean Jaurès"]
        },
    )
    commune = CommuneResult(
        codgeo="33063",
        name="Bordeaux",
        population=260958,
        name_bdv="Bordeaux",
        global_score=0.78,
        education=edu_metrics,
        housing=HousingMetrics(cat_score=0.5, price_per_sqm=15.0),
    )
    state = GraphState(
        search_criteria=criteria,
        focus_city=commune,
        odis_brief=criteria.odis_brief,
    )

    ctx_str = ODISContextBuilder.agent_context(state, "education_expert")
    ctx = json.loads(ctx_str)

    # 1. Briefing and criteria
    assert "Résumé du dossier (Briefing)" in ctx
    assert ctx["Résumé du dossier (Briefing)"] == criteria.odis_brief
    assert "Critères de recherche" in ctx
    assert "odis_brief" not in ctx["Critères de recherche"]
    assert "Niveaux scolaires recherchés" in ctx["Critères de recherche"]

    # 2. Base Identity
    assert "Commune analysée (Identité)" in ctx
    ident = ctx["Commune analysée (Identité)"]
    assert ident["Code INSEE"] == "33063"
    assert ident["Nom"] == "Bordeaux"

    # 3. Domain metrics & details present
    assert "Données éducation" in ctx
    edu_ctx = ctx["Données éducation"]
    assert "Nombre d'établissements scolaires" in edu_ctx
    assert "Noms des établissements par type" in edu_ctx
    assert edu_ctx["Noms des établissements par type"]["ecoles_elementaires"] == [
        "École Pasteur",
        "École Jean Jaurès",
    ]

    # 4. Zero empty dicts from unrelated domains
    assert "Données logement" not in ctx
    assert "Données santé" not in ctx
    assert "Données mobilité" not in ctx
    assert "Données emploi et formation" not in ctx
    assert "Données inclusion" not in ctx


def test_job_hunter_context_includes_matching_jobs():
    """Verifies that Job Hunter receives EmploymentMetrics and matching_job_offers with ID, company, ROME, and description."""
    offer = JobOfferDetail(
        id="OFFER_123",
        title="Boulanger",
        company="Fournil Moderne",
        contract_type="CDI",
        rome_code="D1102",
        rome_label="Boulangerie",
        location="Bordeaux",
        description="Fabrication de pain traditionnel et viennoiseries.",
    )
    emp_metrics = EmploymentMetrics(
        cat_score=0.9,
        matching_job_offers=[[offer]],
    )
    commune = CommuneResult(
        codgeo="33063",
        name="Bordeaux",
        population=260958,
        global_score=0.8,
        employment=emp_metrics,
    )
    state = GraphState(
        search_criteria=SearchCriterias(
            nb_adultes=1,
            codes_metiers=[[CriteriaItem(code="D1102", label="Boulangerie")]],
        ),
        focus_city=commune,
    )

    ctx_str = ODISContextBuilder.agent_context(state, "job_hunter")
    ctx = json.loads(ctx_str)

    assert "Données emploi et formation" in ctx
    emp_ctx = ctx["Données emploi et formation"]
    offers = emp_ctx[
        "Liste des offres d'emploi correspondantes séparées par adulte du ménage"
    ]
    assert len(offers) == 1
    assert len(offers[0]) == 1
    job_str = offers[0][0]
    assert "OFFER_123" in job_str
    assert "Boulanger" in job_str
    assert "Fournil Moderne" in job_str
    assert "CDI" in job_str
    assert "[ROME: D1102]" in job_str
    assert "Fabrication de pain traditionnel" in job_str


def test_social_integration_expert_context_includes_details():
    """Verifies that Social Integration Expert receives InclusionMetrics, associations (with ID/desc), and services in Option 2 structure."""
    asso = AssociationDetail(
        id="W332001234",
        name="Accueil Réfugiés Gironde",
        description="Aide administrative et cours de français.",
        waldec_label="Action Sociale",
        refugee_focused=True,
    )
    srv = InclusionServiceDetail(
        id="srv_456",
        name="Cours FLE",
        nom_structure="Maison Solidaire",
        distance_km=2.5,
        commune_nom="Bordeaux",
    )
    inc_metrics = InclusionMetrics(
        cat_score=0.88,
        asso_refugee_list=[asso],
        services_detailed={"apprentissage_francais": [srv]},
    )
    commune = CommuneResult(
        codgeo="33063",
        name="Bordeaux",
        population=260958,
        global_score=0.82,
        inclusion=inc_metrics,
        territoire=TerritoryMetrics(ter_insecurite=12.4, maire_extreme_droite=False),
    )
    state = GraphState(
        search_criteria=SearchCriterias(),
        focus_city=commune,
    )

    ctx_str = ODISContextBuilder.agent_context(state, "social_integration_expert")
    ctx = json.loads(ctx_str)

    assert "Données inclusion" in ctx
    inc_ctx = ctx["Données inclusion"]
    assos = inc_ctx["Liste des associations d'aide aux réfugiés (source: RNA officiel)"]
    assert len(assos) == 1
    # Formatted with ID and description
    assert (
        assos[0]
        == "W332001234 | Accueil Réfugiés Gironde | Aide administrative et cours de français."
    )

    # Formatted as list of unique structures with distance/commune under theme
    srvs = inc_ctx["Services d'inclusion détaillés groupés par thématique (source: Data Inclusion)"]
    assert "apprentissage_francais" in srvs
    assert srvs["apprentissage_francais"] == ["Maison Solidaire (2.5 km - Bordeaux)"]

    # Context includes local territory indicators
    assert "Données territoire (Contexte local)" in ctx
    ter_ctx = ctx["Données territoire (Contexte local)"]
    assert ter_ctx["Indice d'insécurité (taux cumulé)"] == 12.4


def test_social_integration_expert_context_empty_refugee_assos_message():
    """Verifies that Social Integration Expert receives an explicit message when no refugee associations are in RNA."""
    inc_metrics = InclusionMetrics(
        cat_score=0.5,
        asso_refugee_list=[],
        asso_refugee_count=0,
    )
    commune = CommuneResult(
        codgeo="33063",
        name="Bordeaux",
        population=260958,
        global_score=0.75,
        inclusion=inc_metrics,
    )
    state = GraphState(
        search_criteria=SearchCriterias(),
        focus_city=commune,
    )

    ctx_str = ODISContextBuilder.agent_context(state, "social_integration_expert")
    ctx = json.loads(ctx_str)

    assert "Données inclusion" in ctx
    inc_ctx = ctx["Données inclusion"]
    assert (
        inc_ctx["Liste des associations d'aide aux réfugiés (source: RNA officiel)"]
        == "Aucune association spécifique recensée dans le Répertoire National des Associations (RNA) pour cette commune."
    )


def test_ts_agent_context_excludes_unwanted_sections():
    """Verifies that ts_agent context contains search criteria, target city, score overview, and territory metrics."""
    criteria = SearchCriterias(nb_adultes=1, nb_enfants=0)
    current = CommuneResult(codgeo="75056", name="Paris", population=2148271)
    rec1 = CommuneResult(
        codgeo="13055",
        name="Marseille",
        population=870018,
        scores={
            "emploi": [
                CommuneScoreDetail(
                    label="Offres",
                    score_id="job1",
                    score_normalise=0.8,
                    relative_weight=1.0,
                    unit="",
                )
            ]
        },
        territoire=TerritoryMetrics(cat_score=0.7, is_strategic=True),
    )

    results_data = SearchResultsData(
        search_hash="abc", current_geo=current, results=[rec1]
    )

    state = GraphState(
        search_criteria=criteria,
        search_results=results_data,
        focus_city=rec1,
        odis_brief="Brief",
    )

    ts_ctx_str = ODISContextBuilder.agent_context(state, "ts_agent")
    ts_ctx = json.loads(ts_ctx_str)

    assert "Ville actuelle (référence)" not in ts_ctx
    assert "Top 5 communes identifiées" not in ts_ctx
    assert "Ville cible" in ts_ctx
    assert ts_ctx["Ville cible"] == "Marseille (13055)"
    assert "Scores thématiques" in ts_ctx
    assert "Données territoire" in ts_ctx

    # Refiner context includes comparison cities and top 5
    refiner_ctx_str = ODISContextBuilder.agent_context(state, "refiner")
    refiner_ctx = json.loads(refiner_ctx_str)
    assert "Ville actuelle (référence)" in refiner_ctx
    assert "Top 5 communes identifiées" in refiner_ctx


def test_refiner_context_without_focus_city_includes_all_candidates_and_scores():
    """Verifies that refiner context correctly populates Top 5, scores, and shortlisted city when focus_city is None (post-scoring runtime)."""
    criteria = SearchCriterias(nb_adultes=1, nb_enfants=0)
    current = CommuneResult(codgeo="75056", name="Paris", population=2148271)
    rec1 = CommuneResult(
        codgeo="13055",
        name="Marseille",
        population=870018,
        scores={
            "emploi": [
                CommuneScoreDetail(
                    label="Offres d'emploi",
                    score_id="job1",
                    score_normalise=0.8,
                    relative_weight=1.0,
                    valeur_kpi=42,
                    unit="offres",
                )
            ]
        },
    )
    pressentie = CommuneResult(
        codgeo="69123",
        name="Lyon",
        population=522250,
        scores={
            "logement": [
                CommuneScoreDetail(
                    label="Loyer moyen",
                    score_id="loy1",
                    score_normalise=0.7,
                    relative_weight=2.0,
                    valeur_kpi=15.0,
                    unit="€/m²",
                )
            ]
        },
    )
    results_data = SearchResultsData(
        search_hash="abc",
        current_geo=current,
        results=[rec1],
        commune_pressentie=pressentie,
    )

    # Runtime state for postscoring: focus_city is None
    state = GraphState(
        search_criteria=criteria,
        search_results=results_data,
        focus_city=None,
        odis_brief="Brief profile",
    )

    refiner_ctx_str = ODISContextBuilder.agent_context(state, "refiner")
    refiner_ctx = json.loads(refiner_ctx_str)

    assert "Critères de recherche" in refiner_ctx
    assert "Ville actuelle (référence)" in refiner_ctx
    assert refiner_ctx["Ville actuelle (référence)"]["Code INSEE"] == "75056"

    assert "Top 5 communes identifiées" in refiner_ctx
    top5 = refiner_ctx["Top 5 communes identifiées"]
    assert len(top5) == 1
    assert top5[0]["Code INSEE"] == "13055"
    assert "Scores thématiques" in top5[0]
    assert "emploi" in top5[0]["Scores thématiques"]

    assert "Commune pressentie (pour comparaison)" in refiner_ctx
    cp = refiner_ctx["Commune pressentie (pour comparaison)"]
    assert cp["Code INSEE"] == "69123"
    assert "Scores thématiques" in cp
    assert "logement" in cp["Scores thématiques"]


def test_detail_formatting_helpers():
    """Verifies individual detail formatting helper outputs."""
    detail = CommuneScoreDetail(
        label="Loyer moyen",
        score_id="loy1",
        score_normalise=0.75,
        relative_weight=2.5,
        valeur_kpi=12.5,
        unit="€/m²",
    )
    val = ODISContextBuilder._process_value(detail)
    assert val == "Loyer moyen: 12.5 €/m², score: 0.75, poids relatif: 2.5%"

    asso = AssociationDetail(
        id="W123",
        name="Asso Test",
        description="Aide",
        waldec_label="Social",
    )
    assert ODISContextBuilder._process_value(asso) == "W123 | Asso Test | Aide"
