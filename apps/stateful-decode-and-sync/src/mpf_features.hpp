#pragma once
#include <complex>
#include <cstddef>
#include <vector>

namespace app {

// Multivariate power-frequency (MPF) featurizer, following Kaifosh et al. 2025
// (Nature, CTRL-labs / Reality Labs; reference implementation
// facebookresearch/generic-neuromotor-interface). This is the *multivariate*
// MPF operator — channel-wise STFT, cross-spectral density, frequency-band
// averaging, and an SPD/Hermitian matrix logarithm — NOT the scalar mean/median
// power-frequency of EMG-fatigue literature that shares the acronym.
//
// Pipeline for one time window of shape [C channels x N samples]:
//   1. Per channel c: STFT with a Hann window (length `stft_size`, hop
//      `stft_hop`) -> complex X_c[f, t].
//   2. Per configured half-open frequency band b (or the legacy even split of
//      the stft_size/2+1 one-sided bins): cross-spectral density
//        S_b = mean_{f in b, t} x[:,f,t] * x[:,f,t]^H          (C x C Hermitian)
//   3. Regularise S_b <- S_b + eps*I, then take the Hermitian matrix logarithm
//        L_b = U diag(log lambda) U^H
//      via a cyclic-Jacobi Hermitian eigensolver.
//   4. Feature = concat over bands of the diagonal and the first
//      `num_off_diag_bands` upper off-diagonal bands of L_b. Diagonal entries
//      are real; off-diagonal entries are flattened as [real, imag].
//      `num_off_diag_bands` is a matrix offset count: 1 retains (i,i+1), 2
//      additionally retains (i,i+2), and values above C-1 are clamped.
//
// Output dimension is reported by feature_dim() so callers can size the MLP
// input layer before any data arrives.
class MpfFeaturizer {
 public:
  struct FrequencyBand {
    double low_hz = 0.0;
    double high_hz = 0.0;
  };

  struct Config {
    std::size_t num_channels = 32;  // C: featurised channel count
    double sample_rate_hz = 20000.0;
    std::size_t stft_size = 256;    // STFT length in samples (need not be pow2)
    std::size_t stft_hop = 128;     // STFT hop within the window
    // Explicit half-open frequency bands [low_hz, high_hz). Empty retains the
    // legacy num_bands-way split over the complete one-sided spectrum.
    std::vector<FrequencyBand> frequency_bands_hz;
    std::size_t num_bands = 8;
    std::size_t num_off_diag_bands = 2;  // retained upper matrix offsets
    float eps = 1e-3f;              // diagonal regularisation before logm
  };

  explicit MpfFeaturizer(const Config& cfg);

  // Number of real feature values produced per window.
  std::size_t feature_dim() const { return feature_dim_; }
  const Config& config() const { return cfg_; }

  // Compute the MPF feature vector for one window.
  //
  // `window` is row-major [C][N]: window[c] is the length-N sample series for
  // channel c (already the desired channel subset, in µV or counts). N must be
  // normally spans at least one sample. Windows shorter than stft_size are
  // zero-padded to one transform without changing their temporal duration.
  // The result has length feature_dim().
  std::vector<float> compute(const std::vector<std::vector<float>>& window) const;

 private:
  using Complex = std::complex<double>;

  // Hermitian eigendecomposition A = V diag(w) V^H via cyclic Jacobi rotations.
  // A is C*C row-major and is overwritten. `evals` (size C, ascending not
  // required) and `evecs` (C*C row-major columns) receive the result.
  static void hermitian_eig(std::vector<Complex>& A, std::size_t n,
                            std::vector<double>& evals, std::vector<Complex>& evecs);

  // In-place radix-2 FFT used by the normal power-of-two STFT path. A
  // precomputed direct DFT retains support for other configured sizes.
  void fft_in_place(std::vector<Complex>& values) const;

  Config cfg_;
  std::vector<float> hann_;         // precomputed Hann window (stft_size)
  std::size_t num_bins_ = 0;        // stft_size/2 + 1 one-sided bins
  bool radix2_fft_ = false;
  std::vector<std::size_t> bit_reversal_;
  std::vector<Complex> fft_roots_;
  std::vector<Complex> dft_twiddles_;
  std::vector<std::size_t> band_lo_;  // inclusive bin start per band
  std::vector<std::size_t> band_hi_;  // exclusive bin end per band
  std::size_t feature_dim_ = 0;
};

}  // namespace app
