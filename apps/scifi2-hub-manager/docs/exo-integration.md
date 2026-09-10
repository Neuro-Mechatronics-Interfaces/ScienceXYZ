# On-device NML_Hand_Exo integration

The scifi2-hub-manager App can drive an NML Hand Exoskeleton that is connected to the **SciFi-2 headstage's USB**. The App owns the exo serial link directly on a dedicated worker thread; the exo is **not** a separate Synapse peripheral and does not appear as its own node in the signal chain.

Control rides the existing `control` tap and its `ControlCommand` protocol — no new tap or port. Two commands were added: `set_exo_mode` and `set_exo_pose`. The `state` snapshot gained an `exo` section reporting link/arm/firmware status.

## Engagement modes

`set_exo_mode` selects one of three modes; the default at startup is **OFF**, so the hand is never armed or moved until a client explicitly engages it.

| Mode | Who drives the hand | Serial link |
|---|---|---|
| `off` (default) | nobody | closed; hand untouched |
| `external` | host `set_exo_pose` commands (e.g. a GUI button) | opened + armed on entry |
| `decode` | the App's own decode output, via the per-class pose table | opened + armed on entry |

Entering `external` or `decode` opens the link, applies the configured current budget, enables torque, and homes the hand. Returning to `off` disarms and closes the link. An inactivity watchdog eases the hand back to neutral rest when no pose command arrives within `exo_watchdog_ms`.

In `decode` mode the same softmax distribution that drives `class_out` selects a pose: the winning class (argmax), if its probability is at least `exo_decode_min_confidence`, looks up `exo_class_poses[class_id]`; a class with no entry (or an empty entry) holds the current pose.

`set_exo_pose` is accepted only in `external` mode. Poses carry a signed value per joint on the rest-anchored axis: **-100 = extend, 0 = rest, +100 = flex**. An omitted joint is held unchanged. This requires exo firmware **>= 0.6.4** (the `set_finger_angles` batch command); the App reports `firmware_ok=false` and refuses to arm below that.

## Configuration

Exo support is off unless `exo_enabled` is true in the App node parameters. See [`config/rhd2132_with_exo.json`](../config/rhd2132_with_exo.json) for a complete example. Parameters:

| Parameter | Default | Meaning |
|---|---|---|
| `exo_enabled` | `false` | Build the exo worker at all. |
| `exo_device_path` | `/dev/ttyACM0` | Serial device node the App opens (see identification below). |
| `exo_baud` | `1000000` | CDC baud. |
| `exo_total_current_ma` | `800` | Combined current budget applied before torque (0 = firmware default). |
| `exo_per_motor_current_ma` | `250` | Per-motor nominal current (0 = firmware default). |
| `exo_watchdog_ms` | `1000` | Idle ms before easing to neutral rest (0 = disabled). |
| `exo_decode_min_confidence` | `0.6` | Minimum winning-class probability to drive a pose in `decode` mode. |
| `exo_class_poses` | none | List indexed by class id; each entry is a list of `[joint, value]` pairs. |

`exo_class_poses` joint indices are `1=thumb, 2=index, 3=middle, 4=ring, 5=pinky, 6=wrist`. Example: `[[1,100],[2,100]]` flexes thumb and index (a pinch); `[]` holds.

## Identifying the exo serial device

**Operator evidence supplied 2026-09-10:** the observed headstage has
`CONFIG_USB_ACM` unset, no CDC-ACM module, and no `/dev/ttyACM*` for the
enumerated OpenRB-150. The serial backend below therefore remains unverified
and cannot use that absent tty. See [USB debugging notes](scifi2_openrb_usb_debugging_notes.md).
The setup libusb enumeration/open probe tests access only; it does not implement
the userspace dual-CDC transport. The instructions below apply if a supported
tty backend becomes available. Do not infer communication or motion readiness
from a successful libusb open.

`exo_device_path` is a plain config parameter so the target tty is easy to set once the OpenRB-150 board is plugged into the headstage. To find which node it is, compare the device list with and without the board attached. The App itself never runs `synapsectl`; run these yourself (per the repository CLI-execution boundary):

1. **Before** plugging in the exo, list the serial devices on the headstage. If
   you have a shell on the headstage:

   ```bash
   ls /dev/tty*
   # or, more specific to USB CDC-ACM devices:
   ls -l /dev/ttyACM* 2>/dev/null; ls -l /dev/serial/by-id/ 2>/dev/null
   ```

2. **Plug in** the OpenRB-150 exo (headstage USB), wait a few seconds for it to
   enumerate, then list again:

   ```bash
   ls /dev/tty*
   ls -l /dev/ttyACM* 2>/dev/null; ls -l /dev/serial/by-id/ 2>/dev/null
   ```

3. The node that **appears only in the second listing** is the exo. A stable
   `/dev/serial/by-id/...` symlink, when present, is preferable to a bare
   `/dev/ttyACMn` because it survives re-enumeration; use it as
   `exo_device_path` if available.

4. Confirm the App can reach it: set `exo_enabled` and the path, deploy/start
   the App, then read the device state — the `exo` section of the `state`
   snapshot should show `link_open=true` and a parsed `firmware` string once a
   client sends `set_exo_mode:external`. To get the exact start command to run:

   ```bash
   # ask the read-only helper for the line; then run it yourself
   #   (never run synapsectl from an agent/CI path)
   synapsectl -u <device-ip> start config/rhd2132_with_exo.json
   synapsectl -u <device-ip> logs --since 60000   # look for "exo link enabled: device=..."
   ```

> The OpenRB dual-CDC firmware enumerates **two** ACM nodes on one cable. The
> current App opens a single node (the firmware default `reply_route:both`
> carries replies on whichever node receives commands). If a split reply route
> is needed later, add the second node behind the same `SerialPort` seam.

## Host control

From the host client, the controller exposes:

```python
controller.set_exo_mode("external")          # engage; arms + homes the hand
controller.set_exo_pose({"index": 100, "thumb": 100})   # pinch
controller.set_exo_mode("decode")            # let the decoder drive it
controller.set_exo_mode("off")               # disarm + release the link
```

These send `ControlCommand`s over the same `control` tap the GUI already uses, so a GUI button maps to one `set_exo_pose` call to the SciFi-2 over Wi-Fi, which the on-device worker turns into a `set_finger_angles` serial write.

## Safety notes

- The default mode is `off`; nothing energizes until a client engages a mode.
- Arming applies the current budget **before** enabling torque.
- Every teardown path disarms: `set_exo_mode:off`, App shutdown, and the worker
  destructor all send `disable:all`.
- The watchdog returns the hand to neutral rest if pose commands stop.
- All physical behavior is **unverified against hardware** at the time of
  writing — the worker and command formatting are covered only by the
  hardware-free `exo_link` unit test.
