"""Merge/upsert behaviour of science-mcp-install: no duplicates, no clobber."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from science_mcp import config_installer as ci  # noqa: E402


def test_upsert_json_adds_and_preserves_siblings(tmp_path):
    target = tmp_path / ".mcp.json"
    target.write_text(json.dumps({
        "mcpServers": {"handoff": {"command": "handoff-mcp", "args": ["serve"]}},
        "otherTopLevel": {"keep": True},
    }), encoding="utf-8")
    entry = ci.build_entry("science-mcp", tmp_path, "18765")

    action = ci.upsert_json(target, entry)

    data = json.loads(target.read_text(encoding="utf-8"))
    assert action == "added"
    assert data["otherTopLevel"] == {"keep": True}           # sibling untouched
    assert data["mcpServers"]["handoff"]["command"] == "handoff-mcp"  # peer server kept
    assert data["mcpServers"]["science-mcp"] == entry


def test_upsert_json_is_idempotent_upsert(tmp_path):
    target = tmp_path / ".claude.json"
    entry_a = ci.build_entry("science-mcp", tmp_path, "18765")
    entry_b = ci.build_entry("/abs/science-mcp", tmp_path, "20000")

    assert ci.upsert_json(target, entry_a) == "added"
    assert ci.upsert_json(target, entry_b) == "updated"      # replaced, not duplicated

    data = json.loads(target.read_text(encoding="utf-8"))
    servers = data["mcpServers"]
    assert list(servers).count("science-mcp") == 1           # single key
    assert servers["science-mcp"] == entry_b                 # newest wins


def test_upsert_json_creates_missing_file_and_parents(tmp_path):
    target = tmp_path / "nested" / ".mcp.json"
    ci.upsert_json(target, ci.build_entry("science-mcp", tmp_path, "18765"))
    assert target.exists()
    assert "science-mcp" in json.loads(target.read_text())["mcpServers"]


def test_upsert_toml_adds_and_preserves_and_roundtrips(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(
        'model = "gpt-5"\n\n'
        '[mcp_servers.handoff]\n'
        'command = "handoff-mcp"\n'
        'args = ["serve"]\n',
        encoding="utf-8",
    )
    entry = ci.build_entry("science-mcp", tmp_path, "18765")

    action = ci.upsert_toml(target, entry)

    parsed = tomllib.loads(target.read_text(encoding="utf-8"))
    assert action == "added"
    assert parsed["model"] == "gpt-5"                         # scalar preserved
    assert parsed["mcp_servers"]["handoff"]["command"] == "handoff-mcp"
    assert parsed["mcp_servers"]["science-mcp"]["command"] == "science-mcp"
    assert parsed["mcp_servers"]["science-mcp"]["env"]["SCIENCE_MCP_SERVICE_PORT"] == "18765"


def test_upsert_toml_is_idempotent_upsert(tmp_path):
    target = tmp_path / "config.toml"
    ci.upsert_toml(target, ci.build_entry("science-mcp", tmp_path, "18765"))
    ci.upsert_toml(target, ci.build_entry("/abs/science-mcp", tmp_path, "9999"))

    parsed = tomllib.loads(target.read_text(encoding="utf-8"))
    assert parsed["mcp_servers"]["science-mcp"]["command"] == "/abs/science-mcp"
    assert parsed["mcp_servers"]["science-mcp"]["env"]["SCIENCE_MCP_SERVICE_PORT"] == "9999"
    # No duplicate table: the header appears exactly once.
    assert target.read_text().count("[mcp_servers.science-mcp]") == 1


def test_apply_both_scope_writes_four_files(tmp_path):
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    repo.mkdir()
    home.mkdir()
    results = ci.apply("both", "science-mcp", "18765", repo, home, dry_run=False)
    paths = {label: path for _, label, path in results}
    assert (home / ".codex" / "config.toml").exists()
    assert (home / ".claude.json").exists()
    assert (repo / ".codex" / "config.toml").exists()
    assert (repo / ".mcp.json").exists()
    assert set(paths) == {"Codex user", "Claude user", "Codex repo", "Claude repo"}


def test_dry_run_writes_nothing(tmp_path):
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    repo.mkdir()
    home.mkdir()
    results = ci.apply("both", "science-mcp", "18765", repo, home, dry_run=True)
    assert all(action.startswith("would-") for action, _, _ in results)
    assert not (repo / ".mcp.json").exists()
    assert not (home / ".claude.json").exists()


def test_main_repo_scope_end_to_end(tmp_path, capsys):
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    repo.mkdir()
    home.mkdir()
    rc = ci.main(["--scope", "repo", "--repo-root", str(repo), "--home", str(home)])
    assert rc == 0
    data = json.loads((repo / ".mcp.json").read_text())
    assert data["mcpServers"]["science-mcp"]["env"]["SCIENCE_MCP_REPO_ROOT"] == str(repo.resolve())
    assert "Installed 'science-mcp'" in capsys.readouterr().out
    # user files were NOT touched under repo scope
    assert not (home / ".claude.json").exists()


def test_upsert_toml_preserves_float_scalars(tmp_path):
    # Regression: a real Codex config.toml may hold a float (e.g. a timeout).
    # The merge must round-trip it, not crash on an "unsupported TOML scalar".
    target = tmp_path / "config.toml"
    target.write_text(
        'request_timeout = 60.0\n'
        'temperature = 0.7\n\n'
        '[mcp_servers.handoff]\n'
        'command = "handoff-mcp"\n',
        encoding="utf-8",
    )
    ci.upsert_toml(target, ci.build_entry("science-mcp", tmp_path, "18765"))
    parsed = tomllib.loads(target.read_text(encoding="utf-8"))
    assert parsed["request_timeout"] == 60.0
    assert parsed["temperature"] == 0.7
    assert "science-mcp" in parsed["mcp_servers"]
    assert "handoff" in parsed["mcp_servers"]


def test_minimal_toml_dump_handles_float(monkeypatch):
    # Force the tomli_w-absent path to exercise the bundled writer's float support.
    import builtins
    real_import = builtins.__import__

    def no_tomli_w(name, *args, **kwargs):
        if name == "tomli_w":
            raise ModuleNotFoundError("tomli_w")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_tomli_w)
    text = ci._toml_dump({"timeout": 60.0, "mcp_servers": {"science-mcp": {"command": "x"}}})
    parsed = tomllib.loads(text)
    assert parsed["timeout"] == 60.0
    assert parsed["mcp_servers"]["science-mcp"]["command"] == "x"


def test_toml_dump_refuses_unrepresentable_without_tomli_w(monkeypatch):
    # A shape the minimal writer cannot round-trip (datetime) must raise an
    # actionable install hint rather than corrupt the file, when tomli_w is absent.
    import builtins
    from datetime import datetime
    real_import = builtins.__import__

    def no_tomli_w(name, *args, **kwargs):
        if name == "tomli_w":
            raise ModuleNotFoundError("tomli_w")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_tomli_w)
    with pytest.raises(RuntimeError, match="pip install tomli-w"):
        ci._toml_dump({"created": datetime(2026, 1, 1)})


def test_minimal_toml_dump_matches_tomllib_roundtrip():
    data = {
        "top": "value",
        "mcp_servers": {
            "science-mcp": {
                "command": "science-mcp",
                "args": [],
                "env": {"SCIENCE_MCP_REPO_ROOT": "C:\\repo", "SCIENCE_MCP_SERVICE_PORT": "18765"},
            }
        },
    }
    text = ci._minimal_toml_dump(data)
    parsed = tomllib.loads(text)
    assert parsed["top"] == "value"
    assert parsed["mcp_servers"]["science-mcp"]["env"]["SCIENCE_MCP_REPO_ROOT"] == "C:\\repo"
    assert parsed["mcp_servers"]["science-mcp"]["args"] == []
