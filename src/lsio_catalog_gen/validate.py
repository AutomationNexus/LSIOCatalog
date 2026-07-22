"""Validate generated services with a *pinned* CognitiveSystems renderer.

Every generated ``service.yml`` MUST render cleanly under a compatible, pinned CS
renderer before it is written/tagged — a definition that fails is rejected, never
emitted. This module calls the SAME functions CS's own ``kind: "git"`` catalog fetcher
(``cognitivesystems.catalog_fetch``) runs at refresh time, so what we validate here is
exactly what CS will validate when it consumes a reviewed tag of this repo:

- per-service: :func:`cognitivesystems.core.ops.custom._dry_run` (a full placeholder-leak
  render under a real profile) — the same gate a hand-added custom app passes;
- whole-tree: :func:`cognitivesystems.catalog_fetch.validate_manifest` /
  :func:`~cognitivesystems.catalog_fetch.validate_tree` /
  :func:`~cognitivesystems.catalog_fetch.dry_run_all`.

The CS renderer is an OPTIONAL dependency (it lives in a private repo). When it is not
installed, :class:`RendererUnavailable` is raised so the caller can surface a clear,
actionable message instead of an opaque ``ImportError``.
"""
from __future__ import annotations

import pathlib


class RendererUnavailable(Exception):
    """Raised when the pinned ``cognitivesystems`` renderer is not importable."""


class ServiceValidationError(Exception):
    """Raised when a generated service (or the whole tree) fails CS render validation."""


def _import_cs():
    """Import the pinned CS renderer modules, or raise :class:`RendererUnavailable`.

    Returns
    -------
    tuple
        ``(custom_ops, catalog_fetch)`` modules from the installed ``cognitivesystems``.

    Raises
    ------
    RendererUnavailable
        If ``cognitivesystems`` is not installed in the current environment.
    """
    try:
        from cognitivesystems import catalog_fetch
        from cognitivesystems.core.ops import custom as custom_ops
    except Exception as e:  # noqa: BLE001 - any import failure means "renderer unavailable"
        raise RendererUnavailable(
            "the pinned `cognitivesystems` renderer is not installed. Install it "
            "(e.g. `pip install -e ../CognitiveSystems` locally, or "
            "`pip install \"cognitivesystems @ git+https://github.com/AutomationNexus/"
            "CognitiveSystems@<CS_PINNED_REF>\"` in CI) before validating."
        ) from e
    return custom_ops, catalog_fetch


def validate_service(name: str, definition: dict) -> None:
    """Render-validate a single generated service definition with the pinned CS renderer.

    Parameters
    ----------
    name : str
        The service (directory) name.
    definition : dict
        The generated CS service definition.

    Raises
    ------
    RendererUnavailable
        If the CS renderer is not installed.
    ServiceValidationError
        If the definition fails to render (bad shape / unresolved placeholders).
    """
    custom_ops, _ = _import_cs()
    try:
        custom_ops._dry_run(name, definition)
    except Exception as e:  # noqa: BLE001 - normalize CS's ValueError/RenderError alike
        raise ServiceValidationError(f"{name}: {e}") from e


def validate_tree(catalog_dir: pathlib.Path, *, source_id: str) -> None:
    """Run CS's full manifest + tree + dry-run gate over a generated catalog directory.

    This is the belt-and-suspenders final gate: it invokes the exact validators CS's
    ``catalog_fetch`` pipeline runs after a git fetch, so a tree that passes here is
    one CS will accept when it pins a tag of this repo.

    Parameters
    ----------
    catalog_dir : pathlib.Path
        The catalog tree root (holds ``catalog.yml`` and per-service directories).
    source_id : str
        The catalog id the manifest must declare (``"lsio"``).

    Raises
    ------
    RendererUnavailable
        If the CS renderer is not installed.
    ServiceValidationError
        If the manifest, tree, or any service fails CS validation.
    """
    _, catalog_fetch = _import_cs()
    catalog_dir = pathlib.Path(catalog_dir)
    try:
        catalog_fetch.validate_manifest(catalog_dir, source_id)
        catalog_fetch.validate_tree(catalog_dir)
        catalog_fetch.dry_run_all(catalog_dir)
    except Exception as e:  # noqa: BLE001 - normalize CS's CatalogFetchError
        raise ServiceValidationError(str(e)) from e
