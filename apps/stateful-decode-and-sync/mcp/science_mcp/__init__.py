"""MCP toolkit for the SciFi-2 stateful-decode-and-sync kApplication.

Two entry points are shipped:

* ``science-mcp`` -- the stdio MCP server (:mod:`science_mcp.server`).
* ``science-mcp-install`` -- the merge/upsert config installer
  (:mod:`science_mcp.config_installer`).

The server is agent-facing, so it honours the AGENTS.md Synapse CLI execution
boundary: it never runs ``synapsectl`` and never issues device *control*
commands (start/stop/capture/fit/task mutations). It only reads device state
through the operator-run loopback NDJSON service, parses operator-supplied
``synapsectl info`` captures, and performs offline task/model diagnostics and
recording analysis.
"""

__version__ = "0.1.0"
