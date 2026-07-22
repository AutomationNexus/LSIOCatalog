"""Module entry point: ``python -m lsio_catalog_gen``."""
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
