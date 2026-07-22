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
