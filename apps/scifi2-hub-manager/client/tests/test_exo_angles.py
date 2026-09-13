import struct
from types import SimpleNamespace
import pytest
from scifi2_hub_manager.exo_angles import decode_angles


def _torque_halves(value):
    bits = struct.unpack("<I", struct.pack("<f", value))[0]
    return bits & 0xFFFF, (bits >> 16) & 0xFFFF


def test_measured_and_missing_motor_decode_angle_current_torque():
    # Motor 11: angle -12.3 deg (measured), 7 ms old, 110 mA, torque 0.1265 N*m.
    # Motor 12: angle unavailable, current unavailable, torque NaN marker.
    t_lo, t_hi = _torque_halves(0.1265)
    frame = SimpleNamespace(
        frame_data=[-123, 7, 0, 0, 110, 0, t_lo - 0x10000 if t_lo > 0x7FFF else t_lo,
                    t_hi - 0x10000 if t_hi > 0x7FFF else t_hi,
                    -32768, 2, 0, 1, 0, 1, -1, -1],
        timestamp_ns=5000000000, unix_timestamp_ns=9000000000,
        sample_rate_hz=10, sequence_number=77)
    decoded = decode_angles(frame, [11, 12])
    m0, m1 = decoded["motors"]
    assert m0["absolute_degrees"] == -12.3
    assert m0["source_sample_timestamp_ns"] == 4993000000
    assert m0["current_mA"] == 110 and m0["current_valid"] is True
    assert m0["torque_Nm"] is not None and abs(m0["torque_Nm"] - 0.1265) < 1e-4
    assert m1["absolute_degrees"] is None
    assert m1["current_mA"] is None and m1["torque_Nm"] is None
    assert decoded["scifi_receive_steady_ns"] != decoded["source_timestamp_ns"]
    assert decoded["synchronization"] == "unaligned"


def test_inconsistent_angle_validity_is_rejected():
    frame = SimpleNamespace(
        frame_data=[-32768, 7, 0, 0, 0, 0, -1, -1] + [0, 0, 0, 0, 0, 0, -1, -1],
        timestamp_ns=5000000000, unix_timestamp_ns=9000000000,
        sample_rate_hz=10, sequence_number=77)
    with pytest.raises(ValueError, match="validity"):
        decode_angles(frame, [11, 12])


def test_wrong_width_is_rejected():
    frame = SimpleNamespace(frame_data=[0, 0, 0, 0], timestamp_ns=1,
                            unix_timestamp_ns=1, sample_rate_hz=10, sequence_number=1)
    with pytest.raises(ValueError, match="channel"):
        decode_angles(frame, [11])
