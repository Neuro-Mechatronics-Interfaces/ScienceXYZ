# scifi-server USB → Axon peripheral discovery (host-side RE)

All addresses are in the analyzed `.local/scifi-server` (AARCH64 ELF, Ghidra project `science-server.rep`). Functions are named as demangled in the symbol table. This is the `scifi-server` host process that runs *on the SciFi-2 headstage* and talks to the Axon/peripheral USB bus through libusb. It is the counterpart to the gateware/transport side documented in `docs/axon-peripheral-protocol.md`.

## Summary answers

1. Device acceptance is gated **only by USB idVendor**: `0x399a` or `0x2ac1`. No PID, class, endpoint, or descriptor-string check anywhere in the acceptance path.
2. `USBDevice` requires exactly one **BULK OUT** and one **BULK IN** endpoint, discovered on the active config; it then claims **interface 0**.
3. The USB wire frame written by `fill_tx_buffer`/`wrap_axon_packet` is a 24-byte header (`magic 0xC0FFEE00`, address, length, type) + payload + 4-byte CRC trailer; overhead is 28 bytes.
4. Yes — a recurring 28-byte EP2 OUT packet is an Axon control frame with an empty payload (24-byte header + 4-byte CRC). The enumerate request is exactly such a zero-payload frame.
5. To be discovered, the device must answer an enumerate request (Axon type 3) with an enumerate response (type 4) whose header address matches the request and whose payload is a `uint32[]` of peripheral IDs.
6. Yes — a plain USB microcontroller can advertise user peripheral ID `0xF001` with no Lattice FPGA, provided it (a) enumerates with VID `0x399a`/`0x2ac1`, (b) exposes a bulk IN/OUT pair on interface 0, and (c) implements the Axon framing + the enumerate request/response exchange on those endpoints.
7. Confirmed: an enumerated ID that is not a hard-coded built-in is dispatched through `PluginRegistry::find_by_peripheral_id(id)`, which resolves the `.so` registered for that ID (the `SCIFI_REGISTER_PERIPHERAL` path) and calls its factory.

---

## 1. Device acceptance criteria

`usb::USBDeviceManager::discover_devices() @ 0x289680`

```
list = libusb_get_device_list(ctx)
for dev in list:
    if libusb_get_device_descriptor(dev, &desc) == 0
       and (desc.idVendor == 0x399A or desc.idVendor == 0x2AC1):
        connect_device(dev)
libusb_free_device_list(list)
```

Only `idVendor` is tested. `0x399A` is Science Corporation's assigned USB vendor ID; `0x2AC1` is a second accepted vendor ID (bootloader / alternate).

`usb::USBDeviceManager::connect_device(libusb_device*) @ 0x288FC0`
- `generate_device_id()` → `"usb-<bus>-<addr>"`.
- Dedupe against a map keyed by that string.
- `libusb_open`, `libusb_get_device_descriptor`, `libusb_get_port_numbers`.
- `new USBDevice(id, …, vid, pid, …)` (600 bytes), then `USBDevice::init()`.
- No PID/class/endpoint check. PID is stored only as metadata.

`usb::USBDeviceManager::generate_device_id[abi:cxx11](libusb_device*) @ 0x285560`
- `id = "usb-" + to_string(bus_number) + "-" + to_string(device_address)`. Pure topology; carries no identity. The device's identity to the peripheral layer comes later, over Axon.

`usb::USBDevice::USBDevice(...) @ 0x2842A0`
- Stores `vid | (pid<<16)` at `+0x40` as metadata. Creates a ZMQ PUSH (type 2) TX socket and a ZMQ PULL (type 1) RX socket. No PID acceptance logic.

## 2. Endpoints

`usb::USBDevice::discover_endpoints() @ 0x286244`

```
cfg = libusb_get_active_config_descriptor(dev)
for interface in cfg.interface[0 .. bNumInterfaces-1]:
    for alt in interface.altsetting[0 .. num_altsetting-1]:
        for ep in alt.endpoint[0 .. bNumEndpoints-1]:
            if (ep.bmAttributes & 0x3) != 2:   # 2 = BULK
                continue
            if ep.bEndpointAddress & 0x80:     # IN
                bulk_in.push_back(ep.bEndpointAddress & 0x0F)
            else:                              # OUT
                bulk_out.push_back(ep.bEndpointAddress & 0x0F)
if bulk_out.empty() or bulk_in.empty():
    return FAIL
```

- Only **BULK** endpoints are collected; interrupt/iso/control endpoints are ignored.
- At least one BULK OUT and one BULK IN are mandatory, else init fails.
- Endpoint *numbers* (low nibble) are stored; the first BULK OUT is used for writes (see §3). Observed hardware uses EP 0x02 OUT, which is the default OUT when present.

`usb::USBDevice::init() @ 0x286704`
1. `discover_endpoints()` (must succeed).
2. `libusb_set_auto_detach_kernel_driver(handle, 1)`.
3. `libusb_claim_interface(handle, 0)` — **interface 0 is hard-coded**. The bulk pair must live on interface 0.
4. Bind internal ZMQ TX/RX sockets; create a `ThreadedDepacketizer` that reparses the inbound Axon byte stream.
5. Set connected flag; submit `0x30` (48) read transfers (the BULK IN read ring).

## 3. TX framing

`usb::USBDevice::process_outgoing_messages() @ 0x284D24`
```
if not connected: return
buf = new uchar[0x1000]           # 4096-byte USB transfer buffer
n = fill_tx_buffer(buf, 0x1000)
if n != 0: submit_write_transfer(buf, n)
else:      delete[] buf
```

`usb::USBDevice::fill_tx_buffer(uchar* out, int cap) @ 0x284A60`
- Polls an internal ZMQ socket (`+0x70`) for queued outbound messages.
- Each queued ZMQ message is laid out as `[u16 addr @0][pad @2][u16 type @4][pad @6][8-byte meta header][payload...]`. `fill_tx_buffer` reads `addr = msg[0]` (u16) and `type = msg[4]` (u16), takes `payload = msg+8`, `payload_len = msg_size-8`, and calls `wrap_axon_packet`.
- Wrapped frames are concatenated into `out` until the queue drains or `out` is full.

`axon::wrap_axon_packet(u16 addr, u16 type, const u32* payload, u16 len, u32* out) @ 0x3FD840`
- Validates `payload != null` and `len <= 0xFE4` (4068 bytes max payload).
- Emits the frame (little-endian), then returns `len + 0x1C`.

```
out[0]        = 0xC0FFEE00          # magic, little-endian bytes: 00 EE FF C0
out[1]        = 0x00000000          # reserved / sequence (always 0 here)
out[2 (off 8)]= addr  (u16) + 0x0000 pad  # routing address -> low 16 bits of word 2
off 0x0C      = 0x00000000          # reserved
off 0x10      = 0x00000000          # reserved
off 0x14      = len   (u16)         # payload length in bytes
off 0x16      = type  (u16)         # message/type word
off 0x18      = payload[0 .. len-1] # memcpy
off 0x18+len  = crc32               # compute_crc32(out, 0x18+len)
```

- Total frame size = `0x1C + len` (28-byte overhead: 24-byte header + 4-byte CRC).
- `axon::compute_crc32 @ 0x3FCD20` in this build is a **stub**: `mov w0, #0x20; ret` — it always returns `0x00000020` and never reads the buffer. So in this host build the CRC trailer is the constant `0x00000020` and is not a real CRC. A custom peripheral can therefore write `0x00000020` in the trailer to match host-originated frames. Caveat: the gateware/transport on the real link may still validate a CRC on the wire; that is outside this binary and untested.

`usb::USBDevice::submit_write_transfer(uchar* buf, int len) @ 0x284864`
- `libusb_alloc_transfer(0)`, type = `LIBUSB_TRANSFER_TYPE_BULK (2)`, endpoint = first BULK OUT (default `0x02` if the vector is empty), timeout 2000 ms, callback `write_callback`.
- Submits via `USBDeviceManager::submit_transfer`.

## 4. The 28-byte EP2 OUT packets

A 28-byte frame is exactly `wrap_axon_packet` overhead with `len == 0` (`0x1C + 0 = 28`): a 24-byte header and a 4-byte trailer, no payload. Every Axon command that carries no payload — including the enumerate request — is a 28-byte EP2 OUT frame. So the recurring 28-byte EP2 OUT packets observed on the SciNetics RHD adapter are Axon control/management frames (the discovery worker re-issues the enumerate request every ~2 seconds; see §5). The payload-bearing frames (configure, start stream) are longer.

## 5. Response required for discovery

`axon::PeripheralManager::peripheral_discovery_worker_() @ 0x3F7A90`
- Loop, every 2 s (or on trigger): `enumerate_peripherals_(controller_addr)`. On success, copy the returned ID buffer and call `parse_peripherals_`; on failure, `clear_peripherals_`.

`axon::PeripheralManager::enumerate_peripherals_(u16 addr) @ 0x3F4870`
```
sub = zmq_socket(ctx, ZMQ_SUB)            # type 2
subscribe_to_axon_messages(sub, addr)     # ZMQ_SUBSCRIBE prefix = 4-byte addr
zmq_setsockopt(sub, ZMQ_RCVTIMEO, DISCOVERY_TIMEOUT_MS=2000)
zmq_connect(sub, device_rx_endpoint)
send_axon_packet(tx, addr, 3 /*ENUMERATE*/, empty)      # -> 28-byte EP2 OUT frame
retries = 3
loop:
    msg = zmq_msg_recv(sub)               # waits up to 2000 ms
    if timeout: resend enumerate; if --retries == 0: break; else continue
    break
if msg empty: clear_peripherals(); return status=0
# validate
if msg.size < 0x0C: "invalid enumerate response size"; return 0
if msg[u32 @4] != 4: "unexpected message type"; return 0   # 4 = ENUMERATE response
if (msg[u32 @0] & 0xFFFF) != addr: "address mismatch"      # warn only
if msg.size < 0x0D: "no payload"; return 0
ids = (u32*)(msg.data + 0x0C)             # header = 3 u32 words (0x0C bytes)
count = (msg.size>>2) - 3
return status=1, buffer=ids[0..count-1]
```

Discovery therefore requires the peripheral/controller to:
- accept the enumerate command (Axon type `3`), and
- publish an enumerate response (Axon type `4`) whose 12-byte header is `[u32 addr][u32 type=4][u32 reserved]` followed by a little-endian `uint32` array of peripheral IDs.

`axon::subscribe_to_axon_messages(socket, u32 addr, optional<u32>) @ 0x3FDE00`
- `zmq_setsockopt(sock, ZMQ_SUBSCRIBE(6), &addr, 4)` (or 8 with the optional second word). The Axon message bus is a ZMQ PUB/SUB keyed by a 4-byte address prefix; the controller address selects the stream.

`DISCOVERY_TIMEOUT_MS @ 0x483724 = 0x7D0 = 2000` ms.

## 6. Custom microcontroller without FPGA

The host USB-acceptance path imposes no FPGA-specific requirement. A bare MCU (STM32/nRF52/RP2040/etc.) can be discovered as a Synapse peripheral if it:

1. Enumerates over USB with `idVendor == 0x399A` (or `0x2AC1`). PID is unchecked.
2. Exposes, on **interface 0**, at least one BULK OUT and one BULK IN endpoint (the RHD adapter presents EP 0x02 OUT and a matching BULK IN).
3. Implements the Axon framing on those bulk endpoints:
   - Parse inbound frames (magic `0xC0FFEE00`, read `type @0x16`, `addr @0x08`, `len @0x14`).
   - On an enumerate command (type `3`) addressed to it, emit an enumerate response (type `4`) carrying its peripheral ID list, e.g. `[0xF001]`.
4. Advertises the user peripheral ID `0xF001` (user window `0xF001..0xFFFE`; `0x0000..0xF000` reserved; `0xFFFF` broadcast — consistent with `docs/axon-peripheral-protocol.md` §1).

This resolves, from the host side, open question #3 of `docs/axon-peripheral-protocol.md` ("is there any supported path for a peripheral not on the internal Lattice fabric to appear as a Synapse peripheral?"): `scifi-server` discovers peripherals purely by the USB + Axon exchange above, with no dependency on the gateware. The FPGA/transport in the reference design is the mechanism Science ships to *produce* that exchange on the probe's physical uplink; it is not a precondition the host enforces.

Two caveats remain, both outside this binary:
- The on-wire CRC: the host's `compute_crc32` is a stub (`0x20`), but the physical Axon link hardware between the MCU and `scifi-server` may apply its own framing/CRC. Untested.
- After discovery, `BroadbandSourceConfig`/configure/start-stream command frames (Axon payload-bearing types) must also be honored for the peripheral to actually stream; this analysis covers discovery, not the full streaming contract.

## 7. PluginRegistry dispatch (SCIFI_REGISTER_PERIPHERAL)

`axon::PeripheralManager::parse_peripherals_(vector<u32> ids, u16, u16) @ 0x3F5714`
- Iterates the ID list. Each ID's fabric address is `(index+1) | base`. Built-in IDs are dispatched to hard-coded classes:

| Peripheral ID | Built-in class |
|---|---|
| `0x6004` | `AxonTestPeripheral` (Axon source) |
| `0x9000` | `IntanRhd2132Peripheral` |
| `0x8000` | `AxonTerminalPeripheral` |
| `0x4001` | `Pixel16k` |
| `0x5002`, `0x5004` | Nixel-class handling |
| `0xF000` | sub-axon hub: recurse (`base += 0x100`) and enumerate sub-peripherals |

- **Default (non-built-in ID):**
```
if plugin_registry != null:
    factory = PluginRegistry::find_by_peripheral_id(id)
    if factory != null:
        p = factory(...)     # "plugin '{}' factory returned nullptr for ID 0x{:x}" on failure
        insert_if_absent(id, p)
```

`axon::PluginRegistry::find_by_peripheral_id(u32 id) const @ 0x27ADB0`
- `rdlock`; ordered-map / lower-bound lookup over registered claims keyed by peripheral ID; returns the stored factory pointer (`+0x28`) when the ID matches.

Plugin loader (from strings and `PluginRegistry::load_dir @ 0x171044`, `load_file_locked_`):
- Scans `/usr/lib/scifi/plugins`, `dlopen`s each `.so`.
- Checks the plugin's ABI version against the host ABI (`"ABI version {} does not match host ABI {}; skipping"`).
- Requires a non-null `make_record` factory (`"make_record is null; skipping"`).
- Registers `peripheral_id` → factory, rejecting duplicates (`"peripheral_id 0x{:x} already claimed by {}; skipping"`).
- Logs `"loaded {} ({} v{}, ABI v{}, ID 0x{:x})"`.

So ID `0xF001`, being outside the built-in set and outside the `0xF000` hub case, falls straight to `find_by_peripheral_id(0xF001)`. A plugin that registered ID `0xF001` via `SCIFI_REGISTER_PERIPHERAL` (installed to `/usr/lib/scifi/plugins` with a matching host ABI) is dispatched and its factory constructs the peripheral object. Discovery on the wire and plugin dispatch on the host are independent; both must be satisfied.

---

## Minimum USB/Axon handshake — wire diagrams

### Axon frame on the bulk endpoints (produced by `wrap_axon_packet`)

```
 byte   0    1    2    3    4    5    6    7
      +----+----+----+----+----+----+----+----+
 0x00 | 00   EE   FF   C0 | 00   00   00   00 |   magic=0xC0FFEE00 | reserved
      +----+----+----+----+----+----+----+----+
 0x08 | addr_lo addr_hi 00 00 | 00   00   00   00 |  addr (u16) @0x08 | reserved
      +----+----+----+----+----+----+----+----+
 0x10 | 00   00   00   00 | len_lo len_hi typ_lo typ_hi |  len (u16) @0x14 | type (u16) @0x16
      +----+----+----+----+----+----+----+----+
 0x18 | payload[0..len-1] ...                           |
      +-------------------------------------------------+
 0x18+len | crc_lo crc_hi crc_2 crc_3 |   crc32 (host stub => 0x00000020)
          +----+----+----+----+
 total = 0x1C + len bytes   (len==0 => 28-byte control frame)
```

### Discovery exchange (host = scifi-server, dev = custom MCU)

```
host                                   dev (peripheral/controller at addr A)
 |  enumerate request                   |
 |  EP OUT, 28 bytes:                   |
 |  magic | addr=A | len=0 | type=3     |
 |------------------------------------->|
 |                                      |  (build ID list, e.g. [0xF001])
 |  enumerate response (EP IN)          |
 |  [u32 addr=A][u32 type=4][u32 rsvd]  |
 |  [u32 0xF001] ...                    |
 |<-------------------------------------|
 |  parse_peripherals_: 0xF001 ->       |
 |  PluginRegistry::find_by_peripheral_id(0xF001)
 |  -> factory() -> peripheral object   |
 | (retried every 2 s; 2000 ms timeout) |
```

### Minimum device-side pseudocode

```c
/* USB descriptors */
idVendor      = 0x399A;          /* or 0x2AC1 */
idProduct     = <any>;
/* Interface 0, one bulk pair */
EP_OUT = 0x02;  /* bulk, host->dev */
EP_IN  = 0x81;  /* bulk, dev->host (number arbitrary; must be bulk + IN bit) */

const uint32_t PERIPHERAL_IDS[] = { 0xF001 };
const uint16_t MY_ADDR = /* controller address the host subscribes to */;

void on_bulk_out(const uint8_t *buf, int n) {
    if (n < 0x18) return;
    uint32_t magic = rd_le32(buf + 0x00);
    if (magic != 0xC0FFEE00) return;
    uint16_t addr = rd_le16(buf + 0x08);
    uint16_t len  = rd_le16(buf + 0x14);
    uint16_t type = rd_le16(buf + 0x16);
    /* optional: verify/ignore crc at buf[0x18+len] */
    if (type == 3 /* ENUMERATE */) {
        send_enumerate_response(addr);
    }
    /* later: type == CONFIGURE / START_STREAM (payload-bearing) */
}

void send_enumerate_response(uint16_t addr) {
    uint8_t out[0x0C + sizeof(PERIPHERAL_IDS)];
    wr_le32(out + 0x00, addr);       /* header word 0: addr */
    wr_le32(out + 0x04, 4);          /* header word 1: type = ENUMERATE response */
    wr_le32(out + 0x08, 0);          /* header word 2: reserved */
    memcpy(out + 0x0C, PERIPHERAL_IDS, sizeof(PERIPHERAL_IDS));
    bulk_in_send(EP_IN, out, sizeof(out));
}
```

Note: the enumerate *response* the host parses (above) is the inner Axon message payload — the 12-byte header plus the ID array — as delivered to `enumerate_peripherals_` after the link's depacketizer has stripped the outer `0xC0FFEE00` framing. On the physical wire the response is itself carried in a `0xC0FFEE00`-framed frame (type 4) the same way requests are; the host's `ThreadedDepacketizer` unwraps it before `enumerate_peripherals_` sees it.
