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
# used to disambiguate when an image documents more than one port. The bare "ui" token
# is word-boundary-anchored so it doesn't false-match inside unrelated words (e.g. the
# "ui" in "Req[ui]red"); the longer, unambiguous tokens stay plain substrings so
# "WebUI"/"webgui" still match.
_WEB_PORT_RE = re.compile(r"web|http|gui|dashboard|panel|interface|\bui\b", re.IGNORECASE)

# Words in a port description that mark a port as the TLS/HTTPS *sibling* of a plain-HTTP
# web port. Used to prefer the plain-HTTP port as the routed primary (CS/traefik
# terminates TLS itself) while still publishing the HTTPS port alongside it.
_HTTPS_PORT_RE = re.compile(r"https|\bssl\b|\btls\b", re.IGNORECASE)

# Well-known HTTP/HTTPS port pairs (plain-HTTP first). When descriptions don't
# disambiguate but the two ports are a recognizable http+https pair, the plain-HTTP
# side is the routed primary. Description signals always take precedence over this.
_HTTP_HTTPS_PAIRS = (("3000", "3001"), ("80", "443"), ("8080", "8443"))


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


def _is_valid_tcp(port: str) -> bool:
    """Return whether a port string is a bare, in-range TCP port number.

    Rejects ``/udp`` / ``/tcp`` suffixes, port ranges, and named ports — CS routes a
    single TCP web port via traefik, so only bare TCP numbers are representable.

    Parameters
    ----------
    port : str
        A candidate internal or external port string.

    Returns
    -------
    bool
        ``True`` if ``port`` is all digits and ``0 < port < 65536``.
    """
    return port.isdigit() and 0 < int(port) < 65536


def _choose_primary(tcp: list) -> tuple | None:
    """Pick the primary (routed) web port from several valid-TCP candidates.

    Signals, in priority order (a genuinely ambiguous set returns ``None`` — the caller
    rejects rather than guesses):

    1. **Description** — if exactly one port's description names a plain-HTTP web UI
       (matches :data:`_WEB_PORT_RE` and *not* :data:`_HTTPS_PORT_RE`), it is the
       primary. This deliberately prefers the plain-HTTP port over its HTTPS sibling
       (CS/traefik terminates TLS) and correctly handles cases where the real web UI is
       the HTTPS-numbered port (e.g. Unifi's ``8443`` "web admin", ``8080`` "device
       communication") because only ``8443`` matches a web description.
    2. **Well-known pair by number** — when descriptions don't disambiguate but the two
       ports are a recognizable http+https pair (:data:`_HTTP_HTTPS_PAIRS`), the
       plain-HTTP side is primary.
    3. **Single web port** — if exactly one candidate looks like a web port at all
       (HTTP or HTTPS) and the rest are clearly non-web (p2p/rpc/dns/…), route it.

    Parameters
    ----------
    tcp : list of (str, str, str)
        ``(external, internal, desc)`` triples, each a valid TCP port.

    Returns
    -------
    tuple or None
        The chosen ``(external, internal, desc)``, or ``None`` when no unambiguous
        primary web port can be identified.
    """
    if len(tcp) == 1:
        return tcp[0]

    # 1. exactly one plain-HTTP web port by description (HTTPS siblings excluded).
    http_web = [c for c in tcp if _WEB_PORT_RE.search(c[2]) and not _HTTPS_PORT_RE.search(c[2])]
    if len(http_web) == 1:
        return http_web[0]

    # 2. a recognizable http+https pair by number → the plain-HTTP side (only when the
    #    two candidates ARE that pair, so an app with more ports isn't mis-picked).
    if len(tcp) == 2:
        ints = {c[1] for c in tcp}
        for lo, hi in _HTTP_HTTPS_PAIRS:
            if ints == {lo, hi}:
                return next(c for c in tcp if c[1] == lo)

    # 3. exactly one web-ish port, rest clearly non-web.
    web = [c for c in tcp if _WEB_PORT_RE.search(c[2])]
    if len(web) == 1:
        return web[0]

    return None


def _select_ports(rv: dict) -> tuple[str, list]:
    """Select the routed primary web port and the full set of published TCP ports.

    Improves on a naive "one port only" rule: an image that documents several ports
    (very commonly an HTTP+HTTPS web-UI pair, or a web UI plus an auxiliary port) is now
    representable — the primary web port is routed and every valid-TCP port is published
    — instead of being rejected. Genuinely ambiguous images (multiple unrelated services,
    no web signal) are still rejected, never guessed.

    Parameters
    ----------
    rv : dict
        The parsed ``readme-vars.yml`` mapping.

    Returns
    -------
    (str, list of str)
        ``(service_port, published)`` where ``service_port`` is the primary web port's
        internal port (traefik's loadbalancer target) and ``published`` is the list of
        ``ext:int`` host:container bind strings (primary first). External ports default
        to the app's OWN documented port, never an invented one.

    Raises
    ------
    RejectionError
        If the image declares no port (nothing to route via traefik), only non-TCP
        ports (udp/ranges/named), or multiple ports with no unambiguous primary web
        port.
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

    tcp = [(e, i, d) for (e, i, d) in normalized if _is_valid_tcp(i)]
    if not tcp:
        # Preserve the precise reason (e.g. a single udp-only or named port).
        raise RejectionError(f"internal port {normalized[0][1]!r} is not a valid TCP port")

    primary = _choose_primary(tcp)
    if primary is None:
        raise RejectionError("multiple ports with no unambiguous primary web port")

    published: list = []
    seen: set = set()

    def _publish(ext: str, internal: str) -> None:
        if not _is_valid_tcp(ext):
            ext = internal  # default host port == container port; operator overrides later
        mapping = f"{ext}:{internal}"
        if mapping not in seen:
            seen.add(mapping)
            published.append(mapping)

    p_ext, p_internal, _ = primary
    _publish(p_ext, p_internal)  # primary first
    for ext, internal, _ in tcp:
        _publish(ext, internal)  # HTTPS sibling + any auxiliary TCP ports, deduped

    return p_internal, published


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
        If a required volume path is not a plain absolute container path, or if ANY
        volume path (required or optional) contains a ``..`` traversal segment —
        ``readme-vars.yml`` is untrusted third-party content, so a traversal segment
        rejects the whole app rather than being silently dropped.
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
        # Reject path traversal in untrusted metadata. This fires for required AND
        # optional volumes: a '..' segment is a red flag for the whole definition, so
        # the app is rejected outright rather than the one volume being dropped.
        if any(seg == ".." for seg in path.split("/")):
            raise RejectionError(f"volume path {path!r} contains a '..' traversal segment")
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
        represent (see :func:`_reject_unsafe`, :func:`_select_ports`, :func:`_volumes`).
    """
    if not isinstance(rv, dict):
        raise RejectionError("readme-vars is not a mapping")

    _reject_unsafe(rv, app)
    slug = _project_name(rv, app)
    display_name = _display_name(rv, slug)
    service_port, published_ports = _select_ports(rv)

    image_tag = "latest"
    if not _IMAGE_TAG_RE.match(image_tag):  # pragma: no cover - constant, defensive
        raise RejectionError(f"image tag {image_tag!r} fails the OCI tag grammar")

    container = {
        "image_url": f"lscr.io/linuxserver/{slug}",
        "image_tag": image_tag,
        "ports": published_ports,
        "volumes": _volumes(rv, display_name),
        "service_port": str(service_port),
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
