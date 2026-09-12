"""Decode the Exo 0.2 angle source; no interpolation or clock alignment."""
from __future__ import annotations


def decode_angles(frame, motor_ids):
    ids = list(motor_ids)
    if not ids or len(ids) > 18 or len(set(ids)) != len(ids):
        raise ValueError("select 1..18 distinct Dynamixel motor IDs")
    width = 4 * len(ids)
    if len(frame.frame_data) != width:
        raise ValueError(f"expected one {width}-channel Exo frame")
    result = []
    for i, motor in enumerate(ids):
        angle, low, high, status = frame.frame_data[4*i:4*i+4]
        if not all(-32768 <= v <= 32767 for v in (angle, low, high, status)):
            raise ValueError("Exo fields must be signed 16-bit values")
        age_ms = (low & 65535) | ((high & 65535) << 16)
        valid = status == 0 and angle != -32768
        if status not in (0, 1) or (status == 0) != (angle != -32768):
            raise ValueError("inconsistent Exo validity fields")
        if age_ms * 1_000_000 > frame.timestamp_ns:
            raise ValueError("sample age exceeds source uptime")
        result.append({"motor_id": motor, "absolute_degrees": angle / 10 if valid else None,
                       "valid": valid, "source_sample_timestamp_ns": frame.timestamp_ns - age_ms * 1_000_000,
                       "sample_age_ms": age_ms, "status": status})
    return {"sequence": frame.sequence_number, "source_timestamp_ns": frame.timestamp_ns,
            "scifi_receive_steady_ns": frame.unix_timestamp_ns,
            "nominal_poll_rate_hz": frame.sample_rate_hz, "synchronization": "unaligned",
            "motors": result}
