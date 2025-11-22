# Enhance Test Coverage & Strategy (Item 7)

The user wants to improve the quality and coverage of the test suite, specifically for the core logic in `scoring.py`. The goal is a "full refactoring" of the test setup to follow best practices.

## User Review Required

> [!NOTE]
> I will be moving shared fixtures to `conftest.py` and rewriting `test_scoring.py` to be more modular and comprehensive. I will also ensure strict separation between unit tests (fast, mocked) and E2E tests (slower, integration).

## Proposed Changes

### Configuration
#### [MODIFY] [pytest.ini](file:///Users/jacques/dev/13_odis_stream2/pytest.ini)
- Add `unit` marker to distinguish unit tests from E2E tests.

### Test Infrastructure
#### [MODIFY] [app/tests/conftest.py](file:///Users/jacques/dev/13_odis_stream2/app/tests/conftest.py)
- Move `sample_data`, `sample_scores_cat`, `sample_incl_index`, and `default_config` fixtures from `test_scoring.py` to here.
- Ensure these fixtures return deep copies to prevent state leakage between tests.
- Add a `mock_config` fixture that returns a flexible configuration object.

### Unit Tests
#### [MODIFY] [app/tests/test_scoring.py](file:///Users/jacques/dev/13_odis_stream2/app/tests/test_scoring.py)
- Rewrite to use the shared fixtures from `conftest.py`.
- Group tests by function (e.g., `TestFilterCommunes`, `TestComputeOdisScore`).
- Use `pytest.mark.parametrize` to cover edge cases (empty inputs, boundary values).
- Mock external calls if any (though `scoring.py` is mostly pure logic).
- Ensure 100% coverage of `scoring.py` logic (excluding the `run_scoring_pipeline` orchestration which is better tested in integration/E2E, though we can unit test the flow with mocks).

#### [NEW] [app/tests/test_data_loader.py](file:///Users/jacques/dev/13_odis_stream2/app/tests/test_data_loader.py)
- Create basic unit tests for `data_loader.py` to ensure data loading functions handle errors gracefully (mocking file I/O).

## Verification Plan

### Automated Tests
- Run `pytest -m unit` to verify unit tests pass and are fast.
- Run `pytest -m e2e` to ensure no regression in end-to-end functionality.
- Check coverage (optional, if `pytest-cov` is installed, otherwise just manual verification of cases).
