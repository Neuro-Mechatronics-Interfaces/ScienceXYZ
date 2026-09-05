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
    def gpio_frame(self, sequence, timestamp, levels):
        frame = BroadbandFrame(sequence_number=sequence, timestamp_ns=timestamp,
                               sample_rate_hz=20000, frame_data=[0] * 32 + levels)
        frame.channel_ranges.add(type=ChannelType.ELECTRODE, count=32)
        frame.channel_ranges.add(type=ChannelType.GPIO, count=2, channel_ids=[0, 1])
        return frame

    def test_gpio_one_edges_independent_of_gpio_zero(self):
        stats = FrameStats()
        for i, value in enumerate([0, 1, 1, 0, 1]):
            stats.add(self.gpio_frame(i, 1000 + i * 50000, [0, value]))
        self.assertEqual(stats.gpio[0]["rises"], 0)
        self.assertEqual(stats.gpio[1]["rises"], 2)
        self.assertEqual(stats.gpio[1]["falls"], 1)
        self.assertEqual(stats.gpio[1]["positions"], {33})
        self.assertEqual(stats.gpio[1]["edge_delta_samples_min"], 1)
        self.assertEqual(stats.gpio[1]["edge_delta_samples_max"], 2)

    def test_gpio_gap_not_counted_as_edge_or_interval(self):
        stats = FrameStats()
        for seq, levels in [(0, [0, 0]), (1, [1, 1]), (3, [0, 0]), (4, [1, 1])]:
            stats.add(self.gpio_frame(seq, 1000 + seq * 50000, levels))
        self.assertEqual(stats.gpio[0]["rises"], 2)
        self.assertEqual(stats.gpio[0]["falls"], 0)
        self.assertEqual(stats.gpio[0]["intervals"], 0)

    def test_nonbinary_changes_are_distinct_from_boolean_edges(self):
        stats = FrameStats()
        for i, value in enumerate([2, 3, 2]):
            stats.add(self.gpio_frame(i, 1000 + i * 50000, [0, value]))
        self.assertEqual(stats.gpio[1]["value_changes"], 2)
        self.assertEqual(stats.gpio[1]["rises"], 0)
        self.assertEqual(stats.gpio[1]["nonbinary_samples"], 3)

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
