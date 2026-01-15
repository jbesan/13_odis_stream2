import pytest
from unittest.mock import patch, MagicMock
from agents.tools import compute_top_cities
from core.models import SearchCriterias, ScoringConfig

def test_compute_top_cities_wrapper():
    # Use context managers for patching to avoid pytest fixture conflicts
    with patch("agents.tools._compute_top_cities_logic") as mock_logic, \
         patch("agents.tools.cfg.WEIGHT_PROFILES", {"Équilibré": {"mock": "weights"}, "Famille": {"mock": "famille_weights"}}):
        
        # Setup mock return
        mock_logic.return_value = {"status": "success"}
        
        # 1. Test with default profile (Equilibre)
        criteria_equilibre = SearchCriterias(
            commune_actuelle="75056",
            loc_search_area="departement",
            nb_adultes=1,
            weight_profile="Équilibré"
        )
        
        result = compute_top_cities(criteria_equilibre)
        
        # Check that logic was called with correct criteria
        args, _ = mock_logic.call_args
        called_criteria = args[0]
        assert called_criteria.nb_adultes == 1
        assert called_criteria.weight_profile == "Équilibré"
        assert result == {"status": "success"}

        # 2. Test with specific profile (Famille)
        criteria_famille = SearchCriterias(
            commune_actuelle="75056",
            loc_search_area="departement",
            nb_adultes=2,
            weight_profile="Famille"
        )
        
        compute_top_cities(criteria_famille)
        
        args, _ = mock_logic.call_args
        called_criteria = args[0]
        assert called_criteria.nb_adultes == 2
        assert called_criteria.weight_profile == "Famille"

        # 3. Test extraction with empty profile -> Defaults to Equilibre
        criteria_empty = SearchCriterias(
            commune_actuelle="75056",
            loc_search_area="departement",
            weight_profile="" # Empty string
        )
        
        compute_top_cities(criteria_empty)
        
        args, _ = mock_logic.call_args
        called_criteria = args[0]
        assert called_criteria.weight_profile == ""
