# science-mcp

An MCP (Model Context Protocol) toolkit that works alongside the running
`stateful-decode-and-sync` kApplication on the SciFi-2 headstage to help with
task/device development, model diagnostics, and offline data analysis.

It is a thin, agent-facing wrapper around the existing `nml-science-xyz` client
library. It exposes that library's **read-only** and **offline** capabilities to
any MCP client (Claude Code, Codex, etc.) as tools and resources.

## CLI execution boundary

Per `AGENTS.md`, an agent must never run `synapsectl` and must never control the
device. This server is agent-facing, so it holds to that boundary:

- It **never** runs `synapsectl`.
- It **never** issues device *control* commands (start/stop/capture/fit or any
  task mutation).
- Its only device access is **read-only**, through the operator-run loopback
  NDJSON control service (`run_service.py`), which is the single controller
  owner.
- `synapsectl_command` returns the exact command *string* for the operator to
  run by hand; it does not execute anything.

Everything else operates on files already on disk.

## Tools

| Tool | Group | What it does |
| --- | --- | --- |
| `device_state` | device (read-only) | Reads the current `AppState` snapshot via the loopback service. |
| `device_info_from_capture` | device (read-only) | Parses an operator-saved `synapsectl info` capture; reports the App `Running: True` gate. |
| `synapsectl_command` | boundary | Formats the exact `synapsectl start`/`info` line for the operator to run. |
| `build_motion_profile` | task / model | Builds a hub-and-spoke calibration profile from MOTION_LUT gesture keys + its canonical hash. |
| `inspect_profile` | task / model | Loads a profile JSON and reports shape, hash, labels, validity. |
| `profile_definition_hash` | task / model | Recomputes a profile's canonical hash and checks it against the stored value. |
| `list_recordings` | analysis | Lists recording directories under the data root, newest first. |
| `recording_summary` | analysis | Frame/task counts, channel layout, continuity counters for a raw HDF5 file. |
| `analyze_recording` | analysis | Full offline diagnostics (epochs, GPIO, plot, optional fit) into a fresh output dir. |

Resources: `science://repo/agents` (AGENTS.md), `science://repo/todo` (TODO.md).

## Setup

Prerequisites: 64-bit CPython 3.13 and the repo's virtual environment
(`README.md` at the repository root covers venv creation).

From the repository root, with the venv active:

```bash
# 1. Install the client library this toolkit wraps (with the recording extra
#    so the analysis tools work).
pip install -e "apps/stateful-decode-and-sync/client[recording]"

# 2. Install this toolkit (adds the science-mcp and science-mcp-install scripts).
pip install -e apps/stateful-decode-and-sync/mcp

# 3. Register the server in your agent config(s) (merge/upsert; see below).
#    With the venv active, science-mcp is already on PATH, so the simplest form is:
science-mcp-install --scope both
```

`--command` defaults to the bare `science-mcp` name, which works whenever the
venv is active when the agent launches. To pin the absolute path instead (robust
even without the venv active), pass the script location for your shell:

```bash
# bash / POSIX
science-mcp-install --scope both --command "$(which science-mcp)"
```

```powershell
# Windows PowerShell
science-mcp-install --scope both --command (Get-Command science-mcp).Source
```

```bat
:: Windows cmd.exe  ($(...) is NOT cmd syntax; find the path first)
where science-mcp
science-mcp-install --scope both --command "C:\path\to\.venv\Scripts\science-mcp.exe"
```

Restart your MCP client (or reload its config) to pick up the new server.

The installer depends on `tomli-w` (declared in `pyproject.toml`, installed by
step 2) to write Codex TOML, so it round-trips floats, datetimes, and other
values a real `config.toml` may already contain without corrupting them.

## Registering with agents (`science-mcp-install`)

`science-mcp-install` merges a `science-mcp` entry into the MCP-server tables of
Codex and Claude config files, at user scope, repo scope, or both. It is a
merge/upsert: an existing `science-mcp` entry is replaced in place (never
duplicated), and every other server and key in the file is preserved.

Targets:

| Scope | Codex (TOML) | Claude (JSON) |
| --- | --- | --- |
| user | `~/.codex/config.toml` | `~/.claude.json` |
| repo | `<repo>/.codex/config.toml` | `<repo>/.mcp.json` |

Options:

```
science-mcp-install [--scope user|repo|both] [--command CMD]
                    [--service-port PORT] [--repo-root DIR] [--dry-run]
```

- `--scope` — which files to touch (default `both`).
- `--command` — the command the agent launches (default `science-mcp` on PATH;
  pass an absolute path to pin a specific venv).
- `--service-port` — the loopback NDJSON service port the server reads
  (default `18765`, matching the calibration workflow).
- `--dry-run` — print the planned actions without writing anything.

Preview first:

```bash
science-mcp-install --scope both --dry-run
```

The entry written is:

```jsonc
"science-mcp": {
  "command": "science-mcp",
  "args": [],
  "env": {
    "SCIENCE_MCP_REPO_ROOT": "<repo>",
    "SCIENCE_MCP_SERVICE_PORT": "18765"
  }
}
```

## Environment variables

The server reads these at launch (the installer writes the first two):

- `SCIENCE_MCP_REPO_ROOT` — repository root (else autodetected by walking up).
- `SCIENCE_MCP_SERVICE_PORT` — loopback NDJSON service port (default `18765`).
- `SCIENCE_MCP_SERVICE_HOST` — loopback host (default `127.0.0.1`).
- `SCIENCE_MCP_DATA_ROOT` — recordings root (default `<repo>/data`).

## Using the device-state tool

`device_state` reads the live App snapshot, so the operator must first have the
control service running past the App-Running gate:

```bash
# operator terminal (past the synapsectl start + info gate):
service-main --device-ip 192.168.100.157 --port 18765
```

Then an agent can call `device_state` to read pipeline/model/task state. If the
service is not running the tool returns a structured `service_unreachable` error
rather than failing the call.

## Tests

Hardware-free, device-free:

```bash
pytest apps/stateful-decode-and-sync/mcp/tests -q
```

The installer tests are fully self-contained. The server tests skip gracefully
when the MCP SDK or the client library is not installed.
