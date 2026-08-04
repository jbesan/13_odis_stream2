import pandas as pd
from unittest.mock import MagicMock, patch
from pipeline.salesforce_ingest import (
    aggregate_by_bassin_de_vie,
    clean_postal_code,
    aggregate_salesforce_data,
    get_salesforce_status,
    run_salesforce_ingest,
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
    assert "lead_ids" not in df.columns
    assert "contact_ids" not in df.columns

    row_69001 = df[df["code_postal"] == "69001"].iloc[0]
    assert row_69001["lead_count"] == 0
    assert row_69001["contact_count"] == 1
    assert row_69001["total_jaccueille_count"] == 1


def test_get_salesforce_status(tmp_path, monkeypatch):
    """Test 7-day TTL status check."""
    mock_output = tmp_path / "salesforce_jaccueille.parquet"
    mock_bdv_output = tmp_path / "salesforce_jaccueille_bdv.parquet"
    monkeypatch.setattr("pipeline.salesforce_ingest.OUTPUT_PATH", mock_output)
    monkeypatch.setattr("pipeline.salesforce_ingest.OUTPUT_BDV_PATH", mock_bdv_output)

    status_before = get_salesforce_status()
    assert not status_before["exists"]
    assert not status_before["within_ttl"]

    # The postal-code aggregate is the cache validity marker.  The BDV output
    # is candidate-specific and is not reused as a source cache.
    df = pd.DataFrame([{"code_postal": "75015", "total_jaccueille_count": 1}])
    df.to_parquet(mock_output)

    status_after = get_salesforce_status()
    assert status_after["exists"]
    assert status_after["within_ttl"]
    assert status_after["ttl_days"] == 7


def test_get_salesforce_status_uses_catalog_ttl(tmp_path, monkeypatch):
    mock_output = tmp_path / "salesforce_jaccueille.parquet"
    pd.DataFrame([{"code_postal": "75015", "total_jaccueille_count": 1}]).to_parquet(
        mock_output
    )
    monkeypatch.setattr("pipeline.salesforce_ingest.OUTPUT_PATH", mock_output)
    monkeypatch.setattr(
        "pipeline.salesforce_ingest.load_config",
        lambda _: {"local_files": {"salesforce_jaccueille": {"ttl_days": 3}}},
    )

    assert get_salesforce_status()["ttl_days"] == 3


def test_salesforce_bdv_release_omits_record_ids(tmp_path, monkeypatch):
    """The published application dataset contains counts, not Salesforce IDs."""
    postal_codes_path = tmp_path / "codes_postaux.parquet"
    communes_path = tmp_path / "odis_communes.parquet"
    pd.DataFrame({"code_postal": ["75015"], "codgeo": ["75115"]}).to_parquet(
        postal_codes_path
    )
    pd.DataFrame({"codgeo": ["75115"], "bassin_de_vie": ["75056"]}).to_parquet(
        communes_path
    )
    bdv = aggregate_by_bassin_de_vie(
        pd.DataFrame(
            {
                "code_postal": ["75015"],
                "lead_count": [2],
                "contact_count": [3],
                "total_jaccueille_count": [5],
            }
        ),
        postal_codes_path=postal_codes_path,
        communes_path=communes_path,
    )

    assert bdv.to_dict("records") == [
        {
            "bassin_de_vie": "75056",
            "lead_count": 2,
            "contact_count": 3,
            "total_jaccueille_count": 5,
            "codes_postaux": '["75015"]',
        }
    ]


def test_salesforce_cleaner_builds_the_artifact_from_candidate_references(
    tmp_path, monkeypatch
):
    """A run-scoped pipeline must publish the Salesforce artifact it validated."""
    from pipeline.ingest import clean_salesforce_jaccueille

    candidate_output_dir = tmp_path / "candidate" / "output"
    candidate_clean_dir = tmp_path / "candidate" / "clean"

    monkeypatch.setattr("pipeline.ingest.OUTPUT_DIR", candidate_output_dir)
    monkeypatch.setattr("pipeline.ingest.CLEAN_DIR", candidate_clean_dir)
    expected_output = candidate_output_dir / "salesforce_jaccueille_bdv.parquet"
    source_cache = tmp_path / "salesforce_jaccueille.parquet"
    source_cache.write_bytes(b"source cache")
    monkeypatch.setattr(
        "pipeline.salesforce_ingest.get_salesforce_status",
        lambda: {
            "exists": True,
            "within_ttl": True,
            "path": str(source_cache),
        },
    )
    monkeypatch.setattr(
        "pipeline.salesforce_ingest.run_salesforce_ingest",
        lambda force, **kwargs: (
            kwargs["output_bdv_path"].parent.mkdir(parents=True, exist_ok=True)
            or pd.DataFrame({"bassin_de_vie": ["75056"]}).to_parquet(
                kwargs["output_bdv_path"]
            )
            or kwargs["output_bdv_path"]
        ),
    )
    logger = MagicMock()

    clean_salesforce_jaccueille({}, logger)

    candidate_artifact = candidate_output_dir / "salesforce_jaccueille_bdv.parquet"
    assert candidate_artifact.exists()
    assert pd.read_parquet(candidate_artifact).to_dict("records") == [
        {"bassin_de_vie": "75056"}
    ]
    assert expected_output == candidate_artifact
    logger.log_source.assert_called_once()
    assert logger.log_source.call_args.args[:2] == (
        "salesforce_jaccueille",
        "CACHED",
    )
    assert logger.log_source.call_args.kwargs["observed_at"].endswith("+00:00")


@patch("pipeline.salesforce_ingest.get_salesforce_jwt_token")
@patch("pipeline.salesforce_ingest.fetch_soql_records")
def test_run_salesforce_ingest_force(mock_fetch, mock_jwt, tmp_path, monkeypatch):
    """Test pipeline run execution with mock fetch."""
    raw_path = tmp_path / "raw.parquet"
    out_path = tmp_path / "output.parquet"
    bdv_path = tmp_path / "output_bdv.parquet"
    postal_codes_path = tmp_path / "codes_postaux.parquet"
    communes_path = tmp_path / "communes.parquet"

    monkeypatch.setattr("pipeline.salesforce_ingest.RAW_OUTPUT_PATH", raw_path)
    monkeypatch.setattr("pipeline.salesforce_ingest.OUTPUT_PATH", out_path)
    monkeypatch.setattr("pipeline.salesforce_ingest.OUTPUT_BDV_PATH", bdv_path)
    pd.DataFrame({"code_postal": ["75015"], "codgeo": ["75115"]}).to_parquet(
        postal_codes_path
    )
    pd.DataFrame({"codgeo": ["75115"], "bassin_de_vie": ["75056"]}).to_parquet(
        communes_path
    )
    mock_jwt.return_value = ("fake_token", "https://fake.salesforce.com")
    mock_fetch.side_effect = [
        [{"Id": "00Q1", "PostalCode": "75015"}],  # Leads
        [{"Id": "0031", "MailingPostalCode": "75015"}],  # Contacts
    ]

    res_path = run_salesforce_ingest(
        force=True,
        output_bdv_path=bdv_path,
        postal_codes_path=postal_codes_path,
        communes_path=communes_path,
    )
    assert res_path == bdv_path
    assert out_path.exists()
    assert raw_path.exists()
    assert bdv_path.exists()

    df = pd.read_parquet(out_path)
    assert len(df) == 1
    assert df.iloc[0]["code_postal"] == "75015"
    assert df.iloc[0]["total_jaccueille_count"] == 2
