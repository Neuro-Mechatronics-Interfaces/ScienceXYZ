"""Tests for the hub-and-spoke calibration profile generator (T-42).

Covers generation correctness, the cross-language canonical-hash fixture shared
with the C++ parser, contract boundary cases, and load_profile dispatch for both
the linear MVP and hub-and-spoke shapes.
"""
import json
from pathlib import Path
import re
import tempfile
import unittest

from scifi2_hub_manager.calibration_task import load_profile, make_profile
from scifi2_hub_manager import motion_profile
from scifi2_hub_manager.motion_profile import (
    MAX_OUTGOING_TRANSITIONS,
    MAX_STATES,
    NAME_RE,
    definition_hash,
    load_motion_lut,
    make_hub_spoke_profile,
    validate_profile,
)

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "hub_spoke_definition.json"


class HubSpokeGeneratorTests(unittest.TestCase):
    def test_two_gesture_topology_and_naming(self):
        profile = make_hub_spoke_profile(["Fist", "Paper"])
        definition = profile["definition"]
        self.assertEqual(profile["profile_shape"], "hub_and_spoke")
        # rest hub id 1 plus one spoke per gesture.
        self.assertEqual([(s["id"], s["name"]) for s in definition["states"]],
                         [(1, "rest"), (2, "hand_squeeze"), (3, "hand_extend")])
        self.assertEqual(definition["initial_state_id"], 1)
        self.assertFalse(any(s["terminal"] for s in definition["states"]))
        # Two transitions per gesture: rest->g (to_active) and g->rest (to_rest).
        events = {t["trigger"]["event_name"]: (t["from_state_id"], t["to_state_id"])
                  for t in definition["transitions"]}
        self.assertEqual(events["to_active.hand_squeeze"], (1, 2))
        self.assertEqual(events["to_rest.hand_squeeze"], (2, 1))
        self.assertEqual(events["to_active.hand_extend"], (1, 3))
        self.assertEqual(events["to_rest.hand_extend"], (3, 1))

    def test_event_and_state_names_are_contract_legal(self):
        # Every gesture key in the LUT must produce contract-legal names.
        lut = load_motion_lut()["lut"]
        profile = make_hub_spoke_profile(list(lut)[:MAX_OUTGOING_TRANSITIONS])
        for state in profile["definition"]["states"]:
            self.assertRegex(state["name"], NAME_RE)
        for transition in profile["definition"]["transitions"]:
            self.assertRegex(transition["name"], NAME_RE)
            self.assertRegex(transition["trigger"]["event_name"], NAME_RE)
            # A colon would fail the device valid_name check.
            self.assertNotIn(":", transition["trigger"]["event_name"])

    def test_labels_classes_and_instructions(self):
        profile = make_hub_spoke_profile(["Fist", "Paper", "ThumbTap"])
        self.assertEqual(profile["num_classes"], 3)
        # rest carries no decoder class; gestures are 0-based contiguous.
        self.assertEqual(profile["state_to_class"], {"1": None, "2": 0, "3": 1, "4": 2})
        # state_to_label uses the motion id; rest keeps its name.
        self.assertEqual(profile["state_to_label"],
                         {"1": "rest", "2": "hand_squeeze", "3": "hand_extend", "4": "thumb_tap"})
        # Human instructions come from MOTION_CATALOG labels where present.
        self.assertEqual(profile["instructions"]["1"], "Rest")
        self.assertEqual(profile["instructions"]["4"], "Thumb Tap")

    def test_unique_priorities_leaving_rest(self):
        profile = make_hub_spoke_profile(["Fist", "Paper", "ThumbTap"])
        rest_priorities = [t["priority"] for t in profile["definition"]["transitions"]
                           if t["from_state_id"] == 1]
        self.assertEqual(len(rest_priorities), len(set(rest_priorities)))
        self.assertTrue(all(1 <= p <= 255 for p in rest_priorities))

    def test_single_gesture_minimum(self):
        profile = make_hub_spoke_profile(["Fist"])
        validate_profile(profile)
        self.assertEqual(len(profile["definition"]["states"]), 2)
        self.assertEqual(len(profile["definition"]["transitions"]), 2)

    def test_num_classes_five_boundary(self):
        keys = ["Fist", "Paper", "ThumbTap", "IndexFlexion", "IndexExtension"]
        profile = make_hub_spoke_profile(keys)
        self.assertEqual(profile["num_classes"], 5)
        self.assertEqual(sorted(v for v in profile["state_to_class"].values() if v is not None),
                         [0, 1, 2, 3, 4])

    def test_max_outgoing_gesture_cap(self):
        lut_keys = list(load_motion_lut()["lut"])
        at_cap = lut_keys[:MAX_OUTGOING_TRANSITIONS]
        profile = make_hub_spoke_profile(at_cap)
        rest_outgoing = sum(1 for t in profile["definition"]["transitions"]
                            if t["from_state_id"] == 1)
        self.assertEqual(rest_outgoing, MAX_OUTGOING_TRANSITIONS)
        with self.assertRaises(ValueError):
            make_hub_spoke_profile(lut_keys[:MAX_OUTGOING_TRANSITIONS + 1])

    def test_duplicate_and_unknown_keys_rejected(self):
        with self.assertRaises(ValueError):
            make_hub_spoke_profile(["Fist", "Fist"])
        with self.assertRaises(ValueError):
            make_hub_spoke_profile(["NotAGesture"])
        with self.assertRaises(ValueError):
            make_hub_spoke_profile([])

    def test_hash_is_order_independent_but_content_sensitive(self):
        a = make_hub_spoke_profile(["Fist", "Paper"])
        # Reordering the states/transitions arrays must not change the hash.
        shuffled = json.loads(json.dumps(a["definition"]))
        shuffled["states"].reverse()
        shuffled["transitions"].reverse()
        self.assertEqual(definition_hash(shuffled), a["definition_hash"])
        # A different gesture order is a different task and hashes differently.
        b = make_hub_spoke_profile(["Paper", "Fist"])
        self.assertNotEqual(b["definition_hash"], a["definition_hash"])


class CrossLanguageFixtureTests(unittest.TestCase):
    def test_fixture_matches_generator(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))
        regenerated = make_hub_spoke_profile(fixture["gesture_keys"])
        self.assertEqual(regenerated["definition"], fixture["task_definition"])
        self.assertEqual(regenerated["definition_hash"], fixture["expected_definition_hash"])

    def test_fixture_hash_recomputes(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))
        self.assertEqual(definition_hash(fixture["task_definition"]),
                         fixture["expected_definition_hash"])


class ValidateProfileTests(unittest.TestCase):
    def test_tampered_hash_is_rejected(self):
        profile = make_hub_spoke_profile(["Fist", "Paper"])
        profile["definition"]["revision"] = 2  # hash no longer matches
        with self.assertRaises(ValueError):
            validate_profile(profile)

    def test_terminal_state_is_rejected(self):
        profile = make_hub_spoke_profile(["Fist"])
        profile["definition"]["states"][1]["terminal"] = True
        profile["definition_hash"] = definition_hash(profile["definition"])
        with self.assertRaises(ValueError):
            validate_profile(profile)

    def test_missing_return_edge_is_rejected(self):
        profile = make_hub_spoke_profile(["Fist"])
        # Drop the gesture->rest transition, leaving a spoke with no way back.
        profile["definition"]["transitions"] = [
            t for t in profile["definition"]["transitions"] if t["to_state_id"] != 1]
        profile["definition_hash"] = definition_hash(profile["definition"])
        with self.assertRaises(ValueError):
            validate_profile(profile)


class LoadProfileDispatchTests(unittest.TestCase):
    def test_hub_spoke_round_trip(self):
        profile = make_hub_spoke_profile(["Fist", "Paper", "ThumbTap"])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "profile.json"
            path.write_text(json.dumps(profile))
            loaded = load_profile(str(path))
        self.assertEqual(loaded["definition_hash"], profile["definition_hash"])

    def test_linear_mvp_still_loads(self):
        # The generalized load_profile must not break the linear MVP profile.
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "linear.json"
            path.write_text(json.dumps(make_profile()))
            loaded = load_profile(str(path))
        self.assertEqual(loaded["definition_hash"], make_profile()["definition_hash"])


if __name__ == "__main__":
    unittest.main()
