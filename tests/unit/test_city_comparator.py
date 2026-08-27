from core.models import CommuneResult, CommuneScoreDetail
from agents.comparator import compute_city_comparison
from agents.state import AgentArtifact


def test_city_comparator_with_reference_city():
    """Verify direction-aware comparative table generation against a reference city."""
    focus = CommuneResult(
        codgeo="17347",
        name="Saint-Jean-d'Angély",
        global_score=0.82,
        scores={
            "logement": [
                CommuneScoreDetail(
                    score_id="log_loyer_m2",
                    label="Loyer moyen m²",
                    score_normalise=0.85,
                    valeur_kpi=9.5,
                    unit="€/m²",
                    relative_weight=20.0,
                ),
            ],
            "emploi": [
                CommuneScoreDetail(
                    score_id="emp_taux_chomage",
                    label="Taux d'insertion professionnelle",
                    score_normalise=0.75,
                    valeur_kpi=65,
                    unit="%",
                    relative_weight=30.0,
                ),
            ],
        },
    )

    ref = CommuneResult(
        codgeo="33063",
        name="Bordeaux",
        global_score=0.68,
        scores={
            "logement": [
                CommuneScoreDetail(
                    score_id="log_loyer_m2",
                    label="Loyer moyen m²",
                    score_normalise=0.40,
                    valeur_kpi=16.2,
                    unit="€/m²",
                    relative_weight=20.0,
                ),
            ],
            "emploi": [
                CommuneScoreDetail(
                    score_id="emp_taux_chomage",
                    label="Taux d'insertion professionnelle",
                    score_normalise=0.70,
                    valeur_kpi=60,
                    unit="%",
                    relative_weight=30.0,
                ),
            ],
        },
    )

    artifact = compute_city_comparison(focus, ref)
    assert isinstance(artifact, AgentArtifact)
    assert artifact.domain == "city_comparator"
    assert artifact.usage.total_tokens == 0

    res = artifact.result
    assert "Comparatif territorial : Saint-Jean-d'Angély vs Bordeaux" in res
    assert "82%" in res
    assert "68%" in res
    assert "Loyer moyen m²" in res
    assert "9.5 €/m²" in res
    assert "16.2 €/m²" in res
    assert "+45 pts" in res  # (0.85 - 0.40 = +45 pts)


def test_city_comparator_single_city_fallback():
    """Verify single-city score highlight overview when no reference city is provided."""
    focus = CommuneResult(
        codgeo="17347",
        name="Saint-Jean-d'Angély",
        global_score=0.78,
        scores={
            "sante": [
                CommuneScoreDetail(
                    score_id="san_medecins",
                    label="Médecins généralistes",
                    score_normalise=0.90,
                    valeur_kpi=12,
                    unit="praticiens",
                    relative_weight=15.0,
                )
            ]
        },
    )

    artifact = compute_city_comparison(focus, None)
    assert artifact.domain == "city_comparator"
    assert "Synthèse des indicateurs clés pour Saint-Jean-d'Angély" in artifact.result
    assert "Médecins généralistes" in artifact.result
    assert "90%" in artifact.result
    assert "12 praticiens" in artifact.result


def test_city_comparator_empty_focus_city():
    """Verify graceful handling when focus_city is None."""
    artifact = compute_city_comparison(None)
    assert artifact.domain == "city_comparator"
    assert "Aucune commune cible spécifiée" in artifact.result
