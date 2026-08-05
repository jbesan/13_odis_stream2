import pandas as pd
from utils.data_loader import _enrich_rome_index


def test_enrich_rome_index_sorting():
    # Setup
    rome_index = pd.DataFrame(
        {"label": ["Job A", "Job B", "Job C"]}, index=["A1", "B2", "C3"]
    )
    rome_index.index.name = "code"

    live_jobs_data = pd.DataFrame(
        {
            "romeCode": ["B2", "C3", "B2"],
            "total_postes": [10, 5, 20],  # Total B2 = 30, Total C3 = 5, Total A1 = 0
        }
    )

    # Execute
    enriched, top = _enrich_rome_index(rome_index, live_jobs_data)

    # Assertions
    assert enriched.loc["B2", "total_postes"] == 30
    assert enriched.loc["C3", "total_postes"] == 5
    assert enriched.loc["A1", "total_postes"] == 0

    # Sorting: B2 (30) > C3 (5) > A1 (0)
    assert enriched.index.tolist() == ["B2", "C3", "A1"]
    assert len(top) == 3  # All items since total < 200


def test_enrich_rome_index_keeps_all_rome_codes():
    # Setup: 250 items
    codes = [f"J{i:03d}" for i in range(250)]
    rome_index = pd.DataFrame({"label": codes}, index=codes)
    live_jobs_data = pd.DataFrame(
        {
            "romeCode": codes,
            "total_postes": list(range(250)),  # Sorting will be J249 to J000
        }
    )

    # Execute
    enriched, top = _enrich_rome_index(rome_index, live_jobs_data)

    # Assertions
    assert len(enriched) == 250
    assert len(top) == 250
    assert top.index[0] == "J249"
    assert top.index[-1] == "J000"


def test_enrich_rome_index_empty():
    rome_index = pd.DataFrame({"label": ["A"]}, index=["A1"])
    # Empty jobs
    enriched, top = _enrich_rome_index(rome_index, pd.DataFrame())
    assert len(enriched) == 1
    assert len(top) == 1
    assert enriched.index[0] == "A1"
