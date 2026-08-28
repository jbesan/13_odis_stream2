import pytest
import pandas as pd
import numpy as np
from app.core.models import (
    ScoresConfigFileSchema,
    ScoreDisplayConfigSchema,
    CommuneScoreDetail,
)
from app.core.scoring import _resolve_discrete_label, _format_kpi_value
from app.agents.state import ODISContextBuilder


def test_resolve_discrete_label():
    mapping = {
        1.0: "Commune / EPCI signataire",
        0.5: "Département signataire",
        0.0: "Non signataire",
    }

    # Exact float matches
    assert _resolve_discrete_label(1.0, mapping) == "Commune / EPCI signataire"
    assert _resolve_discrete_label(0.5, mapping) == "Département signataire"
    assert _resolve_discrete_label(0.0, mapping) == "Non signataire"

    # Int & float tolerance matches
    assert _resolve_discrete_label(1, mapping) == "Commune / EPCI signataire"
    assert _resolve_discrete_label(0, mapping) == "Non signataire"
    assert _resolve_discrete_label(0.50000001, mapping) == "Département signataire"

    # None & NaN
    assert _resolve_discrete_label(None, mapping) is None
    assert _resolve_discrete_label(np.nan, mapping) is None

    # Empty mapping
    assert _resolve_discrete_label(1.0, None) is None
    assert _resolve_discrete_label(1.0, {}) is None


def test_format_kpi_value_discrete():
    mapping = {
        1.0: "Gare présente",
        0.0: "Aucune gare",
    }
    # Discrete metric format
    assert _format_kpi_value(
        1.0, "", "mob_gare_scaled", val_scaled=1.0, metric_type="discrete", discrete_mapping=mapping
    ) == "Gare présente"
    assert _format_kpi_value(
        0.0, "", "mob_gare_scaled", val_scaled=0.0, metric_type="discrete", discrete_mapping=mapping
    ) == "Aucune gare"


def test_scores_config_discrete_indicators_present():
    catalog = ScoresConfigFileSchema.load_default()
    score_map = {item.id: item for item in catalog.scores}

    # Verify target discrete metrics are configured
    target_discrete_ids = [
        "mob_gare_scaled",
        "mob_epci_scaled",
        "ter_anvita_scaled",
        "ter_ctai_scaled",
        "ter_pol_scaled",
        "ter_strategic_locations_scaled",
        "heb_jaccueille_accueillants_score",
        "heb_jaccueille_prospects_score",
    ]

    for sid in target_discrete_ids:
        assert sid in score_map, f"Missing indicator {sid} in catalog"
        display = score_map[sid].display
        assert display is not None, f"Missing display config for {sid}"
        assert display.metric_type == "discrete", f"Expected {sid} to have metric_type discrete"
        assert display.discrete_mapping is not None, f"Expected {sid} to have discrete_mapping"
        assert len(display.discrete_mapping) > 0, f"Expected non-empty discrete_mapping for {sid}"
        assert display.show is True, f"Expected {sid} to have show=True"


def test_commune_score_detail_model():
    detail_continuous = CommuneScoreDetail(
        label="Taux Vacance",
        score_id="log_vac_scaled",
        valeur_kpi=4.5,
        score_normalise=0.8,
        unit="%",
        relative_weight=15.0,
    )
    assert detail_continuous.metric_type == "continuous"
    assert detail_continuous.status_label is None

    detail_discrete = CommuneScoreDetail(
        label="Signataire CTAI",
        score_id="ter_ctai_scaled",
        valeur_kpi=0.5,
        score_normalise=0.5,
        unit="contrat signé",
        relative_weight=33.3,
        metric_type="discrete",
        status_label="Département signataire",
    )
    assert detail_discrete.metric_type == "discrete"
    assert detail_discrete.status_label == "Département signataire"


def test_agent_context_builder_formats_discrete_status():
    detail_discrete = CommuneScoreDetail(
        label="Signataire CTAI",
        score_id="ter_ctai_scaled",
        valeur_kpi=0.5,
        score_normalise=0.5,
        unit="contrat signé",
        relative_weight=33.3,
        metric_type="discrete",
        status_label="Département signataire",
    )

    formatted = ODISContextBuilder._format_detail_item(detail_discrete)
    assert "Signataire CTAI: Département signataire (score: 0.5, poids relatif: 33.3%)" == formatted

    detail_continuous = CommuneScoreDetail(
        label="Loyer Moyen",
        score_id="log_loyer_moyen_appt_all_scaled",
        valeur_kpi=12.5,
        score_normalise=0.75,
        unit="€/m²",
        relative_weight=20.0,
    )
    formatted_cont = ODISContextBuilder._format_detail_item(detail_continuous)
    assert "Loyer Moyen: 12.5 €/m², score: 0.75, poids relatif: 20.0%" == formatted_cont
