"""Pure, network-free conversion of an LSIO ``readme-vars.yml`` into a CS ``service.yml``.

This is the heart of the generator and is deliberately side-effect-free: it takes an
already-parsed ``readme-vars.yml`` mapping and returns a CognitiveSystems service
definition mapping, or raises :class:`RejectionError` for any image that cannot be
represented **faithfully and safely** in CS's schema.

Design principles
-----------------
- **Upstream defaults, zero opinion.** The emitted definition reflects the app's OWN
  documented defaults (its own port, LSIO's ``/config`` convention, the minimal data
  mounts the app documents) expressed through CS's per-operator profile placeholders
  (``${CONFIG_ROOT}``, ``${STORAGE_ROOT}``, ``${PUID}``, ``${PGID}``, ``${TZ}``).
  It never bakes in an operator-specific host port, directory, or routing quirk —
  customization happens at CS *install* time, not here.
- **Safe subset or reject.** Anything using semantics CS cannot represent
  (host networking, privileged, device passthrough, added capabilities, custom
  seccomp/apparmor, host PID, or an ambiguous/absent primary web port) is REJECTED
  with a recorded reason, never emitted lossily.
"""
from __future__ import annotations

import re

# A single documented data-mount cap keeps a converted definition minimal and avoids
# dragging in an app's every optional bind. LSIO's own docs list these "extra" mounts
# as optional; we surface a bounded, neutral set rather than all of them.
_MAX_DATA_VOLUMES = 4

# CS's OCI tag grammar (#78, mirrored from render._IMAGE_TAG_RE) — a defensive check so
# a generated image_tag can never fail CS render validation downstream.
_IMAGE_TAG_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}$")

# CS's service/source slug (catalog.NAME_RE) — every generated service directory name
# must satisfy this or CS's validate_tree rejects the whole catalog.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# A conservative environment-variable name shape (POSIX-ish). Anything outside this is
# treated as non-representable and skipped/rejected rather than emitted.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Jinja/compose interpolation markers that, if present in a value we would emit, mean
# the value is not a plain literal we can faithfully reproduce.
_TEMPLATE_MARKERS = ("{{", "}}", "${")

# Words in a port description that mark it as the primary human-facing web/UI port,
# used to disambiguate when an image documents more than one port.
_WEB_PORT_RE = re.compile(r"web|ui|interface|http|gui|dashboard|panel", re.IGNORECASE)


class RejectionError(Exception):
    """Raised by :func:`convert` when an image cannot be safely/faithfully represented.

    The message is the machine-recordable rejection reason (see
    :mod:`lsio_catalog_gen.generate`, which writes it into ``rejected.json``).
    """


def _truthy(value: object) -> bool:
    """Interpret an LSIO ``readme-vars`` flag as a boolean.

    LSIO metadata expresses "feature on" both as a real YAML boolean and, occasionally,
    as a truthy string. This normalizes both.

    Parameters
    ----------
    value : object
        The raw field value.

    Returns
    -------
    bool
        Whether the field should be treated as enabled/present.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "on", "1", "host"}
    return bool(value)


def _has_template(value: object) -> bool:
    """Report whether a would-be-emitted value carries unresolved template markers.

    Parameters
    ----------
    value : object
        Candidate value.

    Returns
    -------
    bool
        ``True`` if the stringified value contains a Jinja/compose interpolation marker.
    """
    text = str(value)
    return any(marker in text for marker in _TEMPLATE_MARKERS)


def _reject_unsafe(rv: dict, app: str) -> None:
    """Reject any image using container semantics CS cannot faithfully represent.

    Parameters
    ----------
    rv : dict
        The parsed ``readme-vars.yml`` mapping.
    app : str
        The app name (only used in the raised message).

    Raises
    ------
    RejectionError
        On host networking, privileged mode, required device passthrough, added
        capabilities, custom seccomp/apparmor, or host PID namespace sharing.
    """
    net = rv.get("param_net")
    if net is not None and str(net).strip().lower() not in {"", "bridge"}:
        raise RejectionError(f"host/custom networking (param_net: {net!r}) is not representable")

    if _truthy(rv.get("privileged")) or _truthy(rv.get("param_privileged")):
        raise RejectionError("privileged mode is not representable")

    if _truthy(rv.get("param_device_map")) or rv.get("param_devices"):
        raise RejectionError("required device passthrough is not representable")

    if (_truthy(rv.get("cap_add_param")) or rv.get("param_cap_add") or rv.get("cap_add")):
        raise RejectionError("added Linux capabilities are not representable")

    if _truthy(rv.get("security_opt_param")) or rv.get("param_security_opt"):
        raise RejectionError("custom security options (seccomp/apparmor) are not representable")

    pid = rv.get("param_pid")
    if pid is not None and str(pid).strip().lower() == "host":
        raise RejectionError("host PID namespace sharing is not representable")


def _project_name(rv: dict, app: str) -> str:
    """Resolve the upstream project slug, falling back to the requested app name.

    Parameters
    ----------
    rv : dict
        The parsed ``readme-vars.yml`` mapping.
    app : str
        The app name the generator asked for.

    Returns
    -------
    str
        A lowercase slug used as the service directory name and subdomain.

    Raises
    ------
    RejectionError
        If neither the metadata nor the requested name yields a CS-safe slug.
    """
    name = rv.get("project_name")
    if not isinstance(name, str) or _has_template(name) or not name.strip():
        name = app
    name = str(name).strip().lower()
    if not _NAME_RE.match(name):
        raise RejectionError(f"project name {name!r} is not a CS-safe slug (NAME_RE)")
    return name


def _display_name(rv: dict, slug: str) -> str:
    """Derive a human display name (and the ``/config`` subdirectory) for the app.

    Parameters
    ----------
    rv : dict
        The parsed ``readme-vars.yml`` mapping.
    slug : str
        The resolved lowercase project slug.

    Returns
    -------
    str
        A capitalized display name (never templated); defaults to ``slug.capitalize()``.
    """
    raw = rv.get("project_name")
    if isinstance(raw, str) and raw.strip() and not _has_template(raw):
        raw = raw.strip()
        return raw if not raw.islower() else raw.capitalize()
    return slug.capitalize()


def _primary_port(rv: dict) -> tuple[str, str]:
    """Select the single primary web port to publish and route.

    Parameters
    ----------
    rv : dict
        The parsed ``readme-vars.yml`` mapping.

    Returns
    -------
    (str, str)
        ``(external_port, internal_port)`` for the primary web port. The external
        port defaults to the app's OWN documented external port (a sensible, neutral
        default the operator overrides at install time), never an invented one.

    Raises
    ------
    RejectionError
        If the image declares no port (nothing to route via traefik) or multiple
        ports with no unambiguous primary web port.
    """
    if not _truthy(rv.get("param_usage_include_ports")):
        raise RejectionError("no published port declared (cannot route via traefik)")
    ports = rv.get("param_ports") or []
    if not isinstance(ports, list) or not ports:
        raise RejectionError("no published port declared (cannot route via traefik)")

    def _norm(entry: dict) -> tuple[str, str, str]:
        ext = str(entry.get("external_port", "")).strip()
        internal = str(entry.get("internal_port", "")).strip()
        desc = str(entry.get("port_desc", ""))
        return ext, internal, desc

    normalized = [_norm(p) for p in ports if isinstance(p, dict)]
    normalized = [(e, i, d) for (e, i, d) in normalized if i and not _has_template(i)]
    if not normalized:
        raise RejectionError("no usable internal port declared")

    if len(normalized) == 1:
        ext, internal, _ = normalized[0]
    else:
        web = [(e, i, d) for (e, i, d) in normalized if _WEB_PORT_RE.search(d)]
        if len(web) != 1:
            raise RejectionError(
                "multiple ports with no unambiguous primary web port")
        ext, internal, _ = web[0]

    if not internal.isdigit() or not (0 < int(internal) < 65536):
        raise RejectionError(f"internal port {internal!r} is not a valid TCP port")
    if not ext.isdigit() or not (0 < int(ext) < 65536):
        ext = internal  # default host port == container port; operator overrides later
    return ext, internal


def _volumes(rv: dict, display_name: str) -> list[str]:
    """Map LSIO's documented volumes onto neutral CS profile-placeholder binds.

    ``/config`` maps to ``${CONFIG_ROOT}/<Display>/config`` (LSIO's single-config
    convention). Every other documented mount maps generically to
    ``${STORAGE_ROOT}<container_path>`` — no assumption about a specific media layout.

    Parameters
    ----------
    rv : dict
        The parsed ``readme-vars.yml`` mapping.
    display_name : str
        The app display name, used as the ``${CONFIG_ROOT}`` subdirectory.

    Returns
    -------
    list of str
        ``host:container`` bind strings, ``/config`` first, then a bounded set of
        documented data mounts.

    Raises
    ------
    RejectionError
        If a required volume path is not a plain absolute container path.
    """
    required = rv.get("param_volumes") or []
    optional = rv.get("opt_param_volumes") or []
    binds: list[str] = []
    seen: set[str] = set()

    def _add(entry: object, *, is_required: bool) -> None:
        if not isinstance(entry, dict):
            return
        path = entry.get("vol_path")
        if not isinstance(path, str) or not path.startswith("/") or _has_template(path):
            if is_required:
                raise RejectionError(f"required volume path {path!r} is not representable")
            return
        path = path.rstrip("/") or "/config"
        if path in seen:
            return
        seen.add(path)
        if path == "/config":
            binds.insert(0, f"${{CONFIG_ROOT}}/{display_name}/config:/config")
        else:
            binds.append(f"${{STORAGE_ROOT}}{path}:{path}")

    for entry in required:
        _add(entry, is_required=True)
    for entry in optional:
        if len([b for b in binds if b.endswith(":/config") is False]) >= _MAX_DATA_VOLUMES:
            break
        _add(entry, is_required=False)

    # LSIO images all persist their state under /config; if the metadata somehow
    # omitted it, synthesize the standard bind so the app has durable config.
    if not any(b.endswith(":/config") for b in binds):
        binds.insert(0, f"${{CONFIG_ROOT}}/{display_name}/config:/config")
    return binds


def _environment(rv: dict) -> dict:
    """Build the container environment: standard LSIO base vars + required documented env.

    Always emits the LSIO base trio (``PUID``/``PGID``/``TZ``) as CS profile
    placeholders. Any REQUIRED, plain-literal ``param_env_vars`` the app documents are
    appended; optional env and templated/secret-shaped values are skipped.

    Parameters
    ----------
    rv : dict
        The parsed ``readme-vars.yml`` mapping.

    Returns
    -------
    dict
        The ``environment`` mapping for the container.
    """
    env: dict = {"PUID": "${PUID}", "PGID": "${PGID}", "TZ": "${TZ}"}
    for entry in (rv.get("param_env_vars") or []):
        if not isinstance(entry, dict):
            continue
        name = entry.get("env_var")
        value = entry.get("env_value")
        if not isinstance(name, str) or not _ENV_NAME_RE.match(name):
            continue
        if name in env:
            continue
        if not isinstance(value, (str, int, float, bool)):
            continue
        if _has_template(value) or (isinstance(value, str) and not value.strip()):
            # Templated or empty-by-design (e.g. a claim token) — an install-time
            # concern, not an upstream default. Skip rather than emit a broken value.
            continue
        env[name] = value if not isinstance(value, bool) else str(value).lower()
    return env


def convert(rv: dict, *, app: str) -> dict:
    """Convert one parsed ``readme-vars.yml`` into a CS ``service.yml`` mapping.

    Parameters
    ----------
    rv : dict
        The parsed ``readme-vars.yml`` mapping for a single LSIO image.
    app : str
        The app name the generator asked to convert (used as a slug fallback and in
        rejection messages).

    Returns
    -------
    dict
        A CS service definition mapping (``display_name``, ``subdomain``, ``routing``,
        ``auto_update_default``, ``containers``, ``variants_supported``) expressed
        entirely through CS profile placeholders — upstream defaults, zero opinion.

    Raises
    ------
    RejectionError
        If the image is not a mapping, or uses semantics CS cannot faithfully/safely
        represent (see :func:`_reject_unsafe`, :func:`_primary_port`, :func:`_volumes`).
    """
    if not isinstance(rv, dict):
        raise RejectionError("readme-vars is not a mapping")

    _reject_unsafe(rv, app)
    slug = _project_name(rv, app)
    display_name = _display_name(rv, slug)
    ext, internal = _primary_port(rv)

    image_tag = "latest"
    if not _IMAGE_TAG_RE.match(image_tag):  # pragma: no cover - constant, defensive
        raise RejectionError(f"image tag {image_tag!r} fails the OCI tag grammar")

    container = {
        "image_url": f"lscr.io/linuxserver/{slug}",
        "image_tag": image_tag,
        "ports": [f"{ext}:{internal}"],
        "volumes": _volumes(rv, display_name),
        "service_port": str(internal),
        "environment": _environment(rv),
    }
    return {
        "display_name": display_name,
        "subdomain": slug,
        "routing": "traefik",
        "auto_update_default": True,
        "containers": {slug: container},
        "variants_supported": ["web", "websecure", "wildcard"],
    }
