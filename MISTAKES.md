# Mistakes

### 2026-09-11 - Android polling disabled pose controls and exposed an arm/pose timing race

Operator connected MyoHID and reported flickering Send pose/working state and
success only when pressing Enable All and Send pose quickly. Android reused its
command-busy flag for every 250ms poll. The tracked device config also has a
1000ms inactivity watchdog; separate button presses leave an arm-to-pose window
that can expire (live watchdog settings were not queried). Android polling now
leaves command availability unchanged; the explicitly labelled Enable + send
pose action sends EXTERNAL then pose consecutively on the owning executor.
New session-gated app decode mappings use the same sequence for fresh events.
Watchdog/gates remain unchanged; no timer re-arm, pose keepalive, or motion retry.
Android fake-client regression tests were added; build/test/device validation
of this change remains with the operator as requested.

### 2026-09-11 - Two-source App graph rejected by the server

After restoring exo node 3, operator configuration failed with "Node 2 already
has an incoming connection" on Synapse 2.4.1. Protobuf parsing and SDK reader
inspection did not validate server graph semantics. Removed the second edge
and the App's edge requirement; retain the configured source and subscribe by
node ID using the SDK. Added a tracked-config single-input regression check.
This auxiliary subscription still needs operator verification that the server
starts/publishes an unconnected source. Do not equate SDK support or schema
validity with server acceptance. This supersedes the preceding edge repair.

### 2026-09-11 - Exo source parameter referenced an absent graph node

Operator info showed the new Exo BroadbandSource peripheral 300, but App startup
failed because `exo_source_node_id=3` referenced no configured node. The current
tracked JSON also lacked node 3 and its edge to App 2, contradicting the earlier
handoff's configuration claim; when the omission occurred is not established.
Restored the 10 Hz, 36-channel motor source and connection, preserving RHD and
App settings. Canonical protobuf parsing and graph-reference checks pass.
Operator must stop/start with the corrected JSON; rebuilding the App alone does
not install a device graph. Physical stream acceptance remains pending.

### 2026-09-11 - Native Exo SDK rejected legacy output Tap metadata

Operator built MyoHID with the SDK AAR and reported `wrong Tap direction:
command_result (status 2)` both while the device App was running and stopped.
The native SDK required explicit PRODUCER metadata; the installed working
Python Tap client also subscribes to UNSPECIFIED outputs. The mock advertised
only explicit directions, missing this compatibility case. Accept UNSPECIFIED
for the named read-only result/state Taps, keep control explicitly CONSUMER,
and include numeric metadata in errors. Added localhost handshake/state/C ABI
coverage for omitted direction and rejection tests for invalid directions.
The exact live metadata value is inferred, not captured from the device.

### 2026-09-11 - Exo reconnect reused the previous App's Tap session

Operator rebuilt/deployed the App, then supplied repeated client timeouts and
CDC control=1/data=2 reply-route silence. Fresh info listed RHD 200, Exo 300 and
App Running True; its July log tail was historical. GUI Connect only called
controller.connect(), which is a no-op when its old session is still marked
connected. It now disconnects/re-discovers Taps and establishes an acknowledged
state request before the USB command. Installed Tap.read supports timeout_ms;
the transport had incorrectly claimed it did not, and closed sockets while
reader threads could still use them. Receives now honor 500ms and disconnect
joins readers before closing. Regression tests cover ordering and timeouts.
CDC open additionally clears then asserts DTR after normal baud setup. Physical
board-side silence is not proven fixed; matched deployment/retry is pending.
Candidate rule: after process restart, refresh discovered endpoints and verify
an application round trip before diagnosing a downstream device timeout.

### 2026-09-11 - Raw Exo command omitted from protocol validation

Operator reports digit controls work but terminal `version` / `info;` time out
waiting for a client request ID. Raw dispatch and worker code existed, but
`control_protocol.hpp` omitted COMMAND_EXO_RAW from both the known-command list
and payload validation. Incoming requests were dropped without a correlated
rejection before reaching the worker. Added recognition and bounded single-line
payload validation; the default-off raw authorization gate remains in the App.
Protocol round-trip regression tests now cover both reported commands, missing
payload, and malformed text. Candidate rule: exercise new commands through the
wire validator, not only the worker and mocked GUI. Bench retry requires App
rebuild/redeployment; local passing tests do not prove deployed behavior.

### 2026-09-11 - CPack filename omitted the Synapse RPC package version

The operator's axon-exo deployment failed with `Package version is required`.
The `.deb` had Version 0.1.0 internally, but CPack's default hyphenated
`scifi-axon-exo-0.1.0-Linux.deb` filename did not match the CLI's
`package_version_arch.deb` split, so `create_metadata` sent an empty RPC
version. Set `CPACK_DEBIAN_FILE_NAME=DEB-DEFAULT`, rebuilt as
`scifi-axon-exo_0.1.0_arm64.deb`, and checked filename/metadata agreement.
Candidate rule: validate both Debian control metadata and the deployment
client's filename-derived metadata before delivering a package.

### 2026-09-11 - Portable SDK build and PUB/SUB startup assumptions

**Attempt:** Build a portable Exo DLL/JNI client and validate its live transport
against a localhost-only mock, then cross-build dependencies for Android.
**Failures/causes:** MSVC used /MD while the selected vcpkg dependencies used
/MT (LNK2038); Java found exo_jni.dll but not its dependent exo_control.dll;
the Windows loopback test received command replies before the state SUB was
ready. The pinned vcpkg rejected an assumed `--classic` switch; its normal
package-list install already selects classic mode outside a manifest directory.
**Corrections:** Match the static triplet's CRT, load exo_control explicitly
before exo_jni, and require an initial state plus acknowledged read-only
handshake before reporting connected. Use fresh IDs for retried GET_STATE so
the App republishes a snapshot instead of only replaying a cached result.
Windows/Linux controller and real localhost transport tests and Windows JNI
smoke now pass. The Android OpenSSL parallel install also hit a Makefile rename
race (`Device or resource busy`); serial compilation/install succeeded. Build
OpenSSL alone with VCPKG_MAX_CONCURRENCY=1, then restore parallelism for the
remaining dependencies. The porting guide records that reproducible workaround.
An attempted Linux-wide `--exclude-libs,ALL` also caused duplicate generated
protobuf registration when tests linked the C++ core and the C DLL against one
shared libprotobuf. Restrict symbol hiding to the Android static-dependency
bundle; do not apply that isolation setting to a shared protobuf runtime.
The first Android AAR bundled NDK 27 libc++_shared.so, whose RELRO end failed
the 16 KB check even though its LOAD segments passed. The final SDK links private
static runtimes behind the C boundary, hides all implementation symbols, and
exports only 14 C/3 JNI functions. The final AAR's two libraries pass both
alignment and dependency checks; no shared runtime is bundled.
**Candidate prevention:** Verify DLL loading in a JVM and subscription readiness
in a real local PUB/SUB test; compilation and a terminal command reply alone do
not prove a complete connection.

### 2026-09-10 - Exo probe/limits/angles all failed identically: no reply_route ACK

**Attempt:** First bench run of the wireless Exo path (exo-integration.md): App
built/deployed, device started with `rhd2132_with_exo.json`, `run_service.py`
started, then `exo_via_scifi.py probe|limits|angles > exo-*.log`.

**Failure:** All three logs were byte-identical (1143 B). Each raised
`RemoteCommandError: no 'OK: reply_route cmd' ack for set_reply_route:cmd;
outcome unknown; link closed` on the FIRST step, `set_exo_mode connected`. The
`probe`/`limits`/`angles`-specific `query_exo` was never reached; the only
`succeeded` line in each log is the `set_exo_mode off` the script's `finally`
runs during cleanup.

**Cause (reconciled against firmware + USB notes; not yet bench-confirmed):**
Not a host-client or config bug. The device App's `exo_link.cpp do_connect()`
opened/claimed the USB CDC pair (interface 0) but got no `OK: reply_route cmd`
reply to `set_reply_route:cmd` within the 1500 ms budget, so it closed the link
(exo_link.cpp:245, send_command/read_until). The `run_service.py` path is
correct for this architecture (it drives the on-device App over the Synapse
Tap; `run_exo_service.py` is the *separate* laptop-attached-exo path and was not
in play). The firmware on branch `feat/set_finger_angles` DOES implement
`set_reply_route` and acks the exact string `OK: reply_route cmd` framed with
`;` (utils.cpp:1066-1084, COMMAND_DELIMITER=";"), and command parsing/framing
match the App (`getArg` splits on ':', LineReader needs '\n', App sends
"...\r\n"). So on a correctly-flashed dual-CDC OpenRB the handshake should
succeed. The two remaining device-side causes, both producing this exact
timeout, are: (1) the OpenRB is flashed with firmware OLDER than this branch
(no `set_reply_route`); an unknown command is answered only by `debugPrint`
(silent unless VERBOSE) with no `ERROR:` (utils.cpp:1712), so the App times out;
(2) USB enumeration assigned `SerialTelem` to interface 0 (the documented
"static-init order surprise", config.h:85-91) so the ACK, routed to CMD_SERIAL
after route=cmd, lands on the physical interface the App does not read
(the firmware needs `DUAL_CDC_SWAP`).

**Correction:** Diagnosis recorded; no code change to the handshake. Decisive
next bench step (operator, not agent): confirm the OpenRB firmware version and
that a direct serial `set_reply_route:cmd` returns `OK: reply_route cmd;` on the
lower COM port. See T-57 / handoff for the exact checks. Also clarified
exo-integration.md (run_service.py vs run_exo_service.py; this failure mode).

**Candidate rule:** When every variant of a multi-step device command fails
identically, read the log for WHICH step fails before theorising per-variant;
here all three share the `set_exo_mode connected` handshake. For a request/reply
serial protocol, a silent unknown-command path on the device (no negative ACK)
is indistinguishable at the host from a wiring/route fault -- verify the device
firmware version and reply routing directly before suspecting the host client.

**Resolution (2026-09-10, bench):** Operator confirmed Exo Version 0.7.1 (so the
firmware DOES implement `set_reply_route`; hypothesis 1 ruled out) and that the
board's COM ports came up swapped (COM11 not COM10) -- the CDC enumeration-order
swap of hypothesis 2. The OpenRB's two USB CDCs (`Serial`/`SerialTelem`) are C++
globals with no defined init order, so which one is USB interface 0 is not
guaranteed. The App always claims interface 0 and read replies only there; the
old `set_reply_route:cmd` routed replies to `CMD_SERIAL` only, which on the
swapped board was the interface the App had not claimed, so the ACK (and every
later current-limit/home/pose ACK) was missed. Fixed order-independently on the
host: `exo_link.cpp do_connect` now requests `set_reply_route:both` (matching
`OK: reply_route both`), so replies mirror to both CDCs and reach whichever the
App claimed -- no firmware `DUAL_CDC_SWAP`/reflash needed. This also matches the
App serial layer's documented single-node "defaults to reply_route:both"
assumption. Updated the fake in exo_link_test.cpp and added assertions that the
handshake requests `both` and never `cmd`; the exo-link C++ suite passes. App
rebuild/redeploy and a bench re-run of probe/limits/angles remain to confirm.
**Candidate rule (added):** For a shared-bus device that exposes multiple
interfaces with host-unpredictable enumeration order, do not pin the reply path
to one interface the host must guess; request a broadcast/mirrored reply route
so correctness does not depend on enumeration order.

**Follow-up (2026-09-10, same bench):** With reply_route:both deployed, the
FIRST probe connected and read version, but the next connect (limits/angles, and
gui-exo) still failed the handshake with `no 'OK: reply_route both' ack`, even on
a fresh synapsectl start + fresh run_service.py. Ruled out: host port/relay (the
cleanup `set_exo_mode off` returned succeeded, so the service reached the device)
and config-not-loaded (the App journal `exo-startup.log` shows `exo link
enabled: device=/dev/ttyACM0 baud=1000000` on every start, and the connect
enumerated all four interfaces: iface0 class2/sub2, iface1 class10 -- the correct
claimed pair -- before the ~1.5 s timeout). So interface selection is right and
routing is `both`; the board simply returns no bytes on the claimed pipe for the
first command after open. Consistent with an OpenRB/SAMD native CDC that gates TX
on DTR and needs a brief settle after DTR assert, and/or a stale startup banner
in the TX FIFO -- the first reply is lost. Fix (host, exo_link.cpp do_connect):
(1) sleep open_settle_ms (default 300) after open before the first write; (2)
retry the idempotent set_reply_route handshake up to connect_handshake_attempts
(default 3), draining buffered bytes before each attempt and recording the seen
pre-write bytes into state.exo.last_error for diagnosis. exo-link C++ suite
passes incl a new retry-then-connect test. App rebuild/redeploy + bench re-run
still pending to confirm the settle/retry actually clears it (and, if not, the
captured pre-write bytes will say whether ANY reply arrives on the claimed CDC).
**Candidate rule (added):** For a host-driven serial/CDC link, do not treat the
first command after open as reliable -- settle after asserting DTR, drain stale
bytes, and retry an idempotent handshake; and instrument the connect path to
capture received bytes so a "no ack" is diagnosable without device-side logs.

**Second follow-up (2026-09-10):** The settle+retry deployed and its diagnostic
was decisive: the client showed `reply_route attempt 3/3: ... (no bytes seen
before write)`. So the App's claimed CDC (interface 0) receives NOTHING from the
board across all attempts -- not a DTR race (settle+drain didn't help) but the
wrong interface: the firmware's command channel is the OTHER CDC. The OpenRB
enumerates two CDC-ACM pairs (interfaces 0/1 and 2/3); the App had always claimed
0, but `Serial`/`SerialTelem` init order put the command channel on 2. Even
route:both can't help when the App writes to a CDC the firmware isn't bound to.
Fix (host): the App now tries each CDC control interface in turn --
UsbCdcConfig.control_interfaces default {0,2}; SerialPort gained an optional
select_next_candidate(); UsbCdcPort rotates candidates; exo_link do_connect loops
open->handshake->select_next_candidate until one answers OK: reply_route both.
Fully enumeration-order-independent: the App discovers the command CDC instead of
assuming interface 0. exo-link + usb-cdc C++ suites pass, incl new
fall-back-to-second-CDC and no-candidate-answers tests. App rebuild/redeploy +
bench re-run still pending.
**Candidate rule (revised):** For a multi-interface device whose functional
channel is not identifiable from descriptors (dual-CDC with host-unpredictable
role assignment), the host must PROBE each candidate interface with the actual
handshake and keep the responsive one -- neither pinning one interface nor a
broadcast reply route is sufficient when the host may be claiming the wrong
physical endpoint pair entirely.

### 2026-09-10 - gui-exo motion button gating: two wrong theories, then command-driven

After the CDC candidate-fallback fix, gui-exo's `set_exo_mode connected` finally
succeeded on every click, but the "Enable + send 250 ms test" button never became
clickable when the motion checkbox was ticked.

Attempt 1 (WRONG): the gate was `self.link_open = value.connected and
exo.link_open`, where `AppState.connected` is the NEURAL pipeline state
(`pipeline_state not in {disconnected, unspecified}`), unrelated to the exo. I
changed it to `exo.configured and exo.link_open` and asserted it fixed the
problem -- WITHOUT checking the operator's log, which showed the live broadcast
`state.exo` as `{"configured": false, "link_open": false}` even right after a
`succeeded` set_exo_mode. So gating on `exo.configured` guaranteed the button
stayed disabled: strictly worse. Lesson: I claimed a fix worked without
reconciling it against evidence already in the conversation.

Attempt 2 (CORRECT): the broadcast `state.exo` is unreliable/stale on this bench
(configured/link_open not reflecting a just-succeeded command), so gating on it
at all is wrong. Made link readiness COMMAND-DRIVEN: `submit(work, op=...)` posts
`("op", (op, ok, detail))`; drain_events sets `link_open=True` on a succeeded
`connect`, `False` on `off` or a failed op whose detail says "link closed"/
"outcome unknown". State is now display-only. Tests assert a stale
`configured:false` broadcast does NOT drive the link and a succeeded connect op
does. Client-only change (reinstall/relaunch; no App redeploy). Candidate rule:
when a device's broadcast state proves laggy/inconsistent with its own command
acks, gate UI on the authoritative command results, not the state stream -- and
never claim a UI fix works without checking it against the actual reported state.
(Open, separate: WHY the device broadcasts exo.configured=false while
set_exo_mode succeeds -- configured is set straight from cfg_.exo_enabled which
exo-startup.log proves true; likely a stale/initial snapshot or a
fill_exo_status/publish timing bug. Affects exo_via_scifi's printed snapshot too.
Not yet diagnosed.)

### 2026-09-10 - Exo candidate index not reset: connected once, never reconnected

Operator: connected to the OpenRB once (even saw the Arduino startup banner), but
after cycling synapsectl stop -> start -> Connect, could never reconnect. Cause
(host-side, at least in part): the CDC candidate-fallback loop's
UsbCdcPort::select_next_candidate() only advances forward and nothing reset it.
When a connect fell back to candidate 1 (interface 2) to succeed, or exhausted
{0,2} on a failure, candidate_index_ was left at the last interface. The next
do_connect's port_->open() then used current_control() = that leftover candidate
and the loop's select_next_candidate() immediately returned false, so a reconnect
only ever probed ONE interface -- and if the board's CDC role assignment differed
on the new App instance, that one was wrong. Fix: added SerialPort::reset_candidate()
(no-op default; UsbCdcPort resets candidate_index_ to 0 and reopens), called at the
top of ExoLinkWorker::do_connect so every connect re-probes all candidates from the
start. Regression test test_reconnect_reprobes_from_first_candidate: connect via
fallback candidate 1, disconnect, reconnect must succeed re-probing from 0.
CAVEAT: this fixes the host's index-stuck bug, but the operator's symptom may ALSO
involve board-side wedging (an abrupt synapsectl stop kills the App with the link
open, leaving DTR asserted / the interface not cleanly released; a SAMD CDC can
need a physical replug to recover). The failed-Connect detail distinguishes them:
'no bytes seen' on both candidates = board not answering; 'claim ... BUSY' = stale
claim; 'open failed'/'no matching USB device' = dropped off the bus. Candidate rule:
any "try candidates in order" selector must be RESET at the start of each fresh
attempt, or a prior failure silently pins all future attempts to the last candidate.

### 2026-09-10 - gui-exo hung on close after synapsectl stop

Closing gui-exo after a successful disarm + `synapsectl stop` hung: the window
would not shut. Cause: closeEvent's cleanup unconditionally called
`self.controller.set_exo_mode("off")`, a blocking device round-trip. After
`synapsectl stop` the App is gone but the ZMQ PUB/SUB transport does not detect
the dead peer, so the call blocked (no reply, no prompt error), the
`("closed")`/`cleanup_error` event never posted, and `self.closing` stayed True
making every further close a no-op. Fix: (1) only command the device on close
when `self.link_open` is believed true (skip it after off/stop); (2) always run
the bounded `disconnect()` regardless; (3) post a single `cleanup_done` that
closes the window (warning if disarm failed) instead of trapping it open; (4) a
second close forces the window shut; (5) an 8 s fallback QTimer closes anyway if
cleanup hangs. Regression tests: close still closes on a failed disarm, and
close skips set_exo_mode entirely when link_open is False. Candidate rule: a GUI
close path must never block on an unbounded device call -- gate the call on
believed-live state, bound it, and always provide a force-close escape.

Follow-up enforcement: the SAME clean-teardown-before-the-App-dies principle now
gates the **Run: stop device** button, not just window close. When the link is
open, stop first runs set_exo_mode("off") off-thread (bounded by the controller
timeout), posts pre_stop_teardown, then launches synapsectl stop; when the link
is already closed it stops immediately. So the operator can no longer kill the
App with the exo link open via the GUI. Tests: stop disarms first then stops;
stop skips the disarm when the link is closed. The doc also tells operators
running synapsectl stop by hand to disarm first.

### 2026-09-10 - libusb dependency build requires automake

The operator's clean App build failed in Docker while building libusb 1.0.27:
`autoreconf -vfi` exited 1. Reproduced in the cached builder and read its inner
log: `Can't exec "aclocal": No such file or directory`. The earlier diagnostic
fix declared libusb but omitted its automake build prerequisite; an isolated
syntax check using distro development headers did not exercise vcpkg. Added
`automake` to both Dockerfile architecture branches. With that addition, the
pinned vcpkg libusb build and complete ARM64 App compile/link passed in a
disposable SDK container; ELF dependencies include `libusb-1.0.so.0`. Device
execution and CLI packaging remain operator checks.

### 2026-09-10 - editable reinstall encountered a running MCP executable

Refreshing editable installs after the hub directory rename failed with Windows
`WinError 32` because `science-mcp.exe` was running. Pip partially moved its
metadata before failing. Restored the metadata and refreshed editable path and
source-URL metadata from a temporary install without replacing the running
executable. New Python processes import both renamed packages; the existing MCP
process still needs restarting. Close consumers before reinstalling executable
entry points on Windows.

### 2026-09-10 - USB startup diagnostic omitted its build dependency

The new `setup()` diagnostic included libusb and called its API without declaring
the dependency or linking it. The cached App builder also lacked `libusb.h`.
Added `libusb` to the vcpkg manifest and a pkg-config imported CMake target for
headers/linkage; changed the include to `<libusb.h>`. A full ARM64 syntax check
of `main.cpp` passed in the cached SDK container after installing the ARM64
development package in that disposable container. The normal image rebuild,
final link/package, and operator runtime check remain separate verification.
Candidate rule: adding a native-library include also requires explicit target
dependency/linkage and a builder refresh when dependencies are image-cached.

This file records concrete mistakes encountered while working in this repository. Recurring failure modes may be promoted into durable rules in `AGENTS.md`.

## Entry Template

### 2026-09-09 - Passive recorder crashed with QueueFull; per-frame HDF5 writes too slow

First bench run of passive mode showed `asyncio.queues.QueueFull` in calibrate-gui
and produced only a 16 kB data.hdf5. Cause: PassiveRecorder._drain_broadband
handed broadband frames from the blocking tap thread to the async drainer via an
`asyncio.Queue(maxsize=256)` using `loop.call_soon_threadsafe(queue.put_nowait,
frame)`. `put_nowait` raises QueueFull the instant the queue is full, and it
filled immediately because the drainer called `writer.append_broadband` once per
frame -- one HDF5 resize+write per frame -- far slower than the tap delivers, so
almost no broadband was written before the pump thread died. Two-part fix: (1)
use a thread-safe `queue.Queue` with a BLOCKING `put`, so a full queue applies
backpressure (the tap thread waits) instead of raising and dropping; the async
side pulls via `asyncio.to_thread(q.get)`. (2) batch many frames (200) into one
`append_broadband` and flush only every ~20 batches, so a high-rate stream is a
few large writes rather than thousands of tiny resizes. A regression test now
streams 1000 frames through the real BridgeSession and asserts every row lands
in order with no QueueFull. Rule candidate: a producer feeding a bounded queue
from a thread must block (backpressure) rather than put_nowait+drop, and per-item
HDF5 resize/write does not keep up with a high-rate stream -- batch appends.

### 2026-09-09 - Native Windows recorder build: two CMake/vcpkg blockers

Building host/recording natively on Windows (so the Windows GUI/bridge can spawn
task-recorder.exe) hit two distinct blockers. (1) Ninja generation: the VS2022
*bundled* CMake 3.29.5-msvc4 emits a malformed C++20 module scan rule
(`rule CXX_SCAN__recorder_synapse_$Config` with the literal `$Config`
unexpanded), so `ninja` fails with `rules.ninja:23: expected newline, got lexing
error`. The standalone CMake 4.3.3 at `C:\Program Files\CMake\bin\cmake.exe`
expands it correctly (`..._Release`) and generates valid Ninja; the build script
now prefers it. Setting `CMAKE_CXX_SCAN_FOR_MODULES=OFF` did NOT remove the rule
on either CMake and was a red herring. (2) Compile: with vcpkg's dynamic
protobuf 25.1 (`x64-windows`, which defines `PROTOBUF_USE_DLLS`), synapse-cpp
sources that use protobuf maps (tap.cpp, device.cpp, spike_detector.cpp) fail
with `map_field.h(682): error C2370: kVTable: redefinition; different storage
class` -- a known protobuf-25/MSVC-DLL header issue. Candidate fix: build the
deps with the `x64-windows-static` triplet (no PROTOBUF_USE_DLLS) and configure
with `-DVCPKG_TARGET_TRIPLET=x64-windows-static`; not yet done. Note: passive
mode does not need this recorder at all (the bridge reads broadband in Python),
so the Cognescent workflow is unblocked regardless. Rule candidate: on Windows,
prefer a standalone modern CMake over the VS-bundled one for Ninja C++20 builds,
and prefer static vcpkg triplets for protobuf-heavy C++ to avoid the DLL
map_field ABI break.

### 2026-09-09 - "Connected but no data": Windows bridge cannot spawn the Linux-ELF recorder

The Reactions page connected to the bridge and started/stopped recording, but no
`raw.h5` appeared. First theory was a port-9999 collision between the recorder
and the WebSocket; that was wrong. The recorder does not use port 9999 at all --
the bridge spawns it as a subprocess pointed at the device *tap*
(`--device <ip:port>`). The bridge journal (`browser-events.ndjson` in the
`reactions-<uuid>` session folder) showed the actual cause: right after
`recording_requested`, a `request_failed` with `[WinError 193] %1 is not a valid
Win32 application`. The recorder at `build/raw-recorder/task-recorder` is a Linux
ELF (built in WSL), but `calibrate-gui` / the bridge were running as Windows
processes, so `asyncio.create_subprocess_exec` could not launch it (ELF is not a
PE). No `raw.h5`/`recorder.log` was ever created because the subprocess never
started. The browser then sent `enabled:false` ~145 ms later, consistent with a
failed start. Diagnosis rule: the bridge journal is the source of truth --
inspect `request_failed` records before theorising. Structural rule: the bridge,
GUI, and C++ recorder must all run on the same OS; either run the whole host
stack in WSL (where the ELF runs) or build a native Windows `task-recorder.exe`.
The bridge's single-controller guard (code 1013) was also confirmed real -- a GUI
stage-4 client and the browser compete for one slot -- so the GUI's manual
recording panel is now hidden behind an advanced checkbox to avoid stealing it.

### 2026-09-09 - Reactions bridge bound only IPv4, so the browser (localhost) could not connect

The GUI console connected to the bridge fine (`ws://127.0.0.1:9999`), but the
Reaction-Task page at chr.nml.wtf failed to open its WebSocket to the same
port. Cause: `serve_bridge` bound only the literal IPv4 `"127.0.0.1"`, while the
page's `CtrlrSocketClient.connect()` always dials `ws://localhost:<port>`. On
this Windows host `localhost` resolves to IPv6 `::1` **before** `127.0.0.1`
(verified: `socket.getaddrinfo('localhost', 9999)` returned `::1` first), so the
browser hit `::1:9999` where nothing was listening and got connection-refused.
The GUI's own client used the literal `127.0.0.1`, bypassing `localhost`
resolution, which is why it worked and masked the problem. Corrected by binding
both loopback families: `serve(handler, ["127.0.0.1", "::1"], port, ...)` (a host
sequence is passed through to `loop.create_server`, which listens on each),
staying loopback-only. Added tests/test_reactions_bridge_bind.py connecting over
`127.0.0.1`, `[::1]` and `localhost`. Candidate rule: a loopback server a browser
reaches by name must bind both `127.0.0.1` and `::1` (or resolve the name), never
a single hard-coded IPv4 literal; "a client connected" is not proof the server is
reachable by the name the browser actually uses.

### 2026-09-09 - calibrate-session two-pass flow could never reach pass two

The documented calibration launcher workflow (README "Run a Calibration
Session") runs `calibrate-session` twice with the *same* `--session-dir`: pass 1
generates the session and prints the operator `synapsectl start` line (exit 2),
pass 2 reruns with `--info-capture` to gate on App Running and launch the host
processes. Pass 2 always failed with `[WinError 183] Cannot create a file when
that file already exists ... choose a fresh --session-dir`. Cause:
`run_calibration_session.main` unconditionally called `build_profile` ->
`calibration_task.prepare_session`, whose `out.mkdir(exist_ok=False)` is an
exclusive create; pass 2 tried to regenerate over the directory pass 1 had
created, so the two-pass flow was structurally impossible. The unit tests missed
it because each test used a fresh temp dir and never ran two passes into one
directory; one test even asserted the collision (`assertRaises(SystemExit)`) as
if it were the intended contract. Separately, running the child commands printed
by `--dry-run` by hand also failed (`FileNotFoundError: task-profile.json`)
because `--dry-run` deliberately generates nothing; those lines are launched by
the launcher on pass 2, not run manually. Corrected by adding `obtain_session`:
if the session dir already contains the three artifacts (extra files such as an
`info.txt` capture saved inside it are tolerated via a subset check) it is
reused via `load_profile` instead of regenerated; a dir missing a core artifact
still errors. Tests updated to exercise the real two-pass reuse and a
conflicting-directory rejection. Candidate rule: when a CLI documents a
multi-invocation workflow into the same output path, add a test that actually
runs the invocations in sequence against one path — a per-test fresh temp dir
hides state-collision bugs.

### 2026-09-09 - App host-tests build required CONFIG-mode protobuf absent on the distro

Building the SDK-free host tests to run `test_hub_spoke_cross_language_hash`
(`cmake -S apps/scifi2-hub-manager -B build/app-tests -DBUILD_DEVICE_APP=OFF`)
failed at configure: `find_package(Protobuf CONFIG REQUIRED)` at
`apps/scifi2-hub-manager/CMakeLists.txt:109` could not find
`protobuf-config.cmake`. The cause was a protobuf-version mismatch, not a missing
package: the device App is built inside the Synapse SDK image (protobuf >=22, which
ships CMake config files), but the operator's Ubuntu host had protobuf 3.21.12,
whose `libprotobuf-dev` provides only module-mode `FindProtobuf.cmake`. Installing
`libabsl-dev`/`protobuf-compiler` did not help because 3.21.x simply predates the
config-file packaging. The recorder project builds because it uses module-mode
`find_package(Protobuf REQUIRED)`. Corrected by making only the `BUILD_TESTING`
protobuf discovery prefer CONFIG then fall back to `MODULE` (both create the same
`protobuf::libprotobuf` target the tests link); the device-App discovery stays
strict CONFIG. Candidate rule: on a version-mismatch `find_package(... CONFIG)`
failure, verify the installed version against the config-file requirement before
adding packages, and prefer a CONFIG-then-MODULE fallback for targets that only
need a mode-agnostic imported target.

### 2026-09-05 - ARM64 protobuf map lookup used inconsistent Abseil hashes

Reproduced the task parser failure locally using Docker image
`stateful-decode-and-sync:latest-amd64`, GCC 10 ARM64, its shared protobuf 25.1
and static Abseil archives, under `qemu-aarch64-static`. The unmodified
`field()` triggered protobuf map.h:1069's bucket-consistency assertion:
`BucketNumberFromHash(hash_function()(k)) != VariantBucketNumber(...)`.
Native host protobuf tests passed, masking this dependency-boundary failure.
Changed bounded schema lookup to compare keys by iteration, avoiding hashed
lookup across the shared-library boundary. Updated unknown-field test setup
to use protobuf JSON parsing instead of executable-side map insertion (which
also triggered the assertion), and added a genuinely missing-field check.
The ARM64 task-state suite and native suite pass after the change. Device App
rebuild/deployment and live startup verification remain outstanding; no claim
that the installed device binary has been fixed.

### 2026-09-05 - Missing-field error was mistaken for missing uploaded data

Repeated configuration uploads were suggested after the App reported missing
`task_definition.schema_version`, although the host JSON already contained it.
The operator's completed 0.8.0 report now shows the field as numeric 1 in both
the saved device JSON and the same App process's configuration dump, followed
by the missing-field error. Upload loss is not supported by that evidence.
Investigate the deployed C++ parser/build/runtime instead; exact cause remains
unverified. Both operator 0.8.0 deployments completed, and every collection
section in the downloaded report returned exit 0. This supersedes the earlier
installation-blocker assessment without proving why 0.7.0 hung.

### 2026-09-05 - Diagnostic installer lacked time bounds

Diagnostic 0.7.0 repeatedly stalled at installation after App startup while
device queries stayed responsive; it completed quickly in a boot without App
journal entries. The script ran unbounded journal scans and printed the whole
report into installer stdout. The exact blocked operation is unverified;
journal collection and an undrained installer pipe are hypotheses. Version
0.8.0 adds command/copy timeouts, incremental reports, removes full-journal
scans and report stdout. Local shell/build checks pass; device recovery and
0.8.0 behavior remain unverified. Installer diagnostics should be time-bounded
and avoid bulk stdout.

### 2026-09-05 - WSL diagnostic build lost shell variables

The first package-build invocation used `wsl.exe sh -c`; the extra shell parsing
lost the temporary-directory variable and `cp` attempted `/package`, failing
with permission denied. Re-running with `wsl.exe --exec sh -c` preserved the
script and built successfully. Prefer explicit `--exec` for WSL shell scripts.

### 2026-09-05 - Server logs were requested to diagnose an App process

Requested `synapsectl logs` for an App startup failure before checking
`scripts/device-diag/README.md`, which already documents that this command
returns curated server logs, not the App journal. Also initially treated July
timestamps as stale evidence despite known device clock skew. The supplied
output included its own GET_LOGS request. Corrected the interpretation and
prepared boot-scoped App diagnostics. Check documented log scope and clock
limitations before requesting more operator output.

### 2026-09-05 - MEGA2560 sync sketch timed edges by hand-counted cycles

**Attempt:** Generate GPIO_0/GPIO_1 sync edges for the RHD2132 adapter with
`firmware/MEGA2560_GPIO_SYNC`, timing toggles by a `while(1)` loop in `setup()`
claimed to take "exactly 16 clock cycles" per iteration.
**Failure:** The LUT's labeled frequencies (16 Hz..2048 Hz) did not correspond
to real output. Branches, 16-bit compares, and a per-edge `random()` call made
iteration cost variable, so the iteration-count thresholds were not microseconds.
`noInterrupts()` was held forever, disabling `micros()/millis()` and `loop()`.
**Cause:** Timing by counting CPU cycles in C is not deterministic once branches
and library calls (`random()`) are in the loop body; the "16 cycles" premise was
false.
**Correction:** Rewrote to a hardware Timer3 CTC compare ISR at the fastest rate
(2048 Hz). D23 is the master clock (toggled every interrupt); D22 hops
pseudo-randomly by counting master ticks, so both lines share one timebase and
stay phase-locked for cross-device alignment. Edge is emitted before `random()`
so the PRNG never jitters an edge. Verified with a throwaway PlatformIO
`megaatmega2560` build (avr-gcc): clean compile/link, `firmware.hex` produced.
**Candidate rule:** For MCU edge timing, use a hardware timer/ISR, not
hand-counted C-loop cycles; never hold `noInterrupts()` across the main program.

### 2026-09-05 - Recorder instructions omitted provenance creation

**Attempt:** Operator followed the host recorder build/run instructions.
**Failure:** The command failed with `cannot read metadata file`.
**Cause:** The documentation named a required provenance file but supplied only
a comment telling the operator to create it, with no template or field guidance.
**Correction:** Added `config/calibration-provenance.template.json`, copy/edit
commands, and guidance distinguishing unknowns, supplied config and live evidence.
**Candidate rule:** Include a usable template whenever a quick-start command
requires a user-authored input file.

### YYYY-MM-DD — Short description

**Attempt:** What was being attempted.

**Failure:** What went wrong.

**Cause:** The actual cause, once established.

**Correction:** How it was fixed.

**Candidate rule:** A general safeguard worth considering if this failure recurs.

### 2026-08-24 — Assumed the Axon "custom peripheral" is a USB-attached MCU

**Attempt:** Planned the NML Synapse Bridge as an external MCU (STM32/nRF52) that enumerates on the SciFi's peripheral-facing USB port by implementing an "Axon USB peripheral protocol," and began framing the work as reverse-engineering that USB wire format.

**Failure:** No such public USB peripheral protocol exists to implement. Time budgeted for "USB descriptor / endpoint / handshake reverse engineering" would have been spent chasing an interface that is not exposed to an external device.

**Cause:** Science's public reference (`vendor/axon-peripheral-example`) implements a custom peripheral as **FPGA gateware inside the SciFi's own Lattice fabric** (target `via-devkit`, Radiant 2024.2) plus an **ARM64 driver `.so`** that `scifi-server` `dlopen`s on the SciFi. The peripheral exchanges 32-bit AXI4-Stream frames with an encrypted SDK transport and never speaks USB; the device↔headstage physical link is an IR/optical serdes, and the host↔SciFi link is FTDI FT60x. The reference exposes only a *record source* plugin base (`RecordPluginWithLimits`); no external-MCU path and no stim-sink plugin base are present. (Evidence: `via_top.sv`, `axon_test_source_peripheral.{sv,cpp,h}`, `Dockerfiles/gateware.Dockerfile`, `peripheral.yaml`.)

**Correction:** Documented the actual architecture in `docs/axon-peripheral-protocol.md` with proven/inferred/unknown tags, and recorded a feasibility decision in `docs/feasibility-mcu-vs-fpga.md`: proceed on the proven host-side fallback (MCU as a host USB/serial device feeding host fusion + a Synapse consumer Tap), treat the Lattice FPGA as the only proven *native*-peripheral path (gated on device identification + Radiant licensing + obtaining a via-devkit), and do not write MCU USB-peripheral firmware until Science confirms whether any external-MCU-as-SciFi-peripheral path exists.

**Candidate rule:** Before implementing against an assumed hardware/wire interface, confirm the interface boundary from the vendored reference implementation. Do not treat a "custom peripheral" as an external USB device until the reference shows an external-device enumeration path; here the reference peripheral lives on the SciFi's internal FPGA fabric.

### 2026-08-24 — Asserted the Axon→Omnetics probe maps to peripheral ID 200

**Attempt:** Given `synapsectl info` listing `IntanRHD2132 (ID 200, kBroadbandSource)` as the only real 32-channel broadband source, stated that ID 200 is "the Axon→Omnetics probe path" and wrote a recording config (`config/axon-omnetics-32ch.json`) binding to `peripheral_id: 200`, plus doc text asserting the RHD2132 is the adapter's ADC.

**Failure:** Presented an inference as fact. The user (correctly) noted the RHD2132 is itself the ADC/SPI chip, so it is plausible the Axon front-end sits *in place of* an Intan chip and would enumerate as a different peripheral — meaning ID 200 could be unrelated to the Omnetics probe path.

**Cause:** No vendored code maps the "Axon Omnetics adapter" to any peripheral ID or type (grep across `vendor/` finds no such mapping). ID 200 was the only real 32-ch `kBroadbandSource` in one `info` reading, and its name (`IntanRHD2132`, a real Intan part) was over-read as "the probe path" without evidence that the Omnetics connector routes through that chip.

**Correction:** Reframed the ID as a *candidate to test, not a fact* in the config README, `docs/axon-peripheral-protocol.md` §12, and `TODO.md`, and added a bench step to resolve it (diff `info` with vs. without the adapter attached; confirm with Science) before binding any recording config.

**Candidate rule:** Do not bind configuration or documentation to a specific peripheral ID from a single `info` reading unless the ID→device mapping is confirmed by attaching/ removing the device or by vendor documentation. A peripheral's *name* is not proof of what physical signal path feeds it.

### 2026-08-24 — Attributed the missing IntanRHD2132 (ID 200) to the peripheral-example deploy

**Attempt:** After deploying the `scifi-axon-test-source` driver .deb, `synapsectl info` no longer listed `IntanRHD2132` (ID 200) and the device screen showed 0 peripherals. Concluded the deploy had likely broken the stock driver — the .deb installs `libscifi-peripheral-sdk.so{,.0,.0.2.0}` into system `/usr/lib`, so a symlink-clobber over a firmware copy was hypothesized — and recovery planning (symlink repair, driver reinstall) began on that basis.

**Failure:** The hypothesis was wrong. On-device evidence showed no clobber was possible: the firmware never ships `libscifi-peripheral-sdk` (the .deb's copy is the only one), the Intan driver is not a plugin file (`/usr/lib/scifi/plugins/` held only `axon_test_source.so`; no Intan package exists in `dpkg -l`), and the boot journal shows the plugin loading cleanly (`ABI v3, ID 0xf001`) followed by `PeripheralRegistry initialized successfully`. Unplugging/replugging the adapter produced zero kernel, `usbd`, or `scifi-server` log activity — detection fails at the electrical/link level (adapter, cable, or port), not in software.

**Cause:** Post-hoc reasoning from coincidence (deploy happened; peripheral vanished) plus limited visibility: the `scifi-sftp` account is jailed to data directories, the `GET_LOGS` RPC returns only curated `scifi-server` entries, and the device clock jumps across boots so timestamp-sorted `journalctl` tails showed the wrong boot. Evidence had to be gathered by shipping read-only diagnostic .debs over the DeployApp channel with a data-jail mailbox (see `scripts/device-diag/`).

**Correction:** Software exonerated; escalated to Science as a hardware/link failure with the journal evidence. Diagnostic tooling and the device facts learned along the way are preserved in `scripts/device-diag/README.md`. Also recorded there: IDs 1–2 in `BroadbandSourceConfig.peripheral_id` are command-range aliases ("first broadband source"), not concrete IDs — a port number is never a peripheral ID.

**Candidate rule:** Correlation with a recent software change is a hypothesis, not a diagnosis: before planning recovery from an assumed software regression on a device, capture the device's actual state through a read-only channel (package list, plugin dir, boot-scoped journal). On devices with unstable clocks, scope journal queries with `journalctl -b`; a curated log RPC returning nothing is not evidence of absence.

### 2026-08-26 — Carried-over virtual electrode map rejected by the real RHD2132

**Attempt:** First real recording from the physical Axon Omnetics 32-ch probe (Intan RHD2132, `peripheral_id` 200) reused the electrode/reference map copied from the virtual peripheral example (`id` 1000): `electrode_id` values like 122, 126, 116, and `reference_id` 512/513. `synapsectl start` was expected to stream.

**Failure:** `start` failed with a misleading top-level "Failed to start device: connection error" from the CLI. The device logs showed the true cause: `IntanRhd2132: Invalid electrode ID: 32 (max 31)` -> `Failed to configure channels` -> `BroadbandNode: Failed to start peripheral recording`.

**Cause:** The virtual peripheral (id 1000) accepts arbitrary electrode/reference ids; the **real RHD2132 driver validates `electrode_id` to its 32 physical amplifier channels, 0-31**, and hard-rejects anything above. The carried-over map was never a valid physical channel map (this exact risk was pre-flagged in `TODO.md`). The CLI's "connection error" is a downstream symptom, not the cause -- the device log is authoritative.

**Correction:** Set an identity map (`electrode_id` = channel `id`, 0..31; `reference_id` 0) in `config/axon-omnetics-32ch-broadband.json`. Recording then streamed cleanly: 5 s @ 20 kHz, 100,305 frames / 3,209,760 samples, 0.00% loss; HDF5 attrs confirm `lsb_uv = 0.195`, `sample_rate_hz = 20000`. Two device-side facts also confirmed in the logs: `clkmc 80000000 Hz`, `period 4000 cycles` at 20 kHz.

**Candidate rule:** Do not carry an electrode/reference map from a virtual/simulator peripheral to a physical one; physical front ends validate ids against real channel counts. When a `synapsectl` action fails with a generic CLI-level error, read `synapsectl ... logs` for the device-side cause before diagnosing. (Also: `synapsectl` on Windows crashes printing a U+2713 checkmark under the console's cp1252 codec -- set `PYTHONUTF8=1` for its commands.)

### 2026-08-31 — Imported a generated protobuf enum from the wrong module

**Attempt:** Added the read-only `broadband_out` Python probe and imported both
`BroadbandFrame` and `ChannelType` from `synapse.api.datatype_pb2`.

**Failure:** The CPython 3.13 test run failed during module import because the
installed generated bindings expose the imported `ChannelType` enum through
`synapse.api.channel_pb2`, not `datatype_pb2`.

**Cause:** Protobuf Python generation keeps imported declarations in their
source module; the `datatype.proto` reference does not re-export the enum.

**Correction:** Imported `BroadbandFrame` from `datatype_pb2` and `ChannelType`
from `channel_pb2`, then added offline parser tests. The full client suite
passed.

**Candidate rule:** When adding a generated-protobuf client, inspect the
installed binding modules for imported message/enum ownership before writing
imports; compile/import the smallest consumer immediately.

### 2026-08-26 — On-device MLP would not learn separable MPF classes (feature scale)

**Attempt:** Offline smoke test of the `stateful_decode_and_sync` MLP: two synthetic classes, one amplitude-scaled 4x (an obvious, large feature difference), trained with the config default `mlp_lr` (~0.01-0.02) on the raw MPF feature vectors.

**Failure:** Training accuracy stuck at ~0.5 and loss pinned exactly at ln(2)=0.693 (uniform softmax); the network never moved. First read as an MLP/backprop bug.

**Cause:** Not a code bug. The MLP is correct (a 2-feature toy problem trained to 100%). Raw MPF features are the upper triangle of a Hermitian matrix-log of a
cross-spectral density: values span orders of magnitude and reach |x|~10. At
lr>=~0.01 the first-layer gradients explode and the softmax collapses to uniform; at lr~=0.001 the same data trains to 95-100%. The default lr was simply too large for the unnormalized feature scale.

**Correction:** Added per-feature standardization (z-scoring) to the MLP: fit mean/std over the captured training set, apply identically at inference. With standardized inputs the default lr converges to 100% on the smoke test. Documented in the app README and PLAN.md.

**Candidate rule:** For a hand-rolled on-device classifier, standardize features before SGD rather than hand-tuning the learning rate to the feature scale; a loss frozen exactly at ln(num_classes) with no weight movement is diverging (exploding gradients), not a stuck optimizer -- check input magnitude and lr before suspecting backprop.

### 2026-08-31 — Omitted a helper while adding the bounded collection store

**Attempt:** Compiled the new SDK-independent collection-store tests with strict warnings after adding generation-overflow checks.

**Failure:** The compiler rejected references to `can_advance_generation()` because the helper had been called but not defined.

**Cause:** The generation guard was added in the public mutation methods without completing the corresponding private helper in the same header-only implementation.

**Correction:** Added the helper, then reran the strict direct g++ build and the WSL CMake/ CTest workflow; all seven tests passed.

**Candidate rule:** After adding a header-only API guard, run the smallest strict compile before continuing documentation or integration work.

### 2026-08-31 — T-5 protobuf test integration exposed generated-name and target mistakes

**Attempt:** Added the typed GUI protocol schema and put its serialization tests beside the existing collection-store test in one CMake executable.

**Failure:** The first generated-code compile used nested enum names that this `protoc` version emits as namespace-level names, and the combined test target linked two `main` functions. A direct WSL test configuration also initially used CMake's module Protobuf target against the static vcpkg library without its Abseil dependencies.

**Cause:** Generated C++ enum naming is determined by the protobuf generator, not by the source enum's visual nesting. The existing test already owns the executable entry point. The module-mode imported target did not carry the static package's transitive Abseil link interface in that ad-hoc configuration.

**Correction:** Used the generated `Flush_Scope_*` names, split protocol tests into their own executable, and selected the vcpkg Protobuf config package so its dependency targets are linked. The WSL build then passed both CTest targets.

**Candidate rule:** Compile a small generated-protobuf consumer immediately after changing a local schema; keep independently owned test programs in separate executables and use the package-manager config target for static dependency graphs.

### 2026-08-31 — Fit failure status was default-initialized as unsuccessful

**Attempt:** Added per-epoch MLP progress publication to the existing synchronous fit path.

**Failure:** The fit path's local `TransitionResult failure` started with its default `success=false`, so the terminal check classified every otherwise successful training pass as failed.

**Cause:** The result type intentionally defaults to failure for safety, but the local variable represented an as-yet-unset error and was tested as though it represented a successful operation.

**Correction:** Initialized the local result with `control::success()` and added an MLP regression test covering successful progress and malformed data. The terminal fit check now also rejects non-finite metrics and reports `ERROR_MALFORMED`.

**Candidate rule:** Initialize deferred error/result variables explicitly to the success identity when the surrounding branch uses success as the no-error sentinel.

### 2026-08-31 — Assumed a successful Tap send meant the device received the command

**Attempt:** Used the legacy Python control scripts to switch source mode, capture labels, and start an MLP fit after deploying `stateful_decode_and_sync`.

**Failure:** The scripts printed successful sends, but the device emitted no corresponding app logs or `class_out` data. The `broadband_out` producer was healthy, so the apparent control-plane success was misleading.

**Cause:** Synapse consumer taps use ZeroMQ PUB/SUB. The client scripts sent immediately after connecting, during the PUB/SUB slow-joiner window; `Tap.send()` only confirmed a local socket send and could not confirm device receipt. The scripts' 0.1–1 ms connection settle delay was insufficient.

**Correction:** Added a 500 ms post-connect settle delay to the project command scripts before their first send. Device-side `app_logs` and `taps stream` should be monitored when validating command delivery; a local `send()` result is not an application acknowledgement.

**Candidate rule:** Treat PUB/SUB command delivery as asynchronous: allow the subscription handshake to settle and verify receipt through an application result/state/log stream rather than trusting the publisher's local send return value.

### 2026-08-31 — Windows Proactor socket cleanup raised an unhandled reset

**Attempt:** Closed a loopback NDJSON client during the reconnect integration test and caught `ConnectionError` around `StreamWriter.wait_closed()`.

**Failure:** Windows reported `OSError: [WinError 64]` from the Proactor transport after the peer reset, leaving an unhandled exception in the server callback even though the test passed.

**Cause:** The platform surfaced this peer-close path as a broad `OSError`, not the narrower exception covered by the cleanup handler.

**Correction:** Service shutdown and per-client cleanup now catch `OSError` around `wait_closed()`.

**Candidate rule:** Treat socket close cleanup as best-effort and catch platform-level `OSError` when awaiting peer shutdown.

### 2026-09-01 — Started an unconfirmed application rebuild

**Attempt:** After applying the raw-forwarding feature-work gate, started `synapsectl apps build --clean apps/scifi2-hub-manager` to produce a package for bench validation.

**Failure:** The user intended to perform the build themselves and had not approved a build action. The long Docker dependency build was stopped before application compilation; no replacement package was deployed.

**Cause:** Interpreted authorization to implement the requested fix as authorization to run the separate, long-running build workflow.

**Correction:** Stopped the build on request. The code change remains in the working tree for the user's build.

**Candidate rule:** Ask for explicit confirmation before starting any build, even when the code change itself was requested.

### 2026-09-01 — App Docker context omitted the shared wireless proto

**Attempt:** The user ran `synapsectl apps build --clean apps/scifi2-hub-manager` in WSL and supplied the build transcript.

**Failure:** Docker image creation succeeded, but CMake stopped at
`tests/CMakeLists.txt:43` with `protobuf_generate could not find any .proto files`.

**Cause:** `synapsectl apps build` supplies the app directory as Docker context;
the CMake test path referenced the repository-level `protocol/` directory,
which is outside that context and therefore absent in the image.

**Correction:** Added the wireless contract under the app's context-local
`proto/wireless/v1/` path and changed app/test CMake generation to use it. The
root `protocol/wireless/v1/` contract remains the repository-level reference.

**Candidate rule:** Treat app Docker contexts as self-contained; every proto,
script, and include needed by an app build must be inside the app context or be
explicitly copied into it.

### 2026-09-01 — Ran Axon gateware generation from the source subdirectory

**Attempt:** Ran `synapsectl peripherals gateware generate` from
`firmware/axon-virtual-sources/src/gateware/`, following the copied SDK README's
command examples.

**Failure:** The installed CLI searched for
`src/gateware/Dockerfiles/gateware.Dockerfile` and stopped before generation.

**Cause:** This CLI resolves the peripheral-plugin root from the current
directory, whereas the copied SDK README did not state that its commands must
be run from the directory containing `Dockerfiles/gateware.Dockerfile`.

**Correction:** Documented the required plugin-root working directory in the
parent-owned peripheral README and gateware README. No files were generated or
deployed.

**Candidate rule:** When a CLI wraps a project Dockerfile, state the required
working directory explicitly and perform a generation-only smoke check before
asking for a build or deployment.

### 2026-09-01 — Ran Axon gateware generation through the Windows CLI

**Attempt:** Ran the root-level `synapsectl peripherals gateware generate`
command from Windows PowerShell after correcting its working directory.

**Failure:** The CLI failed while starting its gateware Docker image with
`[WinError 2] The system cannot find the file specified`.

**Cause:** The installed Windows CLI cannot launch the Docker-backed Axon
gateware workflow in this environment. The user's working environment for this
toolchain is Ubuntu/WSL.

**Correction:** Updated the parent-owned peripheral instructions to require
Ubuntu/WSL with Docker for gateware commands. No generated files, package, or
device state changed.

**Candidate rule:** Use the established Linux/WSL toolchain for Docker-backed
peripheral gateware commands; reserve the Windows host for source editing and
non-gateware workflows unless a successful Windows smoke check exists.

### 2026-09-01 — Axon gateware CLI omitted its required named Docker context

**Attempt:** Ran `synapsectl peripherals gateware generate` from the correct
parent-owned peripheral root in Ubuntu/WSL with Docker available.

**Failure:** Docker failed before code generation, attempting to pull
`radiant_installer:latest` and reporting access denied.

**Cause:** `Dockerfiles/gateware.Dockerfile` mounts
`from=radiant_installer`, which requires a named BuildKit context containing the
Radiant installer ZIP. The installed CLI invoked `docker build ... .` without
`--build-context radiant_installer=...`, so Docker interpreted the context name
as an image name.

**Correction:** Marked the gateware generation workflow blocked and documented
the exact CLI/Docker mismatch. No generated gateware, package, or device state
changed.

**Candidate rule:** Before relying on a Dockerfile's named build context,
inspect the wrapper CLI's emitted `docker build` command and require it to pass
every named context explicitly.

### 2026-09-01 — Gateware image unpacked Radiant before installing `unzip`

**Attempt:** Built the parent-owned gateware image with the supplied
`radiant_installer` named context.

**Failure:** The first installer layer stopped with `/bin/sh: 1: unzip: not found`.

**Cause:** `Dockerfiles/gateware.Dockerfile` invoked `unzip` before its later
package-install layer provisioned build dependencies.

**Correction:** Added an explicit `apt-get install ... unzip` to the installer
layer before unpacking Radiant. No generated gateware, package, or device state
changed.

**Candidate rule:** Each Dockerfile layer must install every executable it uses;
do not rely on a later layer to provide an earlier layer's prerequisites.

### 2026-09-01 — Radiant installer lacked its XKB runtime dependency

**Attempt:** Rebuilt the parent-owned gateware image after provisioning
`unzip` in its installer layer.

**Failure:** The Radiant installer started but exited with
`libxkbcommon-x11.so.0: cannot open shared object file`.

**Cause:** The installer executable dynamically links the XKB/X11 runtime even
in console mode; the Ubuntu base image did not provide that library.

**Correction:** Added the Ubuntu `libxkbcommon-x11-0` runtime package before
invoking the installer. No generated gateware, package, or device state changed.

**Candidate rule:** Treat hardware-tool installers as native GUI-linked
executables until their runtime library dependencies have been verified, even
when invoked in a console-only mode.

### 2026-09-01 — Radiant installer exposed a second Qt/X11 dependency

**Attempt:** Rebuilt the image after adding the XKB runtime required by the
Radiant installer.

**Failure:** Dynamic loading then stopped at `libxcb-cursor.so.0`.

**Cause:** The console installer has a broader Qt/X11 runtime dependency set
than the minimal Ubuntu base provides.

**Correction:** Provisioned the small X11/Qt runtime closure (including
`libxcb-cursor0`) in the installer layer rather than discovering one missing
library per rebuild. No generated gateware, package, or device state changed.

**Candidate rule:** When a GUI-linked native tool fails in a minimal container,
install its runtime dependency set as a group and then re-test.

### 2026-09-01 — Radiant installer also required the GLVND OpenGL ABI library

**Attempt:** Rebuilt the image with the X11/Qt runtime dependency set.

**Failure:** Dynamic loading then stopped at `libOpenGL.so.0`.

**Cause:** Ubuntu packages the GLVND OpenGL ABI library (`libopengl0`) separately
from `libgl1`; the initial runtime set included only the latter.

**Correction:** Added `libopengl0` to the installer layer. No generated
gateware, package, or device state changed.

**Candidate rule:** For GLVND applications, include both the legacy `libgl1`
loader and the `libopengl0` ABI library when the binary's dependencies require
them.

### 2026-09-01 — Gateware image cloned Verilator without installing Git

**Attempt:** Continued the parent-owned gateware image build after the Radiant
installer dependencies were supplied.

**Failure:** The Verilator source layer stopped at `/bin/sh: 1: git: not found`.

**Cause:** The layer runs `git clone` but the preceding build-tool package list
did not include Git.

**Correction:** Added `git` to the build-tool installation layer. No generated
gateware, package, or device state changed.

**Candidate rule:** Audit each source-fetching Docker layer for its VCS client
as well as compiler and build-tool prerequisites.

### 2026-09-01 — Initial T-22 acceptance fixtures masked failures

**Attempt:** Added the first measured-alignment acceptance analyzer and ran its
unit tests.

**Failure:** The fixture's requested edge error was not applied to the measured
timestamp, and an early return on nonzero continuity counters prevented the
report from exposing the available edge metrics.

**Correction:** Made the fixture construct the actual offset, continued metric
collection while retaining continuity failures, and added full mode ×
rate/batch coverage checks.

**Candidate rule:** Acceptance tests must exercise the measurement values they
claim to test and should report independent evidence even when one gate fails.

### 2026-09-04 — Building the task-recorder deps on WSL's default GCC 15

**Attempt:** To build the gated `task-recorder` consumer (T-32) end-to-end,
bootstrapped vcpkg in WSL (Ubuntu 26.04) and drove `vendor/synapse-cpp`'s
manifest build to produce the `synapse` CMake package plus `hdf5`, using the
distro-default compiler (GCC 15.2.0).

**Failure:** Two dead ends. (1) Running the synapse-cpp configure and the `hdf5`
install as two concurrent background vcpkg processes against one vcpkg root: both
died at "Detecting compiler hash … failed" because they race on the shared
`buildtrees/detect_compiler` tree. (2) After serializing, the real blocker
surfaced: the science overlay port set pins **abseil 20240116.2**, which does not
compile on GCC 15 — `absl/container/internal/container_memory.h:66: 'uintptr_t'
does not name a type` (older Abseil relied on a transitive `<cstdint>` include
that GCC 15 no longer provides). grpc/protobuf of the same vintage hit the same
wall.

**Cause:** Two independent issues. vcpkg does not support concurrent installs
against one root (shared `detect_compiler` buildtree). And the pinned overlay
ports were fixed for an older compiler era than the WSL default (GCC 15).

**Correction:** (1) Serialize all vcpkg operations against one root. (2) Install
`g++-13` and pin the build to it via a vcpkg custom triplet
(`VCPKG_CHAINLOAD_TOOLCHAIN_FILE` setting `CMAKE_C/CXX_COMPILER=gcc-13/g++-13`)
so the entire pinned overlay set compiles as intended, without editing any
upstream-pinned port. Host unit-test build (13/13) is unaffected — it is SDK/
HDF5-free and uses the distro g++ directly.

**Candidate rule:** Never run two vcpkg installs concurrently against the same
vcpkg root. When building a vendored dependency's pinned overlay-port set, match
the compiler to the ports' era with a custom triplet rather than patching
upstream-pinned ports one by one; the distro-default GCC on a new Ubuntu is
often newer than the ports were fixed for.

**Follow-up (same day):** The custom triplet is necessary but not sufficient in
two further ways. (a) The custom triplet governs only the *target* triplet;
grpc/protobuf codegen pull a **host-tool** `abseil:x64-linux` that still built
under the default GCC 15 and failed identically — fix by passing
`--host-triplet x64-linux-gcc13` as well as `--triplet`. (b) After that, the
build reached a deeper, non-compiler blocker: **protobuf 6.33.4 does not compile
against abseil 20240116.2** (`arena.cc`: `'WithStaticSizes' ... does not name a
template type`, `layout_type does not name a type`). The synapse-cpp manifest's
vcpkg **builtin-baseline** resolves protobuf/grpc/re2 to *current* versions
(protobuf 6.33.4, grpc 1.81.1, re2 2025-11-05) while the science **overlay** pins
abseil to the old 20240116.2 — an internally inconsistent graph (recent protobuf,
ancient abseil). This is a version-skew in the dependency set itself, not a
toolchain gap, and is unresolved: it needs a coherent version set (pin the whole
baseline to abseil's era via an overlay/version constraint, OR override abseil to
a protobuf-6.33-compatible release, OR build a tagged synapse-cpp release whose
lockfile is self-consistent) rather than a compiler change. Left for the next
session; the gcc-13-built deps that DID succeed (abseil, cppzmq, zeromq, c-ares,
openssl, re2, utf8-range, zlib) are cached in the vcpkg binary cache.

**Candidate rule (2):** A vendored dependency's vcpkg manifest that mixes a fresh
`builtin-baseline` with old overlay-port pins can resolve an internally
inconsistent graph (recent protobuf vs. ancient abseil). Prefer building a tagged
release with a self-consistent version set, or pin the transitive versions
explicitly, before assuming a build failure is a local toolchain problem.

**Correction to the WSL-host approach:** The right build path for this consumer
is the app's OWN `apps/scifi2-hub-manager/Dockerfile` (arm64 cross-compile,
`ubuntu:20.04` + gcc-10-aarch64, CMake 3.28, vcpkg pinned `0f88ecb8`), driven from
the app's own `vcpkg.json` — that manifest resolves a SELF-CONSISTENT set
(protobuf 4.25.1 with abseil 20240116.2, plus hdf5 1.14.4.3, iir1, spdlog, fmt),
so the protobuf/abseil skew above never arises. The skew was an artifact of
building `vendor/synapse-cpp`'s standalone manifest (fresh baseline → protobuf
6.33.4) in isolation. Build the consumer through the app manifest/Docker path, not
a hand-driven synapse-cpp build.

### 2026-09-04 — szip 2.1.1 cross-compile configure needs try_run seeds (guessed the wrong fix first)

**Attempt:** Ran the app's Docker build (`arm64-linux-dynamic-release`, x86 host
cross-compiling to arm64). vcpkg resolved the consistent dependency set; hdf5
pulls szip 2.1.1 transitively, which failed at the CMake *configure* step
(`/usr/bin/ninja -v ... Error code: 1`).

**Failure (and a wrong first fix):** The in-container config log was not captured
by the failed Docker layer, so the cause was initially GUESSED as the well-known
"CMake 3.28 removed pre-3.5 `cmake_minimum_required` compatibility" error, and
`-DCMAKE_POLICY_VERSION_MINIMUM=3.5` was added to the triplets. The rebuild failed
identically. Reproducing szip's configure inside a container built to the COPY
layer (`docker build` truncated Dockerfile → `docker run` the manifest install →
`cat buildtrees/szip/config-arm64-linux-dynamic-release-out.log`) showed the flag
HAD worked (the pre-3.5 error became a mere deprecation warning) but the real,
distinct blocker was:

    CMake Error: try_run() invoked in cross-compiling mode, please set the
    following cache variables appropriately:
       HAVE_DEFAULT_SOURCE_RUN (+ __TRYRUN_OUTPUT)
       TEST_LFS_WORKS_RUN      (+ __TRYRUN_OUTPUT)

**Cause:** szip 2.1.1's `config/cmake/ConfigureChecks.cmake` uses `try_run()` to
probe runtime behavior (`_DEFAULT_SOURCE`, large-file support). Under
cross-compilation CMake cannot execute the arm64 test binary and hard-errors
unless those result variables are pre-seeded. Nothing to do with CMake policy
version. (The `io.h`/`winsock2.h`/`off64_t` lines in the log are unrelated failed
probes, not the fatal error.)

**Correction:** Replaced the policy flag with the correct cross-compile seeds in
both `external/sciencecorp/vcpkg/triplets/{arm64,x64}-linux-dynamic-release.cmake`:
`HAVE_DEFAULT_SOURCE_RUN=0` and `TEST_LFS_WORKS_RUN=0` (both succeed on
arm64/glibc), plus their `__TRYRUN_OUTPUT` companions. Verified by reproducing the
szip configure in the truncated-Dockerfile container. Rebuild pending (USER drives
the Docker build).

**Candidate rule:** Do not apply a fix from a *guessed* cause when the real log
can be captured. For a failed Docker layer, build a Dockerfile truncated to the
last good layer and run the failing command in a `docker run` to read the actual
error. When a legacy port fails to configure while cross-compiling with a
`try_run()` error, pre-seed the named `*_RUN` (and `*_RUN__TRYRUN_OUTPUT`) cache
variables via the triplet's `VCPKG_CMAKE_CONFIGURE_OPTIONS`, not a policy flag.

### 2026-09-04 — `setWindowIcon` did not change the Windows taskbar icon

**Attempt:** Gave the PySide6 GUIs (`gui.py`, `waveform.py`) a custom icon by
loading `assets/icon.svg` into a `QIcon` and calling `setWindowIcon` on both the
window and the `QApplication`, expecting the taskbar/tray icon to update.

**Failure:** The window title-bar and Alt-Tab icon changed, but the Windows
taskbar still showed the generic launcher icon (the "penguin"/Python icon).

**Cause:** On Windows the taskbar groups and icons a running app by its
**AppUserModelID (AppUMID)**, not by `QApplication.windowIcon`. A
Python-launched Qt process inherits the host launcher's AppUMID and therefore
its taskbar icon; `setWindowIcon` cannot override that surface.

**Correction:** Added `set_windows_app_id()` in
`stateful_decode_and_sync/appicon.py`, which calls
`ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(...)` with a
stable id (`NML.ScienceXYZ.StatefulDecodeAndSync`) before any window is shown;
called it from `run_gui`/`run_waveform`. It is a no-op off Windows and swallows
failures. Verified with `GetCurrentProcessExplicitAppUserModelID` (HRESULT 0,
id round-trips).

**Candidate rule:** On Windows, `QApplication.setWindowIcon` fixes only the
title-bar/Alt-Tab icon. To control the **taskbar** icon of a Python/Qt app, set
an explicit AppUserModelID via `SetCurrentProcessExplicitAppUserModelID` before
the first window is shown.

### 2026-09-04 — Taskbar still showed the penguin: the GUI was launched under WSL

**Attempt:** After adding the Windows AppUserModelID fix above, expected the
taskbar icon to be correct. The user still saw the "penguin" when launching
`stateful-decode-and-sync-waveform`.

**Failure:** The AppUMID fix had no effect on the observed taskbar icon.

**Cause:** The command was run from a **WSL Ubuntu** shell
(`maxmu@MAXLENOVO:/mnt/c/...`), so the GUI is a **Linux** process rendered by
**WSLg**. `sys.platform` is `linux`, so `set_windows_app_id()` correctly no-ops;
the Win32 AppUMID API is not the mechanism there. WSLg icons/groups a window by
a matching freedesktop `.desktop` entry keyed to the window's `WM_CLASS`, and
with none installed it shows its default Linux (penguin) icon. Easy to misread
as "the icon fix failed" when the real variable is *which OS the process runs
under* — the same repo path is reachable from both a native-Windows Python and a
WSL Python, and only the native one takes the AppUMID path.

**Correction:** Added a Linux/WSLg path in `appicon.py`: `install_desktop_entry`
writes `~/.local/share/applications/<app_id>.desktop` (with `StartupWMClass` =
app id) plus a themed PNG under `~/.local/share/icons/hicolor/256x256/apps/`, and
`set_desktop_file_name` calls `QGuiApplication.setDesktopFileName` so Qt sets the
matching `WM_CLASS`. `setup_taskbar_identity(app_leaf, name)` dispatches
per-platform and is called from both entry points. Shipped a rasterized
`assets/icon.png` (WSLg wants a PNG, not the SVG) and added it to
`package-data`. For a guaranteed-correct taskbar icon, run the **native
Windows** venv from PowerShell/CMD, not WSL.

**Candidate rule:** Before diagnosing a GUI desktop-integration symptom
(taskbar/tray icon, window grouping), confirm which OS the process actually runs
under. A `/mnt/c` path in a `user@HOST` prompt means WSL/WSLg (Linux), where
Win32 APIs no-op and freedesktop `.desktop`/`WM_CLASS` is the mechanism — not the
Windows AppUserModelID.

### 2026-09-05 - WSL recorder selected Windows HDF5 and omitted nested protos

**Attempt:** Build the standalone raw recorder with distribution host dependencies.

**Failure:** Linux compilation found Windows HDF5 1.14.3 headers and failed on a
conflicting ssize_t definition; API node headers were also missing.

**Cause:** HDF5 package-config discovery inherited a Windows installation; the
initial protobuf glob included only top-level API definitions.

**Correction:** Use HDF5 C-wrapper discovery (HDF5_NO_FIND_PACKAGE_CONFIG_FILE),
clear cached HDF5 variables, and recursively generate canonical API protos with
relative paths preserved. The Linux host recorder and tests subsequently built.

**Candidate rule:** Keep host library/header discovery within one platform and
preserve imported protobuf directory structure during code generation.

### 2026-09-05 - Mixed browser time units in supplied task client

**Attempt:** Map the supplied Reactions TaskSocketClient timestamps into recording
provenance. Inspection found `_t0 = performance.timeOrigin` (milliseconds) added
to `performance.now()/1000` (seconds), so the resulting value cannot be interpreted
as a consistent epoch clock.

**Correction:** Preserve incoming fields literally in the browser journal. The
companion adapter emits explicitly named millisecond clock fields; authoritative
labels use recorded device task boundaries. The supplied reference JS is unchanged.
No browser-to-source clock mapping or physical onset is claimed.

**Candidate rule:** Check time units at every clock composition before assigning
an epoch or aligning browser events with source samples.

### 2026-09-05 - Short diagnostic traces disappeared

**Attempt:** Render the synthetic 1,300-frame calibration diagnostic using a
min/max envelope. Its one-sample bins produced zero-length vertical lines and
invisible electrode traces.

**Correction:** Plot actual samples within each continuity segment when bins
contain one sample; retain min/max aggregation for long recordings. Inspect the
rendered short fixture as well as the real high-rate recording.

### 2026-09-05 - Preparation depended on missing session provenance

The workflow referenced an ignored previous-session provenance file and claimed
a prepared directory existed. The operator encountered an existing output path,
then a missing provenance input. The preparer wrote config/profile before reading
metadata, leaving partial output on that failure. It now validates inputs before
creating output and reports existing directories without a traceback. The guide
uses the tracked template and a fresh-directory recovery path. Two regression
tests verify no output on missing metadata and no overwrite of existing files.

### 2026-09-05 - Coincident GPIO overlays hid one channel

Full-height GPIO markers were drawn in GPIO 0 then GPIO 1 order. Synchronous
master/hopping edges could overlap exactly, making GPIO 0 appear absent. Each
GPIO now uses a separate vertical half of the subplot without shifting time;
an offscreen regression test verifies both lanes at the same timestamp. Missing
visible markers alone do not prove missing raw GPIO transitions.

### 2026-09-05 - Calibration guide mixed terminal roles and session names

The operator encountered repeated connection, existing-journal and task-definition
errors while following a guide that mixed setup v1/v2, port defaults and service
versus browser roles. The operator also corrected the CLI spelling to synapsectl.
Rewrote the guide with numbered WSL terminals, one setup/port convention, fresh
recording names, expected readiness messages, separate terminal/browser routes,
and an explicit App Running check. Latest info reports App Running False despite
overall device Running; old July log output is not current failure evidence.

### 2026-09-10 - science-mcp-install crashed on a float in an existing config.toml

`science-mcp-install --scope both` raised `TypeError: unsupported TOML scalar:
60.0` while writing the operator's user `~/.codex/config.toml`. Cause: the
installer's dependency-free `_minimal_toml_dump` fallback (used when `tomli_w`
was not installed) only handled str/int/bool, but the real config held float
timeouts (`startup_timeout_sec = 60.0`, etc.) from other MCP servers. The crash
happened before any write, so no config was corrupted, but the install failed.
Two-part fix: (1) added `tomli-w>=1.0` as a hard dependency so a proper writer
is always used; (2) hardened the fallback to emit floats and to REFUSE (with an
actionable `pip install tomli-w` hint) rather than silently mangle a config it
cannot round-trip (datetimes, arrays-of-tables). Regression tests cover a float
scalar surviving the merge, the fallback's float handling, and the refuse path.
Candidate rule: a config-merge tool must round-trip the file's existing values
losslessly or fail loudly; never fall back to a writer that can only represent a
subset of the format. Also: `$(which ...)` is POSIX syntax and fails in cmd.exe;
document per-shell command forms for operator-run install tooling.

### 2026-09-11 - OpenRB core does not link with -DCDC_DISABLED (interface-0 assumption)

**Attempt:** For the OpenRB Axon-enumeration proof (`firmware/OpenRB_Axon_Enum_Proof/`),
scifi-server claims USB interface 0, so the plan was to disable CDC
(`-DCDC_DISABLED`) so the new vendor/bulk PluggableUSB interface would be the
only interface and land at interface 0. An early `arduino-cli compile
--build-property "build.extra_flags=... -DCDC_DISABLED ..."` appeared to SUCCEED.

**What went wrong:** The "success" was a stale cached `core.a` built WITHOUT the
flag; `nm` on the ELF still showed `Serial_`/`SerialUSB`. After clearing the core
cache and a true `--clean` rebuild, the link FAILED: `USBCore.cpp:921: undefined
reference to Serial_::handleEndpoint(int)` and `SAMD21_USBDevice.h:109: undefined
reference to SerialUSB`. The OpenRB-150 SAMD core (0.2.1) references `SerialUSB`
and `Serial_::handleEndpoint` OUTSIDE the `#ifdef CDC_ENABLED` guards, so CDC
cannot be cleanly disabled by the documented macro without editing the shared
core (which must not be modified).

**Cause:** (1) arduino-cli's cached precompiled core masked the real build
outcome -- a build property that changes core compilation needs the core cache
cleared (or trust only a `--clean` build whose verbose output shows the flag on
the core `.cpp` compile lines). (2) This core's CDC gating is incomplete.

**Correction:** Left CDC ENABLED and instead guaranteed interface 0 by plug
order: the SAMD core assigns PluggableUSB interfaces first-plugged-first, and
constructors are collected via `__libc_init_array` with SORT-ed priority, so the
Axon module global is declared `__attribute__((init_priority(101)))` to
construct (and plug) before the core's default-priority `SerialUSB`. Result: a
composite device, Axon = interface 0, CDC = interfaces 1-2, within the 7-EP cap.
Verified by a clean build (no core edits, ~12 KB flash) and by confirming the
linker uses `.init_array`/`__libc_init_array` (forward, priority-sorted).

**Candidate rule:** When a build property affects core/library compilation,
never trust a non-`--clean` arduino-cli build -- clear the core cache or verify
the flag appears on the core file compile lines; a cached `core.a` will silently
hide both failures and the effect of the flag. And do not assume a documented
build macro (`CDC_DISABLED`) actually compiles/links on a vendor core; verify.
# 2026-09-11: SDK test executable needs librt on the focal cross-builder

The standalone axon-exo ARM64 plugin test initially failed to link with
`libscifi-peripheral-sdk.so: undefined reference to shm_open`. SDK 0.2.0 uses
POSIX shared memory, and the focal toolchain requires explicit `librt` linkage
for the test executable. Added `rt` to that target; it then linked and loaded
the actual plugin successfully under QEMU. Retain the real SDK load test so
plugin compilation alone is not mistaken for runtime ABI validation.
