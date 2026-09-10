"""Hub-and-spoke calibration task profiles derived from the tracked MOTION_LUT.

The band-calibration browser (Reaction-Task GifManager) drives a hub-and-spoke
finite state machine rather than a linear chain: ``rest`` is the hub, and each
gesture is a spoke entered from rest by a ``toActive`` event and left back to
rest by a ``toRest`` event. This module compiles an ordered list of gesture keys
into a v1 ``TaskDefinition`` whose ``definition_hash`` is byte-identical to the
on-device ``scifi2_hub::task::canonical_json`` (src/task_state.cpp), plus the typed
profile data (labels, instructions, per-state decoder class) the recorder and
instructor need.

Design constraints enforced here match the normative contract in
docs/task-state-contract.md:

* External event names obey ``valid_name`` (``[A-Za-z][A-Za-z0-9_.-]*``). The
  gesture is encoded in the event name with a ``.`` separator, e.g.
  ``to_active.index_flexion`` / ``to_rest.index_flexion``. A colon is NOT a
  legal name character and would fail App setup.
* Two transitions leaving the same state cannot share an external event name, so
  each spoke's inbound/outbound events are distinct per gesture.
* States 1-64; rest is the hub, so the number of outgoing transitions from rest
  equals the gesture count and must stay <= 32 (kMaxOutgoingTransitions).
* No terminal state: a hub-and-spoke calibration run ends by operator stop, not
  by entering a terminal state. The contract permits a definition with no
  terminal state.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# Contract bounds mirrored from apps/scifi2-hub-manager/src/task_state.hpp
# / docs/task-state-contract.md. Validation here is a client-side pre-check; the
# App remains the authority that fails setup on any violation.
MAX_STATES = 64
MAX_OUTGOING_TRANSITIONS = 32
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")

REST_STATE_ID = 1
REST_STATE_NAME = "rest"
REFERENCE_SOURCE_ID = "rhd2132"
PROFILE_SCHEMA = "sciencexyz.calibration_task.v1"
DEFINITION_ID = "calibration.band_hub_spoke"

# Located relative to the repository so the host never reads the JS repo at
# runtime. config/motion_lut.json is the tracked copy of Reaction-Task's
# MOTION_LUT and human labels.
_MOTION_LUT_PATH = Path(__file__).resolve().parents[4] / "config" / "motion_lut.json"


def _humanize(key: str) -> str:
    """Fallback instruction label for a gesture key absent from MOTION_CATALOG."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key)
    return spaced


def load_motion_lut(path: str | Path | None = None) -> dict:
    """Return the tracked MOTION_LUT (id map) and human labels."""
    lut_path = Path(path) if path is not None else _MOTION_LUT_PATH
    data = json.loads(lut_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data.get("lut"), dict) or not data["lut"]:
        raise ValueError(f"{lut_path} must contain a nonempty 'lut' object")
    labels = data.get("labels", {})
    if not isinstance(labels, dict):
        raise ValueError(f"{lut_path} 'labels' must be an object")
    return {"lut": data["lut"], "labels": labels}


def make_hub_spoke_profile(gesture_keys, *, revision: int = 1,
                           reference_source_id: str = REFERENCE_SOURCE_ID,
                           motion_lut: dict | None = None,
                           loss_action: str = "fault",
                           sequence_gap_action: str = "fault"):
    """Compile an ordered list of MOTION_LUT gesture keys into a profile.

    ``gesture_keys`` is an ordered, duplicate-free list of CamelCase MOTION_LUT
    keys. Each becomes one spoke state reachable from and returning to the rest
    hub. The decoder class of each gesture state is its 0-based index in
    ``gesture_keys``; rest carries no decoder class.
    """
    catalog = motion_lut if motion_lut is not None else load_motion_lut()
    lut, labels = catalog["lut"], catalog["labels"]

    if not isinstance(gesture_keys, (list, tuple)) or not gesture_keys:
        raise ValueError("gesture_keys must be a nonempty ordered list")
    if len(gesture_keys) != len(set(gesture_keys)):
        raise ValueError("gesture_keys must not contain duplicates")
    unknown = [k for k in gesture_keys if k not in lut]
    if unknown:
        raise ValueError(f"gesture keys absent from MOTION_LUT: {unknown}")
    # rest hub occupies one state; each gesture adds a state, and rest gains one
    # outgoing transition per gesture.
    if len(gesture_keys) + 1 > MAX_STATES:
        raise ValueError(f"at most {MAX_STATES - 1} gestures fit in one definition")
    if len(gesture_keys) > MAX_OUTGOING_TRANSITIONS:
        raise ValueError(
            f"rest hub allows at most {MAX_OUTGOING_TRANSITIONS} outgoing transitions "
            f"({MAX_OUTGOING_TRANSITIONS} gestures)")
    if not (1 <= revision <= (1 << 53) - 1):
        raise ValueError("revision is outside the v1 bound")

    states = [{"id": REST_STATE_ID, "name": REST_STATE_NAME, "terminal": False}]
    transitions = []
    state_to_label = {str(REST_STATE_ID): REST_STATE_NAME}
    state_to_class: dict[str, int | None] = {str(REST_STATE_ID): None}
    instructions = {str(REST_STATE_ID): "Rest"}

    transition_id = 1
    for index, key in enumerate(gesture_keys):
        motion_id = lut[key]
        state_id = REST_STATE_ID + 1 + index
        if not NAME_RE.match(motion_id):
            raise ValueError(f"motion id {motion_id!r} is not a legal state name")
        states.append({"id": state_id, "name": motion_id, "terminal": False})
        state_to_label[str(state_id)] = motion_id
        state_to_class[str(state_id)] = index
        instructions[str(state_id)] = labels.get(key) or _humanize(key)

        to_active = f"to_active.{motion_id}"
        to_rest = f"to_rest.{motion_id}"
        for event_name in (to_active, to_rest):
            if not NAME_RE.match(event_name):
                raise ValueError(f"event name {event_name!r} is not a legal name")
        # rest -> gesture (priority is unique per source state; rest has one
        # outgoing edge per gesture, so index-derived priorities stay distinct).
        transitions.append({
            "id": transition_id, "name": f"enter_{motion_id}",
            "from_state_id": REST_STATE_ID, "to_state_id": state_id,
            "priority": (index % 255) + 1,
            "trigger": {"kind": "external_event", "event_name": to_active}})
        transition_id += 1
        # gesture -> rest (single outgoing edge from the spoke).
        transitions.append({
            "id": transition_id, "name": f"exit_{motion_id}",
            "from_state_id": state_id, "to_state_id": REST_STATE_ID,
            "priority": 1,
            "trigger": {"kind": "external_event", "event_name": to_rest}})
        transition_id += 1

    definition = {
        "schema_version": 1, "definition_id": DEFINITION_ID, "revision": revision,
        "initial_state_id": REST_STATE_ID,
        "source_policy": {"loss_action": loss_action, "loss_timeout_ms": 1000,
                          "sequence_gap_action": sequence_gap_action,
                          "staged_command_timeout_ms": 5000},
        "states": states,
        "transitions": transitions,
    }
    return {
        "profile_schema": PROFILE_SCHEMA,
        "profile_shape": "hub_and_spoke",
        "gesture_keys": list(gesture_keys),
        "definition": definition,
        "definition_hash": definition_hash(definition),
        "reference_source_id": reference_source_id,
        "num_classes": len(gesture_keys),
        "state_to_label": state_to_label,
        "state_to_class": state_to_class,
        "instructions": instructions,
    }


def definition_hash(definition):
    """sha256 of canonical UTF-8 JSON, byte-identical to task::canonical_json.

    The C++ canonical form (src/task_state.cpp) emits object keys in
    lexicographic byte order with states/transitions sorted by numeric id and no
    insignificant whitespace; ``json.dumps(sort_keys=True)`` reproduces that
    ordering for this schema. This is the same routine the linear MVP profile
    uses and is cross-checked against the C++ parser in the test suite.
    """
    import hashlib

    canonical = dict(definition)
    canonical["states"] = sorted(definition["states"], key=lambda s: s["id"])
    canonical["transitions"] = sorted(definition["transitions"], key=lambda s: s["id"])
    data = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(data.encode()).hexdigest()


def validate_profile(profile):
    """Structurally validate a hub-and-spoke profile dict; raise on any fault.

    Verifies the recomputed hash matches, the topology is a valid rest-hub
    star (rest reachable to/from every gesture, no terminal state, single edge
    per direction), labels are distinct and nonempty, and instructions cover
    every state. Returns the profile on success.
    """
    definition = profile.get("definition")
    if not isinstance(definition, dict):
        raise ValueError("profile is missing a definition object")
    if profile.get("definition_hash") != definition_hash(definition):
        raise ValueError("definition_hash does not match the canonical definition")

    states = {s["id"]: s for s in definition["states"]}
    if definition.get("initial_state_id") != REST_STATE_ID or REST_STATE_ID not in states:
        raise ValueError("hub-and-spoke definition must start at the rest hub (state 1)")
    if any(s["terminal"] for s in definition["states"]):
        raise ValueError("hub-and-spoke definition must have no terminal state")

    gesture_ids = sorted(sid for sid in states if sid != REST_STATE_ID)
    if not gesture_ids:
        raise ValueError("hub-and-spoke definition requires at least one gesture spoke")

    inbound = {}   # gesture_id -> rest->gesture transition
    outbound = {}  # gesture_id -> gesture->rest transition
    for t in definition["transitions"]:
        if t["trigger"].get("kind") != "external_event":
            raise ValueError("hub-and-spoke transitions must be external_event")
        src, dst = t["from_state_id"], t["to_state_id"]
        if src == REST_STATE_ID and dst in states and dst != REST_STATE_ID:
            if dst in inbound:
                raise ValueError(f"multiple inbound transitions to gesture {dst}")
            inbound[dst] = t
        elif dst == REST_STATE_ID and src in states and src != REST_STATE_ID:
            if src in outbound:
                raise ValueError(f"multiple outbound transitions from gesture {src}")
            outbound[src] = t
        else:
            raise ValueError(f"transition {t['id']} is not a rest<->gesture edge")
    for gid in gesture_ids:
        if gid not in inbound or gid not in outbound:
            raise ValueError(f"gesture state {gid} is not both entered and left from rest")

    labels = profile.get("state_to_label", {})
    gesture_labels = [labels.get(str(gid)) for gid in gesture_ids]
    if any(not isinstance(v, str) or not v for v in gesture_labels):
        raise ValueError("every gesture state requires a nonempty label")
    if len(set(gesture_labels)) != len(gesture_labels):
        raise ValueError("gesture labels must be distinct")
    if not labels.get(str(REST_STATE_ID)):
        raise ValueError("rest state requires a label")

    instructions = profile.get("instructions", {})
    if any(not isinstance(instructions.get(str(sid)), str) or not instructions[str(sid)]
           for sid in states):
        raise ValueError("every state requires a nonempty instruction")

    if not profile.get("reference_source_id"):
        raise ValueError("reference_source_id is required")
    return profile
