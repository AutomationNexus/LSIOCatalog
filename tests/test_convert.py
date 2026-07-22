"""Unit tests for the pure LSIO -> CS converter (no network, vendored fixtures)."""
from __future__ import annotations

from conftest import load_fixture

from lsio_catalog_gen.convert import convert


def test_sonarr_maps_to_upstream_defaults():
    defn = convert(load_fixture("sonarr"), app="sonarr")
    assert defn["display_name"] == "Sonarr"
    assert defn["subdomain"] == "sonarr"
    assert defn["routing"] == "traefik"
    assert defn["auto_update_default"] is True
    assert defn["variants_supported"] == ["web", "websecure", "wildcard"]

    c = defn["containers"]["sonarr"]
    # Upstream image + default tag.
    assert c["image_url"] == "lscr.io/linuxserver/sonarr"
    assert c["image_tag"] == "latest"
    # The app's OWN documented port, host == container (operator overrides at install).
    assert c["ports"] == ["8989:8989"]
    assert c["service_port"] == "8989"
    # LSIO single-/config convention via the CS profile placeholder.
    assert "${CONFIG_ROOT}/Sonarr/config:/config" in c["volumes"]
    # Documented data mounts, mapped generically to ${STORAGE_ROOT}, container path preserved.
    assert "${STORAGE_ROOT}/tv:/tv" in c["volumes"]
    assert "${STORAGE_ROOT}/downloads:/downloads" in c["volumes"]
    # Standard LSIO env trio via placeholders.
    assert c["environment"] == {"PUID": "${PUID}", "PGID": "${PGID}", "TZ": "${TZ}"}


def test_radarr_uses_its_own_port():
    c = convert(load_fixture("radarr"), app="radarr")["containers"]["radarr"]
    assert c["ports"] == ["7878:7878"]
    assert c["service_port"] == "7878"
    assert c["image_url"] == "lscr.io/linuxserver/radarr"


def test_no_private_catalog_specifics_leak_in():
    """The public LSIO defs must carry ZERO private-catalog opinion."""
    for app in ("sonarr", "radarr", "bazarr", "prowlarr", "lidarr"):
        defn = convert(load_fixture(app), app=app)
        c = next(iter(defn["containers"].values()))
        # No private ports (e.g. plex 64209), no MAP_SERVICE_PORT, no VERSION, no
        # labels_extra / traefik.enable=false, no /etc/localtime bind.
        assert "labels_extra" not in c
        assert "MAP_SERVICE_PORT" not in c["environment"]
        assert "VERSION" not in c["environment"]
        assert all("64209" not in p for p in c["ports"])
        assert all("/etc/localtime" not in v for v in c["volumes"])
        # host port defaults to the container port (never a private-chosen host port).
        host, container = c["ports"][0].split(":")
        assert host == container == c["service_port"]


def test_config_volume_synthesized_when_absent():
    defn = convert(
        {
            "project_name": "widget",
            "param_usage_include_ports": True,
            "param_ports": [{"external_port": "9000", "internal_port": "9000", "port_desc": "web ui"}],
        },
        app="widget",
    )
    c = defn["containers"]["widget"]
    assert c["volumes"][0] == "${CONFIG_ROOT}/Widget/config:/config"


def test_required_documented_env_is_kept_but_optional_and_templated_dropped():
    defn = convert(
        {
            "project_name": "thing",
            "param_usage_include_ports": True,
            "param_ports": [{"external_port": "8080", "internal_port": "8080", "port_desc": "web"}],
            "param_usage_include_env": True,
            "param_env_vars": [
                {"env_var": "APP_MODE", "env_value": "server"},          # kept
                {"env_var": "CLAIM", "env_value": ""},                    # dropped (empty)
                {"env_var": "TPL", "env_value": "{{ project_name }}"},    # dropped (templated)
                {"env_var": "bad name", "env_value": "x"},                # dropped (bad name)
            ],
        },
        app="thing",
    )
    env = defn["containers"]["thing"]["environment"]
    assert env["APP_MODE"] == "server"
    assert "CLAIM" not in env and "TPL" not in env
