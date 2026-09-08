#!/usr/bin/env python3
"""Verify the tracked config/motion_lut.json against the Reaction-Task source.

The host derives calibration task profiles from config/motion_lut.json and must
never read the JS repository at runtime (see AGENTS.md host-fusion boundary).
This checker is a developer tool: when the JS source is available it parses the
MOTION_LUT object and MOTION_CATALOG labels out of motion_catalog.js and reports
any drift from the tracked copy. It performs a tolerant text parse (no JS
engine) sufficient for the flat string maps in that file.

Usage:
    python scripts/check_motion_lut.py [--js PATH_TO_motion_catalog.js]

Exit status is nonzero if the tracked copy diverges or the source is missing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACKED = REPO_ROOT / "config" / "motion_lut.json"


def parse_lut(js_text: str) -> dict[str, str]:
    match = re.search(r"export const MOTION_LUT\s*=\s*\{(.*?)\};", js_text, re.DOTALL)
    if not match:
        raise ValueError("MOTION_LUT object not found in JS source")
    pairs = re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', match.group(1))
    return {key: value for key, value in pairs}


def parse_labels(js_text: str) -> dict[str, str]:
    match = re.search(r"export const MOTION_CATALOG\s*=\s*\[(.*?)\];", js_text, re.DOTALL)
    if not match:
        return {}
    labels: dict[str, str] = {}
    for entry in re.findall(r"\{(.*?)\}", match.group(1), re.DOTALL):
        key = re.search(r'key:\s*"([^"]+)"', entry)
        label = re.search(r'label:\s*"([^"]+)"', entry)
        if key and label:
            labels[key.group(1)] = label.group(1)
    return labels


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--js", default=None,
                        help="path to motion_catalog.js (default: 'source' field of the tracked copy)")
    args = parser.parse_args()

    tracked = json.loads(TRACKED.read_text(encoding="utf-8-sig"))
    js_path = Path(args.js) if args.js else Path(tracked.get("source", ""))
    if not js_path or not js_path.exists():
        print(f"JS source not available at {js_path!r}; cannot verify tracked copy.",
              file=sys.stderr)
        return 2

    js_text = js_path.read_text(encoding="utf-8-sig")
    src_lut = parse_lut(js_text)
    src_labels = parse_labels(js_text)

    ok = True
    if tracked["lut"] != src_lut:
        ok = False
        missing = set(src_lut) - set(tracked["lut"])
        extra = set(tracked["lut"]) - set(src_lut)
        changed = {k for k in set(src_lut) & set(tracked["lut"]) if src_lut[k] != tracked["lut"][k]}
        print("LUT drift detected:", file=sys.stderr)
        if missing:
            print(f"  in JS but not tracked: {sorted(missing)}", file=sys.stderr)
        if extra:
            print(f"  in tracked but not JS: {sorted(extra)}", file=sys.stderr)
        if changed:
            print(f"  id changed: {sorted(changed)}", file=sys.stderr)

    tracked_labels = tracked.get("labels", {})
    label_changed = {k for k in set(src_labels) & set(tracked_labels)
                     if src_labels[k] != tracked_labels[k]}
    if label_changed:
        ok = False
        print(f"Label drift for keys: {sorted(label_changed)}", file=sys.stderr)

    if ok:
        print("config/motion_lut.json matches the JS source.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
