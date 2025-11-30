import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from app import data_loader
import config as cfg

@pytest.mark.unit
class TestTaxonomyUpdate:
    @patch('app.data_loader.pd.read_csv')
    @patch('app.data_loader.pd.read_parquet')
    @patch('app.data_loader.shp.from_wkb', autospec=True)
    @patch('app.data_loader.load_bassin_de_vie_data')
    @patch('app.data_loader.load_scores_config_as_df')
    def test_load_inclusion_taxonomy(
        self, 
        mock_load_config, 
        mock_load_bv, 
        mock_from_wkb, 
        mock_read_parquet, 
        mock_read_csv
    ):
        """Tests that inclusion taxonomy is correctly loaded and mapped."""
        
        # 1. Mock Reference CSV (referentiel_services_inclusion.csv)
        mock_ref_inclusion = pd.DataFrame({
            'Nom': [
                'logement-hebergement--sinformer-sur-les-demarches-liees-a-lacces-au-logement',
                'difficultes-administratives-ou-juridiques--accompagnement-aux-demarches-administratives',
                'preparer-sa-candidature--organiser-ses-demarches-de-recherche-demploi',
                'cat--svc_test'
            ],
            'Label': [
                'Info Logement',
                'Accompagnement Admin',
                'Recherche Emploi',
                'Service Test'
            ]
        })
        
        # 2. Mock Inclusion Parquet (odis_services_incl_exploded.parquet)
        mock_inclusion_parquet = pd.DataFrame({
            'nom': ['Struct A', 'Struct B', 'Struct C'],
            'codgeo': ['33063', '33063', '75056'],
            'thematiques': [
                'logement-hebergement--sinformer-sur-les-demarches-liees-a-lacces-au-logement',
                'cat--svc_test',
                'unknown--service' # Test fallback
            ],
            'geometry': [b'dummy', b'dummy', b'dummy']
        })
        
        # 3. Mock other datasets (minimal valid)
        mock_odis = pd.DataFrame({
            'codgeo': ['33063', '75056'],
            'libgeo': ['Bordeaux', 'Paris'],
            'polygon': [b'dummy', b'dummy'],
            'dep_code': ['33', '75'],
            'reg_code': ['75', '11'],
            'epci_code': ['243300316', '200054781'],
            'epci_nom': ['Bordeaux Métropole', 'Métropole du Grand Paris'],
            'codgeo_voisins': [[], []],
            'population': [1000, 2000],
            'pop_be': [1000, 2000],
            'met': [0, 0],
            'be_codfap_top': [[], []],
            'be_libfap_top': [[], []],
            'codes_formations': [[], []],
            'noms_formations': [[], []],
            'rp_5+pieces': [0, 0],
            'log_rp': [0, 0],
            'log_soc_inoccupes': [0, 0],
            'log_soc_total': [0, 0],
            'log_vac': [0, 0],
            'log_total': [0, 0],
            'ecoles_ct': [0, 0],
            'risque_fermeture': [0, 0],
            'svc_incl_count': [0, 0],
            'pol_num': [0, 0]
        })
        
        # Configure side effects
        def read_csv_side_effect(path, **kwargs):
            if 'referentiel' in path:
                return mock_ref_inclusion.copy()
            if 'caf' in path:
                return pd.DataFrame({'codgeo': [], 'taux_couverture': []})
            if 'lovac' in path:
                return pd.DataFrame({'CODGEO_25': [], 'pp_vacant_plus_2ans_25': []})
            if 'rna' in path:
                return pd.DataFrame({'adrs_codeinsee': [], 'id_waldec': [], 'objet_social2': []})
            if 'metiers' in path:
                return pd.DataFrame({'Code FAP 341': [], 'Intitulé FAP 341': []})
            if 'formations' in path:
                return pd.DataFrame({'codformation': [], 'libformation': []})
            if 'maternites' in path:
                return pd.DataFrame({'FI_ET': []})
            return pd.DataFrame()

        def read_parquet_side_effect(path, **kwargs):
            if 'inclusion.parquet' in path:
                return mock_inclusion_parquet.copy()
            if 'odis.parquet' in path:
                return mock_odis.copy()
            if 'ecoles' in path:
                return pd.DataFrame({
                    'code_commune': [], 'nom_etablissement': [], 'type_etablissement': [], 
                    'ecole_maternelle': [], 'ecole_elementaire': [], 'geometry': []
                })
            if 'sante' in path:
                return pd.DataFrame({
                    'LibelleSph': [], 'coordxet': [], 'coordyet': [], 'nofinesset': [], 
                    'Departement': [], 'Commune': [], 'LibelleCategorieAgregat': []
                })
            return pd.DataFrame()

        mock_read_csv.side_effect = read_csv_side_effect
        mock_read_parquet.side_effect = read_parquet_side_effect
        
        from shapely.geometry import Polygon
        mock_from_wkb.return_value = Polygon([(0,0), (1,0), (1,1), (0,1)])
        mock_load_config.return_value = pd.DataFrame(columns=['score', 'cat', 'metric', 'include_in_binom', 'display', 'min_bound', 'max_bound'])
        mock_load_bv.return_value = pd.DataFrame(columns=['CODGEO', cfg.BV_CODE_COL, cfg.BV_NAME_COL])
        


        # Act
        results = data_loader.load_all_datasets(
            'odis.parquet', 'bv.csv', 'scores.yaml', 'metiers.csv', 
            'formations.csv', 'ecoles.parquet', 'maternites.csv', 
            'sante.parquet', 'inclusion.parquet', 'caf.csv', 'lovac.csv', 'referentiel.csv'
        )
        
        # Unpack results (we need annuaire_inclusion and incl_index)
        # Return signature: odis, scores_cat, codfap_index, codformations_index, annuaire_ecoles, annuaire_sante, annuaire_inclusion, incl_index, associations_data, global_score_stats
        annuaire_inclusion = results[6]
        incl_index = results[7]
        
        # Assertions
        
        # 1. Check Label Mapping
        # First row: known slug
        assert annuaire_inclusion.iloc[0]['label'] == 'Info Logement'
        # Second row: known slug
        assert annuaire_inclusion.iloc[1]['label'] == 'Service Test'
        # Third row: unknown slug -> should fallback to slug
        assert annuaire_inclusion.iloc[2]['label'] == 'unknown--service'
        
        # 2. Check Category/Service Splitting
        assert annuaire_inclusion.iloc[0]['categorie'] == 'logement-hebergement'
        assert annuaire_inclusion.iloc[0]['service'] == 'sinformer-sur-les-demarches-liees-a-lacces-au-logement'
        
        # 3. Check Index Keys
        # incl_index is a Series of sets, indexed by codgeo
        # 33063 has 2 services
        assert '33063' in incl_index.index
        keys_33063 = incl_index.loc['33063', 'key']
        assert 'logement-hebergement--sinformer-sur-les-demarches-liees-a-lacces-au-logement' in keys_33063
        assert 'cat--svc_test' in keys_33063
        
        # 4. Check Config Defaults Existence
        # Verify that the keys in DEFAULT_SOCLE_ADMIN are present in our mock ref (simulating they are valid)
        # We mocked 3 keys in ref_inclusion. 
        # config.DEFAULT_SOCLE_ADMIN has 3 keys.
        # Let's check if the first one matches what we mocked.
        assert cfg.DEFAULT_SOCLE_ADMIN[0] in mock_ref_inclusion['Nom'].values
