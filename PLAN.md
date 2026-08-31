# PLAN — `broadband-mode-switch` Synapse App

A new on-device Synapse App that (1) toggles a broadband stream between the real
RHD2132 probe and an in-app synthetic source, (2) collects labeled feature
windows into a per-class ring buffer, (3) computes Kaifosh-2025 multivariate
power-frequency (MPF) features, and (4) trains and runs a small MLP classifier —
all controlled live over consumer taps.

Status: **implemented (offline); not yet bench-verified.** All five stages'
source is written under `apps/broadband-mode-switch/` (App + synthetic source +
MPF featurizer + MLP + ring buffer + config + clients + README). The four
SDK-independent modules compile clean under `g++ -std=c++20 -Wall -Wextra
-Wshadow` and pass an offline smoke test (deterministic synthetic source,
`feature_dim = num_bands·C²`, MLP learns separable synthetic classes). Remaining
work is the on-bench Docker build + deploy + staged verification (§7), plus the
two build-time unknowns below. This is the root working plan; the narrative
design also lives in `docs/broadband-mode-switch-app-plan.md`.
Implementation-derived math/state-machine detail should later graduate to
`manuscript/` per repo convention.

Implementation notes vs. the original plan:
- Synthetic source ported from the actual gateware file
  `axon_test_source_peripheral.sv` (the `axon_omnetics32ch_source_peripheral.sv`
  name in §5.1 does not exist). Per-sample values and per-channel lags match;
  the AXI-stream bus timing is not emulated.
- `logm` uses a hand-rolled cyclic-Jacobi Hermitian eigensolver — **no `eigen3`
  added to `vcpkg.json`** (resolves the §5.2 note and open-question #2).
- The MLP z-scores features (mean/std fit at train time) so `mlp_lr` is robust
  to raw MPF magnitude; without this the default lr diverges on real features.
- Feature vectorization keeps the full Hermitian-log upper triangle as `C`
  real diagonals + `C(C−1)/2` complex off-diagonals `[re,im]` = `C²` reals/band,
  so `feature_dim = num_bands·C²` (§5.2 gave `B·C(C+1)/2`, the real-symmetric
  count; the complex off-diagonals double the off-diagonal term). Shipped config
  starts small: 8-ch subset, `num_bands=4` → `4·64 = 256`.
- `external/sciencecorp/{synapse-api,vcpkg}` are vendored as plain files for an
  offline build; `.gitmodules` records the upstream pins.

---

## 1. Verified context (facts this plan is built on)

- **Recording works today via the stock RHD2132.** `synapsectl -u 192.168.100.157
  info` lists `IntanRHD2132` (ID 200, `kBroadbandSource`, Intan Technologies).
  `config/axon-omnetics-32ch-broadband.json` streams cleanly: 20 kHz, 16-bit, 32
  ch, 0.00% loss; HDF5 confirms `lsb_uv=0.195`, `sample_rate_hz=20000`. The real
  driver validates `electrode_id` to **0-31** (identity map required; the virtual
  peripheral 1000 accepted anything). On Windows run `synapsectl` with
  `PYTHONUTF8=1` (its ✓ output crashes under cp1252). See `MISTAKES.md`.
- **App SDK pattern** (from `apps/synapse-example-app`, SDK v0.6.3):
  - Subclass `synapse::App`; override `bool setup()` and `void main()`.
  - `main()` runs `while (node_running_) { ... }`.
  - Config: `get_app_config(validate_fn, cfg)`; params are
    `ApplicationNodeConfig.parameters` = `map<string, google.protobuf.Value>`,
    read via `.at("k").number_value()/bool_value()/string_value()/list_value()`.
  - **Consumer tap:** `create_consumer_tap<T>("name", callback)`. Callback runs on
    its **own thread** — guard shared state with `std::atomic`/`std::mutex`.
  - **Reader:** `setup_reader(node_id)` + `data_reader_->receive_multipart()`;
    parse with `synapse::parse_protobuf_message<synapse::BroadbandFrame>(...)`.
  - **Producer tap:** `create_tap<synapse::Tensor>("name")` + `publish_tap(...)`.
  - Entry point: `int main(...) { return synapse::Entrypoint<app::T>(); }`.
  - Advanced helpers: `synapse::pack_tensor_data(vec)`,
    `synapse::get_steady_clock_now()`, `synapse::Timer`, function profiling
    (`add_profile`/`enable_function_profiling`/`start_profile`/`stop_profile`).
- **`BroadbandFrame`** (`api/datatype.proto`): `timestamp_ns`, `sequence_number`,
  `repeated sint32 frame_data` (one entry **per channel** = one time-point across
  all channels, NOT a multi-sample block), `sample_rate_hz`, `channel_ranges`,
  `unix_timestamp_ns`. Frame stamp = `start + (seq-start_seq)*1e9/fs` = 50000 ns
  at 20 kHz.
- **MPF (Kaifosh et al. 2025, Nature, CTRL-labs/Reality Labs)** is a *multivariate
  power-frequency* featurizer: channel-wise STFT → cross-spectral density →
  frequency-band averaging → SPD matrix logarithm. NOT the scalar mean/median
  power frequency of EMG-fatigue literature (same acronym). Reference code:
  <https://github.com/facebookresearch/generic-neuromotor-interface>; paper:
  <https://www.nature.com/articles/s41586-025-09255-w>.
- **Build** is Docker cross-compile (`synapsectl apps build <dir>`), SDK 0.6.3 +
  `scifi-headstage-shared-libraries` 1.3.0, two git submodules (`synapse-api`,
  `vcpkg`), CMake 3.28, C++20.

---

## 2. Decisions (locked with the user)

| Question | Decision |
| --- | --- |
| App location | **New app** `apps/broadband-mode-switch/` (not a fork of the example) |
| Synthetic data | **Port the gateware model** (LFP + biphasic spikes + noise), deterministic |
| MPF variant | **Faithful** STFT + CSD + band-avg + SPD matrix-log |
| MLP fit | **Hand-rolled** 2-layer MLP + backprop + SGD, on-device |
| MLP arch | 2 layers, **hidden 64/layer, 20% dropout between layers**, softmax + CE |
| Windowing | **time-based**: `window_ms=200`, `stride_ms=20`, featurize over the window |
| Tunables | window/stride ms, hidden dim, dropout, lr, epochs, num_bands, num_classes |

---

## 3. Node graph & taps

```
kBroadbandSource(id=1, peripheral_id=200, 20 kHz, 16-bit, 32 ch)
        │  (connection src=1 -> dst=2)
        ▼
kApplication(id=2, name="broadband-mode-switch")
        ├─ consumer  "set_source_mode"  ListValue[int mode]              0=SAMPLING 1=SYNTHETIC
        ├─ consumer  "set_capture"       ListValue[int label, int enable]
        ├─ consumer  "fit_mlp"           ListValue[int epochs?]          (trigger)
        ├─ producer  "broadband_out"     BroadbandFrame or Tensor        (real|synthetic)
        └─ producer  "class_out"         Tensor[num_classes] softmax + argmax
```

Callbacks only set atomics / enqueue requests and return fast. Heavy work
(`fit_mlp`) runs in `main()` off a request flag. Buffers and model guarded by
`std::mutex`; `mode_`, `active_label_`, `capture_enabled_`, `model_ready_`,
`fit_requested_` are `std::atomic`.

---

## 4. Configuration parameters

Window/stride are ms, converted with `sample_rate_hz`
(`window_samples = round(window_ms*fs/1000)`).

| Key | Type | Meaning | Default |
| --- | --- | --- | --- |
| `sample_rate_hz` | number | expected upstream rate (validated against frames) | 20000 |
| `num_classes` | number | ring-buffer categories / MLP outputs | 5 |
| `window_ms` | number | feature window length (ms) → one MPF vector | 200 |
| `stride_ms` | number | decoder stride / hop (ms) | 20 |
| `stft_size` | number | STFT length (samples, power of 2) | 256 |
| `stft_hop` | number | STFT hop within window (samples) | 128 |
| `num_bands` | number | frequency bands averaged into CSD | 8 |
| `channel_subset` | list | optional channel ids to featurize (default: all 32) | all |
| `ring_capacity` | number | max feature windows stored per class | 2000 |
| `mlp_hidden` | number | hidden units per layer | 64 |
| `mlp_dropout` | number | dropout prob between layers | 0.2 |
| `mlp_lr` | number | SGD learning rate | 0.01 |
| `mlp_epochs` | number | training epochs per fit | 100 |
| `synthetic_seed` | number | LFSR/noise seed for synthetic mode | 44257 (0xACE1) |

`validate_config` requires keys lacking a safe default; `parse_config` reads the
rest with fallbacks (example-app pattern).

---

## 5. Algorithms

### 5.1 Synthetic source (ported from gateware)

Port `vendor/axon-peripherals/src/gateware/axon_omnetics32ch_source_peripheral.sv`:
256-entry sine LFP LUT, 32-sample biphasic spike ROM, per-channel fixed delay
LUT, 16-bit LFSR spike trigger, 32-bit noise LFSR; amplitudes in counts==µV
(get_lsb=1 path). One sample/channel per `main()` tick in SYNTHETIC mode, emitted
as a `BroadbandFrame` with our own monotonic `sequence_number` and
`timestamp_ns = start + seq*1e9/fs`. Deterministic from `synthetic_seed`.
In SAMPLING mode forward the upstream frame unchanged — **never overwrite source
timestamps** (AGENTS.md).

### 5.2 MPF featurizer

Let `C` = featurized channel count, `B` = `num_bands`. Per `window_ms` window:

1. Per channel `c`: STFT (`stft_size`, hop `stft_hop`, Hann window) →
   complex `X_c[f, t]`.
2. Per band `b` (contiguous split of the `stft_size/2+1` bins into `B` bands):
   Hermitian PSD cross-spectral density
   `S_b = mean_{f∈b, t} x[:,f,t] · x[:,f,t]^H`  (a `C×C` complex-Hermitian matrix).
3. Regularize `S_b ← S_b + εI`; take the **matrix log**
   `L_b = U diag(log λ) U^H` via symmetric/Hermitian eigendecomposition (real
   symmetric after using the Hermitian eigenbasis).
4. Feature = concat over bands of the **upper triangle incl. diagonal** of `L_b`;
   length `B·C(C+1)/2`.

Dimensions: C=32, B=8 → `8·528 = 4224` features/window (MLP input dim). This is
large; `channel_subset` and `num_bands` are the knobs to shrink it. **Start
smaller** for the first working version (e.g. 8-channel subset, B=4 →
`4·36 = 144`).

> `logm` needs a symmetric/Hermitian eigensolver. Confirm a linear-algebra lib in
> the builder image; else add `eigen3` to `vcpkg.json` or hand-roll a cyclic
> Jacobi eigensolver (`mpf_features.cpp`).

### 5.3 MLP

`input → Dense(H) → ReLU → Dropout(p) → Dense(H) → ReLU → Dropout(p) →
Dense(num_classes) → softmax`, cross-entropy loss, SGD (optional momentum).
Dropout active only during `fit`. `float` weights, He init seeded from
`synthetic_seed`. Forward caches activations for backprop. `fit_mlp` trains on all
buffered (feature,label) pairs for `mlp_epochs`, logs loss/accuracy, sets
`model_ready_`. Live windows then classify → publish `class_out`.

### 5.4 Ring buffer

Per-class fixed-capacity circular buffer of feature vectors capped at
`ring_capacity` (drop oldest). `set_capture` sets `active_label_` +
`capture_enabled_`; when enabled each computed feature window is appended to
`buffers_[active_label_]`. Per-class counts logged.

---

## 6. File layout

```
apps/broadband-mode-switch/
├── CMakeLists.txt              # adapted from example (target name, sources)
├── cmake/protos.cmake          # copied verbatim
├── vcpkg.json                  # + eigen3 if used for logm
├── Dockerfile                  # copied (SDK 0.6.3, shared-libs 1.3.0)
├── .gitmodules                 # synapse-api + vcpkg submodules
├── keys/science-repo-public.asc
├── README.md
├── src/
│   ├── mode_switch_app.{hpp,cpp}   # App subclass: taps, main loop, mode FSM
│   ├── synthetic_source.{hpp,cpp}  # ported gateware model
│   ├── mpf_features.{hpp,cpp}      # STFT + CSD + band-avg + SPD logm
│   ├── mlp.{hpp,cpp}               # 2-layer MLP + backprop + SGD
│   └── ring_buffer.hpp             # per-class feature store
├── config/
│   └── rhd2132_mode_switch.json    # kBroadbandSource(200) -> kApplication graph
└── client/
    ├── set_source_mode.py
    ├── set_capture.py
    ├── fit_mlp.py
    └── listen_class.py
```

---

## 7. Staged implementation

**Stage 0 — scaffold + build proof.** Create the app dir, submodules, Docker,
CMake, a trivial `App` that reads node 1 and republishes frames on
`broadband_out`. Goal: `synapsectl apps build` succeeds, `deploy` + `start` runs,
`broadband_out` streams. Resolves the two structural unknowns (BroadbandFrame tap
type; builder linalg) early.

**Stage 1 — source toggle + synthetic.** Add `set_source_mode` consumer tap and
the ported synthetic generator; verify live real↔synthetic toggle on the ID-200
chain, with a client script and a host capture.

**Stage 2 — labeled ring buffer.** Add `set_capture` tap + per-class buffers +
count logging. Verify labels route windows correctly.

**Stage 3 — MPF features.** Add STFT/CSD/SPD-logm; validate feature dim and
numerical sanity offline against a small reference (compare a window's features
to a NumPy/`generic-neuromotor-interface` recomputation). Start with a reduced
channel subset / bands.

**Stage 4 — MLP fit/infer.** Add the hand-rolled MLP + `fit_mlp` trigger +
`class_out`; verify it learns separable synthetic classes (e.g. distinct
synthetic seeds/patterns per label) end-to-end.

Each stage builds, deploys, and is verified on the bench before the next.

---

## 8. Open questions to resolve during implementation

- [ ] **(build-time)** Is `create_tap<synapse::BroadbandFrame>` allowed, or must
      `broadband_out` be a `Tensor`/`Timeseries`? Coded as `BroadbandFrame` with a
      documented fallback at the call site; the Docker build will confirm.
- [x] Linear-algebra/eigensolver for `logm`: hand-rolled cyclic-Jacobi Hermitian
      eigensolver in `mpf_features.cpp`; no `eigen3` dependency added.
- [x] `google::protobuf::ListValue` is used for all three consumer taps, matching
      the example app's `set_cursor_channels` pattern.
- [x] Feature dimension: config-driven; shipped default is an 8-ch subset with
      `num_bands=4` → 256 features (full 32-ch/8-band is `8·1024 = 8192`).
- [x] App `name` = `"broadband-mode-switch"` in both `manifest.json` and
      `config/rhd2132_mode_switch.json` `application.name`.
- [ ] **(bench)** On-device training cost of a 256→64→64→K MLP over
      `ring_capacity` samples — measure; `fit` runs in `main()` off a request
      flag today (move to a worker thread if it stalls the loop).

---

## 9. Related documents

- `docs/broadband-mode-switch-app-plan.md` — narrative design (same content).
- `docs/rhd2132-gateware-plan.md` — the separate (optional/long-term) custom
  RHD2132 SPI-master gateware track.
- `config/axon-omnetics-32ch-broadband.json` — working ID-200 broadband config.
- `TODO.md` — milestone/log; `MISTAKES.md` — electrode-map + Windows-UTF-8 lessons.
