"""Tests for the safe-subset rejection rules (unsafe defs are skipped with a reason)."""
from __future__ import annotations

import pytest
from conftest import load_fixture

from lsio_catalog_gen.convert import RejectionError, convert

_BASE_PORT = {"external_port": "8080", "internal_port": "8080", "port_desc": "web ui"}


def _base(**extra):
    d = {
        "project_name": "app",
        "param_usage_include_ports": True,
        "param_ports": [dict(_BASE_PORT)],
    }
    d.update(extra)
    return d


def test_plex_host_networking_is_rejected():
    with pytest.raises(RejectionError, match="networking"):
        convert(load_fixture("plex"), app="plex")


def test_privileged_rejected():
    with pytest.raises(RejectionError, match="privileged"):
        convert(_base(privileged=True), app="app")


def test_device_passthrough_rejected():
    with pytest.raises(RejectionError, match="device"):
        convert(_base(param_device_map=True, param_devices=[{"device_path": "/dev/dri"}]), app="app")


def test_added_capabilities_rejected():
    with pytest.raises(RejectionError, match="capabilit"):
        convert(_base(cap_add_param=True, param_cap_add=[{"cap_add_var": "NET_ADMIN"}]), app="app")


def test_custom_security_opt_rejected():
    with pytest.raises(RejectionError, match="security"):
        convert(_base(security_opt_param=True), app="app")


def test_host_pid_rejected():
    with pytest.raises(RejectionError, match="PID"):
        convert(_base(param_pid="host"), app="app")


def test_no_port_rejected():
    with pytest.raises(RejectionError, match="route via traefik"):
        convert({"project_name": "app"}, app="app")


def test_ambiguous_multiport_rejected():
    ports = [
        {"external_port": "9000", "internal_port": "9000", "port_desc": "peer to peer"},
        {"external_port": "9001", "internal_port": "9001", "port_desc": "rpc control"},
    ]
    with pytest.raises(RejectionError, match="unambiguous primary"):
        convert(_base(param_ports=ports), app="app")


def test_two_distinct_web_uis_no_primary_still_rejected():
    """Two separate web UIs (Calibre-style desktop gui + webserver gui, plus an HTTPS
    sibling) have no single primary — still rejected, never guessed."""
    ports = [
        {"external_port": "8080", "internal_port": "8080", "port_desc": "Calibre desktop gui (only for reverse proxy access)."},
        {"external_port": "8181", "internal_port": "8181", "port_desc": "Calibre desktop gui HTTPS."},
        {"external_port": "8081", "internal_port": "8081", "port_desc": "Calibre webserver gui (needs to be enabled in gui settings first)."},
    ]
    with pytest.raises(RejectionError, match="unambiguous primary"):
        convert(_base(param_ports=ports), app="calibre")


def test_two_unrelated_ports_no_web_signal_still_rejected():
    """Two ports with no web-UI signal at all (Ubooquity library/admin) stay rejected."""
    ports = [
        {"external_port": "2202", "internal_port": "2202", "port_desc": "The library port."},
        {"external_port": "2203", "internal_port": "2203", "port_desc": "The admin port."},
    ]
    with pytest.raises(RejectionError, match="unambiguous primary"):
        convert(_base(param_ports=ports), app="ubooquity")


def test_unsafe_networking_rejected_even_with_http_https_pair():
    """The safe-subset check precedes port selection: an app with host networking is
    rejected on networking even though its ports are a clean http+https pair."""
    ports = [
        {"external_port": "3000", "internal_port": "3000", "port_desc": "desktop gui HTTP"},
        {"external_port": "3001", "internal_port": "3001", "port_desc": "desktop gui HTTPS"},
    ]
    with pytest.raises(RejectionError, match="networking"):
        convert(_base(param_net="host", param_ports=ports), app="app")


def test_udp_only_port_still_rejected_as_invalid_tcp():
    """An app whose only port is udp/named is still rejected (not routable via traefik)."""
    with pytest.raises(RejectionError, match="not a valid TCP port"):
        convert(
            _base(param_ports=[{"external_port": "514", "internal_port": "5514/udp", "port_desc": "Syslog UDP"}]),
            app="syslog-ng",
        )


def test_multiport_with_single_web_port_is_accepted():
    ports = [
        {"external_port": "51413", "internal_port": "51413", "port_desc": "peer to peer"},
        {"external_port": "9091", "internal_port": "9091", "port_desc": "the web ui"},
    ]
    defn = convert(_base(param_ports=ports), app="app")
    assert defn["containers"]["app"]["service_port"] == "9091"


def test_unsafe_project_slug_rejected():
    with pytest.raises(RejectionError, match="slug"):
        convert(_base(project_name="Bad Name!"), app="Bad Name!")


def test_required_volume_path_traversal_rejected():
    with pytest.raises(RejectionError, match=r"traversal"):
        convert(_base(param_volumes=[{"vol_path": "/../../etc", "desc": "x"}]), app="app")


def test_optional_volume_path_traversal_rejects_whole_app():
    """An untrusted '..' in an OPTIONAL data mount rejects the whole app, not just the mount."""
    unsafe = _base(
        param_volumes=[{"vol_path": "/config", "desc": "config"}],
        opt_param_volumes=[{"vol_path": "/data/../../root", "desc": "sneaky"}],
    )
    with pytest.raises(RejectionError, match=r"traversal"):
        convert(unsafe, app="app")
