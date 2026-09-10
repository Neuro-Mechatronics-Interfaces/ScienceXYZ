# Exo commands through SciFi-2 over Wi-Fi

The laptop's existing Synapse control service sends typed commands over the
`control` Tap to `SciFi2HubManagerApp`. Its dedicated Exo worker owns the USB
connection to OpenRB-150. No laptop Exo SDK, COM port, SSH relay, new headstage
network listener, or separate Synapse peripheral is needed.

## Operator workflow

Rebuild and deploy the updated App using your normal `synapsectl apps` workflow.
The deployed version must include the new `connected` mode and `query_exo` schema.
From the repository root, start the Exo configuration (baseline `rhd2132.json`
does not enable the Exo worker):

```cmd
synapsectl -u 192.168.100.157 start apps/scifi2-hub-manager/config/rhd2132_with_exo.json
```

In a laptop terminal with the project Python environment activated:

```cmd
python apps/scifi2-hub-manager/client/run_service.py --device-ip 192.168.100.157
```

Keep that process running. In another terminal, capture a read-only probe locally:

```cmd
python apps/scifi2-hub-manager/client/exo_via_scifi.py probe > exo-probe.log 2>&1
python apps/scifi2-hub-manager/client/exo_via_scifi.py limits > exo-limits.log 2>&1
python apps/scifi2-hub-manager/client/exo_via_scifi.py angles > exo-angles.log 2>&1
```

These commands claim the primary CDC pair, route replies to it, read firmware
and the requested diagnostic, print results/status, and close the connection.
They do not enable torque or home. `limits` reports the firmware's calibration
check; `angles` returns its gesture-angle representation, not a new independent
measurement or evidence that a commanded trajectory completed.

After checking calibration and preparing the unloaded mechanism, set
`exo_motion_enabled` to `true` in the App configuration and restart with that
configuration. Review current limits against the particular motors first.
Then send one explicit bounded test from the laptop:

```cmd
python apps/scifi2-hub-manager/client/exo_via_scifi.py move --joint index --value 10 --allow-motion > exo-move.log 2>&1
```

This connects, applies the configured current limits, sends `enable:all`,
commands index +10, waits 250 ms, reports status, and sends `disable:all` before
closing. Enabling torque can itself move a motor toward an existing target.
No automatic homing occurs. Other joints are omitted from the pose, which
means hold their existing targets; torque enable/disable applies to all motors.
An explicit disconnect attempt is also available:

```cmd
python apps/scifi2-hub-manager/client/exo_via_scifi.py off
```

## GUI alternative

After installing the updated client, launch:

```cmd
pip install -e apps/scifi2-hub-manager/client
gui-exo --device-ip 192.168.100.157
```

Without reinstalling console scripts, use
`python apps/scifi2-hub-manager/client/run_exo_gui.py --device-ip 192.168.100.157`.
The GUI connects directly through the same typed Synapse controller; a separate
`run_service.py` process is unnecessary. Use one operator control client at a time.
Click **Connect / check USB**, then **Version**, **Limits**, or **Angles**.
The log displays replies and changes in Exo status. Checking the motion-test box
unlocks **Enable + send 250 ms test** for the selected joint/value; the device
configuration must also enable motion. Editing a value does not send a command.
Each test explicitly enables, sends once, and disarms back to connected mode.
Errors clear the motion checkbox. Closing attempts disarm and disconnect; if it
fails, the window retains the error and requires another close to exit.
The App must already be deployed and running with the Exo configuration.

## Python or other clients

Use the loopback service on port 8765. It uses newline-delimited JSON, request
IDs, and terminal success/failure results. For example, an operator-run Python
program can use:

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

Other languages can send the same fields with `protocol_version: 1`, a unique
`request_id`, and `command`. No raw firmware-command passthrough is exposed.
Read queries are allowlisted: `version`, `check_limits`,
`get_gesture_angles:all`; they require a connected, disarmed worker.
This service is for a trusted bench network. Keep its bind address loopback;
the existing Synapse control Tap is not a new authenticated motion API.

## Modes and configuration

| Mode | Behavior |
|---|---|
| `off` | Default; connection closed. Leaving an armed mode attempts disarm. |
| `connected` | USB handshake and queries, no enable/home/pose. Disarms on entry from an armed mode. |
| `external` | Explicit motor enable; accepts host poses. Requires motion config gate. |
| `decode` | Explicit motor enable; class pose mapping drives targets. Requires motion config gate. |

Poses use firmware's rest-anchored signed scale: -100 extension, 0 rest,
+100 flexion. They are not degrees. Firmware >= 0.6.4 is required for arming.
The firmware must also implement `set_reply_route:cmd`; lack of its ACK fails
the connection. Named open/precision-grasp actions need calibrated mappings;
this bridge does not infer those mappings.

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

Class joint IDs are 1 thumb, 2 index, 3 middle, 4 ring, 5 pinky, 6 wrist.
An empty class leaves targets unchanged. While a USB operation is pending,
new decode actuation updates are skipped with a debug log; neural acquisition
continues. External commands receive a busy failure while an operation is
pending. There is no motion replay or automatic rearm after a fault.

## Transport, results, and limitations

The observed headstage has no CDC-ACM kernel support. The App therefore uses
libusb, discovers the primary control interface 0's CDC Union slave and bulk
endpoints from descriptors, claims only that pair, sets 8N1 line coding and
DTR/RTS, and sends `set_reply_route:cmd`. The second CDC pair remains unclaimed.
Ambiguous devices, malformed descriptors, bound kernel drivers and failed
claims fail clearly; the App does not detach a kernel driver or chmod devices.
An optional USB serial string disambiguates devices without pinning a bus address.

Bulk writes have a 500 ms total budget; replies have a 1500 ms budget. Partial
reads on USB timeout are retained. Failed writes or missing ACKs close the link
and report an uncertain outcome; never blindly retry a motion. Replugging
requires an explicit new connection command. Transfers occur off the App's
acquisition loop, with one App operation in flight and a bounded worker queue.

`state.exo` reports configured/mode/link/armed, firmware compatibility, transport,
last reply/error, last commanded values, and watchdog status. `armed` describes
software intent: enable/disable are silent firmware commands, so successful
writes do not confirm physical torque state. Pose ACKs indicate command
acceptance, not completed movement. Unknown/uncalibrated-joint ACKs fail and may
represent partial movement. Connection alone does not establish calibration.

The App watchdog attempts `disable:all` after inactivity starting at arm or the
last acknowledged pose; it latches and requires explicit rearm. It is serviced
between worker jobs (25 ms idle polling), so an in-flight USB operation can delay
it. USB unplug, App crash, process stop or headstage power loss can prevent any
disable reaching the Exo. It is not a firmware watchdog or emergency stop.
Physical stop access remains necessary; T-53 tracks the independent safety layer.

## Evidence and remaining acceptance

Operator evidence supplied 2026-09-10 confirms setup enumeration and successful
`libusb_open` under the App's observed root identity, not CDC claims, replies or
motion. See [USB bench notes](scifi2_openrb_usb_debugging_notes.md).
Hardware-free tests cover descriptor selection, claim rollback, packet buffering,
partial writes/timeouts/disconnect, worker framing/watchdog, Python dispatch and
status serialization. ARM64 SDK compile/link has passed. Bench acceptance still
requires probe/limits replies, explicit unloaded motion, watchdog behavior,
unplug/reconnect behavior, and acquisition continuity while issuing commands.
