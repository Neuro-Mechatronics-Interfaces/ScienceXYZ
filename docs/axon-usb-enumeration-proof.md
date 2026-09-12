# OpenRB-150 Axon enumeration proof (bench-only)

Goal: make an OpenRB-150 appear as a **recognized** Synapse/SciFi peripheral in
the built-in enumerated-peripheral UI, with the minimum change, **without** a
Lattice FPGA, and **without** disturbing the working exoskeleton firmware.

This is a *registration* proof only:

```
USB attach -> Axon ENUMERATE (type 3) -> device replies type 4 advertising 0xF001
           -> scifi-server PeripheralManager::parse_peripherals_ sees 0xF001
           -> PluginRegistry::find_by_peripheral_id(0xF001)
           -> /usr/lib/scifi/plugins/axon_test_source.so factory constructs it
           -> "Axon Test Source" shows up in the enumerated-peripheral UI
```

Streaming (`CONFIGURE` / `START_STREAM` / `DATA_FRAME`) and exoskeleton control
are explicitly **out of scope** here. The established host facts this builds on
are in the reverse-engineering findings (`usb::USBDeviceManager`,
`usb::USBDevice`, `axon::PeripheralManager`, `axon::PluginRegistry`,
`axon::ThreadedDepacketizer`), summarized in
[`docs/axon-peripheral-protocol.md`](axon-peripheral-protocol.md) §1/§7 and the
USB/Axon handshake findings.

## Pieces

| Piece | Where | Kind |
|---|---|---|
| Bench firmware | `firmware/OpenRB_Axon_Enum_Proof/` | OpenRB firmware (new, standalone) |
| Host VID patch | `scripts/scifi-server-accept-openrb-vid.py` | SciFi host (reversible binary patch recipe) |
| Registration plugin | `/usr/lib/scifi/plugins/axon_test_source.so` | already installed (reused, unchanged) |

The exoskeleton firmware (`third_party/exo`, submodule) is **not touched**: the
proof is a separate sketch under `firmware/`.

## USB descriptor / interface / endpoints used

The firmware registers one Arduino-SAMD PluggableUSB module:

```
Device descriptor:
  idVendor  = 0x2F5D   (ROBOTIS, the OpenRB-150's real VID -- not spoofed)
  idProduct = 0x2202   (OpenRB-150 runtime PID, from the board definition)

Configuration (composite; CDC left enabled):
  Interface 0  = VENDOR-SPECIFIC (class 0xFF, sub 0x00, proto 0x00)   <-- Axon
      EP  OUT  bulk, 64-byte max packet   (host -> device, ENUMERATE in)
      EP  IN   bulk, 64-byte max packet   (device -> host, response out)
  Interface 1-2 = CDC-ACM (SerialUSB)  [ignored by scifi-server]
```

`scifi-server` accepts the device on VID alone (no class/PID/endpoint-shape
check at `discover_devices`), requires >=1 BULK IN and >=1 BULK OUT anywhere on
the active config, and claims **interface 0**. So the Axon bulk pair must be
interface 0.

### Why interface 0 is guaranteed

The SAMD core numbers PluggableUSB interfaces in **plug order** (first plugged
-> interface 0; see `PluggableUSB_::plug`). The core's CDC `SerialUSB` plugs
itself at static-init. The Axon module global is declared
`__attribute__((init_priority(101)))`, and this core collects constructors via
`__libc_init_array` with `SORT`-ed priority sections, so the Axon constructor
runs **before** `SerialUSB` -> Axon gets interface 0, CDC gets 1-2. Endpoint
budget: Axon 2 + CDC 3 + control 1 = 7 = the core's `USB_ENDPOINTS` cap.

(Disabling CDC entirely would also free interface 0, but this OpenRB core does
not link with `-DCDC_DISABLED` -- it references `SerialUSB` outside the CDC
guards. See MISTAKES.md. The init_priority approach keeps CDC and needs no core
edits.)

## Peripheral ID advertised

`0xF001` -- the user-window ID the already-installed `axon_test_source.so`
registered via `SCIFI_REGISTER_PERIPHERAL`. `0xF001` is not a built-in ID in
`parse_peripherals_`, so it falls through to
`PluginRegistry::find_by_peripheral_id(0xF001)`, which resolves that plugin.
The plugin's transport-touching work happens only in `start_recording_impl`
(streaming), so **construction + `to_proto()` need no Axon round-trip** -- the
peripheral registers and displays ("Axon Test Source", vendor "Science
Corporation") from the enumerate handshake alone.

## The wire contract (what the firmware implements)

Axon frames are 32-bit-word little-endian, framed as (from the RE findings):

```
 offset  field
 0x00    u32  magic = 0xC0FFEE00
 0x04    u32  reserved / seq (0)
 0x08    u16  addr        (routing address; high u16 unused)
 0x0C    u32  reserved (0)
 0x10    u32  reserved (0)
 0x14    u32  (type << 16) | payload_len_bytes     # len @0x14, type @0x16
 0x18    ...  payload (little-endian packed)
 0x18+L  u32  CRC32 trailer
```

- The host's `compute_crc32` is a **stub returning 0x00000020** and the receive
  path (`ThreadedDepacketizer`) slices purely by length and does **not** verify
  the trailer, so the firmware emits `0x00000020` as the trailer.
- ENUMERATE request (host->device): `type=3`, no payload (a 28-byte frame).
- ENUMERATE response (device->host): `type=4`, payload = one u32 = `0xF001`.

Critically, the device sends the **outer 0xC0FFEE00-framed** form. The host's
`ThreadedDepacketizer` is a streaming state machine that unwraps it and
republishes the inner message `[u32 addr][u32 type=4][u32 reserved][u32 0xF001]`
that `enumerate_peripherals_`/`parse_peripherals_` consume. The firmware does
**not** put the inner representation on the wire directly.

Response bytes the firmware emits (addr echoed from the request; shown for
addr = 0x0001):

```
00 EE FF C0  00 00 00 00  01 00 00 00  00 00 00 00
04 00 04 00  01 F0 00 00  20 00 00 00
^magic       ^rsvd        ^addr=1      ^rsvd
             len=4,type=4 ^id=0xF001   ^crc stub
```

## How the VID gate was handled

The OpenRB keeps its honest ROBOTIS VID `0x2F5D`. `scifi-server` accepts only
`0x399A`/`0x2AC1`, so the **host** is adjusted to accept **three** VIDs:

* `0x399A` — Science (unchanged)
* `0x2AC1` — SciNetics/RHD adapter (**must remain**; the RHD adapter enumerates
  as `2AC1:0004`, so removing it would break the existing RHD path)
* `0x2F5D` — OpenRB-150 / ROBOTIS (**added**)

`discover_devices` tests `idVendor` with a two-value idiom that cannot express a
third discrete value in place:

```
mov  w24,#0x399A
mov  w23,#0x2AC1
...
ldrh w1,[sp,#0x88]          ; idVendor
cmp  w1,w24                 ; 0x289708
ccmp w1,w23,#0x4,ne         ; 0x28970c
b.ne 0x2896e0  (skip)       ; 0x289710  ; accept = fall through to 0x289714
```

The three VIDs share no usable mask or range (a single fixed-bit mask over them
accepts 2048 VIDs; a range accepts 3802), so a mask/range approach is rejected
as far too permissive. Instead the patch is a minimal, reversible **detour into
a zero-filled `.text` code cave** (inter-function padding), which re-runs the
original two compares and adds the third:

```
site 0x289708:  cmp  w1,w24         ->  b  0x242278   (detour to cave)
     0x28970c:  ccmp w1,w23,#4,ne   ->  nop
     0x289710:  b.ne 0x2896e0       ->  nop

cave 0x242278:  cmp  w1,w24             ; 0x399A
     0x24227c:  ccmp w1,w23,#4,ne       ; 0x2AC1   (Z if either matched)
     0x242280:  b.eq 0x289714           ; -> accept
     0x242284:  mov  w17,#0x2F5D        ; third VID (w17/IP1 = ABI scratch, dead here)
     0x242288:  cmp  w1,w17
     0x28228c:  b.eq 0x289714           ; -> accept
     0x242290:  b    0x2896e0           ; -> skip
```

Both existing VIDs are preserved bit-for-bit (the cave re-runs the identical
`cmp`/`ccmp`). `w17` (IP1) is the AArch64 intra-procedure scratch register and
holds no live value across this straight-line sequence.

`scripts/scifi-server-accept-openrb-vid.py` implements it with `--check`,
`--revert`, and `--self-test`, guards every original byte before writing
(refusing a non-matching binary), and requires the cave to be all-zero before
use. Verified three independent ways:

* `--self-test` simulates the cave's NZCV logic and asserts `0x399A`/`0x2AC1`/
  `0x2F5D` **accept** and unrelated VIDs (incl. near-misses `0x2AC0`, `0x2F5C`,
  `0x399B`) **reject**;
* a patch -> check -> re-patch (idempotent no-op) -> revert round-trip on a copy
  of the analyzed binary reproduces a **byte-identical** file;
* `capstone` (third-party AArch64 decoder) decodes the patched bytes to exactly
  the instructions above, with both `b.eq` landing on accept (`0x289714`) and
  the final `b` on skip (`0x2896e0`).

This is a bench expedient, not a production-legitimate use of a VID: the OpenRB
is not a Science device. Production would require a Science-assigned ID path.

## Build / deploy / flash commands

Firmware (no hardware needed to build; builds clean locally):

```bash
cd firmware/OpenRB_Axon_Enum_Proof
arduino-cli compile --fqbn OpenRB-150:samd:OpenRB-150 --export-binaries .
# artifacts: build/OpenRB-150.samd.OpenRB-150/OpenRB_Axon_Enum_Proof.ino.{bin,hex}
```

Flash (operator, at the bench; double-tap reset for the bootloader port):

```bash
arduino-cli upload --fqbn OpenRB-150:samd:OpenRB-150 \
  --port <BOOTLOADER_PORT> --input-dir build/OpenRB-150.samd.OpenRB-150
```

Host patch (operator, on a COPY of the device's scifi-server, then deploy it).
This ADDS `0x2F5D` while keeping `0x399A` and `0x2AC1` (the RHD adapter) working:

```bash
# optional: verify the patch logic with no binary at all
python3 scripts/scifi-server-accept-openrb-vid.py --self-test

# on the device / a copy of its scifi-server binary:
cp /opt/.../scifi-server /tmp/scifi-server.copy
python3 scripts/scifi-server-accept-openrb-vid.py --check /tmp/scifi-server.copy   # expect state: orig, cave free
python3 scripts/scifi-server-accept-openrb-vid.py       /tmp/scifi-server.copy     # -> accepts 399A/2AC1/2F5D
# deploy the patched copy in place and restart scifi-server
# revert when done:  python3 scripts/scifi-server-accept-openrb-vid.py --revert /tmp/scifi-server.copy
```

The patch is idempotent (re-running it on an already-patched binary is a no-op)
and `--revert` restores a byte-identical original.

(The agent does not run flashing, `synapsectl`, or device commands. Those are
operator steps.)

## Bench test procedure (concise)

1. Flash the OpenRB with the proof firmware (above). On boot, the LED blinks
   slowly (USB not yet configured by a host).
2. Apply the scifi-server three-VID patch on the SciFi (`--check` first, then
   patch a copy, deploy, restart scifi-server). This keeps `0x2AC1` working, so
   the existing SciNetics/RHD adapter (`2AC1:0004`) is unaffected -- you can
   leave it attached; it should still enumerate and register as before.
3. Connect the OpenRB to the SciFi's peripheral-facing USB port. The LED should
   go to the fast (configured) blink once the host configures the device.
4. Watch scifi-server logs (operator runs `synapsectl logs`). Expected, in order:
   - "Connecting device usb-<bus>-<addr>" / "USBDevice ... initialized
     successfully" (USB accepted + `USBDevice::init` claimed interface 0 and
     found the bulk pair);
   - "Discovering peripherals for device ..." then an enumerate exchange;
   - the OpenRB LED does a quick double-blink each time it answers an ENUMERATE
     (every ~2 s while the discovery worker polls);
   - "... received enumerate response ... [0xf001]" and plugin dispatch
     ("PluginRegistry ... ID 0xf001" / factory construct).
5. `synapsectl -u <dev> info` (operator) should list a peripheral **"Axon Test
   Source"** (vendor "Science Corporation"), i.e. the `0xF001` plugin, now
   backed by the OpenRB.

Success = that peripheral appears in `info` / the enumerated-peripheral UI.

## Known remaining blockers / caveats (before the UI shows it)

1. **On-wire framing beyond scifi-server.** The findings analyzed the
   `scifi-server` host only. The physical peripheral-facing link on the SciFi
   (the USB host controller path into scifi-server) is assumed to deliver the
   device's bulk bytes to `scifi-server`'s libusb unchanged. If the SciFi
   interposes any additional transport/CRC/link-training between the USB bulk
   endpoint and scifi-server's `ThreadedDepacketizer`, the raw-frame assumption
   needs bench confirmation. The CRC stub finding says scifi-server itself does
   not check it; an intermediate layer might.
2. **Enumerate addressing.** `enumerate_peripherals_` sends to a controller
   address and `subscribe`s to it; the firmware echoes the request's addr into
   the response. If the host expects a specific non-zero controller address
   (slot) before it will even emit ENUMERATE to this device, that address may
   need to match what the SciFi assigns this USB port. Confirm the addr seen in
   the first ENUMERATE at the bench (the firmware echoes whatever arrives).
3. **libusb claim vs. CDC.** scifi-server claims interface 0 (Axon). The CDC
   interfaces (1-2) remain; a host OS CDC driver binding them is harmless to
   scifi-server but, if it interferes, build a CDC-less variant (requires a core
   fix, see MISTAKES.md).
4. **Plugin ABI.** Dispatch requires `axon_test_source.so`'s ABI to match the
   running host (`PluginRegistry` skips on ABI mismatch). The installed plugin
   is assumed current; if `info` does not show it after a clean enumerate, check
   scifi-server logs for an ABI-skip line.

None of these are known to be failing -- they are the points to watch if the
peripheral does not appear after a clean enumerate exchange.
