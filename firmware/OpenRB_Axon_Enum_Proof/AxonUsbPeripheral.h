// AxonUsbPeripheral.h
//
// Bench-only Arduino-SAMD PluggableUSB module that makes an OpenRB-150 appear
// to `scifi-server` as an Axon peripheral-bus device for the MINIMUM
// registration proof only:
//
//     USB attach -> Axon ENUMERATE (type 3) -> reply type 4 advertising 0xF001
//
// It exposes exactly one vendor-specific USB interface with one BULK OUT and
// one BULK IN endpoint. The sketch constructs it with init_priority(101),
// before the core's SerialUSB, while leaving CDC enabled. It therefore
// lands on USB interface 0 -- which is the interface `scifi-server`'s
// USBDevice::init() claims (libusb_claim_interface(handle, 0)).
//
// Wire contract (established by reverse-engineering scifi-server; see
// docs/axon-usb-enumeration-proof.md and the RE findings it cites):
//   - host accepts the USB device only if idVendor == 0x399A or 0x2AC1. The
//     OpenRB keeps its real ROBOTIS VID 0x2F5D; the bench host runs the
//     three-VID scifi-server patch to also accept 0x2F5D (see the doc). This firmware
//     does NOT spoof a VID.
//   - Axon frames are 32-bit-word, little-endian, framed as:
//       word0 = 0xC0FFEE00 (magic)
//       word1 = reserved/seq (0)
//       word2 = addr (low 16 bits) | 0 (high 16)
//       word3 = reserved (0)
//       word4 = reserved (0)
//       word5 = (type << 16) | (payload_len_bytes)   [len @0x14, type @0x16]
//       word6.. = payload bytes (little-endian packed)
//       trailer u32 = CRC32. The host's own compute_crc32 is a stub returning
//       0x00000020 and it does NOT validate the trailer on receive (the
//       ThreadedDepacketizer slices purely by length), so we emit 0x00000020.
//   - ENUMERATE request  : type = 3, no payload.
//   - ENUMERATE response : type = 4, payload = one u32 peripheral id (0xF001).
//     The device must send the OUTER 0xC0FFEE00-framed form; the host's
//     ThreadedDepacketizer unwraps it into the inner [addr][type][reserved][id]
//     representation that PeripheralManager::parse_peripherals_ consumes.
//
// This file implements ONLY discovery/registration. It does not implement
// CONFIGURE / START_STREAM / DATA_FRAME or any exoskeleton behavior.

#pragma once

#include <Arduino.h>
#include <stdint.h>

#include "USB/USBAPI.h"
#include "USB/USBCore.h"
#include "api/PluggableUSB.h"

namespace axon_proof {

// Axon constants (subset needed for the enumerate handshake).
static constexpr uint32_t AXON_MAGIC        = 0xC0FFEE00u;
static constexpr uint16_t AXON_TYPE_ENUM_REQ  = 3;   // host -> device
static constexpr uint16_t AXON_TYPE_ENUM_RESP = 4;   // device -> host
static constexpr uint32_t CRC_STUB_VALUE      = 0x00000020u;  // host stub value
static constexpr uint32_t PERIPHERAL_ID       = 0xF001u;      // -> axon_test_source.so

// Header layout (byte offsets) inside an Axon frame.
static constexpr uint8_t OFF_MAGIC  = 0x00;
static constexpr uint8_t OFF_ADDR   = 0x08;  // u16 (+ high u16 unused)
static constexpr uint8_t OFF_LENTYP = 0x14;  // (type<<16)|len_bytes
static constexpr uint8_t OFF_PAYLOAD = 0x18; // header is 0x18 (24) bytes
static constexpr uint8_t AXON_HEADER_BYTES = 0x18;
static constexpr uint8_t AXON_TRAILER_BYTES = 4;

// A PluggableUSB module presenting one vendor-specific interface with a
// BULK OUT + BULK IN endpoint pair. 2 endpoints, 1 interface.
class AxonUsbPeripheral : public arduino::PluggableUSBModule {
 public:
  AxonUsbPeripheral();

  // Call from loop(): drains the BULK OUT endpoint, parses any complete Axon
  // frame, and answers an ENUMERATE request. Returns true if it sent a
  // response this call (useful for a status LED / log).
  bool poll();

  // Diagnostics (read by the sketch for the OLED/serial-less status LED).
  uint32_t frames_seen() const { return frames_seen_; }
  uint32_t enum_requests() const { return enum_requests_; }
  uint32_t enum_responses() const { return enum_responses_; }

  // Which USB interface / endpoints the core assigned at plug time. For the
  // proof to work, assigned_interface() MUST be 0 (the interface scifi-server
  // claims). Exposed so a debug build with CDC can print/verify it.
  uint8_t assigned_interface() const { return pluggedInterface; }
  uint8_t assigned_ep_out() const { return pluggedEndpoint; }
  uint8_t assigned_ep_in() const { return pluggedEndpoint + 1; }

 protected:
  // PluggableUSBModule contract.
  bool setup(arduino::USBSetup& setup) override;
  int getInterface(uint8_t* interfaceCount) override;
  int getDescriptor(arduino::USBSetup& setup) override;
  uint8_t getShortName(char* name) override;

 private:
  void send_enumerate_response(uint16_t addr);

  // Endpoint index helpers (relative to the core-assigned pluggedEndpoint).
  uint8_t epOut() const { return pluggedEndpoint;     }  // BULK OUT
  uint8_t epIn()  const { return pluggedEndpoint + 1; }  // BULK IN

  unsigned int epType_[2];

  // Reassembly buffer for one inbound frame (header + a small payload). The
  // enumerate request is header-only (28 bytes); keep margin anyway.
  static constexpr size_t RX_BUF = 64;
  uint8_t rx_[RX_BUF];
  size_t rx_len_ = 0;

  uint32_t frames_seen_ = 0;
  uint32_t enum_requests_ = 0;
  uint32_t enum_responses_ = 0;
};

}  // namespace axon_proof
