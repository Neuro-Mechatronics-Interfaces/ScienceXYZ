"""Decode the Exo 0.3 angle/current/torque source; no interpolation or clock alignment.

Schema 0xF212 packs eight signed-16-bit fields per motor, in order:
angle (0.1 deg), age_lo, age_hi, angle_status, current_mA, current_status,
torque_lo, torque_hi. Torque is a little-endian float32 split across the last
two fields; the pair 0xFFFF/0xFFFF (NaN payload) marks it unavailable. Each
measurement carries its own validity so a failed current read does not discard a
good angle. This is read-only telemetry; nothing here commands motion.
"""
from __future__ import annotations

import struct

FIELDS_PER_MOTOR = 8


def _torque_from_halves(low: int, high: int):
    """Reconstruct the little-endian float32 torque from its two int16 halves.

    Returns None when the pair is the all-ones (NaN) unavailable marker or the
    reinterpreted value is not finite.
    """
    bits = (low & 0xFFFF) | ((high & 0xFFFF) << 16)
    if bits == 0xFFFFFFFF:
        return None
    value = struct.unpack("<f", struct.pack("<I", bits))[0]
    return value if value == value and abs(value) != float("inf") else None


def decode_angles(frame, motor_ids):
    ids = list(motor_ids)
    if not ids or len(ids) > 18 or len(set(ids)) != len(ids):
        raise ValueError("select 1..18 distinct Dynamixel motor IDs")
    width = FIELDS_PER_MOTOR * len(ids)
    if len(frame.frame_data) != width:
        raise ValueError(f"expected one {width}-channel Exo frame")
    result = []
    for i, motor in enumerate(ids):
        base = FIELDS_PER_MOTOR * i
        (angle, low, high, angle_status, current_mA, current_status,
         torque_lo, torque_hi) = frame.frame_data[base:base + FIELDS_PER_MOTOR]
        block = (angle, low, high, angle_status, current_mA, current_status,
                 torque_lo, torque_hi)
        if not all(-32768 <= v <= 32767 for v in block):
            raise ValueError("Exo fields must be signed 16-bit values")
        age_ms = (low & 65535) | ((high & 65535) << 16)
        angle_valid = angle_status == 0 and angle != -32768
        if angle_status not in (0, 1) or (angle_status == 0) != (angle != -32768):
            raise ValueError("inconsistent Exo angle validity fields")
        if current_status not in (0, 1):
            raise ValueError("inconsistent Exo current validity fields")
        if age_ms * 1_000_000 > frame.timestamp_ns:
            raise ValueError("sample age exceeds source uptime")
        current_valid = current_status == 0
        torque_Nm = _torque_from_halves(torque_lo, torque_hi)
        result.append({
            "motor_id": motor,
            "absolute_degrees": angle / 10 if angle_valid else None,
            "valid": angle_valid,
            "source_sample_timestamp_ns": frame.timestamp_ns - age_ms * 1_000_000,
            "sample_age_ms": age_ms,
            "status": angle_status,
            "current_mA": current_mA if current_valid else None,
            "current_valid": current_valid,
            "current_status": current_status,
            "torque_Nm": torque_Nm if current_valid else None,
        })
    return {"sequence": frame.sequence_number, "source_timestamp_ns": frame.timestamp_ns,
            "scifi_receive_steady_ns": frame.unix_timestamp_ns,
            "nominal_poll_rate_hz": frame.sample_rate_hz, "synchronization": "unaligned",
            "motors": result}
