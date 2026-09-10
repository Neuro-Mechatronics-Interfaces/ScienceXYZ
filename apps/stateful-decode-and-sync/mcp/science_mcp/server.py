"""Stdio MCP server for the SciFi-2 stateful-decode-and-sync kApplication.

The server is agent-facing and therefore honours the AGENTS.md Synapse CLI
execution boundary. It NEVER runs ``synapsectl`` and NEVER issues device control
commands (start/stop/capture/fit/task mutations). Its device-facing tools are
strictly read-only and reach the device only through the operator-run loopback
NDJSON control service (the single controller owner). Everything else is offline
analysis of files already on disk.

Tool groups
-----------
* Device introspection (read-only): ``device_state``, ``device_info_from_capture``.
* Task / model diagnostics: ``build_motion_profile``, ``inspect_profile``,
  ``profile_definition_hash``.
* Recording analysis: ``list_recordings``, ``recording_summary``,
  ``analyze_recording``.
* Boundary awareness: ``synapsectl_command`` returns the exact operator command
  string to run by hand (it does NOT execute it).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:  # mcp 2.x renamed FastMCP -> MCPServer; both expose tool/resource/run.
    from mcp.server.mcpserver import MCPServer as _Server
except ModuleNotFoundError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from ._paths import data_root, repo_root, resolve_under

# The loopback NDJSON control service the operator runs (run_service.py).
import os

SERVICE_HOST = os.environ.get("SCIENCE_MCP_SERVICE_HOST", "127.0.0.1")
SERVICE_PORT = int(os.environ.get("SCIENCE_MCP_SERVICE_PORT", "18765"))
APP_NAME = "stateful-decode-and-sync"

mcp = _Server(
    "science-mcp",
    instructions=(
        "Read-only device introspection and offline diagnostics for the SciFi-2 "
        "stateful-decode-and-sync kApplication. This server never runs synapsectl "
        "and never controls the device; device state is read through the "
        "operator-run loopback NDJSON service on "
        f"{SERVICE_HOST}:{SERVICE_PORT}. To start/stop the device App the operator "
        "runs synapsectl by hand -- use synapsectl_command to get the exact line."
    ),
)


# --------------------------------------------------------------------------- #
# Client-library import (deferred; gives a clear message if not installed)
# --------------------------------------------------------------------------- #
def _client_module(name: str):
    try:
        return __import__(f"stateful_decode_and_sync.{name}", fromlist=[name])
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "The nml-science-xyz client is not importable. Install it into the "
            "same environment as science-mcp: "
            "pip install -e apps/stateful-decode-and-sync/client"
        ) from exc


# --------------------------------------------------------------------------- #
# Device introspection (read-only)
# --------------------------------------------------------------------------- #
@mcp.tool()
def device_state(timeout_s: float = 5.0) -> dict[str, Any]:
    """Read the current AppState snapshot from the running kApplication.

    Connects to the operator-run loopback NDJSON control service
    (``run_service.py``) and issues a single ``get_state`` request. This is
    read-only: it opens no device taps of its own and sends no control command.
    Requires the operator to have the service running and the device App started.

    Returns the JSON state snapshot (pipeline/model/task fields) or an ``error``
    describing why the service could not be reached.
    """
    client_mod = _client_module("client")
    NdjsonClient = client_mod.NdjsonClient
    try:
        with NdjsonClient(SERVICE_HOST, SERVICE_PORT, timeout=timeout_s) as client:
            return {"service": f"{SERVICE_HOST}:{SERVICE_PORT}",
                    "state": client.get_state_snapshot(timeout=timeout_s)}
    except OSError as exc:
        return {"error": "service_unreachable",
                "detail": f"cannot reach loopback service {SERVICE_HOST}:{SERVICE_PORT}: {exc}",
                "hint": "start it with: service-main --device-ip <DEV> --port "
                        f"{SERVICE_PORT} (operator, past the App-Running gate)"}
    except Exception as exc:  # noqa: BLE001 - report any client-side failure as data
        return {"error": "state_request_failed", "detail": str(exc)}


@mcp.tool()
def device_info_from_capture(info_capture_path: str) -> dict[str, Any]:
    """Parse an operator-supplied ``synapsectl info`` capture.

    Does NOT run synapsectl. The operator saves ``synapsectl -u <DEV> info`` to a
    text file; this reports whether the ``stateful-decode-and-sync`` App shows
    ``Running: True`` (the standing bench gate) plus the raw text for reference.
    """
    launcher = _import_launcher()
    path = Path(info_capture_path).expanduser()
    if not path.exists():
        return {"error": "not_found", "detail": f"no info capture at {path}"}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "path": str(path),
        "app_name": APP_NAME,
        "app_running": bool(launcher.app_running(text)),
        "capture_text": text if len(text) <= 20000 else text[:20000] + "\n...[truncated]",
    }


@mcp.tool()
def synapsectl_command(device_uri: str, config_path: str | None = None) -> dict[str, Any]:
    """Return the exact operator ``synapsectl`` command to run by hand.

    This server never executes synapsectl (AGENTS.md CLI boundary). It only
    formats the command the operator should run: ``start <config>`` when a config
    path is given, else ``info``. Run the returned ``command`` yourself.
    """
    if config_path:
        argv = ["synapsectl", "-u", device_uri, "start", config_path]
        purpose = "deploy+start the device App, then save `synapsectl -u <DEV> info` to gate on Running: True"
    else:
        argv = ["synapsectl", "-u", device_uri, "info"]
        purpose = "read device/App status; save to a file for device_info_from_capture"
    return {"command": " ".join(argv), "argv": argv, "purpose": purpose,
            "note": "operator-run only; this server does not execute it"}


# --------------------------------------------------------------------------- #
# Task / model diagnostics
# --------------------------------------------------------------------------- #
@mcp.tool()
def build_motion_profile(gesture_keys: list[str], revision: int = 1) -> dict[str, Any]:
    """Build a hub-and-spoke calibration task profile from MOTION_LUT gesture keys.

    Wraps ``motion_profile.make_hub_spoke_profile``. Gesture keys are MOTION_LUT
    CamelCase names (e.g. ``Fist``, ``Paper``). Returns the profile dict plus its
    canonical ``definition_hash`` (which must equal the C++ app's
    ``task::canonical_json`` hash) and a structural validation result. Offline;
    nothing is deployed.
    """
    mp = _client_module("motion_profile")
    try:
        profile = mp.make_hub_spoke_profile(gesture_keys, revision=revision)
    except Exception as exc:  # noqa: BLE001 - surface validation errors as data
        return {"error": "profile_build_failed", "detail": str(exc)}
    report = _validate(mp, profile)
    return {"profile": profile, "definition_hash": profile.get("definition_hash"),
            "num_classes": profile.get("num_classes"), "validation": report}


@mcp.tool()
def inspect_profile(profile_path: str) -> dict[str, Any]:
    """Load a task-profile JSON and report its shape, hash, labels, and validity."""
    mp = _client_module("motion_profile")
    path = Path(profile_path).expanduser()
    if not path.exists():
        return {"error": "not_found", "detail": f"no profile at {path}"}
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"error": "invalid_json", "detail": str(exc)}
    summary = {
        "path": str(path),
        "profile_shape": profile.get("profile_shape"),
        "definition_hash": profile.get("definition_hash"),
        "num_classes": profile.get("num_classes"),
        "state_to_label": profile.get("state_to_label"),
    }
    summary["validation"] = _validate(mp, profile)
    return summary


@mcp.tool()
def profile_definition_hash(profile_path: str) -> dict[str, Any]:
    """Recompute the canonical definition hash of a profile file.

    Reports the recomputed hash and whether it matches the ``definition_hash``
    stored in the file -- a mismatch means the profile was hand-edited without
    rehashing, which the device App would reject.
    """
    mp = _client_module("motion_profile")
    path = Path(profile_path).expanduser()
    if not path.exists():
        return {"error": "not_found", "detail": f"no profile at {path}"}
    profile = json.loads(path.read_text(encoding="utf-8"))
    definition = profile.get("definition", profile)
    recomputed = mp.definition_hash(definition)
    stored = profile.get("definition_hash")
    return {"path": str(path), "recomputed_hash": recomputed, "stored_hash": stored,
            "matches": stored is not None and recomputed == stored}


def _validate(mp, profile: dict[str, Any]) -> dict[str, Any]:
    try:
        mp.validate_profile(profile)
        return {"valid": True, "problems": []}
    except Exception as exc:  # noqa: BLE001
        return {"valid": False, "problems": [str(exc)]}


# --------------------------------------------------------------------------- #
# Recording analysis
# --------------------------------------------------------------------------- #
@mcp.tool()
def list_recordings(subdir: str = "", limit: int = 50) -> dict[str, Any]:
    """List recording directories under the data root (``<repo>/data`` by default).

    Reports directories that look like recordings (contain ``raw.h5``,
    ``data.hdf5``, or ``provenance.json``), newest first. ``subdir`` narrows the
    search under the data root; paths are confined to the data root.
    """
    root = data_root()
    try:
        base = resolve_under(root, subdir) if subdir else root
    except ValueError as exc:
        return {"error": "path_outside_root", "detail": str(exc)}
    if not base.exists():
        return {"data_root": str(root), "recordings": [], "note": f"{base} does not exist"}
    markers = ("raw.h5", "data.hdf5", "provenance.json")
    found = []
    for path in base.rglob("*"):
        if path.is_dir() and any((path / m).exists() for m in markers):
            present = [m for m in markers if (path / m).exists()]
            found.append((path.stat().st_mtime, str(path), present))
    found.sort(reverse=True)
    return {"data_root": str(root), "count": len(found),
            "recordings": [{"path": p, "artifacts": a} for _, p, a in found[:limit]]}


@mcp.tool()
def recording_summary(raw_path: str, max_frames: int = 2_000_000) -> dict[str, Any]:
    """Summarize a raw HDF5 recording without full analysis.

    Reads the raw broadband recording (``sciencexyz.raw_taps.v1``) and reports
    frame/task counts, channel layout, continuity counters, and integrity
    problems. Read-only; requires the ``analysis`` extra (h5py). For epoch/label
    diagnostics or model fitting use ``analyze_recording`` instead.
    """
    try:
        ra = _client_module("recording_analysis")
    except RuntimeError as exc:
        return {"error": "analysis_unavailable", "detail": str(exc)}
    path = Path(raw_path).expanduser()
    if not path.exists():
        return {"error": "not_found", "detail": f"no recording at {path}"}
    try:
        data = ra.read_recording(str(path), max_frames)
    except Exception as exc:  # noqa: BLE001 - schema/size errors are data
        return {"error": "read_failed", "detail": str(exc)}
    return {
        "path": data["path"],
        "sha256": data["sha256"],
        "status": data["status"],
        "frame_count": len(data["frames"]),
        "task_events": len(data["events"]),
        "layout": data["layout"],
        "counters": dict(data["counters"]),
        "problems": data["problems"],
        "local_stop": data["local_stop"],
    }


@mcp.tool()
def analyze_recording(raw_path: str, output_dir: str, profile_path: str | None = None,
                      fit: bool = False, journal_path: str | None = None,
                      max_frames: int = 2_000_000) -> dict[str, Any]:
    """Run full offline diagnostics on a recording (epochs, GPIO, plot, optional fit).

    Wraps ``analyze_recording.analyze``: reopens the raw HDF5 read-only, builds
    epochs against an optional task profile, cross-checks an optional journal,
    writes CSV/PNG/JSON diagnostics into a fresh ``output_dir``, and optionally
    fits a baseline model. The raw input is never modified. Returns the report
    summary and the output directory. Requires the ``analysis`` extra.
    """
    try:
        analyze_mod = _import_analyze()
        ct = _client_module("calibration_task")
    except RuntimeError as exc:
        return {"error": "analysis_unavailable", "detail": str(exc)}
    raw = Path(raw_path).expanduser()
    out = Path(output_dir).expanduser()
    if not raw.exists():
        return {"error": "not_found", "detail": f"no recording at {raw}"}
    if out.exists():
        return {"error": "output_exists",
                "detail": f"{out} already exists; analyze writes into a fresh directory"}
    profile = None
    if profile_path:
        pp = Path(profile_path).expanduser()
        if not pp.exists():
            return {"error": "not_found", "detail": f"no profile at {pp}"}
        profile = ct.load_profile(str(pp))
    try:
        report = analyze_mod.analyze(str(raw), str(out), profile, fit,
                                     max_frames, journal_path)
    except Exception as exc:  # noqa: BLE001 - analysis rejections are data
        return {"error": "analysis_failed", "detail": str(exc)}
    keys = ("frame_count", "task_events", "valid_epochs", "invalid_epochs",
            "gpio_changes", "raw_unchanged", "problems")
    return {"output_dir": str(out),
            "summary": {k: report[k] for k in keys if k in report},
            "fit_refused": report.get("fit_refused"),
            "physical_sync_verified": report.get("physical_sync_verified")}


# --------------------------------------------------------------------------- #
# Deferred imports of module-scripts (not part of the package)
# --------------------------------------------------------------------------- #
def _import_launcher():
    import run_calibration_session
    return run_calibration_session


def _import_analyze():
    try:
        import analyze_recording
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError(
            "analyze_recording is not importable; install the client with its "
            "recording extra: pip install -e 'apps/stateful-decode-and-sync/client[recording]'"
        ) from exc
    return analyze_recording


# --------------------------------------------------------------------------- #
# Repository resources (handy read-only context for the agent)
# --------------------------------------------------------------------------- #
@mcp.resource("science://repo/agents")
def agents_md() -> str:
    """The repository AGENTS.md (durable rules, incl. the CLI execution boundary)."""
    return (repo_root() / "AGENTS.md").read_text(encoding="utf-8")


@mcp.resource("science://repo/todo")
def todo_md() -> str:
    """The repository TODO.md (current milestones and open work)."""
    return (repo_root() / "TODO.md").read_text(encoding="utf-8")


def main() -> None:
    """Console-script entry point: run the stdio MCP server."""
    print(f"science-mcp starting (service {SERVICE_HOST}:{SERVICE_PORT}, "
          f"repo {repo_root()})", file=sys.stderr, flush=True)
    mcp.run()


if __name__ == "__main__":
    main()
