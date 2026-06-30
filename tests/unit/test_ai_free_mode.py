import os
import pytest
from unittest.mock import patch, MagicMock
from app.config import is_ai_free_mode, ORGANIZATION_PROFILES
from app.core.postscoring import generate_static_pitch, _curate_jobs_with_agent
from app.core.models import CommuneResult, CommuneScoreDetail

def test_is_ai_free_mode_env():
    # 1. Test when env variable is not set
    with patch.dict(os.environ, {}, clear=True):
        assert not is_ai_free_mode()

    # 2. Test when env variable is True
    with patch.dict(os.environ, {"ODIS_AI_FREE_MODE": "True"}):
        assert is_ai_free_mode()

    # 3. Test when env variable is 1
    with patch.dict(os.environ, {"ODIS_AI_FREE_MODE": "1"}):
        assert is_ai_free_mode()

    # 4. Test when env variable is False
    with patch.dict(os.environ, {"ODIS_AI_FREE_MODE": "False"}):
        assert not is_ai_free_mode()

@patch("streamlit.session_state")
def test_is_ai_free_mode_org(mock_session_state):
    # Mock active organization profile that has ai_free_mode=True
    with patch.dict(ORGANIZATION_PROFILES, {
        "test_org": {
            "name": "Test Org",
            "description": "Test",
            "zone_type": "departement",
            "default_zones": ["33"],
            "defaults": {},
            "ai_free_mode": True
        }
    }):
        # Mock session state returns the org ID
        mock_session_state.get.side_effect = lambda key, default=None: "test_org" if key == "ui_org_context" else default
        
        with patch.dict(os.environ, {}, clear=True):
            assert is_ai_free_mode()

def test_generate_static_pitch():
    # Create mock score details
    detail_1 = CommuneScoreDetail(
        label="Logement Social",
        score_id="log_soc_delay_scaled",
        valeur_kpi=10,
        score_normalise=0.8,
        unit="mois",
        relative_weight=15.0,
        strong_point_text="Délai d'attente pour un logement social réduit"
    )
    detail_2 = CommuneScoreDetail(
        label="Crèches",
        score_id="edu_petite_enfance_scaled",
        valeur_kpi=25,
        score_normalise=0.9,
        unit="places",
        relative_weight=20.0,
        strong_point_text="Bonne couverture en crèches et assistantes maternelles"
    )
    detail_3 = CommuneScoreDetail(
        label="Emplois en tension",
        score_id="met_match_adult1_tension_scaled",
        valeur_kpi=100,
        score_normalise=0.4,
        unit="offres",
        relative_weight=30.0,
        strong_point_text="Recrutements difficiles pour l'adulte 1 (Fort besoin de main d'oeuvre)"
    )
    detail_4 = CommuneScoreDetail(
        label="Gare SNCF",
        score_id="mob_gare_scaled",
        valeur_kpi="Oui",
        score_normalise=1.0,
        unit="",
        relative_weight=5.0,
        strong_point_text="Présence d'une gare ferroviaire dans la commune"
    )

    # Calculate contributions:
    # detail_1: 0.8 * 15 = 12.0
    # detail_2: 0.9 * 20 = 18.0 (highest)
    # detail_3: 0.4 * 30 = 12.0
    # detail_4: 1.0 * 5 = 5.0
    # Top 3 should be detail_2, detail_1, detail_3

    mock_commune = MagicMock()
    mock_commune.scores = {
        "logement": [detail_1],
        "education": [detail_2],
        "emploi": [detail_3],
        "mobilite": [detail_4]
    }
    
    pitch = generate_static_pitch(mock_commune)
    
    assert "**Points forts du territoire :**" in pitch
    assert "- **Bonne couverture en crèches et assistantes maternelles** : 25 places" in pitch
    assert "- **Délai d'attente pour un logement social réduit** : 10 mois" in pitch
    assert "- **Recrutements difficiles pour l'adulte 1 (Fort besoin de main d'oeuvre)** : 100 offres" in pitch
    assert "Gare SNCF" not in pitch # Since it contributes 5.0, not in top 3

def test_curate_jobs_fallback():
    # Construct list of 15 mock jobs
    mock_jobs = [{"id": f"job_{i}", "title": f"Job {i}"} for i in range(15)]
    
    # Enable AI-free mode via env variable
    with patch.dict(os.environ, {"ODIS_AI_FREE_MODE": "True"}):
        curated = _curate_jobs_with_agent(
            jobs=mock_jobs,
            profile_brief="test candidate",
            notes_qualitatives=[]
        )
        # Should directly return top 10 raw jobs without calling LLM agent
        assert len(curated) == 10
        assert curated[0]["id"] == "job_0"
        assert curated[9]["id"] == "job_9"
