// MEGA2560_GPIO_SYNC.ino
// Sync-edge generator for the RHD2132 probe adapter (GPIO_0 / GPIO_1).
//
// Target board: Arduino MEGA2560 (Rev2/Rev3). ATmega2560 @ 16 MHz.
//
// Purpose
//   Drive two identifiable digital edge streams into the RHD2132 auxiliary
//   digital inputs so host recordings can validate physical-edge timing and
//   cross-device frequency/phase alignment. The device surfaces these as GPIO
//   channels 0 and 1 (BroadbandFrame positions 32/33; see
//   docs/calibration-recording-mvp.md).
//
//   GPIO_1 (D23) = MASTER CLOCK. Toggles every timer interrupt at the fastest
//     rate (2048 Hz). Hardware-timer driven, so edges are cycle-exact and
//     phase-stable. This is the shared reference every device correlates against.
//
//   GPIO_0 (D22) = HOPPING SIGNAL. Toggles at a pseudo-random half-period that
//     is always an integer number of MASTER ticks (1..128), i.e. master/1 ..
//     master/128 = 2048 Hz .. 16 Hz. A new frequency is chosen on every D22
//     edge, guaranteed to differ from the previous one. Because both lines are
//     derived from the SAME timer ISR, D22 edges are locked to master edges
//     with no relative drift, giving a decodable pattern for aligning signals
//     across two or more devices.
//
// Timing / bit-banging
//   All edges are emitted by a single hardware timer compare ISR (Timer3, CTC
//   mode). The pin writes are true bit-bang single-instruction toggles
//   (PINx <- mask). The edge is emitted FIRST in the ISR; the random() hop
//   selection runs AFTER the edge, so the (variable-cost) PRNG never delays or
//   jitters an edge. Interrupts stay ENABLED, so micros()/millis()/Serial keep
//   working and loop() is free for future control code.
//
// !! VOLTAGE WARNING !!
//   The MEGA2560 drives 5 V logic. The RHD2132 auxiliary digital inputs must
//   tolerate the applied level. Adapter input-voltage tolerance is UNCONFIRMED
//   (see TODO.md). Do NOT connect D22/D23 directly to the adapter until a level
//   shifter or divider to the adapter's rail (e.g. 3.3 V) is verified. Bench a
//   scope on D22/D23 first.
//
// Pin mapping (ATmega2560 Port A):
//   D22 = Port A, bit 0 (mask 0x01) -> GPIO_0 (hopping)
//   D23 = Port A, bit 1 (mask 0x02) -> GPIO_1 (master clock)

#include <avr/io.h>         // register/bit definitions (PINA, OCR3A, WGM32, ...)
#include <avr/interrupt.h>  // ISR(), TIMER3_COMPA_vect, sei()/cli()
// Arduino.h (auto-included by the .ino build) provides randomSeed/random/
// analogRead/A8 and the uintN_t typedefs.

// --- Pin masks (Port A) ---
static const uint8_t D22_MASK = 0x01; // GPIO_0, hopping
static const uint8_t D23_MASK = 0x02; // GPIO_1, master clock

// --- Master clock rate ---
// D23 toggles once per interrupt, so its toggle rate == the ISR rate.
// 2048 Hz toggle rate -> ISR period = 1/2048 s.
static const uint32_t MASTER_TOGGLE_HZ = 2048UL;

// Timer3 CTC top for a 16 MHz clock with prescaler /8:
//   f_isr = F_CPU / (prescaler * (OCR3A + 1))
//   OCR3A = F_CPU / (prescaler * f_isr) - 1
//         = 16e6 / (8 * 2048) - 1 = 976.5625 - 1 = ~976  (0.06% error, fine)
static const uint16_t TIMER3_TOP = (uint16_t)(F_CPU / (8UL * MASTER_TOGGLE_HZ) - 1UL);

// --- Hop LUT for D22, expressed in MASTER TICKS (one tick == one D23 toggle) ---
// half-period in ticks -> D22 toggle freq = MASTER_TOGGLE_HZ / ticks.
//   1 -> 2048 Hz, 2 -> 1024, 4 -> 512, 8 -> 256,
//  16 ->  128 Hz, 32 ->  64, 64 ->  32, 128 -> 16 Hz.
static const uint8_t half_period_ticks_lut[8] = { 1, 2, 4, 8, 16, 32, 64, 128 };

// --- ISR-shared state (only touched inside the ISR after init) ---
static volatile uint8_t  d22_lut_index   = 0; // current hop frequency index
static volatile uint16_t d22_tick_count  = 0; // master ticks since last D22 edge

// Timer3 compare-A interrupt: fires at MASTER_TOGGLE_HZ.
ISR(TIMER3_COMPA_vect) {
  // --- MASTER (D23): emit the edge first, unconditionally. ---
  PINA = D23_MASK;

  // --- HOPPING (D22): count master ticks; toggle when the half-period elapses. ---
  if (++d22_tick_count >= half_period_ticks_lut[d22_lut_index]) {
    PINA = D22_MASK;                 // edge FIRST (bit-bang toggle)
    d22_tick_count = 0;

    // Pick the next index AFTER the edge; guaranteed different from current.
    // random() is variable-cost but runs only between edges, never before one.
    d22_lut_index = (uint8_t)((d22_lut_index + (uint8_t)random(1, 8)) % 8);
  }
}

void setup() {
  // Set D22 and D23 as outputs, driven low initially.
  DDRA  |=  (D22_MASK | D23_MASK);
  PORTA &= ~(D22_MASK | D23_MASK);

  // Seed the PRNG for the hop sequence from an unconnected analog pin.
  randomSeed(analogRead(A8));
  d22_lut_index  = (uint8_t)random(0, 8);
  d22_tick_count = 0;

  // --- Configure Timer3 for CTC @ MASTER_TOGGLE_HZ, prescaler /8. ---
  noInterrupts();                 // brief: only while writing timer registers
  TCCR3A = 0;                     // normal port operation, WGM bits set below
  TCCR3B = 0;
  TCNT3  = 0;
  OCR3A  = TIMER3_TOP;
  TCCR3B |= (1 << WGM32);         // CTC mode (TOP = OCR3A)
  TCCR3B |= (1 << CS31);          // prescaler /8
  TIMSK3 |= (1 << OCIE3A);        // enable compare-A interrupt
  interrupts();                   // re-enable globally; ISR now runs on schedule
}

void loop() {
  // Intentionally empty. All edge generation happens in the Timer3 ISR.
  // Free for future serial control (start/stop, seed, rate) without disturbing
  // edge timing.
}
