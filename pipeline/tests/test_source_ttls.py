"""The source catalog is the single authority for cache freshness policies."""

from pipeline.common import CONFIG_FILE, load_config


def test_every_catalog_source_declares_a_positive_ttl():
    config = load_config(CONFIG_FILE)

    for section in ("sources", "local_files"):
        for source_key, source in config[section].items():
            ttl_days = source.get("ttl_days")
            assert isinstance(ttl_days, int) and not isinstance(ttl_days, bool), source_key
            assert ttl_days > 0, source_key


def test_bpe_ttl_is_declared_as_one_year():
    config = load_config(CONFIG_FILE)

    assert config["sources"]["bpe"]["ttl_days"] == 365


def test_odace_rent_supporting_tables_have_explicit_ttls():
    config = load_config(CONFIG_FILE)
    tables = config["sources"]["loyers_apparts"]["odace_tables"]

    assert tables["fact_loyer_annonce"]["ttl_days"] == 30
    assert tables["ref_logement_profil"]["ttl_days"] == 365
