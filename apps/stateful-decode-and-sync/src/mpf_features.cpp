#include "mpf_features.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace app {
namespace {
constexpr double kPi = 3.14159265358979323846;
}  // namespace

MpfFeaturizer::MpfFeaturizer(const Config& cfg) : cfg_(cfg) {
  const std::size_t C = cfg_.num_channels;
  const std::size_t nfft = cfg_.stft_size;

  // Precompute the periodic Hann window used by every STFT block.
  hann_.resize(nfft);
  for (std::size_t n = 0; n < nfft; ++n) {
    hann_[n] = static_cast<float>(0.5 * (1.0 - std::cos(2.0 * kPi * n / nfft)));
  }

  // Plan the transform once. The deployed 256-point STFT takes the radix-2
  // path; unusual sizes retain deterministic support through precomputed DFT
  // twiddles rather than recalculating sine/cosine for every window.
  radix2_fft_ = nfft != 0 && (nfft & (nfft - 1)) == 0;
  if (radix2_fft_) {
    bit_reversal_.resize(nfft);
    std::size_t bits = 0;
    for (std::size_t value = nfft; value > 1; value >>= 1) ++bits;
    for (std::size_t i = 0; i < nfft; ++i) {
      std::size_t value = i;
      std::size_t reversed = 0;
      for (std::size_t bit = 0; bit < bits; ++bit) {
        reversed = (reversed << 1) | (value & 1);
        value >>= 1;
      }
      bit_reversal_[i] = reversed;
    }
    fft_roots_.resize(nfft / 2);
    for (std::size_t k = 0; k < fft_roots_.size(); ++k) {
      const double angle = -2.0 * kPi * static_cast<double>(k) /
                           static_cast<double>(nfft);
      fft_roots_[k] = Complex(std::cos(angle), std::sin(angle));
    }
  }

  // Map explicit Hz bands to half-open one-sided FFT-bin ranges. Preserve the
  // legacy even full-spectrum split only when no physical bands are supplied.
  num_bins_ = nfft / 2 + 1;
  if (!radix2_fft_ && nfft != 0) {
    dft_twiddles_.resize(num_bins_ * nfft);
    for (std::size_t f = 0; f < num_bins_; ++f) {
      for (std::size_t n = 0; n < nfft; ++n) {
        const double angle = -2.0 * kPi * static_cast<double>(f * n) /
                             static_cast<double>(nfft);
        dft_twiddles_[f * nfft + n] = Complex(std::cos(angle), std::sin(angle));
      }
    }
  }
  const std::size_t B = cfg_.frequency_bands_hz.empty()
                            ? std::max<std::size_t>(cfg_.num_bands, 1)
                            : cfg_.frequency_bands_hz.size();
  band_lo_.resize(B);
  band_hi_.resize(B);
  if (cfg_.frequency_bands_hz.empty()) {
    for (std::size_t b = 0; b < B; ++b) {
      band_lo_[b] = (b * num_bins_) / B;
      band_hi_[b] = ((b + 1) * num_bins_) / B;
      if (band_hi_[b] <= band_lo_[b]) {
        band_hi_[b] = std::min(num_bins_, band_lo_[b] + 1);
      }
    }
  } else {
    if (!std::isfinite(cfg_.sample_rate_hz) || cfg_.sample_rate_hz <= 0.0 ||
        nfft == 0) {
      throw std::invalid_argument("explicit MPF bands require a valid sample rate and STFT");
    }
    const double bin_hz = cfg_.sample_rate_hz / static_cast<double>(nfft);
    double previous_high_hz = -1.0;
    for (std::size_t b = 0; b < B; ++b) {
      const auto& band = cfg_.frequency_bands_hz[b];
      if (!std::isfinite(band.low_hz) || !std::isfinite(band.high_hz) ||
          band.low_hz < 0.0 || band.high_hz <= band.low_hz ||
          band.high_hz > cfg_.sample_rate_hz / 2.0 ||
          band.low_hz < previous_high_hz) {
        throw std::invalid_argument("MPF frequency bands must be ordered and non-overlapping");
      }
      band_lo_[b] = std::min(
          num_bins_, static_cast<std::size_t>(std::ceil(band.low_hz / bin_hz)));
      band_hi_[b] = std::min(
          num_bins_, static_cast<std::size_t>(std::ceil(band.high_hz / bin_hz)));
      if (band_hi_[b] <= band_lo_[b]) {
        throw std::invalid_argument("MPF frequency band contains no FFT bins");
      }
      previous_high_hz = band.high_hz;
    }
  }

  // Keep the diagonal plus only the first K upper off-diagonal matrix bands.
  // Offset d contains C-d entries, each represented by two real values.
  const std::size_t K = std::min(cfg_.num_off_diag_bands, C > 0 ? C - 1 : 0);
  const std::size_t per_band_dim = C + K * (2 * C - K - 1);
  feature_dim_ = B * per_band_dim;
}

void MpfFeaturizer::fft_in_place(std::vector<Complex>& values) const {
  const std::size_t n = values.size();
  for (std::size_t i = 0; i < n; ++i) {
    const auto reversed = bit_reversal_[i];
    if (i < reversed) std::swap(values[i], values[reversed]);
  }

  for (std::size_t length = 2; length <= n; length <<= 1) {
    const std::size_t half = length / 2;
    const std::size_t root_step = n / length;
    for (std::size_t base = 0; base < n; base += length) {
      for (std::size_t j = 0; j < half; ++j) {
        const Complex even = values[base + j];
        const Complex odd = values[base + j + half] * fft_roots_[j * root_step];
        values[base + j] = even + odd;
        values[base + j + half] = even - odd;
      }
    }
  }
}

void MpfFeaturizer::hermitian_eig(std::vector<Complex>& A, std::size_t n,
                                  std::vector<double>& evals,
                                  std::vector<Complex>& evecs) {
  // Cyclic Jacobi for a Hermitian matrix. Eigenvectors accumulate in V so that
  // A = V diag(w) V^H. A is destroyed. Complexity is O(n^3) per sweep; for the
  // modest channel counts used here (a subset, typ. <= 32) this is adequate.
  auto at = [n](std::size_t r, std::size_t c) { return r * n + c; };

  evecs.assign(n * n, Complex(0.0, 0.0));
  for (std::size_t i = 0; i < n; ++i) {
    evecs[at(i, i)] = Complex(1.0, 0.0);
  }

  const int kMaxSweeps = 60;
  for (int sweep = 0; sweep < kMaxSweeps; ++sweep) {
    // Stop on a scale-relative off-diagonal Frobenius norm. An absolute-only
    // threshold forces all 60 sweeps for ordinary broadband power scales even
    // after the remaining rotations are far below float feature precision.
    double off = 0.0;
    double diagonal = 0.0;
    for (std::size_t p = 0; p < n; ++p) {
      diagonal += std::norm(A[at(p, p)]);
      for (std::size_t q = p + 1; q < n; ++q) {
        off += std::norm(A[at(p, q)]);
      }
    }
    if (off <= std::max(1e-30, diagonal * 1e-24)) {
      break;
    }

    for (std::size_t p = 0; p < n; ++p) {
      for (std::size_t q = p + 1; q < n; ++q) {
        const Complex apq = A[at(p, q)];
        if (std::norm(apq) <= 1e-30) {
          continue;
        }
        const double app = A[at(p, p)].real();
        const double aqq = A[at(q, q)].real();

        // Rotate the off-diagonal (p,q) to zero. For a Hermitian 2x2 block,
        // write apq = |apq| * e^{i*phi}; a real Jacobi angle theta on the
        // phase-aligned block does the job.
        const double abs_apq = std::abs(apq);
        const Complex phase = apq / abs_apq;  // unit complex e^{i*phi}

        const double tau = (aqq - app) / (2.0 * abs_apq);
        double t;
        if (tau >= 0.0) {
          t = 1.0 / (tau + std::sqrt(1.0 + tau * tau));
        } else {
          t = -1.0 / (-tau + std::sqrt(1.0 + tau * tau));
        }
        const double c = 1.0 / std::sqrt(1.0 + t * t);
        const double s = t * c;

        // Rotation columns: G[p] and G[q]. The unitary is
        //   [ c            s*conj(phase) ]
        //   [ -s*phase     c             ]
        const Complex s_ph = s * phase;
        const Complex s_cph = s * std::conj(phase);

        // Update rows/cols p and q of A: A <- G^H A G.
        for (std::size_t k = 0; k < n; ++k) {
          const Complex akp = A[at(k, p)];
          const Complex akq = A[at(k, q)];
          A[at(k, p)] = c * akp - s_ph * akq;
          A[at(k, q)] = s_cph * akp + c * akq;
        }
        for (std::size_t k = 0; k < n; ++k) {
          const Complex apk = A[at(p, k)];
          const Complex aqk = A[at(q, k)];
          A[at(p, k)] = c * apk - s_cph * aqk;
          A[at(q, k)] = s_ph * apk + c * aqk;
        }

        // Accumulate the eigenvectors: V <- V G.
        for (std::size_t k = 0; k < n; ++k) {
          const Complex vkp = evecs[at(k, p)];
          const Complex vkq = evecs[at(k, q)];
          evecs[at(k, p)] = c * vkp - s_ph * vkq;
          evecs[at(k, q)] = s_cph * vkp + c * vkq;
        }
      }
    }
  }

  evals.resize(n);
  for (std::size_t i = 0; i < n; ++i) {
    evals[i] = A[at(i, i)].real();
  }
}

std::vector<float> MpfFeaturizer::compute(
    const std::vector<std::vector<float>>& window) const {
  const std::size_t C = cfg_.num_channels;
  const std::size_t nfft = cfg_.stft_size;
  const std::size_t hop = std::max<std::size_t>(cfg_.stft_hop, 1);
  const std::size_t B = band_lo_.size();

  std::vector<float> feature(feature_dim_, 0.0f);

  // Guard against malformed input: need C channels each at least one STFT
  // block long. Anything shorter yields a zero feature vector.
  if (window.size() < C) {
    return feature;
  }
  std::size_t N = window[0].size();
  for (std::size_t c = 0; c < C; ++c) {
    N = std::min(N, window[c].size());
  }
  if (N == 0 || nfft == 0) {
    return feature;
  }

  const std::size_t num_blocks = N < nfft ? 1 : 1 + (N - nfft) / hop;
  std::vector<float> analysis_window = hann_;
  if (N < nfft) {
    std::fill(analysis_window.begin(), analysis_window.end(), 0.0f);
    for (std::size_t n = 0; n < N; ++n) {
      analysis_window[n] =
          static_cast<float>(0.5 * (1.0 - std::cos(2.0 * kPi * n / N)));
    }
  }

  // STFT: X[c][t][f]. Use the planned radix-2 FFT for the configured
  // power-of-two size, with a precomputed direct one-sided DFT fallback.
  std::vector<std::vector<Complex>> stft(C);
  for (std::size_t c = 0; c < C; ++c) {
    stft[c].assign(num_blocks * num_bins_, Complex(0.0, 0.0));
  }

  std::vector<Complex> windowed(nfft);
  for (std::size_t c = 0; c < C; ++c) {
    const auto& x = window[c];
    for (std::size_t t = 0; t < num_blocks; ++t) {
      const std::size_t base = t * hop;
      std::fill(windowed.begin(), windowed.end(), Complex(0.0, 0.0));
      const std::size_t available = std::min(nfft, N - base);
      for (std::size_t n = 0; n < available; ++n) {
        windowed[n] =
            Complex(static_cast<double>(x[base + n]) * analysis_window[n], 0.0);
      }
      if (radix2_fft_) {
        fft_in_place(windowed);
        for (std::size_t f = 0; f < num_bins_; ++f) {
          stft[c][t * num_bins_ + f] = windowed[f];
        }
      } else {
        for (std::size_t f = 0; f < num_bins_; ++f) {
          Complex value(0.0, 0.0);
          for (std::size_t n = 0; n < nfft; ++n) {
            value += windowed[n] * dft_twiddles_[f * nfft + n];
          }
          stft[c][t * num_bins_ + f] = value;
        }
      }
    }
  }

  // Per band: cross-spectral density S_b = mean over (f in band, t) of
  // x[:,f,t] * x[:,f,t]^H, then regularise and take the Hermitian matrix log.
  std::vector<Complex> S(C * C);
  std::vector<double> evals;
  std::vector<Complex> evecs;
  std::vector<double> log_evals;

  auto at = [C](std::size_t r, std::size_t c) { return r * C + c; };

  std::size_t out = 0;
  for (std::size_t b = 0; b < B; ++b) {
    std::fill(S.begin(), S.end(), Complex(0.0, 0.0));
    std::size_t count = 0;
    for (std::size_t f = band_lo_[b]; f < band_hi_[b]; ++f) {
      for (std::size_t t = 0; t < num_blocks; ++t) {
        // Outer product x x^H over channels for this (f,t) cell.
        for (std::size_t i = 0; i < C; ++i) {
          const Complex xi = stft[i][t * num_bins_ + f];
          for (std::size_t j = i; j < C; ++j) {
            const Complex xj = stft[j][t * num_bins_ + f];
            const Complex value = xi * std::conj(xj);
            S[at(i, j)] += value;
            if (i != j) S[at(j, i)] += std::conj(value);
          }
        }
        ++count;
      }
    }
    if (count > 0) {
      const double inv = 1.0 / static_cast<double>(count);
      for (auto& v : S) {
        v *= inv;
      }
    }
    // Regularise: S <- S + eps*I. Also force exact Hermitian symmetry to kill
    // accumulated round-off before the eigensolver.
    for (std::size_t i = 0; i < C; ++i) {
      for (std::size_t j = i + 1; j < C; ++j) {
        const Complex avg = 0.5 * (S[at(i, j)] + std::conj(S[at(j, i)]));
        S[at(i, j)] = avg;
        S[at(j, i)] = std::conj(avg);
      }
      S[at(i, i)] = Complex(S[at(i, i)].real() + cfg_.eps, 0.0);
    }

    // Eigendecompose and evaluate only the requested entries of
    // L = U diag(log lambda) U^H. The eigendecomposition still uses the full
    // CSD because every matrix-log entry depends on the complete matrix.
    std::vector<Complex> A = S;  // hermitian_eig destroys its input
    hermitian_eig(A, C, evals, evecs);
    log_evals.resize(C);
    for (std::size_t k = 0; k < C; ++k) {
      log_evals[k] = std::log(std::max(evals[k], 1e-12));
    }

    const auto log_entry = [&](std::size_t i, std::size_t j) {
      Complex value(0.0, 0.0);
      for (std::size_t k = 0; k < C; ++k) {
        value += evecs[i * C + k] * log_evals[k] * std::conj(evecs[j * C + k]);
      }
      return value;
    };

    // Vectorise the diagonal plus the first configured upper off-diagonal
    // bands. Offset d contains entries (i, i+d), each as [real, imag].
    for (std::size_t i = 0; i < C; ++i) {
      feature[out++] = static_cast<float>(log_entry(i, i).real());
    }
    const std::size_t K = std::min(cfg_.num_off_diag_bands, C > 0 ? C - 1 : 0);
    for (std::size_t d = 1; d <= K; ++d) {
      for (std::size_t i = 0; i + d < C; ++i) {
        const auto value = log_entry(i, i + d);
        feature[out++] = static_cast<float>(value.real());
        feature[out++] = static_cast<float>(value.imag());
      }
    }
  }

  return feature;
}

}  // namespace app
