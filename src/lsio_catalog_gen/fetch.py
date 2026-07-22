"""Network I/O: fetch an LSIO image's ``readme-vars.yml`` and resolve its source commit.

Kept separate from :mod:`lsio_catalog_gen.convert` (pure) and imported only by the
orchestrator's *online* path — the test suite exercises conversion against vendored
fixtures and never reaches this module, so unit tests never touch the network.
"""
from __future__ import annotations

import subprocess
import urllib.request

import yaml

_RAW_URL = "https://raw.githubusercontent.com/linuxserver/docker-{app}/{ref}/readme-vars.yml"
_REPO_URL = "https://github.com/linuxserver/docker-{app}"
# Sentinel meaning "resolve this repo's default branch" — LSIO repos are not uniform
# (some use `master`, some `main`), so we detect per-repo rather than assume one.
_AUTO_REF = "auto"
_FALLBACK_REF = "master"
_TIMEOUT = 30


class FetchError(Exception):
    """Raised when an image's metadata cannot be retrieved or parsed."""


def source_repo(app: str) -> str:
    """Return the canonical LSIO GitHub repo URL for an app.

    Parameters
    ----------
    app : str
        The LSIO image name (e.g. ``sonarr``).

    Returns
    -------
    str
        ``https://github.com/linuxserver/docker-<app>``.
    """
    return _REPO_URL.format(app=app)


def resolve_default_ref(app: str) -> str | None:
    """Resolve a repo's default branch name via ``git ls-remote --symref``.

    LSIO images do not all share one default branch (``master`` vs ``main``); this
    detects the actual one so the raw fetch, commit resolution, and recorded provenance
    ref all agree.

    Parameters
    ----------
    app : str
        The LSIO image name.

    Returns
    -------
    str or None
        The default branch name (e.g. ``main``), or ``None`` if it could not be
        resolved (the caller then falls back to ``master``).
    """
    try:
        out = subprocess.run(
            ["git", "ls-remote", "--symref", source_repo(app), "HEAD"],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.stdout.splitlines():
        # e.g. "ref: refs/heads/main\tHEAD"
        if line.startswith("ref:") and "refs/heads/" in line:
            return line.split("refs/heads/", 1)[1].split()[0].strip() or None
    return None


def fetch_readme_vars(app: str, *, ref: str = _FALLBACK_REF) -> tuple[dict, str]:
    """Fetch and parse one image's ``readme-vars.yml``.

    Parameters
    ----------
    app : str
        The LSIO image name.
    ref : str, default: "master"
        The git ref to read the raw file from.

    Returns
    -------
    (dict, str)
        The parsed metadata mapping and the raw source URL it was read from.

    Raises
    ------
    FetchError
        On any network error, non-YAML content, or a non-mapping document.
    """
    url = _RAW_URL.format(app=app, ref=ref)
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:  # noqa: S310 - fixed https host
            raw = resp.read().decode("utf-8")
    except Exception as e:  # noqa: BLE001 - surface every transport failure uniformly
        raise FetchError(f"failed to fetch {url}: {e}") from e
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise FetchError(f"{app}: readme-vars.yml is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise FetchError(f"{app}: readme-vars.yml is not a mapping")
    return data, url


def resolve_commit(app: str, *, ref: str = _FALLBACK_REF) -> str | None:
    """Resolve an app's source ref to a concrete commit SHA via ``git ls-remote``.

    Best-effort provenance: a transient failure returns ``None`` (the generator then
    records the symbolic ref instead of a pinned commit) rather than aborting a run.

    Parameters
    ----------
    app : str
        The LSIO image name.
    ref : str, default: "master"
        The branch ref to resolve.

    Returns
    -------
    str or None
        The 40-hex commit SHA, or ``None`` if it could not be resolved.
    """
    try:
        out = subprocess.run(
            ["git", "ls-remote", source_repo(app), f"refs/heads/{ref}"],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    sha = out.stdout.split()[0].strip()
    return sha if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha) else None
