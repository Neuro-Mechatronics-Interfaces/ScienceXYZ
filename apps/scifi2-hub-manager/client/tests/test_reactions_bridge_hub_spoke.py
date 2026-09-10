"""Hub-and-spoke animation-event translation in the Reactions bridge (T-45).

The band-calibration page (Reaction-Task GifManager) drives the bridge through a
``browser`` stream_batch whose samples carry ``payload.direction`` toActive/toRest
and a MOTION_LUT gesture key. The bridge translates each gesture-transition sample
into a device ``propose_task_event`` with event name ``to_active.<motion_id>`` /
``to_rest.<motion_id>`` (dot separator; a colon is not a legal name), starting the
run once on the first event. These tests use a fake NDJSON service that walks the
actual generated profile definition, so the new profile-derived state validation
in TaskInstructor is exercised end to end.
"""
import json
import tempfile
import unittest
from pathlib import Path

from scifi2_hub_manager.motion_profile import make_hub_spoke_profile
from scifi2_hub_manager.reactions_bridge import BridgeSession


GESTURES = ["Fist", "Paper", "ThumbTap"]  # -> hand_squeeze, hand_extend, thumb_tap


class HubSpokeTaskClient:
    """Fake NDJSON service that honours the hub-and-spoke profile transitions."""
    timeout = .1
    host = "127.0.0.1"
    port = 8765

    def __init__(self, profile):
        self.profile = profile
        self.definition = profile["definition"]
        self.task = {"configured": True, "definition_hash": profile["definition_hash"],
                     "app_session_id": "fixture", "run_sequence": 0, "transition_sequence": 0,
                     "current_state_id": None, "lifecycle": "idle"}
        self.event_sequence = 0
        self.calls = []          # (command, arguments) in order
        self.event = None

    def connect(self): pass
    def close(self): pass
    def subscribe_task_transitions(self, enabled): self.calls.append(("subscribe", enabled))
    def get_state_snapshot(self): return {"task": dict(self.task)}
    def set_capture(self, enabled): self.calls.append(("capture", enabled))

    def _resolve(self, command, arguments):
        previous = self.task["current_state_id"] or 0
        if command == "start_task":
            self.task.update(run_sequence=self.task["run_sequence"] + 1, transition_sequence=1,
                             current_state_id=self.definition["initial_state_id"], lifecycle="running")
            return "start", previous, self.task["current_state_id"]
        if command == "propose_task_event":
            matches = [t for t in self.definition["transitions"]
                       if t["from_state_id"] == previous
                       and t["trigger"]["event_name"] == arguments["event_name"]]
            assert len(matches) == 1, f"no unique transition for {arguments} from {previous}"
            target = matches[0]["to_state_id"]
            self.task["transition_sequence"] += 1
            self.task["current_state_id"] = target
            return "transition", previous, target
        # reset/abort -> NO_STATE
        self.task["transition_sequence"] += 1
        self.task["current_state_id"] = None
        self.task["lifecycle"] = "idle" if command == "reset_task" else "aborted"
        return ("reset" if command == "reset_task" else "abort"), previous, 0

    def request(self, command, request_id, **arguments):
        self.calls.append((command, arguments))
        kind, previous, current = self._resolve(command, arguments)
        self.event_sequence += 1
        self.event = {"request_id": request_id, "app_session_id": "fixture",
                      "run_sequence": str(self.task["run_sequence"]),
                      "transition_sequence": str(self.task["transition_sequence"]),
                      "event_sequence": str(self.event_sequence),
                      "definition_hash": self.profile["definition_hash"], "event_kind": kind,
                      "current_state_id": current, "previous_state_id": previous,
                      "effective_frame": {"source_id": "rhd2132",
                                          "sequence_number": str(self.event_sequence),
                                          "timestamp_ns": str(self.event_sequence * 50000)}}
        return {"status": "succeeded", "request_id": request_id}

    def wait_for_task_transition(self, timeout): return self.event


class FakeRecorder:
    def __init__(self, journal): self.journal, self.starts, self.stops = journal, 0, 0
    def check_running(self):
        if self.starts <= self.stops:
            raise RuntimeError("recorder is no longer running")
    async def start(self, metadata):
        self.starts += 1
        return {"recording": True}
    async def stop(self):
        self.stops += 1
        return {"recording": False, "local_stop_ok": True}


def browser_batch(direction, key, *, name="transitioning", timing=None, ttype="browser_event"):
    if timing is None:
        timing = "start" if direction == "toActive" else "end"
    return {"api_version": "0.12", "stream_batch": {"stream_id": "browser", "browser": {"samples": [
        {"timestamp_s": 1.5, "data": {"type": ttype, "name": name, "time": 2.0, "timing": timing,
                                      "payload": {"name": key, "direction": direction}}}]}}}


class HubSpokeBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from scifi2_hub_manager.calibration_task import Journal
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.journal = Journal(self.path / "events.ndjson")
        self.recorder = FakeRecorder(self.journal)
        self.profile = make_hub_spoke_profile(GESTURES)
        self.client = HubSpokeTaskClient(self.profile)
        self.session = BridgeSession(self.path, self.profile, {"fixture": True}, self.recorder,
                                     client=self.client)

    async def asyncTearDown(self):
        self.session.instructor = None  # skip real NDJSON cleanup from the fake
        await self.session.close()
        self.temp.cleanup()

    async def _open_streams(self):
        await self.session.handle({"api_version": "0.12", "api_request": {"request_id": "r-rec",
            "start_stream_request": {"stream_id": "recording_control"}}})
        await self.session.handle({"api_version": "0.12", "api_request": {"request_id": "r-brow",
            "start_stream_request": {"stream_id": "browser"}}})

    async def _start_recording(self):
        await self.session.handle({"api_version": "0.12", "stream_batch": {"stream_id": "recording_control",
            "recording_control": {"samples": [{"time": 1, "data": {"enabled": True}}]}}})

    def _commands(self):
        return [c for c in self.client.calls if c[0] in
                {"start_task", "propose_task_event", "reset_task", "abort_task"}]

    async def test_to_active_and_to_rest_map_to_dotted_event_names(self):
        await self._open_streams()
        await self._start_recording()
        reply = await self.session.handle(browser_batch("toActive", "Fist"))
        committed = reply["stream_batch"]["sciencexyz_status"]["samples"][0]["data"]
        self.assertTrue(committed["authoritative_task_event"])
        self.assertEqual(committed["committed_events"][0]["current_state_id"], 2)  # hand_squeeze spoke
        await self.session.handle(browser_batch("toRest", "Fist"))
        # start_task fires once, then one propose per direction with dotted names.
        # (arguments also carry the merged task preconditions; assert the shape.)
        self.assertEqual([(c[0], c[1].get("event_name")) for c in self._commands()], [
            ("start_task", None),
            ("propose_task_event", "to_active.hand_squeeze"),
            ("propose_task_event", "to_rest.hand_squeeze"),
        ])

    async def test_different_gestures_select_distinct_spokes(self):
        await self._open_streams()
        await self._start_recording()
        await self.session.handle(browser_batch("toActive", "Paper"))
        self.assertEqual(self.client.task["current_state_id"], 3)  # hand_extend spoke
        await self.session.handle(browser_batch("toRest", "Paper"))
        await self.session.handle(browser_batch("toActive", "ThumbTap"))
        self.assertEqual(self.client.task["current_state_id"], 4)  # thumb_tap spoke
        self.assertEqual([c[1].get("event_name") for c in self._commands() if c[0] == "propose_task_event"],
                         ["to_active.hand_extend", "to_rest.hand_extend", "to_active.thumb_tap"])

    async def test_start_task_emitted_exactly_once(self):
        await self._open_streams()
        await self._start_recording()
        for direction, key in [("toActive", "Fist"), ("toRest", "Fist"), ("toActive", "Paper")]:
            await self.session.handle(browser_batch(direction, key))
        self.assertEqual([c[0] for c in self._commands() if c[0] == "start_task"], ["start_task"])

    async def test_terminal_and_rest_target_events_are_journal_only(self):
        await self._open_streams()
        await self._start_recording()
        # Terminal frame: direction is null (payload has no toActive/toRest).
        terminal = {"api_version": "0.12", "stream_batch": {"stream_id": "browser", "browser": {"samples": [
            {"timestamp_s": 1.0, "data": {"type": "browser_event", "name": "active", "time": 2.0,
                                          "timing": "instant", "payload": {"name": "Fist", "direction": None}}}]}}}
        reply = await self.session.handle(terminal)
        result = reply["stream_batch"]["sciencexyz_status"]["samples"][0]["data"]
        self.assertFalse(result["authoritative_task_event"])
        self.assertEqual(self._commands(), [])  # nothing proposed, no start_task
        # The sample is still journalled.
        lines = [json.loads(l) for l in (self.path / "events.ndjson").read_text().splitlines()]
        self.assertTrue(any(l["kind"] == "browser_stream" for l in lines))

    async def test_unknown_gesture_key_is_rejected(self):
        await self._open_streams()
        await self._start_recording()
        with self.assertRaisesRegex(ValueError, "not in the MOTION_LUT"):
            await self.session.handle(browser_batch("toActive", "NotAGesture"))

    async def test_animation_event_before_recording_is_rejected(self):
        await self._open_streams()
        with self.assertRaisesRegex(ValueError, "start recording"):
            await self.session.handle(browser_batch("toActive", "Fist"))
        self.assertEqual(self._commands(), [])

    async def test_multiple_samples_in_one_batch_translate_in_order(self):
        await self._open_streams()
        await self._start_recording()
        batch = {"api_version": "0.12", "stream_batch": {"stream_id": "browser", "browser": {"samples": [
            {"timestamp_s": 1.0, "data": {"type": "browser_event", "name": "transitioning", "time": 2.0,
                                          "timing": "start", "payload": {"name": "Fist", "direction": "toActive"}}},
            {"timestamp_s": 1.5, "data": {"type": "browser_event", "name": "transitioning", "time": 2.5,
                                          "timing": "end", "payload": {"name": "Fist", "direction": "toRest"}}}]}}}
        reply = await self.session.handle(batch)
        result = reply["stream_batch"]["sciencexyz_status"]["samples"][0]["data"]
        self.assertEqual(result["journaled_samples"], 2)
        self.assertEqual([e["current_state_id"] for e in result["committed_events"]], [2, 1])

    async def test_failed_translation_marks_task_failed_and_cleans_up(self):
        await self._open_streams()
        await self._start_recording()
        await self.session.handle(browser_batch("toActive", "Fist"))  # enter hand_squeeze spoke
        # Force the next committed transition to look like a mismatched state so
        # the instructor rejects it; the bridge must mark the task failed, clean
        # up the active spoke, and refuse further task commands.
        cleaned = []
        self.session.instructor.cleanup = lambda: cleaned.append(True)
        original = self.client.wait_for_task_transition
        def wrong(timeout):
            event = original(timeout)
            event = dict(event); event["current_state_id"] = 99  # not the profile target
            return event
        self.client.wait_for_task_transition = wrong
        with self.assertRaises(Exception):
            await self.session.handle(browser_batch("toRest", "Fist"))
        self.assertTrue(self.session.task_failed)
        self.assertEqual(cleaned, [True])
        with self.assertRaisesRegex(ValueError, "task outcome uncertain"):
            await self.session.handle(browser_batch("toActive", "Paper"))

    async def test_get_profile_returns_hub_spoke(self):
        reply = await self.session.handle({"api_version": "0.12", "api_request": {"request_id": "p1",
            "sciencexyz_request": {"command": "get_profile"}}})
        self.assertEqual(reply["api_response"]["result"]["profile_shape"], "hub_and_spoke")


if __name__ == "__main__":
    unittest.main()
