"""Verify or explicitly refresh the SDK's portable canonical schema snapshot.

python scripts/sync_protocol.py --repo /path/to/ScienceXYZ [--update]
Only --update writes. No CLI/device access. CPython 3.13 baseline.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    sdk = Path(__file__).resolve().parents[1]
    repo = args.repo.resolve()
    sources = {"gui_control.proto": repo / "apps/scifi2-hub-manager/proto/gui_control.proto",
               "SYNAPSE_API_COPYRIGHT": repo / "vendor/synapse-api/COPYRIGHT"}
    sources.update({str(p.relative_to(repo / "vendor/synapse-api")).replace("\\", "/"): p
                    for p in (repo / "vendor/synapse-api/api").rglob("*.proto")})
    if not any(name.startswith("api/") for name in sources):
        raise SystemExit("canonical synapse-api schemas missing; initialize submodules")
    hashes = {}
    different = []
    for name, source in sorted(sources.items()):
        # Normalize line endings for cross-platform Git checkouts.
        data = source.read_bytes().replace(b"\r\n", b"\n")
        hashes[name] = hashlib.sha256(data).hexdigest()
        target = sdk / "proto" / name
        if args.update:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        elif not target.exists() or target.read_bytes().replace(b"\r\n", b"\n") != data:
            different.append(name)
    extras = {p.relative_to(sdk / "proto").as_posix() for p in (sdk / "proto/api").rglob("*.proto")} - set(sources)
    if extras:
        # Deliberately do not silently remove protocol files on refresh.
        raise SystemExit(f"obsolete schema files require review: {sorted(extras)}")
    if args.update:
        revision = subprocess.check_output(["git", "-C", str(repo / "vendor/synapse-api"), "rev-parse", "HEAD"], text=True).strip()
        (sdk / "proto/manifest.json").write_text(json.dumps({"synapse_api_revision": revision,
            "gui_control_source": "apps/scifi2-hub-manager/proto/gui_control.proto",
            "sha256_lf": hashes}, indent=2) + "\n", encoding="utf-8")
    else:
        manifest = json.loads((sdk / "proto/manifest.json").read_text(encoding="utf-8"))
        if manifest["sha256_lf"] != hashes:
            different.append("manifest.json")
    if different:
        raise SystemExit(f"SDK schema drift: {different}; review changes then rerun --update")
    print(f"Verified {len(sources)} canonical files; no device access")


if __name__ == "__main__":
    main()
