# broadband-mode-switch App — Design (plan before code)

Status: **design locked, implementation pending.** A new on-device Synapse App
under `apps/broadband-mode-switch/`, modelled on `apps/synapse-example-app`
(App SDK v0.6.3). Written 2026-08-26.

## Purpose

A `kApplication` node that:

1. **Source toggle** — passes through the real RHD2132 broadband (ID 200) or, on
   command, substitutes an in-app **synthetic** broadband source (the gateware
   test-source LFP+spike+noise model ported to C++), toggled live via a consumer
   tap. Result is republished on a producer tap so downstream consumers see one
   stream that is either real or synthetic.
2. **Labeled ring buffer** — N discrete category buffers; a consumer tap sets the
   active category and a capture-enable flag, so windows of features can be
   appended to the currently-selected class buffer for supervised data collection.
3. **MPF featurizer** — the Kaifosh et al. 2025 (Nature, CTRL-labs/Reality Labs)
   **multivariate power-frequency** feature: channel-wise STFT → cross-spectral
   density → frequency-band averaging → SPD matrix logarithm. Reference:
   <https://github.com/facebookresearch/generic-neuromotor-interface>,
   <https://www.nature.com/articles/s41586-025-09255-w>. NOT the scalar
   mean/median power frequency of EMG-fatigue literature (same acronym, different
   thing).
4. **MLP fit + infer** — a trigger consumer tap trains a **2-layer MLP (hidden
   dim 64 per layer, 20% dropout between layers)**, softmax + cross-entropy,
   hand-rolled forward/backprop/SGD in C++, on the buffered (feature, label)
   pairs. After training, live windows are classified and the predicted class is
   published on a producer tap.

## Why these choices (settled with the user)

- New app (not a fork of the example) — clean separation from Science's example.
- Port the gateware synthetic model (not simple noise) — realistic, deterministic.
- Faithful MPF (STFT+CSD+SPD-log) — not the scalar variant.
- Hand-rolled MLP + backprop on-device (the SDK runs pretrained ONNX/QNN models
  but exposes no trainer) — self-contained fit; keep the net tiny.

## Node graph & taps

```
kBroadbandSource(id=1, peripheral_id=200, 20 kHz, 16-bit, 32 ch)
        │  (connection 1 -> 2)
        ▼
kApplication(id=2, name="broadband-mode-switch")
        │
        ├─ consumer tap  "set_source_mode"   ListValue [int mode]        (0=SAMPLING,1=SYNTHETIC)
        ├─ consumer tap  "set_capture"        ListValue [int label,int enable]
        ├─ consumer tap  "fit_mlp"            ListValue [int epochs?]     (trigger; payload optional)
        ├─ producer tap  "broadband_out"      BroadbandFrame             (real or synthetic passthrough)
        └─ producer tap  "class_out"          Tensor  (shape=[num_classes], softmax probs) + argmax
```

Consumer-tap callbacks run on their own threads (SDK). All shared state
(`mode_`, `active_label_`, `capture_enabled_`, the ring buffers, the model)
is guarded by `std::atomic` for scalars and `std::mutex` for the buffers/model,
per the SDK's threading note. Callbacks only set flags / enqueue; heavy work
(`fit_mlp`) runs in `main()` off a request flag so the callback returns fast.

## Config parameters (`ApplicationNodeConfig.parameters`, all `google.protobuf.Value`)

Window and stride are specified in **milliseconds** and converted to samples with
`sample_rate_hz` (`window_samples = round(window_ms * fs / 1000)`); we featurize
over each `window_ms` window and slide by `stride_ms`.

| Key | Type | Meaning | Default |
| --- | --- | --- | --- |
| `sample_rate_hz` | number | expected upstream rate (validate against frames) | 20000 |
| `num_classes` | number | ring-buffer categories / MLP outputs | 5 |
| `window_ms` | number | feature window length (ms); featurized as one MPF vector | 200 |
| `stride_ms` | number | decoder stride / hop between windows (ms) | 20 |
| `stft_size` | number | STFT length in samples (power of 2) | 256 |
| `stft_hop` | number | STFT hop within a window (samples) | 128 |
| `num_bands` | number | frequency bands averaged into CSD | 8 |
| `ring_capacity` | number | max feature windows stored per class | 2000 |
| `mlp_hidden` | number | hidden units per layer | 64 |
| `mlp_dropout` | number | dropout prob between layers | 0.2 |
| `mlp_lr` | number | SGD learning rate | 0.01 |
| `mlp_epochs` | number | training epochs per fit | 100 |
| `synthetic_seed` | number | LFSR/noise seed for reproducible synthetic mode | 0xACE1 |

`validate_config` requires the keys with no safe default; `parse_config` reads
the rest with fallbacks. Follows the example app's `get_app_config` pattern.
`window_ms`, `stride_ms`, `mlp_hidden`, `mlp_dropout`, `mlp_lr`, `mlp_epochs`,
`num_bands`, and `num_classes` are all runtime-configurable (the user's key
regularizer/architecture knobs). At the default 20 kHz, `window_ms=200` →
`window_samples=4000`, `stride_ms=20` → `stride_samples=400`; each 200 ms window
holds `(4000-256)/128 + 1 ≈ 30` STFT frames to average per band.

## Feature dimension (must be fixed before coding the MLP)

Let `C` = channel count (32), `B` = `num_bands`. The MPF feature per window:

1. Per channel `c`, STFT of the window → complex spectrogram `X_c[f, t]`.
2. For each frequency band `b`, form the `C×C` cross-spectral density
   (Hermitian, PSD): `S_b = mean_over_(f in band, t) X[:,f,t] X[:,f,t]^H`.
3. `S_b` is regularized (`S_b + εI`) and replaced by its **matrix logarithm**
   `L_b = logm(S_b)` (real symmetric after taking the real part / using the
   Hermitian eigenbasis).
4. Feature vector = concatenation over bands of the **upper triangle incl.
   diagonal** of each `L_b`: length `B · C(C+1)/2`.

For C=32, B=8: `8 · (32·33/2) = 8 · 528 = 4224` features/window. That is the MLP
input dimension. (This is large; `num_bands`/channel-subset are the knobs to keep
it tractable. Document the chosen dims in the manifest so the client matches.)

> Implementation note: `logm` of a Hermitian PSD matrix = `U diag(log λ) U^H` via
> symmetric eigendecomposition. Need a small dense eigensolver (Jacobi for
> symmetric, or pull `eigen3`/an SDK-provided linalg). Confirm what linear-algebra
> lib is available in the builder image before committing to a hand-rolled Jacobi.

## MLP

- Input `4224` → Dense(64) → ReLU → Dropout(0.2) → Dense(64) → ReLU →
  Dropout(0.2) → Dense(num_classes) → softmax.
- Loss: cross-entropy. Optimizer: SGD (optionally momentum). Dropout active only
  during `fit`; disabled at inference.
- Weights: `float`, Xavier/He init seeded from `synthetic_seed` for
  reproducibility. Store per-layer W/b; forward caches activations for backprop.
- `fit_mlp` trains on all buffered (feature,label) pairs across the N class
  buffers for `mlp_epochs`, logs loss/accuracy via spdlog, then flips an
  `model_ready_` atomic. Live inference publishes `class_out`.

## Ring buffer

`std::array<std::vector<Feature>, num_classes>` (or a fixed-capacity circular
buffer per class capped at `ring_capacity`, dropping oldest). `set_capture`
sets `active_label_` and `capture_enabled_`. When enabled, each computed feature
window is pushed to `buffers_[active_label_]`. Counts logged per class.

## Synthetic generator (ported from gateware)

Port `axon_omnetics32ch_source_peripheral.sv`'s model to C++: 256-entry sine LFP
LUT, 32-sample biphasic spike ROM, per-channel fixed delay LUT, 16-bit LFSR spike
trigger, 32-bit noise LFSR; amplitudes in counts==µV (get_lsb path). One sample
per channel per `main()` tick when in SYNTHETIC mode, emitted as a BroadbandFrame
with our own monotonic `sequence_number` and `timestamp_ns = start + seq*1e9/fs`.
Deterministic from `synthetic_seed`. In SAMPLING mode we forward the upstream
frame unchanged (preserving its timestamps/sequence — never overwrite source
timestamps, per AGENTS.md).

## BroadbandFrame contract (from datatype.proto)

`timestamp_ns`, `sequence_number`, `repeated sint32 frame_data` (one per channel,
= one time-point across all channels — NOT a multi-sample block), `sample_rate_hz`,
`channel_ranges`, `unix_timestamp_ns`. The example's ~2916 ns inter-frame dt was
host receipt jitter; the frame stamp itself is `start + (seq-start_seq)*1e9/fs` =
50000 ns at 20 kHz.

## File layout (new app)

```
apps/broadband-mode-switch/
├── CMakeLists.txt              # adapted from example (target name, sources)
├── cmake/protos.cmake          # copied verbatim
├── vcpkg.json                  # + any linalg dep (eigen3?) if used
├── Dockerfile                  # copied (SDK 0.6.3, shared-libs 1.3.0)
├── .gitmodules                 # synapse-api + vcpkg submodules
├── keys/science-repo-public.asc
├── src/
│   ├── mode_switch_app.{hpp,cpp}   # App subclass: taps, main loop, mode FSM
│   ├── synthetic_source.{hpp,cpp}  # ported gateware model
│   ├── mpf_features.{hpp,cpp}      # STFT + CSD + band-avg + SPD logm
│   ├── mlp.{hpp,cpp}               # 2-layer MLP + backprop + SGD
│   └── ring_buffer.hpp             # per-class feature store
├── config/
│   └── rhd2132_mode_switch.json    # kBroadbandSource(200) -> kApplication graph
└── client/
    ├── set_source_mode.py          # send mode toggle
    ├── set_capture.py              # send label + enable
    ├── fit_mlp.py                  # trigger training
    └── listen_class.py             # subscribe class_out
```

## Open items to confirm during implementation

- [ ] Linear-algebra availability in the builder image (eigensolver for `logm`).
      If none, add `eigen3` to vcpkg.json or hand-roll a symmetric Jacobi.
- [ ] Consumer-tap payload type: example uses `google::protobuf::ListValue`.
      Confirm that is the right control-message type for all three control taps;
      keep them uniform.
- [ ] Whether `create_tap<synapse::BroadbandFrame>` is allowed for `broadband_out`
      (example only shows `Tensor`); if BroadbandFrame taps are restricted, pack
      into Tensor or a Timeseries instead.
- [ ] Feature dimension is large (4224). Consider a channel subset or fewer bands
      for the first working version; make it config-driven so it is tunable.
- [ ] Deploy manifest / app name string must match `ApplicationNodeConfig.name`.
```
```
