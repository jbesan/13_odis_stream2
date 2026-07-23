import json
import pandas as pd
from unittest.mock import patch
from pipeline.salesforce_ingest import (
    clean_postal_code,
    aggregate_salesforce_data,
    get_salesforce_status,
    run_salesforce_ingest,
    TTL_DAYS,
)


def test_clean_postal_code():
    """Test postal code normalization and validation logic."""
    assert clean_postal_code("75015") == "75015"
    assert clean_postal_code(75015) == "75015"
    assert clean_postal_code(" 75015 ") == "75015"
    assert clean_postal_code("6000.0") == "06000"
    assert clean_postal_code("6000") == "06000"
    assert clean_postal_code("2A000") == "2A000"
    assert clean_postal_code("2B010") == "2B010"

    # Invalid inputs
    assert clean_postal_code(None) is None
    assert clean_postal_code("") is None
    assert clean_postal_code("123") is None
    assert clean_postal_code("INVALID") is None
    assert clean_postal_code(99999) is None  # Out of range (>98999)


def test_aggregate_salesforce_data():
    """Test aggregation of Leads and Contacts by postal code."""
    sample_leads = [
        {"Id": "00Q01", "PostalCode": "75015"},
        {"Id": "00Q02", "PostalCode": "75015"},
        {"Id": "00Q03", "PostalCode": "31100"},
        {"Id": "00Q04", "PostalCode": "INVALID"},
    ]
    sample_contacts = [
        {"Id": "00301", "MailingPostalCode": "75015"},
        {"Id": "00302", "MailingPostalCode": "69001"},
    ]

    df = aggregate_salesforce_data(sample_leads, sample_contacts)

    assert len(df) == 3  # 75015, 31100, 69001
    assert set(df["code_postal"]) == {"75015", "31100", "69001"}

    row_75015 = df[df["code_postal"] == "75015"].iloc[0]
    assert row_75015["lead_count"] == 2
    assert row_75015["contact_count"] == 1
    assert row_75015["total_jaccueille_count"] == 3
    assert json.loads(row_75015["lead_ids"]) == ["00Q01", "00Q02"]
    assert json.loads(row_75015["contact_ids"]) == ["00301"]

    row_69001 = df[df["code_postal"] == "69001"].iloc[0]
    assert row_69001["lead_count"] == 0
    assert row_69001["contact_count"] == 1
    assert row_69001["total_jaccueille_count"] == 1
    assert json.loads(row_69001["lead_ids"]) == []
    assert json.loads(row_69001["contact_ids"]) == ["00302"]


def test_get_salesforce_status(tmp_path, monkeypatch):
    """Test 7-day TTL status check."""
    mock_output = tmp_path / "salesforce_jaccueille.parquet"
    monkeypatch.setattr("pipeline.salesforce_ingest.OUTPUT_PATH", mock_output)

    status_before = get_salesforce_status()
    assert not status_before["exists"]
    assert not status_before["within_ttl"]

    # Create dummy output file
    df = pd.DataFrame([{"code_postal": "75015", "total_jaccueille_count": 1}])
    df.to_parquet(mock_output)

    status_after = get_salesforce_status()
    assert status_after["exists"]
    assert status_after["within_ttl"]
    assert status_after["ttl_days"] == TTL_DAYS


@patch("pipeline.salesforce_ingest.get_salesforce_jwt_token")
@patch("pipeline.salesforce_ingest.fetch_soql_records")
def test_run_salesforce_ingest_force(mock_fetch, mock_jwt, tmp_path, monkeypatch):
    """Test pipeline run execution with mock fetch."""
    raw_path = tmp_path / "raw.parquet"
    out_path = tmp_path / "output.parquet"
    monkeypatch.setattr("pipeline.salesforce_ingest.RAW_OUTPUT_PATH", raw_path)
    monkeypatch.setattr("pipeline.salesforce_ingest.OUTPUT_PATH", out_path)

    mock_jwt.return_value = ("fake_token", "https://fake.salesforce.com")
    mock_fetch.side_effect = [
        [{"Id": "00Q1", "PostalCode": "75015"}],  # Leads
        [{"Id": "0031", "MailingPostalCode": "75015"}],  # Contacts
    ]

    res_path = run_salesforce_ingest(force=True)
    assert res_path == out_path
    assert out_path.exists()
    assert raw_path.exists()

    df = pd.read_parquet(out_path)
    assert len(df) == 1
    assert df.iloc[0]["code_postal"] == "75015"
    assert df.iloc[0]["total_jaccueille_count"] == 2
