// AxonUsbPeripheral.cpp -- see AxonUsbPeripheral.h for the full wire contract.

#include "AxonUsbPeripheral.h"

#include <string.h>

namespace axon_proof {

// Vendor-specific interface class (no subclass/protocol). scifi-server filters
// only on VID and on BULK IN/OUT presence, not on interface class, so 0xFF is
// the least-constrained honest choice.
static constexpr uint8_t USB_CLASS_VENDOR = 0xFF;

// One vendor interface descriptor + two bulk endpoint descriptors.
typedef struct {
  InterfaceDescriptor iface;
  EndpointDescriptor  out;
  EndpointDescriptor  in;
} AxonDescriptor;

AxonUsbPeripheral::AxonUsbPeripheral()
    : PluggableUSBModule(2 /*numEndpoints*/, 1 /*numInterfaces*/, epType_) {
  // Endpoint types, mirroring CDC.cpp's pattern: direction baked in here, the
  // concrete endpoint address is filled by the core at plug time.
  epType_[0] = USB_ENDPOINT_TYPE_BULK | USB_ENDPOINT_OUT(0);  // -> epOut()
  epType_[1] = USB_ENDPOINT_TYPE_BULK | USB_ENDPOINT_IN(0);   // -> epIn()
  PluggableUSB().plug(this);
}

int AxonUsbPeripheral::getInterface(uint8_t* interfaceCount) {
  *interfaceCount += 1;  // one interface consumed
  AxonDescriptor desc = {
      D_INTERFACE(pluggedInterface, 2, USB_CLASS_VENDOR, 0x00, 0x00),
      D_ENDPOINT(USB_ENDPOINT_OUT(epOut()), USB_ENDPOINT_TYPE_BULK, EPX_SIZE, 0),
      D_ENDPOINT(USB_ENDPOINT_IN(epIn()),   USB_ENDPOINT_TYPE_BULK, EPX_SIZE, 0),
  };
  return USBDevice.sendControl(&desc, sizeof(desc));
}

int AxonUsbPeripheral::getDescriptor(arduino::USBSetup& /*setup*/) {
  // No class-specific descriptors (no HID report, etc.).
  return 0;
}

bool AxonUsbPeripheral::setup(arduino::USBSetup& /*setup*/) {
  // No class/vendor control requests are needed for the enumerate proof.
  // Returning false lets the core STALL unsupported requests, which is fine.
  return false;
}

uint8_t AxonUsbPeripheral::getShortName(char* name) {
  memcpy(name, "AXON", 4);
  return 4;
}

bool AxonUsbPeripheral::poll() {
  // Pull whatever is available on BULK OUT into the reassembly buffer.
  uint32_t avail = USBDevice.available(epOut());
  if (avail > 0) {
    if (rx_len_ < RX_BUF) {
      size_t room = RX_BUF - rx_len_;
      uint32_t want = avail < room ? avail : room;
      uint32_t got = USBDevice.recv(epOut(), rx_ + rx_len_, want);
      rx_len_ += got;
    } else {
      // Overflow guard: drop and resync on the next magic.
      uint8_t sink[EPX_SIZE];
      USBDevice.recv(epOut(), sink, sizeof(sink));
      rx_len_ = 0;
    }
  }

  // Need at least a full header to classify the frame.
  if (rx_len_ < AXON_HEADER_BYTES) {
    return false;
  }

  // Validate the outer magic; if it is not aligned at byte 0, resync by
  // scanning for the next magic boundary.
  uint32_t magic;
  memcpy(&magic, rx_ + OFF_MAGIC, 4);
  if (magic != AXON_MAGIC) {
    // Shift out one byte and retry on the next poll (simple resync). For the
    // proof the host always sends frame-aligned data, so this is belt-and-braces.
    memmove(rx_, rx_ + 1, rx_len_ - 1);
    rx_len_ -= 1;
    return false;
  }

  uint32_t lentyp;
  memcpy(&lentyp, rx_ + OFF_LENTYP, 4);
  uint16_t payload_len = (uint16_t)(lentyp & 0xFFFF);
  uint16_t type        = (uint16_t)(lentyp >> 16);

  size_t frame_total = (size_t)AXON_HEADER_BYTES + payload_len + AXON_TRAILER_BYTES;
  if (frame_total > RX_BUF) {
    // Payload larger than we buffer for the proof: we only expect header-only
    // control frames. Drop and resync.
    rx_len_ = 0;
    return false;
  }
  if (rx_len_ < frame_total) {
    return false;  // wait for the rest of the frame
  }

  uint16_t addr;
  memcpy(&addr, rx_ + OFF_ADDR, 2);
  frames_seen_++;

  bool answered = false;
  if (type == AXON_TYPE_ENUM_REQ) {
    enum_requests_++;
    send_enumerate_response(addr);
    answered = true;
  }
  // (Other types -- CONFIGURE/START_STREAM -- are out of scope for the proof.)

  // Consume this frame from the buffer.
  size_t remaining = rx_len_ - frame_total;
  if (remaining > 0) {
    memmove(rx_, rx_ + frame_total, remaining);
  }
  rx_len_ = remaining;
  return answered;
}

void AxonUsbPeripheral::send_enumerate_response(uint16_t addr) {
  // Build the OUTER 0xC0FFEE00-framed type-4 response with a single u32
  // peripheral-id payload (0xF001). The host's ThreadedDepacketizer unwraps
  // this into [addr][type=4][reserved][0xF001].
  uint8_t frame[AXON_HEADER_BYTES + 4 /*payload*/ + AXON_TRAILER_BYTES];
  memset(frame, 0, sizeof(frame));

  uint32_t magic = AXON_MAGIC;
  memcpy(frame + OFF_MAGIC, &magic, 4);           // word0

  // word1 reserved (0), already zeroed.

  uint32_t addr_word = (uint32_t)addr;            // word2: addr in low 16 bits
  memcpy(frame + OFF_ADDR, &addr_word, 4);

  // word3 reserved (0), already zeroed.

  uint32_t lentyp = ((uint32_t)AXON_TYPE_ENUM_RESP << 16) | 0x0004u;  // len=4 bytes
  memcpy(frame + OFF_LENTYP, &lentyp, 4);         // word4: (type<<16)|len

  uint32_t id = PERIPHERAL_ID;                    // payload word
  memcpy(frame + OFF_PAYLOAD, &id, 4);

  uint32_t crc = CRC_STUB_VALUE;                  // trailer (host stub value)
  memcpy(frame + OFF_PAYLOAD + 4, &crc, 4);

  USBDevice.send(epIn(), frame, sizeof(frame));
  enum_responses_++;
}

}  // namespace axon_proof
