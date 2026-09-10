#include "fit_worker.hpp"

#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <thread>
#include <vector>

namespace {

void expect(bool condition, const char* message) {
  if (!condition) {
    std::cerr << "FAIL " << message << '\n';
    std::exit(1);
  }
}

scifi2_hub::Mlp::Config test_config() {
  scifi2_hub::Mlp::Config config;
  config.input_dim = 4;
  config.hidden_dim = 16;
  config.num_classes = 2;
  config.dropout = 0.0f;
  config.lr = 0.03f;
  config.epochs = 12;
  config.seed = 0x4321;
  return config;
}

std::vector<std::vector<float>> make_features() {
  std::vector<std::vector<float>> result;
  result.reserve(96);
  for (std::size_t i = 0; i < 48; ++i) {
    const float v = static_cast<float>(i) / 48.0f;
    result.push_back({-1.0f - v, -0.5f - v, 0.25f, 1.0f});
    result.push_back({1.0f + v, 0.5f + v, -0.25f, -1.0f});
  }
  return result;
}

std::vector<std::size_t> make_labels() {
  std::vector<std::size_t> result;
  result.reserve(96);
  for (std::size_t i = 0; i < 48; ++i) {
    result.push_back(0);
    result.push_back(1);
  }
  return result;
}

void expect_same(const std::vector<float>& lhs, const std::vector<float>& rhs,
                const char* message) {
  expect(lhs.size() == rhs.size(), message);
  for (std::size_t i = 0; i < lhs.size(); ++i) {
    expect(lhs[i] == rhs[i], message);
  }
}

void test_snapshot_worker_and_candidate_visibility() {
  const auto config = test_config();
  const auto original_features = make_features();
  const auto labels = make_labels();

  // Establish a complete live model. It must remain unchanged while the
  // worker trains its private candidate.
  scifi2_hub::Mlp live;
  live.init(config);
  expect(live.fit(original_features, labels) >= 0.0f, "live baseline fit succeeds");
  const std::vector<float> query = {0.2f, 0.1f, 0.0f, -0.2f};
  const auto live_before = live.infer(query);

  scifi2_hub::FitWorker worker;
  scifi2_hub::FitWorker::Request request;
  request.config = config;
  request.features = original_features;
  request.labels = labels;
  expect(worker.start(request), "worker accepts one fit");

  // Mutating the caller's source after start cannot affect the worker's
  // immutable request snapshot.
  request.features[0][0] = std::numeric_limits<float>::quiet_NaN();

  scifi2_hub::FitWorker::Event event;
  std::shared_ptr<scifi2_hub::Mlp> candidate;
  std::size_t progress_count = 0;
  std::size_t activity_ticks = 0;
  for (;;) {
    ++activity_ticks;  // stands in for acquisition/status work on the App thread
    expect_same(live.infer(query), live_before,
                "live inference remains a complete unchanged model during fit");

    while (worker.poll(event)) {
      if (event.kind == scifi2_hub::FitWorker::EventKind::kProgress) {
        ++progress_count;
        expect(!event.candidate, "progress never exposes a candidate model");
      } else if (event.kind == scifi2_hub::FitWorker::EventKind::kSucceeded) {
        candidate = std::move(event.candidate);
      } else {
        expect(false, "snapshot fit succeeds");
      }
    }
    if (!worker.busy()) break;
    std::this_thread::yield();
  }

  expect(activity_ticks > 0, "main-thread activity continues while fitting");
  expect(progress_count == config.epochs, "worker reports every completed epoch");
  expect(candidate != nullptr && candidate->ready(),
         "candidate is exposed only as a ready terminal model");

  scifi2_hub::Mlp reference;
  reference.init(config);
  expect(reference.fit(original_features, labels) >= 0.0f, "reference fit succeeds");
  expect_same(candidate->infer(query), reference.infer(query),
              "worker candidate depends on the immutable snapshot, not caller mutation");
  expect_same(live.infer(query), live_before,
              "live model remains unchanged until an explicit terminal swap");
}

void test_rejected_start_does_not_replace_job() {
  const auto config = test_config();
  scifi2_hub::FitWorker worker;
  scifi2_hub::FitWorker::Request request;
  request.config = config;
  request.features = make_features();
  request.labels = make_labels();
  expect(worker.start(std::move(request)), "first worker request starts");

  scifi2_hub::FitWorker::Request second;
  second.config = config;
  second.features = make_features();
  second.labels = make_labels();
  expect(!worker.start(std::move(second)), "second request is rejected while busy");
  worker.stop();
}

}  // namespace

int main() {
  test_snapshot_worker_and_candidate_visibility();
  test_rejected_start_does_not_replace_job();
  std::cout << "PASS managed fit worker snapshot and candidate visibility\n";
  return 0;
}
