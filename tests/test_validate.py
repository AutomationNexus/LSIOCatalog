"""End-to-end generation + validation against the pinned CS renderer.

These tests require the pinned ``cognitivesystems`` renderer to be importable; when it
is not installed (e.g. a public CI leg without the private repo), they are skipped
rather than failing — the converter/rejection unit tests still fully cover the pure
logic. No network is used: generation runs entirely from the vendored fixtures.
"""
from __future__ import annotations

import json
import pathlib

import pytest

cognitivesystems = pytest.importorskip(
    "cognitivesystems", reason="pinned CS renderer not installed; see README 'Validation'")

from lsio_catalog_gen.convert import convert  # noqa: E402
from lsio_catalog_gen.generate import generate  # noqa: E402
from lsio_catalog_gen.validate import validate_service  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "readme-vars"
PROOF_APPS = ["sonarr", "radarr", "bazarr", "prowlarr", "lidarr", "plex"]


def test_generated_sonarr_renders_under_pinned_cs_renderer():
    import yaml
    defn = convert(yaml.safe_load((FIXTURES / "sonarr.yml").read_text()), app="sonarr")
    validate_service("sonarr", defn)  # raises if it does not render cleanly


def test_full_generation_pipeline_offline(tmp_path):
    import yaml
    out = tmp_path / "catalog"
    result = generate(PROOF_APPS, out, fixtures_dir=FIXTURES, validate=True)

    # The five clean arr-style apps convert + validate; plex is rejected (host net).
    assert set(result.accepted) == {"sonarr", "radarr", "bazarr", "prowlarr", "lidarr"}
    assert any(r["app"] == "plex" and "networking" in r["reason"] for r in result.rejected)
    assert result.tree_validated is True

    # Manifest satisfies the CS id:lsio contract.
    manifest = yaml.safe_load((out / "catalog.yml").read_text())
    assert manifest["id"] == "lsio"
    assert manifest["format_version"] == 1

    # Every accepted app has a service.yml and a provenance record.
    prov = json.loads((out / "provenance.json").read_text())
    for app in result.accepted:
        assert (out / app / "service.yml").is_file()
        assert prov["services"][app]["source_repo"].endswith(f"docker-{app}")
    assert prov["cs_renderer_pinned_ref"]

    # Rejections are recorded machine-readably.
    rejected = json.loads((out / "rejected.json").read_text())
    assert any(r["app"] == "plex" for r in rejected["rejected"])


def test_generated_tree_passes_cs_fetcher_validators(tmp_path):
    """The tree passes the EXACT validators CS's git-catalog fetcher runs."""
    from cognitivesystems import catalog_fetch
    out = tmp_path / "catalog"
    generate(["sonarr", "radarr"], out, fixtures_dir=FIXTURES, validate=True)
    # These are what CS runs on a fetched git catalog before promoting it.
    catalog_fetch.validate_manifest(out, "lsio")
    catalog_fetch.validate_tree(out)
    catalog_fetch.dry_run_all(out)
