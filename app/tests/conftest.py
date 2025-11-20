import pytest

def pytest_addoption(parser):
    parser.addoption(
        "--update-snapshots", action="store_true", default=False, help="Update snapshot files."
    )
