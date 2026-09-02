import pytest
import pandas as pd
from shapely.geometry import Point

from app.core.scoring import ScoringEngine
from app.core.models import SearchCriterias


@pytest.fixture
def mock_comparator_data():
    """Mock dataset with candidate communes in department 33 and comparator communes in 75/69."""
    data = {
        "codgeo": ["33063", "33001", "33002", "33003", "33004", "75056", "69123"],
        "libgeo": [
            "Bordeaux",
            "Ambarès",
            "Arcachon",
            "Blaye",
            "Bègles",
            "Paris",
            "Lyon",
        ],
        "dep_code": ["33", "33", "33", "33", "33", "75", "69"],
        "reg_code": ["75", "75", "75", "75", "75", "11", "84"],
        "epci_code": ["243300154", "243300154", "243300154", "243300154", "243300154", "200054781", "200046977"],
        "bassin_de_vie": ["33063", "33063", "33002", "33003", "33063", "75056", "69123"],
        "population": [250000, 15000, 11000, 5000, 27000, 2100000, 500000],
        "latitude": [44.8378, 44.9250, 44.6583, 45.1278, 44.8083, 48.8566, 45.7640],
        "longitude": [-0.5792, -0.4833, -1.1644, -0.6639, -0.5472, 2.3522, 4.8357],
        "heb_asso_habitant_scaled": [0.5, 0.2, 0.8, 0.4, 0.6, 0.9, 0.7],
        "log_occup_scaled": [0.6, 0.3, 0.7, 0.5, 0.4, 0.8, 0.6],
        "emp_taux_chomage_scaled": [0.4, 0.5, 0.6, 0.3, 0.5, 0.7, 0.4],
        "geometry": [
            Point(-0.5792, 44.8378),
            Point(-0.4833, 44.9250),
            Point(-1.1644, 44.6583),
            Point(-0.6639, 45.1278),
            Point(-0.5472, 44.8083),
            Point(2.3522, 48.8566),
            Point(4.8357, 45.7640),
        ],
    }
    df = pd.DataFrame(data)
    df.set_index("codgeo", inplace=True)
    return df


@pytest.fixture
def mock_scores_cat():
    return pd.DataFrame(
        [
            {
                "cat": "Logement",
                "score": "heb_asso_habitant_scaled",
                "metric": "heb_asso_habitant_scaled",
                "weight": 3.0,
                "bdv_factor": 0.0,
            },
            {
                "cat": "Logement",
                "score": "log_occup_scaled",
                "metric": "log_occup_scaled",
                "weight": 2.0,
                "bdv_factor": 0.0,
            },
            {
                "cat": "Emploi",
                "score": "emp_taux_chomage_scaled",
                "metric": "emp_taux_chomage_scaled",
                "weight": 4.0,
                "bdv_factor": 0.0,
            },
        ]
    )


def make_engine(df_all_communes, scores_cat):
    return ScoringEngine(
        df_all_communes=df_all_communes,
        df_bv_geo=pd.DataFrame(),
        scores_cat=scores_cat,
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        formations_data=pd.DataFrame(),
        rna_rag_service=False,
    )


@pytest.mark.unit
class TestComparatorIsolation:
    def test_commune_pressentie_does_not_alter_top_5_recommendations(
        self, mock_comparator_data, mock_scores_cat
    ):
        """P1-03: Adding an out-of-pool commune_pressentie (e.g. Paris 75056) must not change Top 5 candidate recommendations."""
        engine = make_engine(mock_comparator_data, mock_scores_cat)

        config_base = SearchCriterias(
            loc_search_type="departement",
            loc_search_area="departement",
            loc_search_code=["33"],
            commune_actuelle="33063",
            commune_pressentie=None,
            active_criteria={"heb_asso_habitant_scaled", "log_occup_scaled", "emp_taux_chomage_scaled"},
        )

        # 1. Run without commune_pressentie
        model1, df1 = engine.run_optimized(config_base)
        top5_base_codes = [c.codgeo for c in model1.results]
        top5_base_scores = [c.global_score for c in model1.results]

        # 2. Run with commune_pressentie = 75056 (Paris, out-of-pool)
        config_with_p = SearchCriterias(
            loc_search_type="departement",
            loc_search_area="departement",
            loc_search_code=["33"],
            commune_actuelle="33063",
            commune_pressentie="75056",
            active_criteria={"heb_asso_habitant_scaled", "log_occup_scaled", "emp_taux_chomage_scaled"},
        )
        model2, df2 = engine.run_optimized(config_with_p)
        top5_with_p_codes = [c.codgeo for c in model2.results]
        top5_with_p_scores = [c.global_score for c in model2.results]

        # 3. Assert exact Top 5 candidate stability
        assert top5_base_codes == top5_with_p_codes, (
            f"Top 5 commune codes changed when adding commune_pressentie! Base: {top5_base_codes}, With P: {top5_with_p_codes}"
        )
        assert top5_base_scores == top5_with_p_scores, (
            f"Top 5 scores changed when adding commune_pressentie! Base: {top5_base_scores}, With P: {top5_with_p_scores}"
        )
        assert model2.commune_pressentie is not None
        assert model2.commune_pressentie.codgeo == "75056"

    def test_changing_commune_pressentie_leaves_candidates_identical(
        self, mock_comparator_data, mock_scores_cat
    ):
        """P1-03: Switching commune_pressentie between Paris and Lyon must yield 100% identical candidate scores."""
        engine = make_engine(mock_comparator_data, mock_scores_cat)

        config_paris = SearchCriterias(
            loc_search_type="departement",
            loc_search_area="departement",
            loc_search_code=["33"],
            commune_actuelle="33063",
            commune_pressentie="75056",
            active_criteria={"heb_asso_habitant_scaled", "log_occup_scaled", "emp_taux_chomage_scaled"},
        )

        config_lyon = SearchCriterias(
            loc_search_type="departement",
            loc_search_area="departement",
            loc_search_code=["33"],
            commune_actuelle="33063",
            commune_pressentie="69123",
            active_criteria={"heb_asso_habitant_scaled", "log_occup_scaled", "emp_taux_chomage_scaled"},
        )

        res_paris = engine.run(config_paris)
        res_lyon = engine.run(config_lyon)

        # Filter candidate pool rows (department 33)
        cand_paris = res_paris.loc[res_paris.index.isin(["33063", "33001", "33002", "33003", "33004"])].sort_index()
        cand_lyon = res_lyon.loc[res_lyon.index.isin(["33063", "33001", "33002", "33003", "33004"])].sort_index()

        pd.testing.assert_series_equal(cand_paris["weighted_score"], cand_lyon["weighted_score"])
