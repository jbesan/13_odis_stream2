import pytest
import pandas as pd
from app.core.models import SearchCriterias
from app.core.scoring import ScoringEngine


@pytest.mark.unit
class TestF53Multiselect:
    def test_search_criterias_list_support(self):
        """Tests that loc_search_code supports a list of strings."""
        # This test is expected to FAIL until models.py is updated
        config = SearchCriterias(
            commune_actuelle="33063",
            loc_search_area="departement",
            loc_search_code=["33", "40", "64"],
            nb_adultes=1,
            nb_enfants=0,
        )
        assert isinstance(config.loc_search_code, list)
        assert config.loc_search_code == ["33", "40", "64"]

    def test_filter_communes_multi_departement(self):
        """Tests filtering by multiple departments."""
        df = pd.DataFrame(
            {
                "dep_code": ["33", "33", "40", "64", "75"],
                "reg_code": ["75", "75", "75", "75", "11"],
            },
            index=["33063", "33001", "40001", "64001", "75056"],
        )

        # This test is expected to FAIL until scoring.py is updated
        filtered = ScoringEngine._filter_communes(
            df=df,
            start_commune=pd.DataFrame(),
            loc_type="departement",
            loc_code=["33", "40"],
        )

        assert len(filtered) == 3
        assert "33063" in filtered.index
        assert "33001" in filtered.index
        assert "40001" in filtered.index
        assert "64001" not in filtered.index
        assert "75056" not in filtered.index

    def test_filter_communes_single_departement_as_list(self):
        """Tests filtering by a single department passed as a list."""
        df = pd.DataFrame(
            {"dep_code": ["33", "75"], "reg_code": ["75", "11"]},
            index=["33063", "75056"],
        )

        filtered = ScoringEngine._filter_communes(
            df=df, start_commune=pd.DataFrame(), loc_type="departement", loc_code=["33"]
        )

        assert len(filtered) == 1
        assert "33063" in filtered.index

    def test_filter_communes_region_with_list(self):
        """Tests that region filtering still works if loc_code is a list [region_code]."""
        df = pd.DataFrame(
            {"dep_code": ["33", "40", "75"], "reg_code": ["75", "75", "11"]},
            index=["33063", "40001", "75056"],
        )

        # This test is expected to FAIL until scoring.py is updated to handle list in region filtering
        filtered = ScoringEngine._filter_communes(
            df=df, start_commune=pd.DataFrame(), loc_type="region", loc_code=["75"]
        )

        assert len(filtered) == 2
        assert "33063" in filtered.index
        assert "40001" in filtered.index

    def test_filter_communes_multiple_regions(self):
        """Tests filtering by multiple regions."""
        df = pd.DataFrame(
            {"dep_code": ["33", "75", "13"], "reg_code": ["75", "75", "93"]},
            index=["33063", "75056", "13055"],
        )
        filtered = ScoringEngine._filter_communes(
            df=df, start_commune=pd.DataFrame(), loc_type="region", loc_code=["75", "93"]
        )
        assert len(filtered) == 3
        assert "33063" in filtered.index
        assert "75056" in filtered.index
        assert "13055" in filtered.index
