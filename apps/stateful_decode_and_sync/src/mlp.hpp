#pragma once
#include <cstddef>
#include <cstdint>
#include <functional>
#include <vector>

namespace app {

// Hand-rolled 2-hidden-layer MLP classifier trained on-device with SGD.
//
// Architecture (plan section 5.3):
//   input(D) -> Dense(H) -> ReLU -> Dropout(p)
//            -> Dense(H) -> ReLU -> Dropout(p)
//            -> Dense(K) -> softmax
// with cross-entropy loss. Dropout is active only during fit(); infer() runs
// the deterministic full network (inverted dropout keeps expectations matched,
// so no test-time rescaling is needed). Weights are float; He initialisation is
// seeded so a given `seed` yields a reproducible starting point.
class Mlp {
 public:
  struct Config {
    std::size_t input_dim = 0;
    std::size_t hidden_dim = 64;
    std::size_t num_classes = 5;
    float dropout = 0.2f;
    float lr = 0.01f;
    std::size_t epochs = 100;
    uint64_t seed = 0xACE1;
  };

  Mlp() = default;

  // (Re)initialise all weights for the given config. Must be called before
  // fit()/infer() and whenever input_dim/hidden_dim/num_classes change.
  void init(const Config& cfg);

  bool ready() const { return ready_; }
  const Config& config() const { return cfg_; }

  struct FitProgress {
    std::size_t epoch = 0;       // one-based completed epoch
    std::size_t total_epochs = 0;
    float loss = 0.0f;           // mean training loss for this epoch
    float accuracy = 0.0f;       // training accuracy for this epoch
  };

  using ProgressObserver = std::function<void(const FitProgress&)>;

  // Train on the full (features,label) set for cfg.epochs, SGD over a shuffled
  // sample order each epoch. `features[i]` has length input_dim, `labels[i]` is
  // in [0,num_classes). Returns the final-epoch mean loss; `out_accuracy`, if
  // non-null, receives the final-epoch training accuracy. Sets ready() on
  // success. If supplied, `observer` is called once after each completed epoch.
  // Returns a negative value if the data is empty, malformed, or produces a
  // non-finite training metric.
  float fit(const std::vector<std::vector<float>>& features,
            const std::vector<std::size_t>& labels, float* out_accuracy = nullptr,
            ProgressObserver observer = {});

  // Run the (dropout-free) forward pass and return the softmax distribution
  // over classes (length num_classes). Empty if not ready or dim mismatch.
  std::vector<float> infer(const std::vector<float>& x) const;

 private:
  // Deterministic PRNG (splitmix64) so training order and init are reproducible.
  uint64_t rng_state_ = 0;
  double next_uniform();          // [0,1)
  double next_gaussian();         // standard normal (Box-Muller)

  // Training forward pass: draws dropout masks (mutating the PRNG) and stores
  // them for the backward pass. Fills z*/h* (pre/post activations) and probs.
  void forward_train(const std::vector<float>& x, std::vector<float>& z1,
                     std::vector<float>& h1, std::vector<float>& z2, std::vector<float>& h2,
                     std::vector<float>& probs, std::vector<uint8_t>& mask1,
                     std::vector<uint8_t>& mask2);

  // Inference forward pass: no dropout, no PRNG use. Returns the softmax probs.
  std::vector<float> forward_eval(const std::vector<float>& x) const;

  Config cfg_;
  bool ready_ = false;

  // Per-feature standardisation (z-scoring) fit from the training set. Raw MPF
  // features vary over orders of magnitude across bands and channel pairs;
  // standardising them makes SGD well-conditioned so a single learning rate
  // works. Applied identically at inference.
  std::vector<float> feat_mean_;  // [input_dim]
  std::vector<float> feat_inv_std_;  // [input_dim]
  void standardize(const std::vector<float>& x, std::vector<float>& out) const;

  // Layer 1: input_dim -> hidden_dim
  std::vector<float> W1_;  // [hidden_dim * input_dim]
  std::vector<float> b1_;  // [hidden_dim]
  // Layer 2: hidden_dim -> hidden_dim
  std::vector<float> W2_;  // [hidden_dim * hidden_dim]
  std::vector<float> b2_;  // [hidden_dim]
  // Output: hidden_dim -> num_classes
  std::vector<float> W3_;  // [num_classes * hidden_dim]
  std::vector<float> b3_;  // [num_classes]
};

}  // namespace app
