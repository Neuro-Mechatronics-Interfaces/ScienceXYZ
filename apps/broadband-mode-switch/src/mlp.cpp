#include "mlp.hpp"

#include <algorithm>
#include <cmath>
#include <numeric>

namespace app {

double Mlp::next_uniform() {
  // splitmix64 -> uniform in [0,1).
  uint64_t z = (rng_state_ += 0x9E3779B97F4A7C15ULL);
  z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
  z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
  z = z ^ (z >> 31);
  // 53-bit mantissa for a well-distributed double in [0,1).
  return (z >> 11) * (1.0 / 9007199254740992.0);
}

double Mlp::next_gaussian() {
  // Box-Muller; guard u1 away from 0 for the log.
  double u1 = next_uniform();
  double u2 = next_uniform();
  if (u1 < 1e-12) u1 = 1e-12;
  return std::sqrt(-2.0 * std::log(u1)) * std::cos(2.0 * 3.14159265358979323846 * u2);
}

void Mlp::init(const Config& cfg) {
  cfg_ = cfg;
  rng_state_ = cfg.seed ? cfg.seed : 0x9E3779B97F4A7C15ULL;

  const std::size_t D = cfg_.input_dim;
  const std::size_t H = cfg_.hidden_dim;
  const std::size_t K = cfg_.num_classes;

  auto he_init = [this](std::vector<float>& W, std::size_t rows, std::size_t fan_in) {
    W.resize(rows * fan_in);
    const double scale = std::sqrt(2.0 / static_cast<double>(std::max<std::size_t>(fan_in, 1)));
    for (auto& w : W) {
      w = static_cast<float>(next_gaussian() * scale);
    }
  };

  he_init(W1_, H, D);
  b1_.assign(H, 0.0f);
  he_init(W2_, H, H);
  b2_.assign(H, 0.0f);
  he_init(W3_, K, H);
  b3_.assign(K, 0.0f);

  feat_mean_.clear();
  feat_inv_std_.clear();
  ready_ = false;  // needs a fit() before it should be trusted for inference
}

void Mlp::standardize(const std::vector<float>& x, std::vector<float>& out) const {
  const std::size_t D = cfg_.input_dim;
  out.resize(D);
  if (feat_mean_.size() == D && feat_inv_std_.size() == D) {
    for (std::size_t j = 0; j < D; ++j) {
      out[j] = (x[j] - feat_mean_[j]) * feat_inv_std_[j];
    }
  } else {
    out.assign(x.begin(), x.end());  // no stats yet: pass through
  }
}

namespace {
void softmax_inplace(std::vector<float>& v) {
  float m = v.empty() ? 0.0f : *std::max_element(v.begin(), v.end());
  double sum = 0.0;
  for (auto& x : v) {
    x = std::exp(x - m);
    sum += x;
  }
  const float inv = static_cast<float>(sum > 0.0 ? 1.0 / sum : 0.0);
  for (auto& x : v) x *= inv;
}
}  // namespace

void Mlp::forward_train(const std::vector<float>& x, std::vector<float>& z1,
                        std::vector<float>& h1, std::vector<float>& z2, std::vector<float>& h2,
                        std::vector<float>& probs, std::vector<uint8_t>& mask1,
                        std::vector<uint8_t>& mask2) {
  const std::size_t D = cfg_.input_dim;
  const std::size_t H = cfg_.hidden_dim;
  const std::size_t K = cfg_.num_classes;
  const float p = cfg_.dropout;
  const float keep = 1.0f - p;
  const float inv_keep = keep > 0.0f ? 1.0f / keep : 1.0f;  // inverted dropout

  z1.assign(H, 0.0f);
  h1.assign(H, 0.0f);
  mask1.assign(H, 1);
  for (std::size_t i = 0; i < H; ++i) {
    float acc = b1_[i];
    const float* w = &W1_[i * D];
    for (std::size_t j = 0; j < D; ++j) acc += w[j] * x[j];
    z1[i] = acc;
    float a = acc > 0.0f ? acc : 0.0f;  // ReLU
    if (p > 0.0f && next_uniform() < p) {
      mask1[i] = 0;
      a = 0.0f;
    } else {
      a *= inv_keep;
    }
    h1[i] = a;
  }

  z2.assign(H, 0.0f);
  h2.assign(H, 0.0f);
  mask2.assign(H, 1);
  for (std::size_t i = 0; i < H; ++i) {
    float acc = b2_[i];
    const float* w = &W2_[i * H];
    for (std::size_t j = 0; j < H; ++j) acc += w[j] * h1[j];
    z2[i] = acc;
    float a = acc > 0.0f ? acc : 0.0f;
    if (p > 0.0f && next_uniform() < p) {
      mask2[i] = 0;
      a = 0.0f;
    } else {
      a *= inv_keep;
    }
    h2[i] = a;
  }

  probs.assign(K, 0.0f);
  for (std::size_t i = 0; i < K; ++i) {
    float acc = b3_[i];
    const float* w = &W3_[i * H];
    for (std::size_t j = 0; j < H; ++j) acc += w[j] * h2[j];
    probs[i] = acc;
  }
  softmax_inplace(probs);
}

std::vector<float> Mlp::forward_eval(const std::vector<float>& x) const {
  const std::size_t D = cfg_.input_dim;
  const std::size_t H = cfg_.hidden_dim;
  const std::size_t K = cfg_.num_classes;

  std::vector<float> h1(H, 0.0f);
  for (std::size_t i = 0; i < H; ++i) {
    float acc = b1_[i];
    const float* w = &W1_[i * D];
    for (std::size_t j = 0; j < D; ++j) acc += w[j] * x[j];
    h1[i] = acc > 0.0f ? acc : 0.0f;  // ReLU, no dropout at eval
  }
  std::vector<float> h2(H, 0.0f);
  for (std::size_t i = 0; i < H; ++i) {
    float acc = b2_[i];
    const float* w = &W2_[i * H];
    for (std::size_t j = 0; j < H; ++j) acc += w[j] * h1[j];
    h2[i] = acc > 0.0f ? acc : 0.0f;
  }
  std::vector<float> probs(K, 0.0f);
  for (std::size_t i = 0; i < K; ++i) {
    float acc = b3_[i];
    const float* w = &W3_[i * H];
    for (std::size_t j = 0; j < H; ++j) acc += w[j] * h2[j];
    probs[i] = acc;
  }
  softmax_inplace(probs);
  return probs;
}

float Mlp::fit(const std::vector<std::vector<float>>& features,
               const std::vector<std::size_t>& labels, float* out_accuracy) {
  const std::size_t D = cfg_.input_dim;
  const std::size_t H = cfg_.hidden_dim;
  const std::size_t K = cfg_.num_classes;
  const std::size_t M = features.size();

  if (M == 0 || labels.size() != M || D == 0 || H == 0 || K == 0) {
    return -1.0f;
  }
  for (const auto& f : features) {
    if (f.size() != D) return -1.0f;
  }

  // Fit per-feature standardisation stats (mean/std) over the training set.
  feat_mean_.assign(D, 0.0f);
  feat_inv_std_.assign(D, 1.0f);
  {
    std::vector<double> mean(D, 0.0), m2(D, 0.0);
    for (const auto& f : features) {
      for (std::size_t j = 0; j < D; ++j) mean[j] += f[j];
    }
    for (std::size_t j = 0; j < D; ++j) mean[j] /= static_cast<double>(M);
    for (const auto& f : features) {
      for (std::size_t j = 0; j < D; ++j) {
        const double d = f[j] - mean[j];
        m2[j] += d * d;
      }
    }
    for (std::size_t j = 0; j < D; ++j) {
      const double var = m2[j] / static_cast<double>(M);
      const double sd = std::sqrt(var);
      feat_mean_[j] = static_cast<float>(mean[j]);
      feat_inv_std_[j] = static_cast<float>(sd > 1e-8 ? 1.0 / sd : 1.0);
    }
  }

  // Sample order, reshuffled each epoch via Fisher-Yates on the PRNG.
  std::vector<std::size_t> order(M);
  std::iota(order.begin(), order.end(), 0);

  // Scratch buffers reused across samples.
  std::vector<float> z1, h1, z2, h2, probs, xs;
  std::vector<uint8_t> mask1, mask2;

  float last_loss = 0.0f;
  float last_acc = 0.0f;

  for (std::size_t epoch = 0; epoch < cfg_.epochs; ++epoch) {
    for (std::size_t i = M; i > 1; --i) {
      const std::size_t j = static_cast<std::size_t>(next_uniform() * i);
      std::swap(order[i - 1], order[j < i ? j : i - 1]);
    }

    double epoch_loss = 0.0;
    std::size_t correct = 0;

    for (std::size_t idx = 0; idx < M; ++idx) {
      const std::size_t s = order[idx];
      standardize(features[s], xs);
      const std::vector<float>& x = xs;
      const std::size_t y = labels[s];

      forward_train(x, z1, h1, z2, h2, probs, mask1, mask2);

      // Cross-entropy loss on the true class.
      const float py = (y < K) ? std::max(probs[y], 1e-12f) : 1e-12f;
      epoch_loss += -std::log(py);

      // Training accuracy on this sample.
      std::size_t argmax = 0;
      for (std::size_t k = 1; k < K; ++k) {
        if (probs[k] > probs[argmax]) argmax = k;
      }
      if (argmax == y) ++correct;

      // Backprop. dL/dz3 = probs - onehot(y).
      std::vector<float> dz3(K);
      for (std::size_t k = 0; k < K; ++k) {
        dz3[k] = probs[k] - (k == y ? 1.0f : 0.0f);
      }

      // Grad into h2 and W3/b3.
      std::vector<float> dh2(H, 0.0f);
      for (std::size_t k = 0; k < K; ++k) {
        const float g = dz3[k];
        float* w = &W3_[k * H];
        for (std::size_t j = 0; j < H; ++j) {
          dh2[j] += g * w[j];
          w[j] -= cfg_.lr * g * h2[j];
        }
        b3_[k] -= cfg_.lr * g;
      }

      // Through dropout mask + ReLU of layer 2.
      const float keep = 1.0f - cfg_.dropout;
      const float inv_keep = keep > 0.0f ? 1.0f / keep : 1.0f;
      std::vector<float> dz2(H, 0.0f);
      for (std::size_t j = 0; j < H; ++j) {
        float g = dh2[j];
        if (cfg_.dropout > 0.0f) {
          g = mask2[j] ? g * inv_keep : 0.0f;
        }
        dz2[j] = (z2[j] > 0.0f) ? g : 0.0f;  // ReLU'
      }

      // Grad into h1 and W2/b2.
      std::vector<float> dh1(H, 0.0f);
      for (std::size_t i2 = 0; i2 < H; ++i2) {
        const float g = dz2[i2];
        float* w = &W2_[i2 * H];
        for (std::size_t j = 0; j < H; ++j) {
          dh1[j] += g * w[j];
          w[j] -= cfg_.lr * g * h1[j];
        }
        b2_[i2] -= cfg_.lr * g;
      }

      // Through dropout mask + ReLU of layer 1.
      std::vector<float> dz1(H, 0.0f);
      for (std::size_t j = 0; j < H; ++j) {
        float g = dh1[j];
        if (cfg_.dropout > 0.0f) {
          g = mask1[j] ? g * inv_keep : 0.0f;
        }
        dz1[j] = (z1[j] > 0.0f) ? g : 0.0f;
      }

      // Grad into W1/b1.
      for (std::size_t i1 = 0; i1 < H; ++i1) {
        const float g = dz1[i1];
        float* w = &W1_[i1 * D];
        for (std::size_t j = 0; j < D; ++j) {
          w[j] -= cfg_.lr * g * x[j];
        }
        b1_[i1] -= cfg_.lr * g;
      }
    }

    last_loss = static_cast<float>(epoch_loss / static_cast<double>(M));
    last_acc = static_cast<float>(correct) / static_cast<float>(M);
  }

  ready_ = true;
  if (out_accuracy) *out_accuracy = last_acc;
  return last_loss;
}

std::vector<float> Mlp::infer(const std::vector<float>& x) const {
  if (!ready_ || x.size() != cfg_.input_dim) {
    return {};
  }
  std::vector<float> xs;
  standardize(x, xs);
  return forward_eval(xs);
}

}  // namespace app
