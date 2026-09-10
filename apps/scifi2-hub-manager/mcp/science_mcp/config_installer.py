"""``science-mcp-install`` -- merge/upsert this MCP server into agent configs.

Adds a ``science-mcp`` entry to the MCP-server tables of Codex and Claude
configuration files, at user scope, repo scope, or both, without disturbing
other servers already present. Re-running is idempotent: an existing
``science-mcp`` entry is replaced in place (upsert), never duplicated, and every
other key in the file is preserved.

Target files
------------

Codex (TOML, ``[mcp_servers.<name>]`` tables):
  * user:  ``~/.codex/config.toml``
  * repo:  ``<repo>/.codex/config.toml``

Claude Code (JSON, ``mcpServers`` object):
  * user:  ``~/.claude.json``
  * repo:  ``<repo>/.mcp.json``

The entry written is::

    command = "science-mcp"
    args    = []
    env     = { SCIENCE_MCP_REPO_ROOT = "<repo>", SCIENCE_MCP_SERVICE_PORT = "18765" }

``science-mcp`` is the console script installed by this package's
``[project.scripts]``; it must be on PATH (or the venv active) for the agent to
launch it. Pass ``--command`` to write an absolute interpreter path instead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ._paths import repo_root

SERVER_NAME = "science-mcp"
DEFAULT_SERVICE_PORT = "18765"


# --------------------------------------------------------------------------- #
# Entry description
# --------------------------------------------------------------------------- #
def build_entry(command: str, repo: Path, service_port: str) -> dict[str, Any]:
    """Return the canonical server entry written into every target file."""
    return {
        "command": command,
        "args": [],
        "env": {
            "SCIENCE_MCP_REPO_ROOT": str(repo),
            "SCIENCE_MCP_SERVICE_PORT": str(service_port),
        },
    }


# --------------------------------------------------------------------------- #
# JSON targets (Claude)
# --------------------------------------------------------------------------- #
def upsert_json(path: Path, entry: dict[str, Any]) -> str:
    """Merge ``entry`` under ``mcpServers`` in a JSON file. Returns an action word."""
    existing: dict[str, Any] = {}
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            loaded = json.loads(text)
            if not isinstance(loaded, dict):
                raise ValueError(f"{path} does not contain a JSON object")
            existing = loaded
    servers = existing.get("mcpServers")
    if servers is None:
        servers = {}
        existing["mcpServers"] = servers
    if not isinstance(servers, dict):
        raise ValueError(f"{path}: 'mcpServers' is not an object")
    action = "updated" if SERVER_NAME in servers else "added"
    servers[SERVER_NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return action


# --------------------------------------------------------------------------- #
# TOML targets (Codex)
# --------------------------------------------------------------------------- #
def _toml_load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError as exc:  # pragma: no cover - <3.11 only
        raise RuntimeError("reading existing TOML requires Python 3.11+") from exc
    return tomllib.loads(text)


def _toml_dump(data: dict[str, Any]) -> str:
    """Serialize a config dict to TOML.

    Prefers ``tomli_w`` (a proper writer that round-trips floats, datetimes, and
    arrays-of-tables). The bundled minimal writer only covers the scalar/array/
    table shapes these MCP entries use and would mangle a real-world config, so
    it is used only when the whole file is a shape it can represent losslessly;
    otherwise this raises with an actionable install hint rather than corrupting
    an existing ``config.toml``.
    """
    try:
        import tomli_w
        return tomli_w.dumps(data)
    except ModuleNotFoundError:
        pass
    if _minimal_can_represent(data):
        return _minimal_toml_dump(data)
    raise RuntimeError(
        "This TOML config uses values the bundled writer cannot safely round-trip "
        "(e.g. floats, datetimes, or nested arrays-of-tables). Install the proper "
        "TOML writer and re-run: pip install tomli-w"
    )


def _minimal_can_represent(value: Any) -> bool:
    """True when ``value`` uses only the scalar/array/table shapes the minimal writer handles."""
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float, str)):
        return True
    if isinstance(value, list):
        return all(_minimal_can_represent(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _minimal_can_represent(v) for k, v in value.items())
    return False  # datetime, bytes, or anything else -> defer to tomli_w


def _fmt_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # repr keeps full precision; TOML accepts inf/nan spelled lowercase.
        if value != value:
            return "nan"
        if value == float("inf"):
            return "inf"
        if value == float("-inf"):
            return "-inf"
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)  # JSON string escaping is valid TOML basic-string
    raise TypeError(f"unsupported TOML scalar: {value!r}")


def _fmt_value(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_fmt_value(item) for item in value) + "]"
    if isinstance(value, dict):
        inner = ", ".join(f"{key} = {_fmt_value(val)}" for key, val in value.items())
        return "{" + inner + "}"
    return _fmt_scalar(value)


def _minimal_toml_dump(data: dict[str, Any], prefix: str = "") -> str:
    """Emit top-level scalars/arrays, then nested tables recursively."""
    lines: list[str] = []
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    for key, value in scalars.items():
        lines.append(f"{key} = {_fmt_value(value)}")
    if scalars and tables:
        lines.append("")
    for key, table in tables.items():
        name = f"{prefix}{key}"
        # Emit a [header] only when the table has its own scalar/array keys; a
        # table that holds only sub-tables (e.g. mcp_servers) needs no header of
        # its own -- its children carry the dotted path.
        has_scalars = any(not isinstance(v, dict) for v in table.values())
        if has_scalars or not table:
            lines.append(f"[{name}]")
        body = _minimal_toml_dump(table, prefix=f"{name}.")
        if body:
            lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n" if lines else ""


def upsert_toml(path: Path, entry: dict[str, Any]) -> str:
    """Merge ``entry`` under ``mcp_servers.<name>`` in a TOML file."""
    existing = _toml_load(path)
    servers = existing.get("mcp_servers")
    if servers is None:
        servers = {}
        existing["mcp_servers"] = servers
    if not isinstance(servers, dict):
        raise ValueError(f"{path}: 'mcp_servers' is not a table")
    action = "updated" if SERVER_NAME in servers else "added"
    servers[SERVER_NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_toml_dump(existing), encoding="utf-8")
    return action


# --------------------------------------------------------------------------- #
# Target planning
# --------------------------------------------------------------------------- #
def plan_targets(scope: str, repo: Path, home: Path) -> list[tuple[str, Path, str]]:
    """Return ``(kind, path, label)`` triples for the requested scope."""
    user = [
        ("toml", home / ".codex" / "config.toml", "Codex user"),
        ("json", home / ".claude.json", "Claude user"),
    ]
    repo_targets = [
        ("toml", repo / ".codex" / "config.toml", "Codex repo"),
        ("json", repo / ".mcp.json", "Claude repo"),
    ]
    if scope == "user":
        return user
    if scope == "repo":
        return repo_targets
    return user + repo_targets


def apply(scope: str, command: str, service_port: str, repo: Path, home: Path,
          dry_run: bool) -> list[tuple[str, str, Path]]:
    """Upsert the entry into every planned target. Returns ``(action, label, path)``."""
    entry = build_entry(command, repo, service_port)
    results: list[tuple[str, str, Path]] = []
    for kind, path, label in plan_targets(scope, repo, home):
        if dry_run:
            action = "would-update" if _entry_present(kind, path) else "would-add"
        elif kind == "json":
            action = upsert_json(path, entry)
        else:
            action = upsert_toml(path, entry)
        results.append((action, label, path))
    return results


def _entry_present(kind: str, path: Path) -> bool:
    try:
        if kind == "json":
            if not path.exists():
                return False
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
            return SERVER_NAME in data.get("mcpServers", {})
        return SERVER_NAME in _toml_load(path).get("mcp_servers", {})
    except (ValueError, json.JSONDecodeError):
        return False


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="science-mcp-install",
        description="Merge/upsert the science-mcp server into Codex and Claude configs.",
    )
    parser.add_argument("--scope", choices=("user", "repo", "both"), default="both",
                        help="which config files to update (default: both)")
    parser.add_argument("--command", default=SERVER_NAME,
                        help="command the agent launches (default: 'science-mcp' on PATH; "
                             "pass an absolute path for a specific venv)")
    parser.add_argument("--service-port", default=DEFAULT_SERVICE_PORT,
                        help="loopback NDJSON service port the server reads (default: 18765)")
    parser.add_argument("--repo-root", default=None,
                        help="repository root written into the entry env (default: autodetected)")
    parser.add_argument("--home", default=None, help=argparse.SUPPRESS)  # test hook
    parser.add_argument("--dry-run", action="store_true",
                        help="report the actions without writing any file")
    args = parser.parse_args(argv)

    repo = Path(args.repo_root).expanduser().resolve() if args.repo_root else repo_root()
    home = Path(args.home).expanduser().resolve() if args.home else Path.home()

    results = apply(args.scope, args.command, args.service_port, repo, home, args.dry_run)
    for action, label, path in results:
        print(f"  {action:<13} {label:<12} {path}")
    verb = "Planned" if args.dry_run else "Installed"
    print(f"{verb} '{SERVER_NAME}' into {len(results)} config file(s). "
          f"command={args.command!r} repo_root={repo}")
    if args.command == SERVER_NAME:
        print("Note: 'science-mcp' must be on PATH when the agent launches it "
              "(activate the venv, or re-run with --command <abs path to science-mcp>).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
