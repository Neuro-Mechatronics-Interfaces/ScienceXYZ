#include "mlp.hpp"

#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <vector>

namespace {

void expect(bool condition, const char* message) {
  if (!condition) {
    std::cerr << "FAIL " << message << '\n';
    std::exit(1);
  }
}

app::Mlp::Config test_config() {
  app::Mlp::Config config;
  config.input_dim = 2;
  config.hidden_dim = 8;
  config.num_classes = 2;
  config.dropout = 0.0f;
  config.lr = 0.05f;
  config.epochs = 4;
  config.seed = 0x1234;
  return config;
}

std::vector<std::vector<float>> features() {
  return {{-1.0f, -1.0f}, {-0.8f, -1.2f}, {1.0f, 1.0f}, {0.8f, 1.2f}};
}

std::vector<std::size_t> labels() { return {0, 0, 1, 1}; }

void test_progress_and_determinism() {
  const auto config = test_config();
  const auto x = features();
  const auto y = labels();

  app::Mlp observed;
  observed.init(config);
  std::vector<app::Mlp::FitProgress> progress;
  float observed_accuracy = 0.0f;
  const float observed_loss = observed.fit(
      x, y, &observed_accuracy,
      [&progress](const app::Mlp::FitProgress& item) { progress.push_back(item); });

  expect(observed.ready(), "successful fit marks model ready");
  expect(progress.size() == config.epochs, "observer receives one event per epoch");
  for (std::size_t i = 0; i < progress.size(); ++i) {
    expect(progress[i].epoch == i + 1, "epoch events are one-based and monotonic");
    expect(progress[i].total_epochs == config.epochs, "progress reports total epochs");
    expect(std::isfinite(progress[i].loss) && progress[i].loss >= 0.0f,
           "progress loss is finite and non-negative");
    expect(std::isfinite(progress[i].accuracy) && progress[i].accuracy >= 0.0f &&
               progress[i].accuracy <= 1.0f,
           "progress accuracy is finite and bounded");
  }
  expect(progress.back().epoch == config.epochs, "last progress event is terminal metric");
  expect(progress.back().loss == observed_loss, "terminal progress loss matches fit return");
  expect(progress.back().accuracy == observed_accuracy,
         "terminal progress accuracy matches output accuracy");

  app::Mlp repeat;
  repeat.init(config);
  float repeat_accuracy = 0.0f;
  const float repeat_loss = repeat.fit(x, y, &repeat_accuracy);
  expect(repeat_loss == observed_loss && repeat_accuracy == observed_accuracy,
         "observer does not change deterministic training");
}

void test_malformed_data_is_rejected() {
  const auto config = test_config();
  const auto valid_features = features();
  const auto valid_labels = labels();

  app::Mlp wrong_dimension;
  wrong_dimension.init(config);
  auto short_features = valid_features;
  short_features[0].pop_back();
  expect(wrong_dimension.fit(short_features, valid_labels) < 0.0f,
         "wrong feature dimension is malformed");
  expect(!wrong_dimension.ready(), "malformed fit does not mark model ready");

  app::Mlp wrong_label;
  wrong_label.init(config);
  auto out_of_range_labels = valid_labels;
  out_of_range_labels[0] = config.num_classes;
  expect(wrong_label.fit(valid_features, out_of_range_labels) < 0.0f,
         "out-of-range label is malformed");

  app::Mlp non_finite;
  non_finite.init(config);
  auto non_finite_features = valid_features;
  non_finite_features[0][0] = std::numeric_limits<float>::quiet_NaN();
  expect(non_finite.fit(non_finite_features, valid_labels) < 0.0f,
         "non-finite feature is malformed");
}

}  // namespace

int main() {
  test_progress_and_determinism();
  test_malformed_data_is_rejected();
  std::cout << "PASS MLP progress and malformed-data handling\n";
  return 0;
}
