"""A bounded rest/two-action task, with device-authoritative transitions."""
from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from pathlib import Path

from .client import NdjsonClient, SocketClientError


def make_profile():
    definition = {
        "schema_version": 1, "definition_id": "calibration.rest_two_actions", "revision": 1,
        "initial_state_id": 1,
        "source_policy": {"loss_action": "fault", "loss_timeout_ms": 1000,
                          "sequence_gap_action": "fault", "staged_command_timeout_ms": 5000},
        "states": [{"id": n, "name": name, "terminal": n == 4}
                   for n, name in enumerate(["rest", "action_a", "action_b", "complete"], 1)],
        "transitions": [{"id": n, "name": f"advance_{n}", "from_state_id": n,
                         "to_state_id": n + 1, "priority": 1,
                         "trigger": {"kind": "external_event", "event_name": "advance"}}
                        for n in range(1, 4)],
    }
    return {"profile_schema": "sciencexyz.calibration_task.v1", "definition": definition,
            "definition_hash": definition_hash(definition), "reference_source_id": "rhd2132",
            "state_to_label": {"1": "rest", "2": "action_a", "3": "action_b", "4": None},
            "instructions": {"1": "Rest", "2": "Perform action A", "3": "Perform action B", "4": "Complete"}}


def prepare_session(base_config_path, output_dir, *, provenance_path=None, profile=None):
    """Generate a device-config/profile/provenance triple into a fresh directory.

    Shared by the ``calibration_task.py prepare`` CLI and the session launcher
    (``run_calibration_session.py``) so both embed the task definition into the
    base config identically. ``profile`` defaults to the linear rest/two-action
    MVP (``make_profile``); pass a hub-and-spoke profile from
    ``motion_profile.make_hub_spoke_profile`` to configure a band-calibration run.

    ``output_dir`` is created with ``exist_ok=False`` so an existing session is
    never overwritten. No device commands are executed. Returns the profile.
    """
    config = json.loads(Path(base_config_path).read_text(encoding="utf-8-sig"))
    metadata = json.loads(Path(provenance_path).read_text(encoding="utf-8-sig")) if provenance_path else {
        "purpose": "calibration task acceptance", "device_inventory": None, "physical_sync": None}
    if not isinstance(metadata, dict) or not metadata:
        raise ValueError("provenance must be a nonempty JSON object")
    if not isinstance(config, dict) or not isinstance(config.get("nodes"), list):
        raise ValueError("base config must contain a nodes list")
    app = [n for n in config["nodes"] if n.get("application", {}).get("name") == "scifi2-hub-manager"]
    if len(app) != 1:
        raise ValueError("base config must contain exactly one scifi2-hub-manager App")
    if profile is None:
        profile = make_profile()
    app[0]["application"].setdefault("parameters", {}).update(
        task_definition=copy.deepcopy(profile["definition"]), task_reference_source_id=profile["reference_source_id"])
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=False)  # exclusive: never overwrite a session
    (out / "device-config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    (out / "task-profile.json").write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    metadata["task_profile"] = profile
    metadata["configuration_input"] = {"path": str(out / "device-config.json"), "snapshot": config,
        "evidence": "Generated configuration for operator deployment; not a live device readback"}
    metadata["running_configuration"] = None
    (out / "provenance.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return profile


def definition_hash(definition):
    # All fields/defaults are explicit in this profile. This ordering matches
    # task::canonical_json in src/task_state.cpp; tested against the C++ parser.
    canonical = dict(definition)
    canonical["states"] = sorted(definition["states"], key=lambda s: s["id"])
    canonical["transitions"] = sorted(definition["transitions"], key=lambda s: s["id"])
    data = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(data.encode()).hexdigest()


def _load_linear_profile(profile):
    """Validate the linear rest/two-action MVP profile shape."""
    expected = make_profile()
    if profile["definition"] != expected["definition"] or profile["definition_hash"] != expected["definition_hash"]:
        raise ValueError("This MVP requires the supplied rest/two-action definition unchanged")
    if profile.get("reference_source_id") != expected["reference_source_id"]:
        raise ValueError("reference_source_id does not match calibration task")
    labels = profile.get("state_to_label", {})
    if set(labels) != {"1", "2", "3", "4"} or labels["4"] is not None or any(
            not isinstance(labels[k], str) or not labels[k] for k in ["1", "2", "3"]):
        raise ValueError("three nonempty labels and an unlabeled completion state required")
    if len(set(labels[k] for k in ["1", "2", "3"])) != 3:
        raise ValueError("labels must be distinct")
    instructions = profile.get("instructions", {})
    if any(not isinstance(instructions.get(k), str) or not instructions[k] for k in ["1", "2", "3", "4"]):
        raise ValueError("all four states require nonempty instructions")
    return profile


def load_profile(path):
    """Load and structurally validate a calibration task profile from disk.

    Accepts either the linear rest/two-action MVP shape or the hub-and-spoke
    band-calibration shape (``profile_shape == "hub_and_spoke"``). Both are
    validated against the on-device contract, including a recomputed
    definition_hash that must match the stored value.
    """
    profile = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if profile.get("profile_shape") == "hub_and_spoke":
        # Imported lazily so the linear MVP path stays importable even if the
        # tracked MOTION_LUT copy is absent.
        from .motion_profile import validate_profile
        return validate_profile(profile)
    return _load_linear_profile(profile)


class Journal:
    def __init__(self, path):
        self.file = Path(path).open("x", encoding="utf-8")

    def write(self, kind, **values):
        record = {"kind": kind, "host_monotonic_ns": time.monotonic_ns(),
                  "host_unix_ns": time.time_ns(), **values}
        self.file.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


class TaskInstructor:
    def __init__(self, client, profile, journal):
        self.client, self.profile, self.journal = client, profile, journal
        self.session = None
        self.last_event = None

    def prepare(self):
        self.client.subscribe_task_transitions(True)
        snapshot = self.client.get_state_snapshot()
        task = snapshot.get("task", {})
        if not task.get("configured") or task.get("definition_hash") != self.profile["definition_hash"]:
            raise SocketClientError("Deploy the generated task configuration before running the instructor")
        if task.get("lifecycle") != "idle":
            raise SocketClientError("Task must be idle before a new instructor session")
        self.session = task["app_session_id"]
        self.client.set_capture(False)
        self.journal.write("task_prepared", profile=self.profile, snapshot=snapshot)

    def _expected_current_state(self, command, current_state, arguments):
        """The state id the committed event must land on for this command.

        Resolved from the profile definition rather than assumed to be
        ``current_state + 1`` so both the linear MVP and the hub-and-spoke
        band-calibration shape validate correctly. ``propose_task_event`` is the
        only command whose target depends on the definition: the device selects
        the outgoing external-event transition leaving ``current_state`` whose
        ``event_name`` matches the proposed event, so the expected landing state
        is that transition's ``to_state_id``.
        """
        definition = self.profile["definition"]
        if command == "start_task":
            return int(definition["initial_state_id"])
        if command in ("reset_task", "abort_task"):
            return 0  # NO_STATE
        event_name = arguments.get("event_name")
        matches = [t for t in definition["transitions"]
                   if int(t["from_state_id"]) == current_state
                   and t["trigger"].get("kind") == "external_event"
                   and t["trigger"].get("event_name") == event_name]
        if len(matches) != 1:
            # Two transitions leaving one state cannot share an event name, so
            # zero or many means the proposal cannot uniquely commit from here.
            raise SocketClientError(
                f"no unique external transition for event {event_name!r} from state {current_state}")
        return int(matches[0]["to_state_id"])

    def command(self, command, **arguments):
        snapshot = self.client.get_state_snapshot()
        task = snapshot["task"]
        if task["app_session_id"] != self.session or task["definition_hash"] != self.profile["definition_hash"]:
            raise SocketClientError("Device task session/definition changed; refusing replay")
        if self.last_event and (int(task["run_sequence"]), int(task["transition_sequence"])) != (
                int(self.last_event["run_sequence"]), int(self.last_event["transition_sequence"])):
            raise SocketClientError("Another controller changed the task")
        request_id = "calibration/" + uuid.uuid4().hex
        preconditions = NdjsonClient.task_preconditions(snapshot)
        self.journal.write("task_request", request_id=request_id, command=command, arguments=arguments,
                           preconditions=preconditions)
        result = self.client.request(command, request_id=request_id, **preconditions, **arguments)
        self.journal.write("task_result", result=result)
        deadline = time.monotonic() + self.client.timeout
        while time.monotonic() < deadline:
            event = self.client.wait_for_task_transition(max(0.001, deadline - time.monotonic()))
            self.journal.write("task_observed", event=event)
            if event.get("request_id") != request_id:
                raise SocketClientError("Unexpected concurrent task commit; abort this instructor run")
            expected_run = int(task["run_sequence"]) + (command == "start_task")
            if (event["app_session_id"] != self.session or int(event["run_sequence"]) != expected_run
                    or event["definition_hash"] != self.profile["definition_hash"]):
                raise SocketClientError("Committed task identity does not match request")
            expected_kind = {"start_task": "start", "reset_task": "reset", "abort_task": "abort",
                             "propose_task_event": "transition"}[command]
            expected_state = self._expected_current_state(
                command, int(task.get("current_state_id") or 0), arguments)
            if event["event_kind"] != expected_kind or int(event["current_state_id"]) != expected_state:
                raise SocketClientError("Committed task state does not match request")
            expected_transition = 1 if command == "start_task" else int(task["transition_sequence"]) + 1
            if (int(event["transition_sequence"]) != expected_transition or
                    int(event["previous_state_id"]) != int(task.get("current_state_id") or 0) or
                    event["effective_frame"]["source_id"] != self.profile["reference_source_id"]):
                raise SocketClientError("Committed task sequence/source does not match request")
            self.last_event = event
            self.journal.write("task_committed", event=event)
            return event
        raise SocketClientError("No committed task boundary received")

    def cleanup(self):
        # Fresh connection is needed after a timeout: buffered socket reads may
        # no longer be usable. Never replay an uncertain start/advance request.
        try:
            with NdjsonClient(self.client.host, self.client.port, timeout=self.client.timeout) as c:
                c.set_capture(False)
                snapshot = c.get_state_snapshot()
                task = snapshot.get("task", {})
                if task.get("app_session_id") == self.session and task.get("lifecycle") == "running":
                    c.abort_task(preconditions=c.task_preconditions(snapshot))
            self.journal.write("cleanup_requested")
        except Exception as error:
            self.journal.write("cleanup_failed", error=str(error))
