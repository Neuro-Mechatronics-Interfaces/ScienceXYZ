# Exo commands through SciFi-2 over Wi-Fi

The laptop's existing Synapse control service sends typed commands over the `control` Tap to `SciFi2HubManagerApp`. Its dedicated Exo worker owns the USB connection to OpenRB-150. No laptop Exo SDK, COM port, SSH relay, new headstage network listener, or separate Synapse peripheral is needed.

## Operator workflow

Rebuild and deploy the updated App using your normal `synapsectl apps` workflow. The deployed version must include the new `connected` mode and `query_exo` schema. From the repository root, start the Exo configuration (baseline `rhd2132.json` does not enable the Exo worker):

```cmd
synapsectl -u 192.168.100.157 start apps/scifi2-hub-manager/config/rhd2132_with_exo.json
```

In a laptop terminal with the project Python environment activated, start the host control service. This is the **`run_service.py`** control service (the `BroadbandController`, loopback port 18765): it forwards typed commands to the on-device App over the Synapse `control` Tap, and the App's Exo worker owns the USB link to the OpenRB. Do **not** use `run_exo_service.py` here -- that is a different architecture (a laptop-attached exo over local COM ports, port 18766) and does not apply when the OpenRB is plugged into the SciFi-2:

```cmd
python apps/scifi2-hub-manager/client/run_service.py --device-ip 192.168.100.157
```

Keep that process running. In another terminal, capture a read-only probe locally:

```cmd
python apps/scifi2-hub-manager/client/exo_via_scifi.py probe > exo-probe.log 2>&1
python apps/scifi2-hub-manager/client/exo_via_scifi.py limits > exo-limits.log 2>&1
python apps/scifi2-hub-manager/client/exo_via_scifi.py angles > exo-angles.log 2>&1
```

These commands claim the primary CDC pair, route replies to it, read firmware and the requested diagnostic, print results/status, and close the connection. They do not enable torque or home. `limits` reports the firmware's calibration check; `angles` returns its gesture-angle representation, not a new independent measurement or evidence that a commanded trajectory completed.

### Troubleshooting: `no 'OK: reply_route' ack for set_reply_route`

Every one of `probe`/`limits`/`angles` begins with `set_exo_mode connected`, whose first device action is the CDC handshake: the App sends a `set_reply_route` command and waits ~1.5 s for the firmware's `OK: reply_route ...` reply. If all three probes fail identically with a `no 'OK: reply_route ...' ack ...; outcome unknown; link closed` error, the USB pair was opened and claimed but the firmware's reply never reached the CDC the App reads.

The observed 2026-09-10 cause was a **CDC enumeration-order swap**, not a firmware version problem (the board reported Version 0.7.1, and the same board's COM ports came up swapped on the host). The OpenRB exposes two USB CDCs whose `Serial` / `SerialTelem` roles are assigned by unspecified C++ global init order, so which physical CDC enumerates as USB interface 0 is not guaranteed. The App always claims interface 0 and reads replies only there. The earlier handshake asked for `set_reply_route:cmd`, which routes replies to the firmware's `CMD_SERIAL` alone; when that landed on the interface the App had **not** claimed, every reply -- the handshake ACK and all later current-limit/home/pose ACKs -- was missed and the connect timed out.

Three host-side changes address this, in `src/exo_link.cpp` `do_connect` and `src/usb_cdc_port.*` (rebuild and redeploy the App to pick them up):

1. **Order-independent routing.** The App requests `set_reply_route:both`, so replies mirror to both CDCs and reach whichever one the App claimed. No firmware reflash or compile-time `DUAL_CDC_SWAP` is required.
2. **Settle + drained retry.** After opening the CDC (DTR asserted), the App waits `open_settle_ms` (default 300 ms) before the first write, because an OpenRB/SAMD CDC gates TX on DTR and can drop the first reply if written to immediately. The idempotent `set_reply_route` handshake is then retried up to `connect_handshake_attempts` (default 3), draining any buffered bytes (e.g. a stale startup banner) before each attempt. On failure the App records what bytes it saw in `state.exo.last_error` (`pre-write bytes: ...`), so a persistent timeout is diagnosable from the client without a device journal.
3. **Candidate CDC fallback.** The bench evidence showed the claimed interface receiving *nothing* (`no bytes seen before write`) across every retry, even with `both` -- the firmware's command channel was simply the *other* CDC. The App now tries each CDC-ACM control interface in turn (`control_interfaces`, default `{0, 2}` for the OpenRB): it claims the first, runs the handshake, and on no reply tears down and advances to the next, keeping whichever answers `OK: reply_route both`. Each candidate is still verified from its own Union descriptor. This is fully enumeration-order-independent -- the App discovers the command CDC instead of assuming interface 0.

If a `no 'OK: reply_route ...' ack` still occurs after redeploy, read `state.exo.last_error`: `pre-write bytes:` empty on *every* candidate means no CDC on the device answered at all -- check firmware presence directly (a serial terminal at 1 Mbaud: `set_reply_route:both` should answer `OK: reply_route both;`) and confirm the flashed firmware is the `third_party/exo` `feat/set_finger_angles` build (>= 0.6.4). If a candidate shows a banner but no ACK, raise `open_settle_ms` / `connect_handshake_attempts`.

After checking calibration and preparing the unloaded mechanism, set `exo_motion_enabled` to `true` in the App configuration and restart with that configuration. Review current limits against the particular motors first. Then send one explicit bounded test from the laptop:

```cmd
python apps/scifi2-hub-manager/client/exo_via_scifi.py move --joint index --value 10 --allow-motion > exo-move.log 2>&1
```

This connects, applies the configured current limits, sends `enable:all`, commands index +10, waits 250 ms, reports status, and sends `disable:all` before closing. Enabling torque can itself move a motor toward an existing target. No automatic homing occurs. Other joints are omitted from the pose, which means hold their existing targets; torque enable/disable applies to all motors. An explicit disconnect attempt is also available:

```cmd
python apps/scifi2-hub-manager/client/exo_via_scifi.py off
```

## GUI alternative

After installing the updated client, launch:

```cmd
pip install -e apps/scifi2-hub-manager/client
gui-exo --device-ip 192.168.100.157
```

Without reinstalling console scripts, use `python apps/scifi2-hub-manager/client/run_exo_gui.py --device-ip 192.168.100.157`. The GUI connects directly through the same typed Synapse controller; a separate `run_service.py` process is unnecessary -- the whole flow is self-contained in the one window. Use one operator control client at a time.

Work top to bottom. Section **1. Device and App start** shows the exact `synapsectl -u <uri> start .../rhd2132_with_exo.json` line with a **Copy** button. You can run that yourself and **Load info capture...**, or use the optional **Run: start device** and **Run: fetch info + gate** buttons, which invoke `synapsectl` directly (the `synapsectl command` field defaults to the name resolved from the active venv/PATH; override it for a differently located or WSL install). Either way the window gates on Application `scifi2-hub-manager` -> Running: True. Running `synapsectl` from this operator GUI is permitted under the AGENTS.md CLI-execution boundary; it never happens from a test or an agent path. The device URI and `synapsectl command` fields persist between launches in a per-user INI (`QSettings`; on Windows under `%APPDATA%/NML/ExoBenchConsole.ini`).

Then in section **2**, click **Connect / check USB**, then **Version**, **Limits**, or **Angles**. The log displays replies and changes in Exo status. Button readiness is driven by the command results (a succeeded connect opens the link), not the broadcast device state. Checking the motion-test box unlocks **Enable + send 250 ms test** for the selected joint/value; the device configuration must also enable motion. Editing a value does not send a command. Each test explicitly enables, sends once, and disarms back to connected mode. Errors clear the motion checkbox.

**Clean teardown is enforced.** When the exo link is open, **Run: stop device** first disarms and closes the link (`set_exo_mode off`) and only then runs `synapsectl stop`; closing the window does the same. This matters because `synapsectl stop` kills the App, and an App killed with the link still open never de-asserts DTR or releases the CDC, which can wedge the OpenRB until a physical replug. The teardown is best-effort and bounded, and the window always closes (a second close forces it; an 8 s fallback closes it if a device call hangs). If you run `synapsectl stop` **by hand** instead of via the button, disarm/disconnect the exo first (the GUI's **Disarm / disconnect USB**, or `set_exo_mode off`) for the same reason. The App must already be deployed and running with the Exo configuration.

## Python or other clients

Use the loopback service on port 18765. It uses newline-delimited JSON, request IDs, and terminal success/failure results. For example, an operator-run Python program can use:

```python
from scifi2_hub_manager.client import NdjsonClient

with NdjsonClient(timeout=15) as client:
    try:
        client.request("set_exo_mode", mode="connected")
        client.request("query_exo", query="check_limits")
        print(client.get_state_snapshot()["exo"])
        # Explicit motion, only with exo_motion_enabled in device config:
        # client.request("set_exo_mode", mode="external")
        # client.request("set_exo_pose", joints={"index": 10})
    finally:
        client.request("set_exo_mode", mode="off")
```

Other languages can send the same fields with `protocol_version: 1`, a unique `request_id`, and `command`. No raw firmware-command passthrough is exposed. Read queries are allowlisted: `version`, `check_limits`, `get_gesture_angles:all`; they require a connected, disarmed worker. This service is for a trusted bench network. Keep its bind address loopback; the existing Synapse control Tap is not a new authenticated motion API.

## Modes and configuration

| Mode | Behavior |
|---|---|
| `off` | Default; connection closed. Leaving an armed mode attempts disarm. |
| `connected` | USB handshake and queries, no enable/home/pose. Disarms on entry from an armed mode. |
| `external` | Explicit motor enable; accepts host poses. Requires motion config gate. |
| `decode` | Explicit motor enable; class pose mapping drives targets. Requires motion config gate. |

Poses use firmware's rest-anchored signed scale: -100 extension, 0 rest, +100 flexion. They are not degrees. Firmware >= 0.6.4 is required for arming. The firmware must also implement `set_reply_route` (the App requests `set_reply_route:both` on connect); lack of its ACK fails the connection. Named open/precision-grasp actions need calibrated mappings; this bridge does not infer those mappings.

| Parameter | Default / supported range |
|---|---|
| `exo_enabled` | false; example configuration true |
| `exo_motion_enabled` | false; explicit motor-enable gate |
| `exo_transport` | `usb_cdc`; optional `tty` for a kernel with CDC-ACM |
| `exo_usb_serial` | empty; exactly one VID 2f5d / PID 2202 match required |
| `exo_device_path` | `/dev/ttyACM0`; used only with `tty` |
| `exo_baud` | 1000000, integer 1..4000000 |
| `exo_total_current_ma` | 800, integer 1..2000 |
| `exo_per_motor_current_ma` | 250, integer 1..1000 |
| `exo_watchdog_ms` | 1000, integer 100..5000 |
| `exo_decode_min_confidence` | 0.6 |
| `exo_class_poses` | indexed by class; lists of `[joint, value]` pairs |

Class joint IDs are 1 thumb, 2 index, 3 middle, 4 ring, 5 pinky, 6 wrist. An empty class leaves targets unchanged. While a USB operation is pending, new decode actuation updates are skipped with a debug log; neural acquisition continues. External commands receive a busy failure while an operation is pending. There is no motion replay or automatic rearm after a fault.

## Transport, results, and limitations

The observed headstage has no CDC-ACM kernel support. The App therefore uses libusb, discovers the primary control interface 0's CDC Union slave and bulk endpoints from descriptors, claims only that pair, sets 8N1 line coding and DTR/RTS, and sends `set_reply_route:both`. It requests `both` rather than `cmd` because which physical CDC enumerates as interface 0 is not guaranteed (the firmware's two CDCs have unspecified init order); mirroring replies to both CDCs guarantees the App's claimed interface carries them. The second CDC pair remains unclaimed. Ambiguous devices, malformed descriptors, bound kernel drivers and failed claims fail clearly; the App does not detach a kernel driver or chmod devices. An optional USB serial string disambiguates devices without pinning a bus address.

Bulk writes have a 500 ms total budget; replies have a 1500 ms budget. Partial reads on USB timeout are retained. Failed writes or missing ACKs close the link and report an uncertain outcome; never blindly retry a motion. Replugging requires an explicit new connection command. Transfers occur off the App's acquisition loop, with one App operation in flight and a bounded worker queue.

`state.exo` reports configured/mode/link/armed, firmware compatibility, transport, last reply/error, last commanded values, and watchdog status. `armed` describes software intent: enable/disable are silent firmware commands, so successful writes do not confirm physical torque state. Pose ACKs indicate command acceptance, not completed movement. Unknown/uncalibrated-joint ACKs fail and may represent partial movement. Connection alone does not establish calibration.

The App watchdog attempts `disable:all` after inactivity starting at arm or the last acknowledged pose; it latches and requires explicit rearm. It is serviced between worker jobs (25 ms idle polling), so an in-flight USB operation can delay it. USB unplug, App crash, process stop or headstage power loss can prevent any disable reaching the Exo. It is not a firmware watchdog or emergency stop. Physical stop access remains necessary; T-53 tracks the independent safety layer.

## Evidence and remaining acceptance

Operator evidence supplied 2026-09-10 confirms setup enumeration and successful `libusb_open` under the App's observed root identity, not CDC claims, replies or motion. See [USB bench notes](scifi2_openrb_usb_debugging_notes.md). Hardware-free tests cover descriptor selection, claim rollback, packet buffering, partial writes/timeouts/disconnect, worker framing/watchdog, Python dispatch and status serialization. ARM64 SDK compile/link has passed. Bench acceptance still requires probe/limits replies, explicit unloaded motion, watchdog behavior, unplug/reconnect behavior, and acquisition continuity while issuing commands.
