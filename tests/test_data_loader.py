import pytest
import pandas as pd
import geopandas as gpd
from unittest.mock import MagicMock, patch
from utils import data_loader
import config as cfg

@pytest.fixture
def mock_parquet_data():
    """Mocks the parquet data for testing."""
    # Mock ODIS
    odis_df = pd.DataFrame({
        'codgeo': ['01001', '01002'],
        'libgeo': ['Commune A', 'Commune B'],
        'population': [1000, 2000],
        'dep_code': ['01', '01'],
        'reg_code': ['84', '84'],
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
        'type': ['Ecole', 'Hopital', 'CAF'],
        'name': ['Ecole A', 'Hopital B', 'CAF C'],
        'lat': [45.0, 45.1, 45.2],
        'lon': [5.0, 5.1, 5.2],
        'codgeo': ['01001', '01001', '01002']
    })
    
    # Mock Referentiels
    ref_df = pd.DataFrame({
        'key': ['rome_codes', 'rome_codes'],
        'code': ['M1805', 'M1801'],
        'label': ['Dev', 'Test']
    })
    
    return odis_df, pois_df, ref_df

@patch('utils.data_loader.fetch_jaccueille_data_bq')
@patch('utils.data_loader.os.path.exists')
@patch('utils.data_loader.pd.read_parquet')
@patch('config.get_data_path')
def test_init_datasets(mock_get_data_path, mock_read_parquet, mock_exists, mock_fetch_jaccueille, mock_parquet_data):
    """Tests the initialization of datasets."""
    mock_exists.return_value = True
    mock_get_data_path.return_value = '/mock/path'
    mock_fetch_jaccueille.return_value = pd.DataFrame(columns=['bassin_de_vie', 'heb_accueillants_count'])
    odis_df, pois_df, ref_df = mock_parquet_data
    
    # Configure mock side effects for different files
    def side_effect(path, columns=None, **kwargs):
        if 'odis_communes' in path:
            return odis_df
        elif 'pois' in path:
            return pois_df
        elif 'referentiels' in path:
            return ref_df
        elif 'vertical' in path or 'associations' in path:
            return pd.DataFrame()
        elif 'global_stats' in path: # Added fallback for global_stats
            return pd.DataFrame() # Return an empty DataFrame as a fallback
        return pd.DataFrame()
        
    mock_read_parquet.side_effect = side_effect
    
    # Run init_datasets
    data = data_loader.load_all_data_raw()
    
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
    assert 'rome_index' in data
    assert not data['rome_index'].empty
    assert 'Dev' in data['rome_index']['label'].values

@patch('google.cloud.bigquery.Client')
@patch('utils.data_loader.pd.read_parquet')
@patch('utils.data_loader.os.path.exists')
def test_fetch_jaccueille_data_bq(mock_exists, mock_read_parquet, mock_bq_client_class):
    """Tests that J'Accueille data is fetched from BQ when cache is missing."""
    mock_exists.return_value = False # No cache
    mock_bq_client = mock_bq_client_class.return_value
    mock_query_job = MagicMock()
    mock_bq_client.query.return_value = mock_query_job
    
    mock_df = pd.DataFrame({'bassin_de_vie': ['BV1'], 'heb_accueillants_count': [5]})
    mock_query_job.to_dataframe.return_value = mock_df
    
    # Act
    df = data_loader._fetch_jaccueille_data_bq_logic()
    
    # Assert
    assert mock_bq_client.query.called
    assert mock_query_job.to_dataframe.called
    # Check that create_bqstorage_client=True was passed
    args, kwargs = mock_query_job.to_dataframe.call_args
    assert kwargs.get('create_bqstorage_client') is True
    assert df.loc[0, 'heb_accueillants_count'] == 5
