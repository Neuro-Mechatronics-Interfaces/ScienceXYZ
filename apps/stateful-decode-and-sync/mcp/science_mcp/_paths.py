"""Filesystem discovery helpers shared by the server and installer.

The MCP server may be launched from any working directory, so it resolves the
repository root and data root from an explicit environment variable first and
then by walking up from this file. Nothing here touches the device.
"""

from __future__ import annotations

import os
from pathlib import Path

# Marker files that identify the ScienceXYZ repository root.
_ROOT_MARKERS = ("AGENTS.md", "vendor", ".git")


def repo_root() -> Path:
    """Return the ScienceXYZ repository root.

    ``SCIENCE_MCP_REPO_ROOT`` overrides discovery. Otherwise walk up from this
    file until a directory containing the root markers is found; fall back to
    four levels up (``.../apps/stateful-decode-and-sync/mcp/science_mcp``).
    """
    override = os.environ.get("SCIENCE_MCP_REPO_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if all((candidate / marker).exists() for marker in _ROOT_MARKERS):
            return candidate
    # mcp/science_mcp/_paths.py -> mcp -> stateful-decode-and-sync -> apps -> repo
    return here.parents[4]


def data_root() -> Path:
    """Return the recordings root (``SCIENCE_MCP_DATA_ROOT`` or ``<repo>/data``)."""
    override = os.environ.get("SCIENCE_MCP_DATA_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return repo_root() / "data"


def resolve_under(root: Path, candidate: str | os.PathLike[str]) -> Path:
    """Resolve ``candidate`` and confirm it stays within ``root``.

    Absolute paths are honoured but still confined to ``root`` so a tool cannot
    be pointed at arbitrary filesystem locations by a crafted argument.
    """
    root = root.resolve()
    path = Path(candidate)
    resolved = (path if path.is_absolute() else root / path).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path {resolved} is outside the permitted root {root}")
    return resolved
