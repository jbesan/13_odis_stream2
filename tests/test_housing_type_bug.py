
import sys
import os
import pytest
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.mcp_server import _compute_top_cities_logic, set_data_context
from app.core.models import SearchCriterias, CriteriaItem

@pytest.fixture
def mock_data_context():
    # Mock data context to avoid loading full datasets
    mock_odis = MagicMock()
    mock_odis.index = ['33063']
    mock_odis.loc = {'33063': {'libgeo': 'Bordeaux', 'epci_code': '123', 'reg_code': '456', 'dep_code': '78', 'bassin_de_vie': '12345'}}
    
    # Configure mock_odis for filter_communes
    mock_odis.__getitem__.side_effect = lambda x: MagicMock() if isinstance(x, str) else mock_odis
    
    data = {
        'odis': mock_odis,
        'bv_geo': MagicMock(),
        'area_geo': MagicMock(),
        'scores_cat': MagicMock(),
        'incl_index': MagicMock(),
        'associations_data': MagicMock(),
        'formations_data': MagicMock(),
        'codformations_index': MagicMock(),
        'referentiels_raw': MagicMock(),
        'live_jobs_data': MagicMock()
    }
    
    # Ensure scores_cat is empty but iterable
    data['scores_cat'].empty = True
    data['scores_cat'].iterrows.return_value = []
    
    set_data_context(data)
    return data

def test_type_logement_propagation(mock_data_context):
    """Verify that type_logement is correctly propagated to ScoringConfig."""
    from app.core.scoring import ScoringEngine
    import app.services.mcp_server as mcp_server
    
    # Mock ScoringEngine to capture the config
    mock_engine = MagicMock(spec=ScoringEngine)
    mock_engine.df_all_communes = mock_data_context['odis']
    mock_engine.run.return_value = MagicMock() # Return empty GDF
    
    original_get_engine = mcp_server.get_scoring_engine
    mcp_server.get_scoring_engine = lambda: mock_engine
    
    try:
        criteria = {
            'commune_actuelle': {'code': '33063', 'label': 'Bordeaux'},
            'type_logement': {'code': 'appt_t1_t2', 'label': 'Appartement (T1 & T2)'},
            'hebergement': 'Location'
        }
        
        _compute_top_cities_logic(criteria)
        
        # Check the config passed to engine.run
        assert mock_engine.run.called
        config = mock_engine.run.call_args[0][0]
        assert config.type_logement == 'appt_t1_t2', f"Expected appt_t1_t2, got {config.type_logement}"
        
        # Test default value
        mock_engine.run.reset_mock()
        criteria_no_type = {
            'commune_actuelle': {'code': '33063', 'label': 'Bordeaux'},
            'hebergement': 'Location'
        }
        _compute_top_cities_logic(criteria_no_type)
        config_default = mock_engine.run.call_args[0][0]
        assert config_default.type_logement == 'appt_all', f"Expected appt_all by default, got {config_default.type_logement}"

    finally:
        mcp_server.get_scoring_engine = original_get_engine

if __name__ == "__main__":
    sys.exit(pytest.main(["-v", __file__]))
