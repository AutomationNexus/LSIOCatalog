"""Command-line entry point for the LSIO → CS ``lsio`` catalog generator.

Usage
-----
.. code-block:: text

    lsio-catalog-gen --out catalog --apps-file apps.yml
    lsio-catalog-gen --out catalog --apps sonarr,radarr --no-validate
    lsio-catalog-gen --out catalog --fixtures-dir tests/fixtures/readme-vars

Exit status is non-zero if a requested validation could not run (renderer missing) or
the generated tree failed CS's whole-tree gate — so CI never tags an unvalidated tree.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import yaml

from .generate import generate
from .validate import RendererUnavailable, ServiceValidationError

_DEFAULT_APPS_FILE = "apps.yml"
_DEFAULT_OUT = "catalog"


def _load_apps(apps: str | None, apps_file: str | None) -> list[str]:
    """Resolve the app list from ``--apps`` (CSV) or an ``--apps-file`` YAML.

    Parameters
    ----------
    apps : str or None
        Comma-separated app names, if given (takes precedence).
    apps_file : str or None
        Path to a YAML file with a top-level ``apps:`` list.

    Returns
    -------
    list of str
        The de-duplicated, ordered app list.

    Raises
    ------
    SystemExit
        If neither source yields any app.
    """
    names: list[str] = []
    if apps:
        names = [a.strip() for a in apps.split(",") if a.strip()]
    elif apps_file:
        path = pathlib.Path(apps_file)
        if path.is_file():
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            names = [str(a).strip() for a in (doc.get("apps") or []) if str(a).strip()]
    seen: set[str] = set()
    ordered = [n for n in names if not (n in seen or seen.add(n))]
    if not ordered:
        raise SystemExit("no apps to generate: pass --apps or a non-empty --apps-file")
    return ordered


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser.

    Returns
    -------
    argparse.ArgumentParser
        The configured parser.
    """
    p = argparse.ArgumentParser(prog="lsio-catalog-gen", description=__doc__)
    p.add_argument("--out", default=_DEFAULT_OUT, help="catalog tree output directory")
    p.add_argument("--apps", default=None, help="comma-separated app names (overrides --apps-file)")
    p.add_argument("--apps-file", default=_DEFAULT_APPS_FILE, help="YAML file with a top-level apps: list")
    p.add_argument("--ref", default="auto",
                   help="upstream LSIO ref to read/resolve ('auto' = detect the repo's default branch)")
    p.add_argument("--fixtures-dir", default=None,
                   help="read metadata from <dir>/<app>.yml instead of the network (offline)")
    p.add_argument("--no-validate", action="store_true",
                   help="skip CS render validation (output must NOT be tagged)")
    return p


def main(argv: list[str] | None = None) -> int:
    """Run the generator.

    Parameters
    ----------
    argv : list of str or None, optional
        Argument vector (defaults to ``sys.argv[1:]``).

    Returns
    -------
    int
        Process exit code (``0`` success, ``1`` validation/renderer failure).
    """
    args = build_parser().parse_args(argv)
    apps = _load_apps(args.apps, args.apps_file)
    try:
        result = generate(
            apps, args.out,
            fixtures_dir=args.fixtures_dir, ref=args.ref, validate=not args.no_validate,
        )
    except RendererUnavailable as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except ServiceValidationError as e:
        print(f"ERROR: generated tree failed CS whole-tree validation: {e}", file=sys.stderr)
        return 1

    print(f"accepted ({len(result.accepted)}): {', '.join(result.accepted) or '-'}")
    for r in result.rejected:
        print(f"rejected: {r['app']}: {r['reason']}")
    print(f"tree_validated: {result.tree_validated}")
    print(f"output: {pathlib.Path(args.out).resolve()}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
