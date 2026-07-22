"""Orchestrate fetch → convert → validate → write for the ``lsio`` catalog tree.

Produces a CS-consumable catalog directory whose ROOT is:

.. code-block:: text

    <out>/catalog.yml            # id: lsio manifest (CS validate_manifest contract)
    <out>/<app>/service.yml      # one validated service per accepted app
    <out>/provenance.json        # source repo + commit for every ACCEPTED app
    <out>/rejected.json          # app + reason for every REJECTED app

Service directories are DIRECT children of the tree root (CS's ``validate_tree`` and
``load_catalog`` only discover ``<root>/<name>/service.yml``) — this is why the tree is
published separately from the generator source (whose ``.github``/``src`` roots would
otherwise trip CS's ``NAME_RE`` directory check). See the README "Repository layout".
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import pathlib

import yaml

from . import CATALOG_ID, CS_PINNED_REF, __version__
from . import fetch as _fetch
from .convert import RejectionError, convert
from .validate import (
    ServiceValidationError,
    validate_service,
    validate_tree,
)

_MANIFEST_DISPLAY_NAME = "LinuxServer.io"


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 second-precision string."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclasses.dataclass
class GenerationResult:
    """The outcome of one :func:`generate` run.

    Attributes
    ----------
    accepted : list of str
        Slugs of apps converted, validated, and written.
    rejected : list of dict
        ``{app, source_repo, reason}`` records for skipped apps.
    provenance : dict
        The provenance document written into the tree.
    tree_validated : bool
        Whether the whole-tree CS validation gate ran and passed.
    """

    accepted: list
    rejected: list
    provenance: dict
    tree_validated: bool = False


def _load_input(app: str, *, fixtures_dir: pathlib.Path | None, ref: str) -> tuple[dict, str, str | None, str]:
    """Obtain one app's metadata, source URL, source commit, and resolved ref.

    Parameters
    ----------
    app : str
        The LSIO image name.
    fixtures_dir : pathlib.Path or None
        When set, read ``<fixtures_dir>/<app>.yml`` instead of the network (offline
        mode used by tests and reproducible builds).
    ref : str
        The upstream ref to read/resolve when online; the ``auto`` sentinel resolves
        the repo's default branch (LSIO repos differ: ``master`` vs ``main``).

    Returns
    -------
    (dict, str, str or None, str)
        The parsed metadata, the source location, the resolved commit SHA (or ``None``
        when offline/unresolved), and the concrete ref actually used.
    """
    if fixtures_dir is not None:
        path = pathlib.Path(fixtures_dir) / f"{app}.yml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise _fetch.FetchError(f"{app}: fixture is not a mapping")
        return data, path.as_uri(), None, "vendored-fixture"
    concrete = ref
    if ref in (None, _fetch._AUTO_REF):
        concrete = _fetch.resolve_default_ref(app) or _fetch._FALLBACK_REF
    data, url = _fetch.fetch_readme_vars(app, ref=concrete)
    return data, url, _fetch.resolve_commit(app, ref=concrete), concrete


def _write_service(out_dir: pathlib.Path, slug: str, defn: dict) -> None:
    """Serialize one service definition to ``<out>/<slug>/service.yml``.

    Parameters
    ----------
    out_dir : pathlib.Path
        Catalog tree root.
    slug : str
        Service directory name.
    defn : dict
        The CS service definition (field order preserved).
    """
    svc_dir = out_dir / slug
    svc_dir.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(defn, sort_keys=False, allow_unicode=True, default_flow_style=False)
    (svc_dir / "service.yml").write_text(text, encoding="utf-8")


def _write_manifest(out_dir: pathlib.Path) -> None:
    """Write the ``catalog.yml`` manifest satisfying CS's ``validate_manifest`` contract.

    Parameters
    ----------
    out_dir : pathlib.Path
        Catalog tree root.
    """
    manifest = {
        "id": CATALOG_ID,
        "format_version": 1,
        "display_name": _MANIFEST_DISPLAY_NAME,
        "description": (
            "Community catalog of LinuxServer.io images, generated from each image's "
            "upstream readme-vars.yml and expressed through CognitiveSystems profile "
            "placeholders."
        ),
        "generator": f"lsio_catalog_gen {__version__}",
    }
    (out_dir / "catalog.yml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")


def generate(apps, out_dir, *, fixtures_dir=None, ref="auto", validate=True) -> GenerationResult:
    """Generate the ``lsio`` catalog tree for ``apps`` into ``out_dir``.

    For each app: load metadata, convert (safe subset), render-validate with the pinned
    CS renderer, and — only if it passes — write its ``service.yml``. A rejected or
    invalid app is recorded (never emitted lossily). After all apps, the manifest,
    provenance, and rejection report are written, and (when ``validate`` is set and at
    least one app was accepted) CS's whole-tree gate runs as a final check.

    Parameters
    ----------
    apps : iterable of str
        LSIO image names to convert.
    out_dir : pathlib.Path or str
        Destination catalog tree root (created/overwritten).
    fixtures_dir : pathlib.Path or str or None, optional
        Offline metadata source; when set, no network is used.
    ref : str, default: "auto"
        Upstream ref to read/resolve when online; ``"auto"`` detects each repo's
        default branch (LSIO repos differ: ``master`` vs ``main``).
    validate : bool, default: True
        Whether to render-validate with the pinned CS renderer. When ``False`` (only
        for environments without the private renderer), services are written unvalidated
        and ``tree_validated`` stays ``False`` — such output must NOT be tagged.

    Returns
    -------
    GenerationResult
        The accepted/rejected breakdown and the provenance document.

    Raises
    ------
    RendererUnavailable
        If ``validate`` is set but the pinned CS renderer is not installed.
    ServiceValidationError
        If the whole-tree CS validation gate fails after per-service checks passed
        (a generator bug — a tree that should be internally consistent is not).
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    accepted: list = []
    rejected: list = []
    provenance_services: dict = {}

    for app in apps:
        source_repo = _fetch.source_repo(app)
        try:
            rv, source_url, commit, used_ref = _load_input(
                app, fixtures_dir=_as_path(fixtures_dir), ref=ref)
        except _fetch.FetchError as e:
            rejected.append({"app": app, "source_repo": source_repo, "reason": f"fetch failed: {e}"})
            continue

        try:
            defn = convert(rv, app=app)
        except RejectionError as e:
            rejected.append({"app": app, "source_repo": source_repo, "reason": str(e)})
            continue

        slug = defn["subdomain"]
        if validate:
            try:
                validate_service(slug, defn)
            except ServiceValidationError as e:
                rejected.append({
                    "app": app, "source_repo": source_repo,
                    "reason": f"failed CS render validation: {e}",
                })
                continue

        _write_service(out_dir, slug, defn)
        accepted.append(slug)
        provenance_services[slug] = {
            "source_repo": source_repo,
            "source_file": "readme-vars.yml",
            "source_url": source_url,
            "ref": used_ref,
            "commit": commit,
        }

    provenance = {
        "generator": f"lsio_catalog_gen {__version__}",
        "catalog_id": CATALOG_ID,
        "cs_renderer_pinned_ref": CS_PINNED_REF,
        "generated_at": _utcnow(),
        "validated": bool(validate),
        "services": provenance_services,
    }

    _write_manifest(out_dir)
    _write_json(out_dir / "provenance.json", provenance)
    _write_json(out_dir / "rejected.json", {"generated_at": provenance["generated_at"], "rejected": rejected})

    result = GenerationResult(accepted=accepted, rejected=rejected, provenance=provenance)
    if validate and accepted:
        validate_tree(out_dir, source_id=CATALOG_ID)
        result.tree_validated = True
    return result


def _as_path(value) -> pathlib.Path | None:
    """Coerce an optional path-like to :class:`pathlib.Path` (``None`` passes through)."""
    return None if value is None else pathlib.Path(value)


def _write_json(path: pathlib.Path, data: dict) -> None:
    """Write a JSON document with stable key ordering and a trailing newline.

    Parameters
    ----------
    path : pathlib.Path
        Destination file.
    data : dict
        JSON-serializable content.
    """
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
