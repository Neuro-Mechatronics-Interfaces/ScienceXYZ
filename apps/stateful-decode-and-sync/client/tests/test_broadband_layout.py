"""Broadband channel layout is derived from the device config, not the wire."""
import json
import tempfile
import unittest
from pathlib import Path

from stateful_decode_and_sync.broadband_layout import layouts_from_config


def _source(node_id, groups):
    """A kBroadbandSource node with {group_name: n_channels}."""
    signal = {g: {"channels": [{"id": i} for i in range(n)]} for g, n in groups.items()}
    return {"type": "kBroadbandSource", "id": node_id,
            "broadbandSource": {"peripheral_id": 200, "sample_rate_hz": 20000, "signal": signal}}


class BroadbandLayoutTests(unittest.TestCase):
    def test_electrode_names_and_count_from_config(self):
        cfg = {"nodes": [_source(1, {"electrode": 34})]}
        (layout,) = layouts_from_config(cfg)
        self.assertEqual(layout.node_id, 1)
        self.assertEqual(layout.n_ch, 34)
        self.assertEqual(layout.sample_rate_hz, 20000)
        self.assertEqual(layout.output_layout[0], "electrode_0")
        self.assertEqual(layout.output_layout[-1], "electrode_33")
        self.assertIn("kBroadbandSource", layout.config_json)

    def test_mixed_groups_are_typed_and_ordered(self):
        # electrode then gpio -> electrode_0..1, gpio_0..2 in frame_data order.
        cfg = {"nodes": [_source(2, {"electrode": 2, "gpio": 3})]}
        (layout,) = layouts_from_config(cfg)
        self.assertEqual(layout.output_layout,
                         ["electrode_0", "electrode_1", "gpio_0", "gpio_1", "gpio_2"])
        self.assertEqual(layout.channel_types, ["electrode", "electrode", "gpio", "gpio", "gpio"])

    def test_multiple_sources_each_have_a_layout(self):
        cfg = {"nodes": [_source(1, {"electrode": 32}), _source(7, {"electrode": 8})]}
        layouts = layouts_from_config(cfg)
        self.assertEqual([l.node_id for l in layouts], [1, 7])
        self.assertEqual([l.n_ch for l in layouts], [32, 8])

    def test_reads_from_provenance_snapshot(self):
        prov = {"configuration_input": {"snapshot": {"nodes": [_source(1, {"electrode": 4})]}}}
        (layout,) = layouts_from_config(prov)
        self.assertEqual(layout.n_ch, 4)

    def test_no_broadband_source_yields_empty(self):
        self.assertEqual(layouts_from_config({"nodes": [{"type": "kApplication"}]}), [])
        self.assertEqual(layouts_from_config({}), [])


class MultiSourceWriterTests(unittest.TestCase):
    def test_two_sources_write_separate_device_groups(self):
        import h5py
        from stateful_decode_and_sync.cognescent_writer import CognescentWriter
        with tempfile.TemporaryDirectory() as temp:
            w = CognescentWriter(Path(temp) / "data.hdf5")
            w.open(metadata={"session": {"task_name": "T"}})
            w.add_broadband_device("a", output_layout=["electrode_0", "electrode_1"],
                                    sample_rate_hz=20000, config_json='{"src":"a"}')
            w.add_broadband_device("b", output_layout=["gpio_0"], sample_rate_hz=1000,
                                   config_json='{"src":"b"}')
            w.append_broadband("a", [1.0, 1.0], [1.0, 1.0], [[10, 11], [12, 13]])
            w.append_broadband("b", [2.0], [2.0], [[99]])
            w.close()
            with h5py.File(Path(temp) / "data.hdf5", "r") as f:
                # /devices/0 browser, /devices/1 source a, /devices/2 source b
                self.assertEqual(f["devices/0"].attrs["uid"], "browser")
                self.assertEqual(f["devices/1"].attrs["n_ch"], 2)
                self.assertEqual(f["devices/2"].attrs["n_ch"], 1)
                self.assertEqual(json.loads(f["devices/1"].attrs["output_layout"]),
                                 ["electrode_0", "electrode_1"])
                self.assertEqual(f["devices/1"].attrs["config"], '{"src":"a"}')
                self.assertEqual(f["devices/1"]["timeseries/stream"].shape, (2, 2))
                self.assertEqual(f["devices/2"]["timeseries/stream"].shape, (1, 1))


if __name__ == "__main__":
    unittest.main()
