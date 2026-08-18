import pandas as pd
import streamlit as st
from app.core.models import CommuneResult, HousingMetrics
from app.ui.results import render_jaccueille_housing_info


def test_render_jaccueille_housing_info_uses_codgeo_bdv(monkeypatch):
    """Verify that render_jaccueille_housing_info uses codgeo_bdv to fetch BDV postal codes."""
    info_calls = []

    def mock_info(msg):
        info_calls.append(msg)

    monkeypatch.setattr(st, "info", mock_info)
    monkeypatch.setattr(st, "session_state", {"ui_org_context": "jaccueille"})

    # Mock BDV dataframe containing postal codes for BDV 33063 (Bordeaux)
    mock_df_bdv = pd.DataFrame(
        [
            {
                "bassin_de_vie": "33063",
                "lead_count": 10,
                "contact_count": 20,
                "codes_postaux": '["33000", "33110", "33200"]',
            }
        ]
    )
    monkeypatch.setattr(
        "app.ui.results.fetch_salesforce_jaccueille_bdv", lambda: mock_df_bdv
    )

    # Commune Le Bouscat (codgeo=33069, codgeo_bdv=33063)
    commune = CommuneResult(
        codgeo="33069",
        name="Le Bouscat",
        codgeo_bdv="33063",
        housing=HousingMetrics(host_count=5),
    )

    render_jaccueille_housing_info(commune)

    assert len(info_calls) == 1
    msg = info_calls[0]
    # Check that postal codes (33000, 33110, 33200) are included in the Salesforce report filter parameter fv0
    assert "fv0=33000,33110,33200" in msg
