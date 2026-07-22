"""lsio_catalog_gen — LinuxServer.io → CognitiveSystems (``id: lsio``) catalog generator.

This package fetches each LinuxServer.io image's canonical ``readme-vars.yml`` metadata,
converts the ones that map cleanly and safely onto CognitiveSystems' ``service.yml``
schema (rejecting anything that would need template semantics CS cannot faithfully
represent), validates every generated service by rendering it with a *pinned* CS
renderer, and writes a source-qualified catalog tree (``catalog.yml`` + per-service
``service.yml`` + provenance) that CS's ``kind: "git"`` catalog fetcher consumes.

No LSIO-parsing/fetching logic lives in CS core — it all lives here. CS's default
``lsio`` catalog source points at a reviewed, immutable tag/commit of THIS repo.
"""
from __future__ import annotations

__version__ = "0.0.0"

# The CS git ref whose renderer these definitions are validated against. Pin to an
# immutable commit, never a moving branch. Kept here (single source of truth) so the
# generator can stamp it into provenance and the CI workflow can install the matching
# renderer. See README "Validation" and the cross-repo CI-auth decision.
CS_PINNED_REF = "51992934f69bdfdcebc3101647b2537f9f69a34d"

# The one, exact catalog id CS validates the generated manifest against
# (``catalog_fetch.validate_manifest`` requires ``manifest["id"] == source_id``).
CATALOG_ID = "lsio"
