"""Shared test fixtures: vendored real readme-vars.yml samples (no network in tests)."""
from __future__ import annotations

import pathlib

import yaml

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "readme-vars"


def load_fixture(app: str) -> dict:
    """Load a vendored readme-vars fixture as a parsed mapping.

    Parameters
    ----------
    app : str
        The app whose ``tests/fixtures/readme-vars/<app>.yml`` to load.

    Returns
    -------
    dict
        The parsed metadata mapping.
    """
    return yaml.safe_load((FIXTURES / f"{app}.yml").read_text(encoding="utf-8"))
