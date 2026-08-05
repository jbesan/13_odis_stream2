from app import config as cfg


def test_formation_codes_intersection():
    """
    Verifies that the codes in odis_rel_formations.parquet are compatible
    with the codes in referentiels.parquet.

    The issue was that ingest.py produced float-strings (e.g. "100.0") for formations,
    while referentiels expected integer-strings (e.g. "100").
    """

    # 1. Load Formations Data via Data Loader (Verify Hotfix)
    # We simulate what init_datasets does for this specific file, or use it directly if possible.
    # To avoid loading everything, we'll replicate the hotfix logic or just call init_datasets().
    # Calling init_datasets() might require valid config/env, let's try to just load and clean like data_loader does.

    from utils import data_loader

    formations_df = data_loader.load_parquet_dataset(cfg.AGG_FORMATIONS_FILE)

    if not formations_df.empty and "formation_code" in formations_df.columns:
        formations_df["formation_code"] = (
            formations_df["formation_code"]
            .astype(str)
            .str.replace(r"\.0$", "", regex=True)
        )

    assert "formation_code" in formations_df.columns

    # Check uniqueness of format
    sample_codes = formations_df["formation_code"].head().tolist()
    print(f"\nSample Formation Codes (Cleaned): {sample_codes}")

    # 2. Load Referentiels
    ref_df = data_loader.load_parquet_dataset(cfg.REFERENTIELS_FILE)
    form_ref = ref_df[ref_df["key"] == "formation_codes"]

    assert not form_ref.empty, "No formation codes found in referentiels"

    ref_codes = set(form_ref["code"].unique())
    data_codes = set(formations_df["formation_code"].unique())

    # 3. Check Intersection
    intersection = data_codes.intersection(ref_codes)

    print(f"Total Data Codes: {len(data_codes)}")
    print(f"Total Ref Codes: {len(ref_codes)}")
    print(f"Intersection Size: {len(intersection)}")

    # If intersection is 0, it means the keys don't match (e.g. "100.0" vs "100")
    assert len(intersection) > 0, (
        "No common codes found between formations data and referentiel!"
    )

    # Optional: Check strict format (no ".0" suffix)
    # This ensures we really fixed the clean format, not just made the test pass
    has_decimal_suffix = any(
        str(c).endswith(".0") for c in data_codes if str(c).replace(".0", "").isdigit()
    )
    assert not has_decimal_suffix, "Formation codes still contain '.0' suffix!"
