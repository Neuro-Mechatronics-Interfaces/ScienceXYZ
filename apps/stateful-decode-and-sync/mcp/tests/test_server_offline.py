"""Offline server-tool behaviour that needs no device and no MCP transport.

The server module imports the MCP SDK at import time; if it is not installed the
whole module is skipped. The client library (nml-science-xyz) must be importable
for the diagnostics tools; those tests skip if it is not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

server = pytest.importorskip("science_mcp.server", reason="mcp SDK not installed")


def _client_available() -> bool:
    try:
        import stateful_decode_and_sync.motion_profile  # noqa: F401
        return True
    except ModuleNotFoundError:
        return False


needs_client = pytest.mark.skipif(not _client_available(),
                                  reason="nml-science-xyz client not installed")


def test_synapsectl_command_info_form():
    result = server.synapsectl_command("192.168.100.157")
    assert result["command"] == "synapsectl -u 192.168.100.157 info"
    assert "does not execute" in result["note"]


def test_synapsectl_command_start_form():
    result = server.synapsectl_command("192.168.100.157", "device-config.json")
    assert result["argv"] == ["synapsectl", "-u", "192.168.100.157", "start", "device-config.json"]


def test_device_info_from_capture_missing_file(tmp_path):
    result = server.device_info_from_capture(str(tmp_path / "nope.txt"))
    assert result["error"] == "not_found"


def test_device_info_from_capture_gate(tmp_path):
    capture = tmp_path / "info.txt"
    capture.write_text(
        "Applications:\n"
        "  - Name: stateful-decode-and-sync\n"
        "    Running: True\n",
        encoding="utf-8",
    )
    result = server.device_info_from_capture(str(capture))
    assert result["app_running"] is True
    assert result["app_name"] == "stateful-decode-and-sync"


def test_device_info_from_capture_gate_false(tmp_path):
    capture = tmp_path / "info.txt"
    capture.write_text(
        "Applications:\n"
        "  - Name: stateful-decode-and-sync\n"
        "    Running: False\n",
        encoding="utf-8",
    )
    assert server.device_info_from_capture(str(capture))["app_running"] is False


def test_device_state_unreachable_service_is_reported_not_raised(monkeypatch):
    # Point at a port nothing listens on; the tool must return an error dict.
    monkeypatch.setattr(server, "SERVICE_PORT", 1)
    if not _client_available():
        pytest.skip("client not installed")
    result = server.device_state(timeout_s=0.5)
    assert result["error"] in {"service_unreachable", "state_request_failed"}


@needs_client
def test_build_motion_profile_hub_and_spoke():
    result = server.build_motion_profile(["Fist", "Paper"])
    assert "error" not in result, result
    assert result["num_classes"] == 2
    assert result["validation"]["valid"] is True
    assert result["definition_hash"].startswith("sha256:")


@needs_client
def test_build_motion_profile_rejects_bad_gesture():
    result = server.build_motion_profile(["NotARealGesture"])
    assert result["error"] == "profile_build_failed"


@needs_client
def test_inspect_and_hash_profile_roundtrip(tmp_path):
    built = server.build_motion_profile(["Fist", "Paper"])
    profile_file = tmp_path / "profile.json"
    import json
    profile_file.write_text(json.dumps(built["profile"]), encoding="utf-8")

    inspected = server.inspect_profile(str(profile_file))
    assert inspected["profile_shape"] == "hub_and_spoke"
    assert inspected["validation"]["valid"] is True

    hashed = server.profile_definition_hash(str(profile_file))
    assert hashed["matches"] is True


@needs_client
def test_list_recordings_missing_data_root(monkeypatch, tmp_path):
    monkeypatch.setenv("SCIENCE_MCP_DATA_ROOT", str(tmp_path / "absent"))
    result = server.list_recordings()
    assert result["recordings"] == []


@needs_client
def test_list_recordings_finds_marker_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("SCIENCE_MCP_DATA_ROOT", str(tmp_path))
    rec = tmp_path / "session-001"
    rec.mkdir()
    (rec / "provenance.json").write_text("{}", encoding="utf-8")
    result = server.list_recordings()
    assert result["count"] == 1
    assert result["recordings"][0]["artifacts"] == ["provenance.json"]
