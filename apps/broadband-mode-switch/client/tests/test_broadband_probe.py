import unittest

from broadband_probe import FrameStats, parse_frame
from synapse.api.channel_pb2 import ChannelType
from synapse.api.datatype_pb2 import BroadbandFrame


def encoded_frame(sequence, timestamp, *, sample_rate=20_000, channels=8):
    frame = BroadbandFrame(
        timestamp_ns=timestamp,
        sequence_number=sequence,
        frame_data=list(range(channels)),
        sample_rate_hz=sample_rate,
    )
    channel_range = frame.channel_ranges.add(type=ChannelType.ELECTRODE, count=channels)
    return frame.SerializeToString(), channel_range


class BroadbandProbeTests(unittest.TestCase):
    def test_counts_gaps_and_metadata(self):
        stats = FrameStats()
        for sequence, timestamp in ((100, 1_000), (101, 51_000), (103, 101_000)):
            raw, _ = encoded_frame(sequence, timestamp)
            self.assertTrue(parse_frame(raw, stats))

        self.assertEqual(stats.frames, 3)
        self.assertEqual(stats.missing_sequences, 1)
        self.assertEqual(stats.non_monotonic_sequences, 0)
        self.assertEqual(stats.timestamp_regressions, 0)
        self.assertEqual(stats.sample_rates_hz, {20_000})
        self.assertEqual(stats.channel_counts, {8})
        self.assertEqual(stats.channel_ranges, ("ELECTRODE:8",))
        self.assertEqual(stats.mean_timestamp_delta_ns, 50_000)

    def test_counts_regressions_and_malformed_payload(self):
        stats = FrameStats()
        for sequence, timestamp in ((1, 1_000), (1, 900), (2, 2_000)):
            raw, _ = encoded_frame(sequence, timestamp)
            self.assertTrue(parse_frame(raw, stats))

        self.assertFalse(parse_frame(b"not-a-protobuf-frame", stats))
        self.assertEqual(stats.frames, 3)
        self.assertEqual(stats.non_monotonic_sequences, 1)
        self.assertEqual(stats.timestamp_regressions, 1)
        self.assertEqual(stats.parse_errors, 1)


if __name__ == "__main__":
    unittest.main()
