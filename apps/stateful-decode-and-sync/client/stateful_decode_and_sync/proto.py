"""Compatibility exports for the protobuf binding generated from gui_control.proto.

The checked-in :mod:`gui_control_pb2` module is generated with
``py -3.13 -m grpc_tools.protoc --proto_path=proto --python_out=client/stateful_decode_and_sync proto/gui_control.proto``
from the app directory. Keep this thin module so existing clients importing
``stateful_decode_and_sync.proto`` continue to work while Python and C++ use
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
            .removeprefix("MODEL_")
            .removeprefix("TASK_LIFECYCLE_")
            .removeprefix("TASK_EVENT_")
            .removeprefix("TASK_TRIGGER_")
            .removeprefix("COMMAND_")
            .removeprefix("RESULT_")
            .removeprefix("ERROR_")
            .lower())
