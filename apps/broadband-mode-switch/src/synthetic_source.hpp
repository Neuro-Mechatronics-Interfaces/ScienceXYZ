#pragma once
#include <cstdint>
#include <vector>

namespace app {

// Software port of the synthetic neural source gateware
// (vendor/axon-peripherals/src/gateware/src/axon_test_source_peripheral.sv).
//
// The gateware synthesises a single "master" sample per sample period:
//   master = saturate(LFP_sine + biphasic_spike + uniform_noise)
// then pushes it into a 512-entry delay line. Each channel c reads the delay
// line at (last_write_addr - DELAY_LUT[c]) mod DEPTH, so every channel sees
// the same waveform lagged by its own fixed pseudo-random offset. Spikes are
// triggered by a 16-bit LFSR crossing a threshold; background noise comes from
// a 32-bit LFSR. The whole model is deterministic from reset.
//
// Amplitudes are in counts, and with the driver's get_lsb == 1.0 path one
// count == 1 uV (action potential ~200 uV pk-pk, LFP ~200 uV pk-pk, noise
// +/-32 uV). This port keeps the same fixed-point integer arithmetic, LUTs,
// LFSR taps, and seeds as the hardware, so the master waveform and per-channel
// lags match the gateware model. It does NOT emulate the gateware's multi-cycle
// AXI-stream TX state machine or its 1-cycle BRAM read latency; those affect
// only sub-sample bus timing, not the per-sample values, so a downstream
// consumer sees the same neural-looking, reproducible stream.
class SyntheticSource {
 public:
  // Gateware synthesis parameters (see the .sv localparams).
  static constexpr int kDepth = 512;               // delay-line depth (power of 2)
  static constexpr int kAddrMask = kDepth - 1;     // wrap mask for the 9-bit ptr
  static constexpr uint32_t kLfpPhaseInc = 8389;   // ~10 Hz LFP @ 20 kHz
  static constexpr uint16_t kSpikeThresh = 64;     // lfsr < THRESH fires a spike
  static constexpr int kSpikeN = 32;               // spike-template length
  static constexpr uint16_t kDefaultLfsrSeed = 0xACE1;
  static constexpr uint32_t kNoiseSeed = 0xCAFEF00D;

  // Construct for `channel_count` channels. `lfsr_seed` seeds the 16-bit
  // spike-trigger PRNG (config `synthetic_seed`); the 32-bit noise PRNG uses
  // the fixed gateware NOISE_SEED so the noise stream matches hardware.
  explicit SyntheticSource(std::size_t channel_count,
                           uint16_t lfsr_seed = kDefaultLfsrSeed);

  // Reset all state to the post-reset gateware condition.
  void reset();

  // Advance the master-signal state by one sample (one gen_tick) and write the
  // new master sample into the delay line, then return one sample per channel
  // (the current DATA_FRAME payload). `out` is resized to channel_count.
  //
  // Ordering matches the hardware: the delay line is written with the sample
  // synthesised from the pre-advance state (delay_buf[wptr] <= master_sample),
  // last_waddr latches the pre-increment wptr, and channels then read at
  // (last_waddr - DELAY_LUT[c]). This yields the same per-channel lags the
  // gateware produces.
  void next_frame(std::vector<int32_t>& out);

  std::size_t channel_count() const { return channel_count_; }

 private:
  // Synthesise the master sample from the current LFP/spike/noise state.
  int16_t master_sample() const;
  // Advance LFP phase, both LFSRs, and the spike state machine by one tick.
  void advance_state();

  std::size_t channel_count_;
  uint16_t seed_ = kDefaultLfsrSeed;  // configured spike-LFSR seed (survives reset)

  // Master-signal state (mirrors the always_ff registers).
  uint32_t phase_acc_ = 0;   // 24-bit phase accumulator (top 8 bits index sine)
  uint16_t lfsr_;            // 16-bit spike-trigger PRNG
  uint32_t noise_lfsr_ = kNoiseSeed;
  bool spike_active_ = false;
  int spike_idx_ = 0;

  // Delay line and write pointers.
  std::vector<int16_t> delay_buf_;  // size kDepth
  int wptr_ = 0;
  int last_waddr_ = 0;
};

}  // namespace app
