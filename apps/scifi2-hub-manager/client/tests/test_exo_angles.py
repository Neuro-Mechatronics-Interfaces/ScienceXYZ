from types import SimpleNamespace
import pytest
from scifi2_hub_manager.exo_angles import decode_angles


def test_measured_angle_and_missing_motor_keep_source_times():
    frame = SimpleNamespace(frame_data=[-123, 7, 0, 0, -32768, 2, 0, 1],
                            timestamp_ns=5000000000, unix_timestamp_ns=9000000000,
                            sample_rate_hz=10, sequence_number=77)
    decoded = decode_angles(frame, [11, 12])
    assert decoded["motors"][0]["absolute_degrees"] == -12.3
    assert decoded["motors"][0]["source_sample_timestamp_ns"] == 4993000000
    assert decoded["motors"][1]["absolute_degrees"] is None
    assert decoded["scifi_receive_steady_ns"] != decoded["source_timestamp_ns"]
    assert decoded["synchronization"] == "unaligned"
    frame.frame_data[0] = -32768
    with pytest.raises(ValueError, match="validity"):
        decode_angles(frame, [11, 12])
