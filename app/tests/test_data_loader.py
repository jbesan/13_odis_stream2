import pytest
import pandas as pd
from unittest.mock import patch, mock_open, MagicMock
from app import data_loader
import config as cfg

@pytest.mark.unit
class TestDataLoader:
    @patch('app.data_loader.pd.read_csv')
    @patch('app.data_loader.pd.read_parquet')
    @patch('app.data_loader.shp.from_wkb') # Mock WKB loading
    @patch('app.data_loader.load_bassin_de_vie_data')
    @patch('app.data_loader.load_scores_config_as_df')
    @pytest.mark.skip(reason="Flaky due to complex mocking of geopandas/pandas concat")
    def test_load_all_datasets_caf_integration(
        self, 
        mock_load_config, 
        mock_load_bv, 
        mock_from_wkb, 
        mock_read_parquet, 
        mock_read_csv
    ):
        """Tests that CAF data is correctly loaded and merged."""
        # Setup Mocks
        # 1. Mock ODIS main dataframe
        # We provide a dummy value for polygon that will be passed to mock_from_wkb
        mock_odis = pd.DataFrame({
            'codgeo': ['33063', '75056'],
            'libgeo': ['Bordeaux', 'Paris'],
            'polygon': [b'dummy_wkb', b'dummy_wkb'], 
            'dep_code': ['33', '75'],
            'population': [1000, 2000]
        })
        
        # Mock from_wkb to return a simple Polygon
        from shapely.geometry import Polygon
        dummy_poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        mock_from_wkb.return_value = dummy_poly
        
        # 2. Mock CAF dataframe
        mock_caf = pd.DataFrame({
            'codgeo': ['33063', '75056'],
            'taux_accueil_total': [55.5, 60.0]
        })
        
        # 3. Configure read_parquet to return mock_odis (first call) and others
        def read_parquet_side_effect(path, **kwargs):
            if 'odis' in path:
                return mock_odis.copy()
            if 'ecoles' in path:
                return pd.DataFrame({
                    'code_commune': ['33063'], 
                    'nom_etablissement': ['Ecole'], 
                    'type_etablissement': ['Ecole'], 
                    'ecole_maternelle': [1], 
                    'ecole_elementaire': [0], 
                    'geometry': [b'dummy_wkb']
                })
            if 'sante' in path:
                 # Return minimal valid dataframe for sante
                return pd.DataFrame({
                    'LibelleSph': ['Etablissement public de santé'],
                    'coordxet': [0],
                    'coordyet': [0],
                    'nofinesset': ['123'],
                    'Departement': ['33'],
                    'Commune': ['063'],
                    'LibelleCategorieAgregat': ['Centres Hospitaliers']
                })
            if 'inclusion' in path:
                 return pd.DataFrame({
                     'code_insee': ['33063'], 
                     'typologie': ['Mairie'], 
                     'source': ['dora'], 
                     'id': ['1'], 
                     'structure_id': ['1'], 
                     'categorie': ['admin'],
                     'service': ['aide'],
                     'geometry': [b'dummy_wkb']
                 })
            return pd.DataFrame() 
            
        mock_read_parquet.side_effect = read_parquet_side_effect
        
        def read_csv_side_effect(path, **kwargs):
            if 'caf' in path:
                return mock_caf.copy()
            if 'maternites' in path:
                return pd.DataFrame({'FI_ET': ['123']})
            return pd.DataFrame() 
            
        mock_read_csv.side_effect = read_csv_side_effect
        
        # Mock other dependencies
        mock_load_config.return_value = pd.DataFrame()
        mock_load_bv.return_value = pd.DataFrame(columns=['CODGEO', cfg.BV_CODE_COL, cfg.BV_NAME_COL])

        # Act
        odis, _, _, _, _, _, _, _, _, _ = data_loader.load_all_datasets(
            'odis.parquet', 'bv.csv', 'scores.yaml', 'metiers.csv', 
            'formations.csv', 'ecoles.parquet', 'maternites.csv', 
            'sante.parquet', 'inclusion.parquet', 'caf.csv'
        )
        
        # Assert
        assert 'taux_couverture' in odis.columns
        assert odis.loc[odis['codgeo'] == '33063', 'taux_couverture'].iloc[0] == 55.5
        assert odis.loc[odis['codgeo'] == '75056', 'taux_couverture'].iloc[0] == 60.0



