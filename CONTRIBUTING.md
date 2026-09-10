# Contributing to ScienceXYZ

Start with [README.md](README.md) for setup, [AGENTS.md](AGENTS.md) for repository constraints, [SECURITY.md](SECURITY.md) for data and physical-control requirements, and [TODO.md](TODO.md) for milestones. Prefer focused changes with a reproducible verification path.

## Development workflow

1. Inspect the current Git status and applicable `AGENTS.md` before editing. Preserve unrelated and uncommitted work, especially the WIP Exo integration. Consult open project `handoff` records and TODOs before creating new work; verify their claims against the tree.
2. Use 64-bit CPython 3.13 for Python tooling. Prefer C++ for persistent acquisition, processing, and host fusion; use Python where its SDK or existing integration makes it substantially simpler. On-device code derives from `synapse::App` and uses the App SDK; `vendor/synapse-cpp` is a host client. Use `vendor/synapse-api` as protocol authority.
3. Keep `vendor/` submodules read-only unless explicitly requested or working on the permitted `m053m716/` fork branch described in AGENTS.md. Commit authorized fork changes in the submodule and update the parent pointer. Do not fold unrelated submodule changes into a contribution.
4. Make the change, run focused hardware-free checks, and describe remaining operator acceptance separately. Never run `synapsectl` from an agent, test, or CI process. Follow the recorded operator CLI evidence instead of inventing flags or peripheral IDs.
5. Submit a PR explaining the problem, resulting behavior, affected contracts/defaults, verification commands and results, and material limitations. Include security implications when adding permissions, data export, dependencies, network listeners, model calls, or actuator commands.

## Local MCP development and verification

In an activated project Python environment, install the client and MCP packages together:

```bash
python -m pip install -e 'apps/scifi2-hub-manager/client[test,recording]'
python -m pip install -e 'apps/scifi2-hub-manager/mcp[test,analysis]'
python -m pytest apps/scifi2-hub-manager/mcp/tests -q
```

For changes spanning the client and MCP boundary, also run:

```bash
python -m pytest apps/scifi2-hub-manager/client/tests -q
```

Use offscreen Qt where required by the test environment and report skipped optional-dependency cases. C++ recorder checks and prerequisites are in [the recorder guide](docs/calibration-recording-mvp.md#host-recorder-build-and-use). Run checks relevant to the change; documentation-only edits need link, path, and diff review rather than a hardware trial.

See the [MCP README](apps/scifi2-hub-manager/mcp/README.md) for registration and environment variables. Preview `science-mcp-install --scope repo --dry-run`, review the chosen executable, then register the desired scope and reload the client. Avoid committing machine-specific executable paths or credentials. A configured server is not evidence that a client has loaded it or that the headstage is reachable.

## Adding an MCP tool or runtime assistant

Define the tool contract before implementation: version, purpose, capability class, bounded input/output schemas, units, target identity, timebase, freshness, side effects, authorization, timeout/cancellation behavior, replay policy, and structured errors. Declare annotations accurately but enforce policy below MCP. Keep query tools usable without motion or administration privileges. Do not expose arbitrary shell, SQL, serial bytes, paths, or URLs as shortcuts.

Reuse the existing controller/worker that owns the transport. The planned Exo suite should first expose state and calibrated preset descriptions, then separately authorized preset requests. Do not implicitly connect, arm, home, or change limits while answering a query. Use the local authorization and stop requirements in SECURITY.md.

For the planned online motor-unit decomposer/database in the SciFi-2 `kApplication`, first validate signal suitability and on-device CPU, memory, storage, and latency budgets. The current `src/main.cpp` routes `broadband_out` through the streaming decimator; do not assume it contains native-rate raw EMG suitable for decomposition. Document the chosen input branch, filter/rate/delay, gaps, and source identity. Keep bounded on-device processing and queries separate from host-side durable archival and cloud language interpretation. Database placement and retention must be decided from measured headstage limits.

Version motor-unit IDs within recording/model/tracking epochs; do not assume identity survives retraining, electrode changes, or sessions. Specify spike times, templates and units, quality measures, drift/merge/split behavior, and provenance. A count query must select a defined interval and inclusion criteria, expose uncertainty or unavailable evidence, and preserve raw data for reproducibility. Existing classifier loss/accuracy is not a motor-unit count.

For voice integration, use configurable provider/model adapters, explicit microphone activation, a typed intent representation, and a local dispatcher with least privilege. Preserve the user's distinction between a question and an action. Quoted commands, negation, ambiguous grasp names, background speech, and partial transcripts must not become movement. Establish per-session cloud export consent and bounded inference cost/latency; no API key is needed for ordinary development tests.

## Acceptance evidence

New boundaries need deterministic fixtures for unauthorized calls, malformed/oversized input, path escape, stale state, duplicates/replay, disconnects, timeouts, and prompt injection in tool/database content. Motion tests use fake transports and assert that rejected requests produce no hardware writes. Simulate API errors and delayed transcripts, verify stop priority and no automatic resumption, and distinguish acceptance from observed completion.

Decomposition work needs known synthetic signals plus consented labeled/replay data, reproducible quality/count metrics, and on-device performance evidence. Preserve source timestamps and sequences alongside host receipt/aligned timestamps; log any filtering, resampling, loss, or clock uncertainty. Ordinary tests must not silently substitute simulation for requested live evidence.

Progress from offline fixtures to a supervised unloaded bench before any approved worn-device experiment. Record date, software revision, firmware/device identity, calibration/limits, observed results and unresolved risks. Passing tests, a GUI screenshot, or a cached device ID does not establish physical safety or physiological accuracy.

## Documentation and continuity

Keep setup in README, security requirements in SECURITY, contribution workflow here, durable agent constraints in AGENTS, milestones in TODO, and concrete failure evidence in MISTAKES. Update affected protocol documentation and existing manuscripts when implementation changes their claims. Use GraphViz MCP and versioned bounded `.dot`/SVG sources when adding graph diagrams, following AGENTS.md.

Maintain one canonical handoff TODO per actionable outcome. Update partial scope rather than duplicating it; leave acceptance work open until verified. At a substantive handoff, record revision, uncommitted paths, verification, limitations, and the next concrete step; read back mutations. Never store API keys, private recordings, or participant identifiers in handoff records.
