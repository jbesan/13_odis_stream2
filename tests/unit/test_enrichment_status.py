from core.enrichment_status import (
    EnrichmentStatus,
    is_terminal_enrichment_status,
    is_terminal_refiner_status,
)


def test_provider_terminal_statuses_include_empty_and_failures():
    for status in (
        EnrichmentStatus.SUCCESS_NONEMPTY.value,
        EnrichmentStatus.SUCCESS_EMPTY.value,
        EnrichmentStatus.PARTIAL.value,
        EnrichmentStatus.ERROR.value,
        EnrichmentStatus.TIMEOUT.value,
        EnrichmentStatus.NOT_CONFIGURED.value,
    ):
        assert is_terminal_enrichment_status(status)

    assert not is_terminal_enrichment_status(EnrichmentStatus.PENDING.value)


def test_refiner_error_is_terminal_but_not_a_result_readiness_gate():
    assert is_terminal_refiner_status("done")
    assert is_terminal_refiner_status("error")
    assert not is_terminal_refiner_status("running")
