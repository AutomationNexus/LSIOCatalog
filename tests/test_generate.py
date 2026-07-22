"""Generate-loop robustness: one bad app must never abort a fleet-scale run.

These exercise the orchestrator's per-app isolation offline (``validate=False`` so no
private CS renderer is required): a malformed/missing readme-vars, an unexpected
conversion crash, or a slug collision must be RECORDED as a rejection with a reason and
the run must CONTINUE processing the remaining apps.
"""
from __future__ import annotations

import yaml

from lsio_catalog_gen.generate import generate


def _good(project_name: str = "app") -> dict:
    return {
        "project_name": project_name,
        "param_usage_include_ports": True,
        "param_ports": [{"external_port": "8080", "internal_port": "8080", "port_desc": "web ui"}],
    }


def _write_fixtures(base, mapping: dict) -> None:
    base.mkdir(parents=True, exist_ok=True)
    for name, doc in mapping.items():
        (base / f"{name}.yml").write_text(yaml.safe_dump(doc), encoding="utf-8")


def _reasons(result, app: str) -> str:
    return next(r["reason"] for r in result.rejected if r["app"] == app)


def test_conversion_crash_is_rejected_not_raised(tmp_path):
    """A readme-vars shape that makes convert() raise (non-iterable ``param_env_vars``)
    is recorded as a rejection, and a following good app is still accepted."""
    fixtures = tmp_path / "rv"
    crasher = _good("crasher")
    crasher["param_env_vars"] = 5  # int is not iterable -> convert() would raise TypeError
    _write_fixtures(fixtures, {"crasher": crasher, "good": _good("good")})

    result = generate(
        ["crasher", "good"], tmp_path / "out", fixtures_dir=fixtures, validate=False)

    assert "good" in result.accepted
    assert "crasher" not in result.accepted
    assert "conversion error" in _reasons(result, "crasher")
    assert (tmp_path / "out" / "good" / "service.yml").is_file()


def test_missing_fixture_is_rejected_not_raised(tmp_path):
    """A missing metadata source (load failure) is recorded, not fatal to the run."""
    fixtures = tmp_path / "rv"
    _write_fixtures(fixtures, {"present": _good("present")})

    result = generate(
        ["absent", "present"], tmp_path / "out", fixtures_dir=fixtures, validate=False)

    assert "present" in result.accepted
    assert "fetch failed" in _reasons(result, "absent")


def test_duplicate_slug_is_rejected(tmp_path):
    """Two apps whose project_name resolves to the same slug: the second is rejected."""
    fixtures = tmp_path / "rv"
    _write_fixtures(fixtures, {"first": _good("dup"), "second": _good("dup")})

    result = generate(
        ["first", "second"], tmp_path / "out", fixtures_dir=fixtures, validate=False)

    assert result.accepted == ["dup"]
    assert "duplicate service slug" in _reasons(result, "second")
