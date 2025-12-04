import pytest
import pandas as pd
import geopandas as gpd
from unittest.mock import MagicMock, patch
import app.data_loader as data_loader
import app.config as cfg

@pytest.fixture
def mock_parquet_data():
    """Mocks the parquet data for testing."""
    # Mock ODIS
    odis_df = pd.DataFrame({
        'codgeo': ['01001', '01002'],
        'libgeo': ['Commune A', 'Commune B'],
        'population': [1000, 2000],
        'bassin_de_vie': ['BV1', 'BV1'],
        'met_scaled': [0.5, 0.6],
        'polygon': [
            b'\x01\x03\x00\x00\x00\x01\x00\x00\x00\x05\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
            b'\x01\x03\x00\x00\x00\x01\x00\x00\x00\x05\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
        ]
    })
    
    # Mock POIs
    pois_df = pd.DataFrame({
        'id': ['1', '2', '3'],
        'category': ['education', 'sante', 'incl_services'],
        'name': ['Ecole A', 'Hopital B', 'CAF C'],
        'lat': [45.0, 45.1, 45.2],
        'lon': [5.0, 5.1, 5.2]
    })
    
    # Mock Referentiels
    ref_df = pd.DataFrame({
        'key': ['fap_codes', 'fap_codes'],
        'code': ['A', 'B'],
        'label': ['Label A', 'Label B']
    })
    
    return odis_df, pois_df, ref_df

@patch('app.data_loader.pd.read_parquet')
@patch('app.data_loader.cfg.get_data_path')
def test_init_datasets(mock_get_data_path, mock_read_parquet, mock_parquet_data):
    """Tests the initialization of datasets."""
    mock_get_data_path.return_value = '/mock/path'
    odis_df, pois_df, ref_df = mock_parquet_data
    
    # Configure mock side effects for different files
    def side_effect(path, columns=None):
        if 'odis_communes' in path:
            return odis_df
        elif 'pois' in path:
            return pois_df
        elif 'referentiels' in path:
            return ref_df
        elif 'vertical' in path or 'associations' in path:
            return pd.DataFrame()
        return pd.DataFrame()
        
    mock_read_parquet.side_effect = side_effect
    
    # Run init_datasets
    data = data_loader.init_datasets()
    
    # Assertions
    assert 'odis' in data
    assert 'pois' in data
    assert 'annuaire_ecoles' in data
    assert 'annuaire_sante' in data
    
    # Check ODIS
    assert len(data['odis']) == 2
    assert 'population' in data['odis'].columns
    
    # Check POIs split
    assert len(data['annuaire_ecoles']) == 1
    assert data['annuaire_ecoles'].iloc[0]['name'] == 'Ecole A'
    
    assert len(data['annuaire_sante']) == 1
    assert data['annuaire_sante'].iloc[0]['name'] == 'Hopital B'

    # Check Referentiels
    assert 'codfap_index' in data
    assert not data['codfap_index'].empty
    assert 'Label A' in data['codfap_index']['libelle'].values
