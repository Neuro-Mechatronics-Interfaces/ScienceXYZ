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
//   2. Per contiguous frequency band b (the stft_size/2+1 one-sided bins split
//      into `num_bands` bands): cross-spectral density
//        S_b = mean_{f in b, t} x[:,f,t] * x[:,f,t]^H          (C x C Hermitian)
//   3. Regularise S_b <- S_b + eps*I, then take the Hermitian matrix logarithm
//        L_b = U diag(log lambda) U^H
//      via a cyclic-Jacobi Hermitian eigensolver.
//   4. Feature = concat over bands of the upper triangle (incl. diagonal) of
//      L_b, flattened as [real, imag] for off-diagonal entries and [real] for
//      diagonal entries (the diagonal of a Hermitian log is real). Length is
//      num_bands * (C^2) real values: C diagonal reals + C(C-1)/2 complex
//      off-diagonals * 2.
//
// Output dimension is reported by feature_dim() so callers can size the MLP
// input layer before any data arrives.
class MpfFeaturizer {
 public:
  struct Config {
    std::size_t num_channels = 32;  // C: featurised channel count
    std::size_t stft_size = 256;    // STFT length in samples (need not be pow2)
    std::size_t stft_hop = 128;     // STFT hop within the window
    std::size_t num_bands = 8;      // B: contiguous frequency bands
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
  // >= stft_size; frames shorter than one STFT block yield a zero vector. The
  // result has length feature_dim().
  std::vector<float> compute(const std::vector<std::vector<float>>& window) const;

 private:
  using Complex = std::complex<double>;

  // Hermitian eigendecomposition A = V diag(w) V^H via cyclic Jacobi rotations.
  // A is C*C row-major and is overwritten. `evals` (size C, ascending not
  // required) and `evecs` (C*C row-major columns) receive the result.
  static void hermitian_eig(std::vector<Complex>& A, std::size_t n,
                            std::vector<double>& evals, std::vector<Complex>& evecs);

  Config cfg_;
  std::vector<float> hann_;         // precomputed Hann window (stft_size)
  std::size_t num_bins_ = 0;        // stft_size/2 + 1 one-sided bins
  std::vector<std::size_t> band_lo_;  // inclusive bin start per band
  std::vector<std::size_t> band_hi_;  // exclusive bin end per band
  std::size_t feature_dim_ = 0;
};

}  // namespace app
