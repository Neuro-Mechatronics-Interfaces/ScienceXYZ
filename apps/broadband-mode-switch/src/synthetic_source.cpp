#include "synthetic_source.hpp"

#include <array>

namespace app {
namespace {

// Sine LFP lookup (full wave, 256 entries, amplitude ~100 uV) — transcribed
// verbatim from SINE_LUT in axon_test_source_peripheral.sv.
constexpr std::array<int16_t, 256> kSineLut = {
    0,    2,    5,    7,    10,   12,   15,   17,   20,   22,   24,   27,   29,   31,   34,   36,
    38,   41,   43,   45,   47,   49,   51,   53,   56,   58,   60,   62,   63,   65,   67,   69,
    71,   72,   74,   76,   77,   79,   80,   82,   83,   84,   86,   87,   88,   89,   90,   91,
    92,   93,   94,   95,   96,   96,   97,   98,   98,   99,   99,   99,   100,  100,  100,  100,
    100,  100,  100,  100,  100,  99,   99,   99,   98,   98,   97,   96,   96,   95,   94,   93,
    92,   91,   90,   89,   88,   87,   86,   84,   83,   82,   80,   79,   77,   76,   74,   72,
    71,   69,   67,   65,   63,   62,   60,   58,   56,   53,   51,   49,   47,   45,   43,   41,
    38,   36,   34,   31,   29,   27,   24,   22,   20,   17,   15,   12,   10,   7,    5,    2,
    0,    -2,   -5,   -7,   -10,  -12,  -15,  -17,  -20,  -22,  -24,  -27,  -29,  -31,  -34,  -36,
    -38,  -41,  -43,  -45,  -47,  -49,  -51,  -53,  -56,  -58,  -60,  -62,  -63,  -65,  -67,  -69,
    -71,  -72,  -74,  -76,  -77,  -79,  -80,  -82,  -83,  -84,  -86,  -87,  -88,  -89,  -90,  -91,
    -92,  -93,  -94,  -95,  -96,  -96,  -97,  -98,  -98,  -99,  -99,  -99,  -100, -100, -100, -100,
    -100, -100, -100, -100, -100, -99,  -99,  -99,  -98,  -98,  -97,  -96,  -96,  -95,  -94,  -93,
    -92,  -91,  -90,  -89,  -88,  -87,  -86,  -84,  -83,  -82,  -80,  -79,  -77,  -76,  -74,  -72,
    -71,  -69,  -67,  -65,  -63,  -62,  -60,  -58,  -56,  -53,  -51,  -49,  -47,  -45,  -43,  -41,
    -38,  -36,  -34,  -31,  -29,  -27,  -24,  -22,  -20,  -17,  -15,  -12,  -10,  -7,   -5,   -2};

// Biphasic action-potential template (32 samples), ~200 uV pk-pk — verbatim
// from SPIKE_ROM in the gateware.
constexpr std::array<int16_t, 32> kSpikeRom = {
    0,   -2,  -6,  -20, -48, -90, -130, -144, -122, -74, -24, 13, 35, 47, 54, 56,
    54,  49,  42,  34,  25,  18,  12,   8,    4,    2,   1,   1,  0,  0,  0,  0};

// Per-channel delay offsets (samples), pseudo-random but fixed — verbatim from
// DELAY_LUT in the gateware. Channel index wraps mod 256 (widx[7:0]).
constexpr std::array<uint16_t, 256> kDelayLut = {
    0,   107, 188, 3,   210, 212, 206, 77,  219, 95,  239, 50,  70,  145, 112, 42,
    96,  32,  224, 111, 67,  144, 161, 45,  157, 142, 48,  91,  189, 246, 188, 24,
    136, 251, 189, 106, 150, 130, 43,  38,  53,  185, 20,  49,  215, 88,  83,  7,
    118, 87,  7,   248, 131, 251, 177, 191, 107, 1,   2,   241, 170, 223, 42,  198,
    149, 46,  74,  26,  168, 107, 28,  227, 175, 148, 47,  189, 97,  60,  246, 135,
    153, 182, 234, 212, 85,  207, 11,  60,  31,  224, 128, 56,  189, 206, 194, 143,
    172, 48,  66,  151, 112, 133, 235, 246, 34,  11,  251, 43,  78,  252, 222, 214,
    3,   39,  248, 59,  90,  139, 88,  41,  99,  83,  4,   13,  16,  183, 114, 21,
    100, 255, 205, 236, 46,  202, 3,   198, 183, 95,  56,  174, 132, 195, 32,  124,
    160, 16,  4,   161, 126, 84,  198, 157, 248, 108, 237, 244, 141, 52,  94,  197,
    170, 179, 31,  143, 39,  21,  127, 43,  229, 248, 86,  60,  53,  42,  42,  74,
    39,  138, 24,  134, 179, 1,   38,  1,   169, 127, 227, 48,  214, 156, 240, 205,
    218, 25,  68,  130, 193, 104, 17,  203, 193, 245, 118, 107, 47,  63,  236, 225,
    48,  7,   96,  85,  46,  102, 46,  46,  147, 175, 211, 133, 229, 64,  246, 106,
    132, 178, 107, 84,  31,  18,  165, 118, 191, 50,  161, 160, 147, 52,  192, 35,
    136, 229, 60,  85,  146, 75,  33,  138, 131, 178, 198, 172, 229, 205, 16,  67};

}  // namespace

SyntheticSource::SyntheticSource(std::size_t channel_count, uint16_t lfsr_seed)
    : channel_count_(channel_count), lfsr_(lfsr_seed) {
  delay_buf_.assign(kDepth, 0);
  // Re-seed cleanly so the configured lfsr_seed survives a reset() call.
  seed_ = lfsr_seed;
  reset();
}

void SyntheticSource::reset() {
  phase_acc_ = 0;
  lfsr_ = seed_;
  noise_lfsr_ = kNoiseSeed;
  spike_active_ = false;
  spike_idx_ = 0;
  wptr_ = 0;
  last_waddr_ = 0;
  std::fill(delay_buf_.begin(), delay_buf_.end(), static_cast<int16_t>(0));
}

int16_t SyntheticSource::master_sample() const {
  // lfp_val = SINE_LUT[phase_acc[23:16]]
  const int lfp_val = kSineLut[(phase_acc_ >> 16) & 0xFF];
  const int spike_val = spike_active_ ? kSpikeRom[spike_idx_] : 0;
  // noise_val = signed(noise_lfsr[5:0]) - 32  => range -32..+31
  const int noise_val = static_cast<int>(noise_lfsr_ & 0x3F) - 32;
  int mix = lfp_val + spike_val + noise_val;
  if (mix > 32767) mix = 32767;
  if (mix < -32768) mix = -32768;
  return static_cast<int16_t>(mix);
}

void SyntheticSource::advance_state() {
  // 16-bit maximal-length LFSR: taps 15,13,12,10 (bit 0 is lfsr[0]).
  const uint16_t lfsr_fb = static_cast<uint16_t>(
      ((lfsr_ >> 15) ^ (lfsr_ >> 13) ^ (lfsr_ >> 12) ^ (lfsr_ >> 10)) & 0x1u);
  // 32-bit maximal-length LFSR: taps 31,21,1,0.
  const uint32_t noise_fb =
      ((noise_lfsr_ >> 31) ^ (noise_lfsr_ >> 21) ^ (noise_lfsr_ >> 1) ^ noise_lfsr_) & 0x1u;

  // Spike state machine uses the CURRENT lfsr value (pre-shift), matching the
  // gateware which evaluates `lfsr < SPIKE_THRESH` inside the same always_ff.
  const bool trigger = lfsr_ < kSpikeThresh;

  phase_acc_ = (phase_acc_ + kLfpPhaseInc) & 0xFFFFFF;  // 24-bit accumulator
  lfsr_ = static_cast<uint16_t>((lfsr_ << 1) | lfsr_fb);
  noise_lfsr_ = (noise_lfsr_ << 1) | noise_fb;

  if (spike_active_) {
    if (spike_idx_ == kSpikeN - 1) {
      spike_active_ = false;
    } else {
      ++spike_idx_;
    }
  } else if (trigger) {
    spike_active_ = true;
    spike_idx_ = 0;
  }
}

void SyntheticSource::next_frame(std::vector<int32_t>& out) {
  out.resize(channel_count_);

  // 1. Synthesise the master sample from the current state and write it into
  //    the delay line at wptr (gateware: delay_buf[wptr] <= master_sample).
  const int16_t sample = master_sample();
  delay_buf_[wptr_] = sample;

  // 2. Latch last_waddr and advance the write pointer (wraps at kDepth).
  last_waddr_ = wptr_;
  wptr_ = (wptr_ + 1) & kAddrMask;

  // 3. Each channel reads at (last_waddr - DELAY_LUT[c]) mod DEPTH.
  for (std::size_t c = 0; c < channel_count_; ++c) {
    const uint16_t delay = kDelayLut[c & 0xFF];
    const int raddr = (last_waddr_ - static_cast<int>(delay)) & kAddrMask;
    out[c] = static_cast<int32_t>(delay_buf_[raddr]);
  }

  // 4. Advance the master-signal state for the next sample.
  advance_state();
}

}  // namespace app
