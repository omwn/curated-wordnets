"""
pytest configuration for the curated-wordnets test suite.

Slow (network) tests are skipped by default.
Run them with:  pytest --run-slow
"""

import sys
from pathlib import Path

# Make scripts/ importable so tests can do `import download`
sys.path.insert(0, str(Path(__file__).parent / "scripts"))


def pytest_addoption(parser):
    parser.addoption(
        "--run-slow", action="store_true", default=False,
        help="Run slow integration tests that require network access",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow / requiring network (skip unless --run-slow)",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-slow"):
        skip = __import__("pytest").mark.skip(reason="needs --run-slow to run")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip)
