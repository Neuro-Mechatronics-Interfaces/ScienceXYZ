"""Derive broadband channel layout from a Synapse device configuration.

The Cognescent passive dumper needs the channel COUNT and NAMES from the config
that produced the stream, not from inference over the wire (a frame's
``frame_data`` length is an unreliable proxy and mis-inferring it corrupts the
recording width). Each ``kBroadbandSource`` node in the device config declares
its signal channels; this module turns those nodes into typed layouts, one per
source, so multiple broadband sources can each be written to their own
``/devices/<n>`` group.

Channel names follow ``<type>_<n>`` (0-indexed within the source), where type is
the signal group name (``electrode``, ``gpio``, ...). The full config node is
kept as a JSON string so every recording records exactly which configuration
produced it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class BroadbandLayout:
    node_id: int
    sample_rate_hz: float | None
    output_layout: list[str]          # channel names, in frame_data order
    config_json: str                  # the kBroadbandSource node, serialized
    peripheral_id: int | None = None
    channel_types: list[str] = field(default_factory=list)  # per-channel type tag

    @property
    def n_ch(self) -> int:
        return len(self.output_layout)


def _iter_broadband_nodes(config):
    """Yield kBroadbandSource node dicts from a device config or provenance."""
    if not isinstance(config, dict):
        return
    # Accept either a raw device config ({nodes:[...]}) or a provenance object
    # whose configuration_input.snapshot holds it.
    nodes = config.get("nodes")
    if nodes is None:
        snapshot = (config.get("configuration_input") or {}).get("snapshot") if isinstance(
            config.get("configuration_input"), dict) else None
        nodes = (snapshot or {}).get("nodes") if isinstance(snapshot, dict) else None
    for node in nodes or []:
        if isinstance(node, dict) and node.get("type") == "kBroadbandSource":
            yield node


def layouts_from_config(config) -> list[BroadbandLayout]:
    """All broadband-source layouts in the config, in node order.

    ``config`` may be a device config (``{"nodes": [...]}``) or a provenance dict
    embedding one at ``configuration_input.snapshot``. Returns an empty list when
    no kBroadbandSource is present (the caller then falls back to wire inference).
    """
    layouts = []
    for node in _iter_broadband_nodes(config):
        source = node.get("broadbandSource", {}) or {}
        signal = source.get("signal", {}) or {}
        names, types = [], []
        # frame_data is ordered by the signal groups as declared. Iterate groups
        # in insertion order and number each 0-indexed within its type.
        for group_name, body in signal.items():
            channels = body.get("channels", []) if isinstance(body, dict) else []
            for index in range(len(channels)):
                names.append(f"{group_name}_{index}")
                types.append(group_name)
        layouts.append(BroadbandLayout(
            node_id=int(node.get("id", len(layouts))),
            sample_rate_hz=source.get("sample_rate_hz"),
            output_layout=names,
            channel_types=types,
            peripheral_id=source.get("peripheral_id"),
            config_json=json.dumps(node, separators=(",", ":"), sort_keys=True)))
    return layouts
