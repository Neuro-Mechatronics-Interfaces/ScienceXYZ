"""Integer fixed-point reference for the checked-in 8->16->8->4 canary."""
from __future__ import annotations

# These are the signed significands emitted in firmware/layer_*.x. The XLS
# writer stores Dense weights as [output][input].
W1 = [[7, 3, 3, -7, -8, -7, -3, -4], [-2, -5, 6, -5, 0, -5, 5, 7],
      [6, -6, -6, 0, 1, 2, 7, -5], [-4, 5, 7, 3, -4, -6, -7, 1],
      [-3, -8, -6, -1, -1, 7, -4, -2], [-7, 2, 7, -4, 0, -6, 1, 4],
      [-5, 2, -7, 7, 7, 4, -4, -6], [-3, 1, -1, 4, -8, -1, -6, 7],
      [7, -8, 5, -1, -5, -4, -1, 3], [-4, 7, -4, -1, -4, -5, 4, -6],
      [-1, -1, 5, 3, -3, 6, 2, 3], [1, 0, 7, 5, -8, -2, -5, -7],
      [5, -8, 5, -4, -3, -7, 1, 0], [5, -4, -1, 3, -5, -8, -3, -7],
      [2, 5, -3, 4, 5, -1, -4, -8], [2, -2, -1, 6, 2, 5, 6, -2]]
B1 = [1, 1, 1, 0, 3, 0, -4, -3, 3, 2, -4, 3, 1, 3, -3, 0]
W2 = [[0, -4, -7, -1, 3, -7, 2, 3, 3, 3, -8, 7, 5, 3, 0, -2],
      [-6, 5, 0, -3, 2, 3, -8, -8, 2, -4, -6, 6, -1, 7, 0, -5],
      [-7, 6, 6, -8, 0, -1, -5, 4, 4, 7, 5, -3, -8, -2, 7, -1],
      [2, 3, -6, -7, -7, -4, -5, 4, 1, -6, 2, -3, -1, 4, -8, 5],
      [-8, 6, 7, 1, 5, 6, -5, -8, -5, -8, -3, -4, -6, 0, -8, 2],
      [-5, -8, 6, 2, 3, 4, 3, -7, -8, -1, -8, -6, -3, 5, -5, 4],
      [0, -8, -3, 7, 1, -1, 7, 0, 6, -3, -8, 1, 0, 0, -8, 5],
      [1, -7, -5, -8, 7, 2, -4, 4, -2, 2, -2, -1, 0, -2, -7, -6]]
B2 = [-4, -1, -1, 3, -2, -1, 0, 2]
W3 = [[-6, -1, 6, -4, -8, 1, 5, 2], [6, -8, 6, -3, -1, -1, 4, -2],
      [1, -4, -4, -3, -3, 3, 0, -5], [-1, 1, 0, -2, -4, -1, 7, -6]]
B3 = [-2, -3, -1, 2]


def _wrap(value: int, width: int) -> int:
    value &= (1 << width) - 1
    return value - (1 << width) if value & (1 << (width - 1)) else value


def _trunc16(value: int) -> int:
    return value // 16 if value >= 0 else -((-value) // 16)


def quantized_model_outputs(features: list[int]) -> list[int]:
    """Return final Q8 output significands for eight Q4 input bytes."""
    x = [_wrap(v, 8) for v in features]
    for weights, biases in ((W1, B1), (W2, B2)):
        dense = [sum(x[i] * weights[j][i] for i in range(len(x))) + biases[j] * 16
                 for j in range(len(weights))]
        x = [_wrap(max(0, _trunc16(v)), 8) for v in dense]
    return [_wrap(sum(x[i] * W3[j][i] for i in range(len(x))) + B3[j] * 16, 20)
            for j in range(4)]


def feature_bytes(samples: list[int]) -> list[int]:
    return [min(abs(v if v < 32768 else v - 65536), 255) for v in samples[:8]]
