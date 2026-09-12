"""Compatibility exports for the protobuf binding generated from gui_control.proto.

The checked-in :mod:`gui_control_pb2` module is generated with
``py -3.13 -m grpc_tools.protoc --proto_path=proto --python_out=client/scifi2_hub_manager proto/gui_control.proto``
from the app directory. Keep this thin module so existing clients importing
``scifi2_hub_manager.proto`` continue to work while Python and C++ use
the same canonical schema.
"""

from __future__ import annotations

from .gui_control_pb2 import *  # noqa: F403


COMMAND = {
    "get_state": COMMAND_GET_STATE,
    "subscribe_state": COMMAND_SUBSCRIBE_STATE,
    "prepare_capture": COMMAND_PREPARE_CAPTURE,
    "select_collection": COMMAND_SELECT_COLLECTION,
    "select_label": COMMAND_SELECT_LABEL,
    "set_capture": COMMAND_SET_CAPTURE,
    "fit": COMMAND_FIT,
    "flush": COMMAND_FLUSH,
    "start_task": COMMAND_START_TASK,
    "propose_task_event": COMMAND_PROPOSE_TASK_EVENT,
    "propose_task_transition": COMMAND_PROPOSE_TASK_TRANSITION,
    "abort_task": COMMAND_ABORT_TASK,
    "reset_task": COMMAND_RESET_TASK,
    "query_exo": COMMAND_QUERY_EXO,
    "set_exo_mode": COMMAND_SET_EXO_MODE,
    "set_exo_pose": COMMAND_SET_EXO_POSE,
    "exo_raw": COMMAND_EXO_RAW,
}

EXO_MODE = {
    "off": EXO_MODE_OFF,
    "connected": EXO_MODE_CONNECTED,
    "external": EXO_MODE_EXTERNAL,
    "decode": EXO_MODE_DECODE,
}
#: set_finger_angles joint order; index+1 is the ExoJoint enum value.
EXO_JOINT = {
    "thumb": EXO_JOINT_THUMB,
    "index": EXO_JOINT_INDEX,
    "middle": EXO_JOINT_MIDDLE,
    "ring": EXO_JOINT_RING,
    "pinky": EXO_JOINT_PINKY,
    "wrist": EXO_JOINT_WRIST,
}
FLUSH_LABEL, FLUSH_COLLECTION, FLUSH_ALL = (
    Flush.Scope.SCOPE_LABEL,
    Flush.Scope.SCOPE_COLLECTION,
    Flush.Scope.SCOPE_ALL,
)


def enum_name(message, field_name: str) -> str:
    """Return a compact lower-case enum label for display-only legacy code."""
    field = message.DESCRIPTOR.fields_by_name[field_name]
    value = field.enum_type.values_by_number.get(getattr(message, field_name))
    if value is None:
        return "unspecified"
    return (value.name.removeprefix("PIPELINE_")
            .removeprefix("SOURCE_MODE_")
            .removeprefix("EXO_MODE_")
            .removeprefix("EXO_JOINT_")
            .removeprefix("MODEL_")
            .removeprefix("TASK_LIFECYCLE_")
            .removeprefix("TASK_EVENT_")
            .removeprefix("TASK_TRIGGER_")
            .removeprefix("COMMAND_")
            .removeprefix("RESULT_")
            .removeprefix("ERROR_")
            .lower())
