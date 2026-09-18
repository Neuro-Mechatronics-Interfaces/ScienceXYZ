"""Generate the deterministic hls4ml/Radiant canary RTL used by integration tests."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import hls4ml
from hls4ml.converters import convert_from_keras_model


def build_model(seed: int = 1234):
    import keras

    model = keras.Sequential(
        [
            keras.layers.Input(shape=(8,), name="x_in"),
            keras.layers.Dense(16, name="d1"),
            keras.layers.Activation("relu", name="r1"),
            keras.layers.Dense(8, name="d2"),
            keras.layers.Activation("relu", name="r2"),
            keras.layers.Dense(4, name="d3"),
        ]
    )
    rng = np.random.default_rng(seed)
    for layer in model.layers:
        weights = layer.get_weights()
        if weights:
            kernel = np.round(rng.uniform(-0.5, 0.5, weights[0].shape), 3).astype(np.float32)
            bias = np.round(rng.uniform(-0.25, 0.25, weights[1].shape), 3).astype(np.float32)
            layer.set_weights([kernel, bias])
    return model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--clock-period", type=float, default=25.0)
    args = parser.parse_args()
    model = build_model()
    config = hls4ml.utils.config_from_keras_model(
        model,
        granularity="name",
        default_precision="ap_fixed<8,4>",
        backend="Radiant",
    )
    hls_model = convert_from_keras_model(
        model,
        hls_config=config,
        output_dir=str(args.output_dir),
        backend="Radiant",
        part="LIFCL-17-9SG72C",
        clock_period=args.clock_period,
    )
    hls_model.write()
    hls_model.compile()
    sv_path = Path(hls_model.build(synth=False))
    # XLS emits unpacked-array whole-array nonblocking assignments. Radiant and
    # Verilator accept those directly; Icarus does not. Keep the generated
    # arithmetic/pipeline intact while making the two register banks portable.
    sv = sv_path.read_text()
    sv = sv.replace(
        "    x_in_bits__input_flop <= x_in_bits_unflattened;",
        "    for (integer i = 0; i < 8; i = i + 1)\n"
        "      x_in_bits__input_flop[i] <= x_in_bits_unflattened[i];",
    )
    sv = sv.replace(
        "    out__output_flop <= p1_layer6_out_bits_comb;",
        "    for (integer o = 0; o < 4; o = o + 1)\n"
        "      out__output_flop[o] <= p1_layer6_out_bits_comb[o];",
    )
    sv_path.write_text(sv)
    print(sv_path)


if __name__ == "__main__":
    main()
