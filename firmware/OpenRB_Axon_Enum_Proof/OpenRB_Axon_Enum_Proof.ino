/*
 * OpenRB_Axon_Enum_Proof.ino
 *
 * BENCH-ONLY minimum proof that an OpenRB-150 can be registered as a
 * Synapse/SciFi peripheral through scifi-server's USB/Axon peripheral bus,
 * WITHOUT any Lattice FPGA and WITHOUT touching the working exoskeleton
 * firmware.
 *
 * Scope (deliberately tiny):
 *   USB attach -> Axon ENUMERATE (type 3) -> reply type 4 advertising 0xF001
 *   -> scifi-server's PeripheralManager dispatches 0xF001 to the already
 *   installed /usr/lib/scifi/plugins/axon_test_source.so factory -> the
 *   peripheral appears as "Axon Test Source" in the enumerated-peripheral UI.
 *
 * NOT implemented here: CONFIGURE / START_STREAM / DATA_FRAME streaming,
 * Dynamixel/exo control, OLED, IMU. This is a registration proof only.
 *
 * USB identity: this sketch keeps the OpenRB-150's real ROBOTIS VID 0x2F5D
 * (from the board definition). scifi-server accepts only VID 0x399A/0x2AC1, so
 * the bench host runs a one-instruction scifi-server patch to also accept
 * 0x2F5D (see docs/axon-usb-enumeration-proof.md). The firmware does NOT spoof
 * Science's VID.
 *
 * INTERFACE-0 REQUIREMENT: scifi-server claims USB interface 0, so the Axon
 * bulk pair must be interface 0. The Arduino-SAMD core assigns PluggableUSB
 * interface numbers in *plug order* (first plugged -> interface 0). The core's
 * CDC `SerialUSB` instance also plugs itself, so to guarantee the Axon module
 * wins interface 0 we construct it with a low init_priority (101), which runs
 * before the default-priority `SerialUSB` global. CDC is left ENABLED (the
 * OpenRB core does not link cleanly with CDC fully disabled), so the device
 * enumerates as a composite: interface 0 = Axon (2 bulk EPs), interfaces 1-2 =
 * CDC (3 EPs). 2 + 3 + control = within the core's 7-endpoint budget, and
 * scifi-server simply ignores the CDC interfaces. Serial-over-USB therefore
 * still works for local debugging if desired.
 */

#include "AxonUsbPeripheral.h"

// init_priority(101) makes this global construct (and thus PluggableUSB-plug)
// before the core's default-priority SerialUSB, guaranteeing interface 0.
// NOTE: must be external linkage (not static) for GCC to honor init_priority
// and emit a prioritized .init_array entry.
axon_proof::AxonUsbPeripheral g_axon __attribute__((init_priority(101)));

// Heartbeat / status on the built-in LED:
//   - slow blink  : USB not yet configured by a host
//   - solid-ish   : USB configured, waiting for / serving ENUMERATE
//   - quick double-blink on each ENUMERATE response sent
#ifndef LED_BUILTIN
#define LED_BUILTIN 13
#endif

static uint32_t last_resp_count = 0;
static uint32_t last_blink_ms = 0;
static bool led_state = false;

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);
  // USBDevice is brought up by the core automatically (USBCON). No Serial.
}

void loop() {
  g_axon.poll();

  const uint32_t now = millis();

  // Signal each new ENUMERATE response with a short double blink.
  if (g_axon.enum_responses() != last_resp_count) {
    last_resp_count = g_axon.enum_responses();
    for (int i = 0; i < 4; ++i) {
      digitalWrite(LED_BUILTIN, (i & 1) ? LOW : HIGH);
      delay(40);
    }
    digitalWrite(LED_BUILTIN, LOW);
    last_blink_ms = now;
    led_state = false;
  }

  // Heartbeat: 1 Hz when not configured, 4 Hz once configured.
  const uint32_t period = USBDevice.configured() ? 125 : 500;
  if (now - last_blink_ms >= period) {
    last_blink_ms = now;
    led_state = !led_state;
    digitalWrite(LED_BUILTIN, led_state ? HIGH : LOW);
  }
}
