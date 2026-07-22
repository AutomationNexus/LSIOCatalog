# LSIOCatalog

A scheduled **generator** that converts [LinuxServer.io](https://www.linuxserver.io/)
(LSIO) image metadata into a [CognitiveSystems](https://github.com/AutomationNexus/CognitiveSystems)
(CS) **git catalog** with the id **`lsio`**.

CS consumes a reviewed, immutable tag of this repo as its default `lsio` catalog source.
All LSIO parsing/fetching/conversion logic lives **here** — CS core contains none of it
(epic #68, issue #75). This repo produces a catalog tree; CS's `kind: "git"` catalog
fetcher (#71) validates and renders it on the operator's machine.

## What it does

For each LSIO image in [`apps.yml`](apps.yml) the generator:

1. **Fetches** the image's canonical `readme-vars.yml` (the metadata LSIO itself uses to
   generate its docs/compose) from `github.com/linuxserver/docker-<app>`.
2. **Converts** it — *if it maps cleanly and safely* — into a CS `service.yml`,
   expressed entirely through CS's per-operator profile placeholders. Anything using
   semantics CS cannot faithfully represent is **rejected**, never emitted lossily
   (see [Safe subset & rejection](#safe-subset--rejection)).
3. **Validates** every generated service by rendering it with a **pinned** CS renderer —
   the exact validators CS's own git-catalog fetcher runs (see [Validation](#validation)).
   A service that fails to render is rejected, not shipped.
4. **Writes** the catalog tree: `catalog.yml` + `<app>/service.yml` + machine-readable
   `provenance.json` and `rejected.json`.

## Upstream defaults, zero opinion

The generated `service.yml` reflects **the app's own upstream defaults** — its own
documented port, LSIO's single `/config` convention, the minimal data mounts the app
documents — expressed through CS profile placeholders that each operator resolves at
render time:

| Placeholder | Resolved from |
|---|---|
| `${CONFIG_ROOT}` / `${STORAGE_ROOT}` | the operator's CS profile `paths` |
| `${PUID}` / `${PGID}` | the operator's CS profile / install flags |
| `${TZ}` | the operator's CS profile `timezone` |

It bakes in **no** operator-specific host port, directory, routing quirk, or private
catalog specific. Customization (ports, dirs, config) happens at CS **install** time via
per-service overrides — not here. Example (`catalog/sonarr/service.yml`):

```yaml
display_name: Sonarr
subdomain: sonarr
routing: traefik
auto_update_default: true
containers:
  sonarr:
    image_url: lscr.io/linuxserver/sonarr   # LSIO's own image
    image_tag: latest                        # OCI-tag-grammar safe (#78)
    ports:
    - 8989:8989                              # the app's OWN documented port
    volumes:
    - ${CONFIG_ROOT}/Sonarr/config:/config   # LSIO single-/config convention
    - ${STORAGE_ROOT}/tv:/tv                 # documented data mount, neutral mapping
    - ${STORAGE_ROOT}/downloads:/downloads
    service_port: '8989'
    environment:
      PUID: ${PUID}
      PGID: ${PGID}
      TZ: ${TZ}
variants_supported: [web, websecure, wildcard]
```

## The `id: lsio` contract

CS validates the manifest at the catalog tree root against
`cognitivesystems.catalog_fetch.validate_manifest`. The generated `catalog.yml`:

- lives at the **tree root**, with `id: lsio` **exactly** (must equal the CS source id);
- declares a known `format_version` (`1`) and a valid `display_name` (≤ 120 chars,
  no control characters);
- carries **no** forbidden/imperative top-level key (`hooks`, `scripts`, `exec`,
  `command(s)`, `entrypoint`, `run`, `shell`, `pre_install`, `post_install`).

Every service directory name matches CS's `NAME_RE` (`^[a-z0-9][a-z0-9._-]{0,63}$`), the
tree has no symlinks, and every `service.yml` renders cleanly — the full set of
structural + render checks CS's fetcher enforces before promoting a fetched catalog.

## Repository layout

```
apps.yml                       # configurable app list (the proof/foundation set)
src/lsio_catalog_gen/          # the generator (a small stdlib + PyYAML tool)
  convert.py                   #   pure readme-vars -> service.yml (safe subset + reject)
  fetch.py                     #   network: readme-vars fetch + default-branch/commit resolve
  validate.py                  #   render-validate via the PINNED CS renderer
  generate.py                  #   orchestrate fetch -> convert -> validate -> write tree
  cli.py / __main__.py         #   `lsio-catalog-gen` / `python -m lsio_catalog_gen`
tests/                         # pytest: converter, rejection, and end-to-end validation
  fixtures/readme-vars/*.yml   #   vendored REAL readme-vars samples (no network in tests)
catalog/                       # a COMMITTED, VALIDATED proof snapshot of the output tree
  catalog.yml                  #   id: lsio manifest  (tree ROOT)
  <app>/service.yml            #   one validated service per accepted app
  provenance.json              #   source repo + commit for every accepted app
  rejected.json                #   app + reason for every rejected app
.github/workflows/generate.yml # scheduled convert -> validate -> publish + tag
```

### Where CS points its `lsio` source

CS's git fetcher clones a ref and validates from the **clone root** (it expects
`catalog.yml` and `<app>/` directories as direct children of the tree root; a `.github/`
or `src/` at the root would trip its `NAME_RE` directory check). So the **generator
source** and the **generated catalog tree** cannot share a root.

- The `catalog/` directory in this repo is a committed **proof snapshot** for humans to
  inspect and for tests to validate.
- CI publishes the *contents of `catalog/`* as a **root-level tree** onto a dedicated
  `catalog` branch and an immutable `lsio-catalog-<stamp>` **tag** (via `git subtree
  split --prefix=catalog`). **CS pins the immutable tag** — that tag's tree root is
  `catalog.yml` + `<app>/service.yml`.

## Running the generator

```bash
pip install -e .                                    # + a pinned CS renderer to validate
lsio-catalog-gen --out catalog --apps-file apps.yml # fetch (auto default branch) + validate
lsio-catalog-gen --out catalog --apps sonarr,radarr # explicit list
lsio-catalog-gen --out catalog --fixtures-dir tests/fixtures/readme-vars  # offline
```

Exit status is non-zero if validation cannot run (renderer missing) or the tree fails
CS's whole-tree gate — so CI never tags an unvalidated tree.

## Safe subset & rejection

The converter emits a service **only** when the image maps cleanly and safely onto CS's
schema. It **rejects** (records `{app, source_repo, reason}` in `rejected.json`, never
emits a guessed/lossy def) anything that needs semantics CS cannot faithfully represent:

- host or custom networking (`param_net: host`) — *this is why `plex` is rejected*;
- privileged mode;
- required device passthrough (`param_device_map` / `param_devices`);
- added Linux capabilities (`cap_add`);
- custom seccomp/apparmor (`security_opt`);
- host PID namespace sharing;
- no published port (nothing to route via traefik), or multiple ports with no
  unambiguous primary web port;
- a project slug or required path/volume that isn't CS-safe.

**Provenance** for every *accepted* app is recorded in `provenance.json`: the source
repo, the resolved branch ref, and the exact commit SHA it was converted from, plus the
pinned CS renderer ref it was validated against.

## Validation

Every generated service is validated by **actually rendering it with a pinned CS
renderer** before it is written/tagged — this repo runs the *same* functions CS's own
`kind: "git"` fetcher runs, so what passes here is what CS will accept:

- per-service — `cognitivesystems.core.ops.custom._dry_run` (a full placeholder-leak
  render under a real profile);
- whole-tree — `cognitivesystems.catalog_fetch.validate_manifest` / `validate_tree` /
  `dry_run_all`.

**Pinned CS ref:** `lsio_catalog_gen.CS_PINNED_REF`
(`51992934f69bdfdcebc3101647b2537f9f69a34d`) — an immutable commit, never a moving
branch. Update it deliberately when re-validating against a newer CS renderer.

### Cross-repo CI-auth — SETTLED: CI-Bot GitHub App

LSIOCatalog is **public**; CognitiveSystems is **private**. CI installing the pinned CS
renderer from git therefore needs read access to the private repo. Per org policy
(workspace `CLAUDE.md`), privileged cross-repo automation mints a short-lived **CI-Bot
GitHub App** token via `actions/create-github-app-token@v1` — **never `GITHUB_TOKEN`
and never a PAT**. `.github/workflows/generate.yml` implements exactly this: it mints a
token scoped to **read `CognitiveSystems` only**, then:

```
pip install "cognitivesystems @ git+https://x-access-token:${APP_TOKEN}@github.com/AutomationNexus/CognitiveSystems@<CS_PINNED_REF>"
```

The same-repo `catalog`-branch/tag publish keeps using the ambient `GITHUB_TOKEN` (the
cross-repo rule is about reaching the private CS repo; publishing here is same-repo).

**One remaining prerequisite** (an org-root/admin action, done separately, before the
cron can run):

1. install the **CI-Bot GitHub App** on `AutomationNexus` with **read** access to the
   `CognitiveSystems` repo, and
2. provision the `CI_BOT_APP_ID` and `CI_BOT_APP_PRIVATE_KEY` secrets on **this** repo.

*Alternative (a separate platform-engineer decision, not blocking):* if CS is ever
published to PyPI, install `cognitivesystems==<pinned>` and drop the token entirely.

For **local** development the pinned renderer is installed from the sibling checkout
(`pip install -e ../CognitiveSystems@<ref>`), which is how the committed `catalog/`
proof tree in this repo was generated and validated end-to-end.

## Tests & QA

```bash
pip install -e ".[dev]"
python -m pytest -q          # converter + rejection unit tests (no network),
                             # + end-to-end validation (skipped if CS renderer absent)
python -m ruff check src tests
```

Tests never hit the network: conversion runs against **vendored real** `readme-vars.yml`
fixtures under `tests/fixtures/readme-vars/`.

## Open decisions

- **CI-Bot App provisioning** (not a design decision — an admin action): install the
  CI-Bot GitHub App with `CognitiveSystems` read access and provision
  `CI_BOT_APP_ID` / `CI_BOT_APP_PRIVATE_KEY` on this repo, so the cron can validate.
  The auth *mechanism* is settled (CI-Bot App token) — see
  [Cross-repo CI-auth](#cross-repo-ci-auth--settled-ci-bot-github-app).
- Whether the scheduled generator should become a **shared reusable workflow** in
  `automationnexus/.github` rather than this standalone workflow — platform-engineer.
- Branch-protection / publish model for the `catalog` branch + tags on this new public
  repo — org-root.

## License

MIT — see [LICENSE](LICENSE).
