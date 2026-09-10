# Security and physical-control guidance

## Scope and current status

ScienceXYZ is experimental acquisition and control infrastructure. This document sets contribution and deployment requirements; it does not certify that the WIP implementation already enforces them. Physical acceptance must be documented separately from automated tests.

Reviewed 2026-09-10: [science-mcp](apps/scifi2-hub-manager/mcp/science_mcp/server.py) exposes nine tools and two repository resources over stdio. Device access is read-only through the operator's NDJSON service (default `127.0.0.1:18765`). `synapsectl_command` only formats commands. Offline `analyze_recording` writes derived reports and can fit a baseline; it is not free of filesystem or compute side effects. Operator captures are historical evidence, not live identity checks.

The WIP [Exo service](apps/scifi2-hub-manager/client/scifi2_hub_manager/exo_service.py) has a separate NDJSON interface (default `127.0.0.1:18766`) and a single serial-owning worker. It is not yet an MCP suite. Its arm/home/pose commands can move hardware; signed joint values are not degrees. Its host watchdog returns toward rest, which itself causes movement and is not a certified emergency stop. Online motor-unit decomposition, its database, and voice-command integration are planned, not implemented.

## Trust and permissions

- Keep development-agent tools separate from an operator-launched runtime assistant. Shell, filesystem, browser, unrelated MCP servers, and deployment credentials must not be inherited by a voice-control runtime. Register only the tools needed for its task.
- Separate observation, derived-file creation, acquisition/model mutation, database administration, and physical motion capabilities. Enforce permissions in the service that owns the resource; tool descriptions, MCP annotations, prompts, and UI hiding are not authorization controls.
- Treat transcripts, model output, tool results, recording metadata, database text, and repository resources as untrusted data. None may change permissions, supply executable commands, choose arbitrary destinations, or authorize another tool. Validate bounded typed arguments and reject unknown fields, non-finite numbers, excessive sizes, and unsupported versions.
- Keep stdio services scoped to the operator account and network services on loopback by default. Loopback is not authentication against other local processes. Remote use requires authenticated identities, per-operation authorization, encrypted transport, endpoint/origin validation where applicable, and explicit deployment review. Do not expose the existing NDJSON ports or headstage directly to the internet.
- Preserve the [AGENTS.md Synapse CLI execution boundary](AGENTS.md#synapse-cli-execution-boundary): agents and CI never run `synapsectl`. An operator-run GUI may use its configurable CLI command under that rule. A future runtime interface does not grant development agents device-control authority.

## Exo motion requirements

An utterance is a proposed intent, not evidence of speaker identity or permission. Start with push-to-talk and explicit operator session ownership. Ambient audio, replayed speech, partial transcripts, and synthesized assistant speech must not trigger motion.

Before enabling motion tools, require local arming and a bounded, revocable authorization for the named device, calibrated pose or grasp, permitted range, and expiry. Show the resolved action to the operator; a session may preauthorize a narrow set of tested actions without a confirmation on every repetition. Arming, homing, recalibration, and changing limits require their own explicit scope. Model output cannot mint or widen authorization.

Validate authorization and fresh device state again at execution. Serialize commands through the existing owner; bound queues, reject expired requests, use request IDs with replay protection, and revoke pending commands on stop, disconnect, fault, or ownership change. A timeout means outcome unknown until reconciled from device evidence; never blindly retry movement. Distinguish requested, accepted, executing, completed, rejected, and unknown outcomes, and distinguish measured pose from commanded pose.

Map “open fully” and “precision grasp” to versioned, locally calibrated presets with device/hand identity and tested position, speed, and current/force limits as supported by verified hardware. Do not map “fully” directly to numeric extrema or invent a grasp from language. Unknown calibration or unavailable feedback must refuse motion. Preserve firmware checks; protocol ranges and existing current defaults are not human-safe operating limits.

Provide a locally accessible physical stop and a device-side fault/watchdog mechanism independent of the network, model, and host worker. Define and bench-test the appropriate stop response for the mechanism: holding, releasing, disarming, and returning to rest have different hazards. A voice “stop” is supplementary. Cloud failure must prevent new commands without delaying local stop handling or resuming stale commands on reconnection.

## Experimental data and cloud access

Keep API keys in an operator-controlled host secret store or injected environment, outside Git, headstage App packages, browser code, model context, logs, and handoff records. Use scoped credentials, rotation/revocation, request timeouts, rate limits, and spending limits. Treat a leaked key as compromised and rotate it; deleting a file is insufficient.

Cloud audio or experimental-data transfer is opt-in with a visible recording indicator and an explicit retention/export policy. Send the minimum selected audio and derived summary needed. Default to keeping raw neural/EMG streams, participant identifiers, database contents, and device credentials local. Redact identifiers before sending results to any model; read-only queries can still disclose data. Verify provider/account retention settings at implementation time rather than assuming zero retention.

Confine file inputs to approved roots and outputs to a separate derived-artifact root; resolve traversal, symlinks and Windows junctions, apply resource limits, and preserve immutable raw files. Current MCP path confinement is incomplete: several tools accept `Path(...).expanduser()` directly, and `SCIENCE_MCP_DATA_ROOT` is not a universal sandbox. Until hardened, use only with trusted local callers and inputs. The command formatter's joined string is not shell-safe quoting; consumers must not execute it automatically.

For motor-unit results, report session/source identity, source and host timestamps, analysis interval, stream preprocessing, algorithm/model revision, unit tracking epoch, quality criteria, and missing-data/synchronization status. “Units detected in this interval” is not the total anatomical motor-unit population. Return unavailable/insufficient evidence rather than zero when data or validation is missing. Do not infer “light” contraction from a transcript; use a labeled protocol and a measured intensity reference if that claim is required.

Persist bounded audit records for authorization, request ID, resolved arguments, configuration/model versions, state freshness, acceptance and completion/failure. Keep credentials and unnecessary raw audio out of the audit. Protect access, define retention, and preserve incident evidence without rewriting raw recordings.

## Reporting a security issue

Report suspected credential exposure, unintended motion, authorization bypass, or experimental-data disclosure privately to a repository maintainer through an established private contact. Use GitHub private vulnerability reporting if enabled; this repository does not currently document a dedicated security mailbox or response SLA. If no private route is available, ask a maintainer for one without posting exploit details or sensitive data publicly.

Include the affected revision/component, expected versus observed behavior, a minimal hardware-free reproduction when possible, and redacted logs. For a physical-control incident, use the established local stop procedure and prevent re-arming before investigation. Do not reproduce unsafe motion on a person to establish a report.

## API design references

OpenAI's [MCP and connectors guide](https://developers.openai.com/api/docs/guides/tools-connectors-mcp) documents remote HTTP transports, tool filtering, approvals, and prompt-injection risks. Those approvals supplement local device authorization. The preferred initial design here is an operator-hosted orchestrator using a restricted local MCP client and sending only selected results to the API; registering a local stdio server does not by itself connect it to the cloud API.

The [speech-to-text guide](https://developers.openai.com/api/docs/guides/speech-to-text) documents transcription options, including `gpt-4o-mini-transcribe` support. Evaluate currently available lightweight transcription and structured-intent models for accuracy, latency, and cost before choosing configurable model IDs. Neither transcription nor language-model inference belongs in the real-time motor-control or decomposition loop. References checked 2026-09-10.
