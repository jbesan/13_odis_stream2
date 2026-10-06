import pandas as pd
import pytest
from unittest.mock import MagicMock, patch
from pipeline.salesforce_ingest import (
    aggregate_by_bassin_de_vie,
    clean_postal_code,
    aggregate_salesforce_data,
    fetch_and_aggregate_salesforce,
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


def test_salesforce_cleaner_produces_clean_postal_code_artifact(
    tmp_path, monkeypatch
):
    """The ingestion cleaner must produce the postal-code aggregate in CLEAN_DIR."""
    from pipeline.ingest import clean_salesforce_jaccueille

    candidate_clean_dir = tmp_path / "candidate" / "clean"
    monkeypatch.setattr("pipeline.ingest.CLEAN_DIR", candidate_clean_dir)
    source_cache = tmp_path / "source_cache.parquet"
    pd.DataFrame(
        [
            {
                "code_postal": "75015",
                "lead_count": 2,
                "contact_count": 1,
                "total_jaccueille_count": 3,
            }
        ]
    ).to_parquet(source_cache)
    monkeypatch.setattr(
        "pipeline.salesforce_ingest.OUTPUT_PATH",
        source_cache,
    )
    monkeypatch.setattr(
        "pipeline.salesforce_ingest.get_salesforce_status",
        lambda *args: {
            "exists": True,
            "within_ttl": True,
            "path": str(source_cache),
        },
    )
    monkeypatch.setattr(
        "pipeline.salesforce_ingest.fetch_and_aggregate_salesforce",
        lambda force, **kwargs: (
            kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
            or pd.DataFrame(
                [
                    {
                        "code_postal": "75015",
                        "lead_count": 2,
                        "contact_count": 1,
                        "total_jaccueille_count": 3,
                    }
                ]
            ).to_parquet(kwargs["output_path"])
        ),
    )
    logger = MagicMock()

    clean_salesforce_jaccueille({}, logger)

    clean_artifact = candidate_clean_dir / "salesforce_jaccueille.parquet"
    assert clean_artifact.exists()
    df_clean = pd.read_parquet(clean_artifact)
    assert df_clean.to_dict("records") == [
        {
            "code_postal": "75015",
            "lead_count": 2,
            "contact_count": 1,
            "total_jaccueille_count": 3,
        }
    ]
    logger.log_source.assert_called_once()
    assert logger.log_source.call_args.args[:2] == (
        "salesforce_jaccueille",
        "CACHED",
    )
    assert logger.log_source.call_args.kwargs["observed_at"].endswith("+00:00")


def test_build_salesforce_jaccueille_bdv(tmp_path, monkeypatch):
    """The build step aggregates clean postal-code data to BDV in OUTPUT_DIR."""
    from pipeline.build import build_salesforce_jaccueille_bdv
    import pipeline.build as build_module

    candidate_clean_dir = tmp_path / "candidate" / "clean"
    candidate_output_dir = tmp_path / "candidate" / "output"
    candidate_clean_dir.mkdir(parents=True)
    candidate_output_dir.mkdir(parents=True)

    monkeypatch.setattr(build_module, "CLEAN_DIR", candidate_clean_dir)
    monkeypatch.setattr(build_module, "OUTPUT_DIR", candidate_output_dir)

    # Prepare inputs in CLEAN_DIR
    pd.DataFrame(
        [
            {
                "code_postal": "75015",
                "lead_count": 2,
                "contact_count": 1,
                "total_jaccueille_count": 3,
            }
        ]
    ).to_parquet(candidate_clean_dir / "salesforce_jaccueille.parquet")

    pd.DataFrame({"code_postal": ["75015"], "codgeo": ["75115"]}).to_parquet(
        candidate_clean_dir / "codes_postaux.parquet"
    )

    # Prepare communes in OUTPUT_DIR
    pd.DataFrame({"codgeo": ["75115"], "bassin_de_vie": ["75056"]}).to_parquet(
        candidate_output_dir / "odis_communes_pre.parquet"
    )

    logger = MagicMock()
    build_salesforce_jaccueille_bdv({}, logger)

    expected_output = candidate_output_dir / "salesforce_jaccueille_bdv.parquet"
    assert expected_output.exists()
    df_bdv = pd.read_parquet(expected_output)
    assert len(df_bdv) == 1
    assert df_bdv.iloc[0]["bassin_de_vie"] == "75056"
    assert df_bdv.iloc[0]["lead_count"] == 2
    assert df_bdv.iloc[0]["contact_count"] == 1
    assert df_bdv.iloc[0]["total_jaccueille_count"] == 3


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


def test_build_salesforce_jaccueille_bdv_cli_integration(tmp_path, monkeypatch):
    """Test build.py CLI execution of salesforce_jaccueille_bdv step."""
    from pipeline.build import main as build_main
    import pipeline.build as build_module

    clean_dir = tmp_path / "clean"
    output_dir = tmp_path / "output"
    clean_dir.mkdir()
    output_dir.mkdir()

    monkeypatch.setattr(build_module, "CLEAN_DIR", clean_dir)
    monkeypatch.setattr(build_module, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(build_module, "STATUS_FILE", tmp_path / "status.json")

    pd.DataFrame(
        [
            {
                "code_postal": "31100",
                "lead_count": 5,
                "contact_count": 0,
                "total_jaccueille_count": 5,
            }
        ]
    ).to_parquet(clean_dir / "salesforce_jaccueille.parquet")
    pd.DataFrame({"code_postal": ["31100"], "codgeo": ["31555"]}).to_parquet(
        clean_dir / "codes_postaux.parquet"
    )
    pd.DataFrame({"codgeo": ["31555"], "bassin_de_vie": ["31555"]}).to_parquet(
        output_dir / "odis_communes_pre.parquet"
    )

    build_main(["--steps", "salesforce_jaccueille_bdv"])

    out_file = output_dir / "salesforce_jaccueille_bdv.parquet"
    assert out_file.exists()
    df = pd.read_parquet(out_file)
    assert len(df) == 1
    assert df.iloc[0]["bassin_de_vie"] == "31555"
    assert df.iloc[0]["total_jaccueille_count"] == 5


def test_build_salesforce_jaccueille_bdv_missing_clean_file_fails(tmp_path, monkeypatch):
    """Missing clean salesforce parquet must fail the build step."""
    from pipeline.build import build_salesforce_jaccueille_bdv
    import pipeline.build as build_module
    from pipeline.run_context import PipelineRunError

    clean_dir = tmp_path / "clean"
    output_dir = tmp_path / "output"
    clean_dir.mkdir()
    output_dir.mkdir()

    monkeypatch.setattr(build_module, "CLEAN_DIR", clean_dir)
    monkeypatch.setattr(build_module, "OUTPUT_DIR", output_dir)

    logger = MagicMock()
    with pytest.raises(PipelineRunError, match="salesforce_jaccueille_bdv"):
        build_salesforce_jaccueille_bdv({}, logger)


def test_fetch_and_aggregate_salesforce_uses_cache(tmp_path, monkeypatch):
    """When valid local cache exists, fetch_and_aggregate_salesforce reuses it without API calls."""
    cache_path = tmp_path / "clean_cached.parquet"
    pd.DataFrame(
        [
            {
                "code_postal": "13001",
                "lead_count": 1,
                "contact_count": 1,
                "total_jaccueille_count": 2,
            }
        ]
    ).to_parquet(cache_path)
    monkeypatch.setattr("pipeline.salesforce_ingest.OUTPUT_PATH", cache_path)

    out_target = tmp_path / "candidate_clean" / "salesforce_jaccueille.parquet"
    df = fetch_and_aggregate_salesforce(force=False, output_path=out_target)
    assert len(df) == 1
    assert df.iloc[0]["code_postal"] == "13001"
    assert out_target.exists()


def test_build_bassins_de_vie_isolated_step_reuses_existing_communes(tmp_path, monkeypatch):
    """Running --steps bassins_de_vie when odis_communes_pre.parquet exists must not fail with NoneType."""
    from shapely.geometry import Polygon
    import shapely
    from pipeline.build import main as build_main
    import pipeline.build as build_module

    clean_dir = tmp_path / "clean"
    output_dir = tmp_path / "output"
    clean_dir.mkdir()
    output_dir.mkdir()

    monkeypatch.setattr(build_module, "CLEAN_DIR", clean_dir)
    monkeypatch.setattr(build_module, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(build_module, "STATUS_FILE", tmp_path / "status.json")

    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    poly_wkb = shapely.to_wkb(poly)
    pd.DataFrame(
        {
            "codgeo": ["01001"],
            "bassin_de_vie": ["01001"],
            "polygon": [poly_wkb],
            "population": [100],
        }
    ).to_parquet(output_dir / "odis_communes_pre.parquet")

    build_main(["--steps", "bassins_de_vie"])

    out_file = output_dir / "odis_bassins_de_vie.parquet"
    assert out_file.exists()
    df = pd.read_parquet(out_file)
    assert len(df) == 1
    assert df.iloc[0]["bassin_de_vie"] == "01001"


def test_build_salesforce_jaccueille_alias_cli(tmp_path, monkeypatch):
    """Running --steps salesforce_jaccueille (alias) executes build_salesforce_jaccueille_bdv."""
    from pipeline.build import main as build_main
    import pipeline.build as build_module

    clean_dir = tmp_path / "clean"
    output_dir = tmp_path / "output"
    clean_dir.mkdir()
    output_dir.mkdir()

    monkeypatch.setattr(build_module, "CLEAN_DIR", clean_dir)
    monkeypatch.setattr(build_module, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(build_module, "STATUS_FILE", tmp_path / "status.json")

    pd.DataFrame(
        [
            {
                "code_postal": "31100",
                "lead_count": 3,
                "contact_count": 2,
                "total_jaccueille_count": 5,
            }
        ]
    ).to_parquet(clean_dir / "salesforce_jaccueille.parquet")
    pd.DataFrame({"code_postal": ["31100"], "codgeo": ["31555"]}).to_parquet(
        clean_dir / "codes_postaux.parquet"
    )
    pd.DataFrame({"codgeo": ["31555"], "bassin_de_vie": ["31555"]}).to_parquet(
        output_dir / "odis_communes_pre.parquet"
    )

    build_main(["--steps", "salesforce_jaccueille"])

    out_file = output_dir / "salesforce_jaccueille_bdv.parquet"
    assert out_file.exists()


def test_build_salesforce_jaccueille_bdv_fallback_to_odis_communes(tmp_path, monkeypatch):
    """When odis_communes_pre.parquet is absent but odis_communes.parquet exists, step uses it."""
    from pipeline.build import build_salesforce_jaccueille_bdv
    import pipeline.build as build_module

    clean_dir = tmp_path / "clean"
    output_dir = tmp_path / "output"
    clean_dir.mkdir()
    output_dir.mkdir()

    monkeypatch.setattr(build_module, "CLEAN_DIR", clean_dir)
    monkeypatch.setattr(build_module, "OUTPUT_DIR", output_dir)

    pd.DataFrame(
        [
            {
                "code_postal": "69001",
                "lead_count": 1,
                "contact_count": 0,
                "total_jaccueille_count": 1,
            }
        ]
    ).to_parquet(clean_dir / "salesforce_jaccueille.parquet")
    pd.DataFrame({"code_postal": ["69001"], "codgeo": ["69381"]}).to_parquet(
        clean_dir / "codes_postaux.parquet"
    )
    # Only odis_communes.parquet present (not odis_communes_pre.parquet)
    pd.DataFrame({"codgeo": ["69381"], "bassin_de_vie": ["69123"]}).to_parquet(
        output_dir / "odis_communes.parquet"
    )

    logger = MagicMock()
    build_salesforce_jaccueille_bdv({}, logger)

    out_file = output_dir / "salesforce_jaccueille_bdv.parquet"
    assert out_file.exists()
    df = pd.read_parquet(out_file)
    assert len(df) == 1
    assert df.iloc[0]["bassin_de_vie"] == "69123"


def test_aggregate_by_bassin_de_vie_edge_cases(tmp_path):
    """Test integer postal codes normalization, missing columns, and empty inputs."""
    # Integer postal codes (e.g. 1000 -> "01000")
    df_sf = pd.DataFrame(
        [
            {"code_postal": 1000, "lead_count": 2, "contact_count": 1},
        ]
    )
    df_cp = pd.DataFrame({"code_postal": ["01000"], "codgeo": ["01053"]})
    df_comm = pd.DataFrame({"codgeo": ["01053"], "bassin_de_vie": ["01053"]})

    res = aggregate_by_bassin_de_vie(
        df_sf, df_postal_codes=df_cp, df_communes=df_comm
    )
    assert len(res) == 1
    assert res.iloc[0]["bassin_de_vie"] == "01053"
    assert res.iloc[0]["lead_count"] == 2
    assert res.iloc[0]["contact_count"] == 1
    assert res.iloc[0]["total_jaccueille_count"] == 3

    # Missing column in communes returns empty DataFrame with expected columns
    bad_comm = pd.DataFrame({"codgeo": ["01053"]})
    res_bad = aggregate_by_bassin_de_vie(
        df_sf, df_postal_codes=df_cp, df_communes=bad_comm
    )
    assert res_bad.empty
    assert "bassin_de_vie" in res_bad.columns

    # Missing postal codes file returns empty DataFrame with expected columns
    res_no_cp = aggregate_by_bassin_de_vie(
        df_sf,
        postal_codes_path=tmp_path / "nonexistent_cp.parquet",
        communes_path=tmp_path / "nonexistent_comm.parquet",
    )
    assert res_no_cp.empty
    assert "bassin_de_vie" in res_no_cp.columns
