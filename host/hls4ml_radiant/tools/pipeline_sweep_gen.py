#!/usr/bin/env python3
"""Regenerate the canary MLP at a range of requested clock periods and report
the XLS pipeline depth for each.

The XLS backend schedules the combinational network into pipeline stages to
meet ``clock_period_ps`` under the ``asap7`` delay model (a 7 nm ASIC library).
That model is far more optimistic than Lattice Nexus LUT4 fabric, so the target
period must be requested tighter than the real 25 ns to force XLS to insert
internal registers. This tool sweeps the requested period, generates RTL for
each, counts the emitted pipeline stages (``pN_`` signal prefixes), and writes
each generated ``.sv`` to an output directory for downstream Radiant synthesis.

Runs under WSL with the hls4ml-radiant venv (xls-python is Linux-only).
Generation only -- no Radiant, no device.
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

import numpy as np


def tiny_dense_model(seed: int = 1234):
    import keras

    model = keras.Sequential(
        [
            keras.layers.Input(shape=(8,), name='x_in'),
            keras.layers.Dense(16, name='d1'),
            keras.layers.Activation('relu', name='r1'),
            keras.layers.Dense(8, name='d2'),
            keras.layers.Activation('relu', name='r2'),
            keras.layers.Dense(4, name='d3'),
        ]
    )
    rng = np.random.default_rng(seed)
    for layer in model.layers:
        w = layer.get_weights()
        if not w:
            continue
        kernel = np.round(rng.uniform(-0.5, 0.5, w[0].shape), 3).astype(np.float32)
        bias = np.round(rng.uniform(-0.25, 0.25, w[1].shape), 3).astype(np.float32)
        layer.set_weights([kernel, bias])
    return model


def stage_count(sv_text: str) -> int:
    """Highest pN_ pipeline-stage index present, i.e. registered-boundary count."""
    stages = {int(m) for m in re.findall(r'\bp(\d+)_', sv_text)}
    return (max(stages) + 1) if stages else 0


def generate(period_ns: float, precision: str, out_dir: Path, part: str) -> dict:
    import hls4ml

    model = tiny_dense_model()
    cfg = hls4ml.utils.config_from_keras_model(
        model, granularity='name', default_precision=precision, backend='Radiant'
    )
    prj = out_dir / f'prj_{period_ns:g}ns'
    if prj.exists():
        shutil.rmtree(prj)
    hls_model = hls4ml.converters.convert_from_keras_model(
        model,
        hls_config=cfg,
        output_dir=str(prj),
        backend='Radiant',
        part=part,
        clock_period=period_ns,
    )
    hls_model.write()
    hls_model.compile()
    # Generation only: emit the XLS-scheduled SystemVerilog without invoking
    # radiantc (Linux Radiant is absent in WSL; synthesis runs on Windows).
    hls_model.build(synth=False)

    # Locate the generated top .sv (XLS default project name is 'myproject').
    candidates = list(prj.glob('firmware/*.sv'))
    if not candidates:
        candidates = list(prj.rglob('*.sv'))
    top_sv = max(candidates, key=lambda p: p.stat().st_size)
    text = top_sv.read_text()
    n_stages = stage_count(text)

    dest = out_dir / f'canary_{period_ns:g}ns.sv'
    dest.write_text(text)
    return {
        'period_ns': period_ns,
        'precision': precision,
        'stages': n_stages,
        'sv': str(dest),
        'lines': text.count('\n') + 1,
        'always_blocks': len(re.findall(r'\balways_ff\b|\balways\b', text)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--periods', type=float, nargs='+',
                    default=[12.5, 6.0, 4.0, 3.0, 2.0, 1.5, 1.0])
    ap.add_argument('--precision', default='ap_fixed<8,4>')
    ap.add_argument('--part', default='LIFCL-17-9SG72C')
    ap.add_argument('--out', default=str(Path(__file__).resolve().parent.parent / 'synthesis' / 'sweep'))
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for p in args.periods:
        try:
            row = generate(p, args.precision, out_dir, args.part)
        except Exception as exc:  # noqa: BLE001 - report and continue the sweep
            row = {'period_ns': p, 'precision': args.precision, 'stages': None,
                   'error': f'{type(exc).__name__}: {exc}'}
        rows.append(row)
        print(f"[gen] period={p:>5g} ns  stages={row.get('stages')}  "
              f"{row.get('error', row.get('sv',''))}")

    print('\n=== summary ===')
    print(f"{'period_ns':>10} {'stages':>7} {'sv'}")
    for r in rows:
        print(f"{r['period_ns']:>10g} {str(r.get('stages')):>7} {r.get('sv', r.get('error',''))}")


if __name__ == '__main__':
    main()
