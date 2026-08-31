"""Runtime protobuf classes for the app's small typed control protocol.

The device build generates C++ classes from ``proto/gui_control.proto``.  The
host client builds the same descriptor at import time so installing the Python
client does not require protoc or a generated source file.
"""

from __future__ import annotations

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory


_PKG = "broadband_mode_switch.v1"
_FQ = f".{_PKG}"
_fd = descriptor_pb2.FileDescriptorProto(
    name="gui_control.proto", package=_PKG, syntax="proto3"
)


def _enum(parent, name, values):
    target = parent.enum_type.add() if hasattr(parent, "enum_type") else parent.enum.add()
    target.name = name
    for number, value in enumerate(values):
        item = target.value.add()
        item.name = value
        item.number = number
    return target


def _field(msg, name, number, field_type, *, label=1, type_name=None, oneof=None):
    item = msg.field.add()
    item.name = name
    item.number = number
    item.type = field_type
    item.label = label
    if type_name:
        item.type_name = type_name
    if oneof is not None:
        item.oneof_index = oneof
    return item


T_BOOL = descriptor_pb2.FieldDescriptorProto.TYPE_BOOL
T_UINT32 = descriptor_pb2.FieldDescriptorProto.TYPE_UINT32
T_UINT64 = descriptor_pb2.FieldDescriptorProto.TYPE_UINT64
T_FLOAT = descriptor_pb2.FieldDescriptorProto.TYPE_FLOAT
T_STRING = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
T_MESSAGE = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
T_ENUM = descriptor_pb2.FieldDescriptorProto.TYPE_ENUM
L_REPEATED = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED


_enum(_fd, "CommandKind", [
    "COMMAND_UNSPECIFIED", "COMMAND_GET_STATE", "COMMAND_SUBSCRIBE_STATE",
    "COMMAND_PREPARE_CAPTURE", "COMMAND_SELECT_COLLECTION", "COMMAND_SELECT_LABEL",
    "COMMAND_SET_CAPTURE", "COMMAND_FIT", "COMMAND_FLUSH",
])
_enum(_fd, "ErrorCode", [
    "ERROR_NONE", "ERROR_MALFORMED", "ERROR_UNSUPPORTED_VERSION", "ERROR_UNKNOWN_COMMAND",
    "ERROR_INVALID_ARGUMENT", "ERROR_OUT_OF_RANGE", "ERROR_PIPELINE_NOT_READY",
    "ERROR_CAPTURE_ENABLED", "ERROR_BUSY", "ERROR_EMPTY_COLLECTION",
    "ERROR_DUPLICATE_REQUEST_ID", "ERROR_TIMEOUT", "ERROR_TRANSPORT_DISCONNECTED",
    "ERROR_INTERNAL",
])
_enum(_fd, "ResultStatus", ["RESULT_UNSPECIFIED", "RESULT_ACCEPTED", "RESULT_SUCCEEDED", "RESULT_FAILED"])
_enum(_fd, "PipelineState", ["PIPELINE_UNSPECIFIED", "PIPELINE_DISCONNECTED", "PIPELINE_NOT_READY", "PIPELINE_READY", "PIPELINE_ERROR"])
_enum(_fd, "SourceMode", ["SOURCE_MODE_UNSPECIFIED", "SOURCE_MODE_SAMPLING", "SOURCE_MODE_SYNTHETIC"])
_enum(_fd, "ModelPhase", ["MODEL_UNSPECIFIED", "MODEL_IDLE", "MODEL_QUEUED", "MODEL_RUNNING", "MODEL_SUCCEEDED", "MODEL_FAILED", "MODEL_CANCELLED"])

_fd.message_type.add(name="GetState")
select_collection = _fd.message_type.add(name="SelectCollection")
_field(select_collection, "collection_id", 1, T_UINT32)
select_label = _fd.message_type.add(name="SelectLabel")
_field(select_label, "label", 1, T_UINT32)

subscribe = _fd.message_type.add(name="SubscribeState")
_field(subscribe, "enabled", 1, T_BOOL)
prepare = _fd.message_type.add(name="PrepareCapture")
_field(prepare, "collection_id", 1, T_UINT32)
_field(prepare, "label", 2, T_UINT32)
_field(prepare, "enabled", 3, T_BOOL)
set_capture = _fd.message_type.add(name="SetCapture")
_field(set_capture, "enabled", 1, T_BOOL)
fit = _fd.message_type.add(name="Fit")
_field(fit, "epochs", 1, T_UINT32)
flush = _fd.message_type.add(name="Flush")
_enum(flush, "Scope", ["SCOPE_UNSPECIFIED", "SCOPE_LABEL", "SCOPE_COLLECTION", "SCOPE_ALL"])
_field(flush, "scope", 1, T_ENUM, type_name=f"{_FQ}.Flush.Scope")
_field(flush, "has_collection_id", 2, T_BOOL)
_field(flush, "collection_id", 3, T_UINT32)
_field(flush, "has_label", 4, T_BOOL)
_field(flush, "label", 5, T_UINT32)

error = _fd.message_type.add(name="Error")
_field(error, "code", 1, T_ENUM, type_name=f"{_FQ}.ErrorCode")
_field(error, "message", 2, T_STRING)
_field(error, "field", 3, T_STRING)
_field(error, "retryable", 4, T_BOOL)
progress = _fd.message_type.add(name="FitProgress")
_field(progress, "epoch", 1, T_UINT32)
_field(progress, "total_epochs", 2, T_UINT32)
_field(progress, "loss", 3, T_FLOAT)
_field(progress, "accuracy", 4, T_FLOAT)

command = _fd.message_type.add(name="ControlCommand")
command.oneof_decl.add(name="payload")
_field(command, "protocol_version", 1, T_UINT32)
_field(command, "request_id", 2, T_STRING)
_field(command, "command", 3, T_ENUM, type_name=f"{_FQ}.CommandKind")
_field(command, "get_state", 10, T_MESSAGE, type_name=f"{_FQ}.GetState", oneof=0)
_field(command, "subscribe_state", 11, T_MESSAGE, type_name=f"{_FQ}.SubscribeState", oneof=0)
_field(command, "prepare_capture", 12, T_MESSAGE, type_name=f"{_FQ}.PrepareCapture", oneof=0)
_field(command, "select_collection", 13, T_MESSAGE, type_name=f"{_FQ}.SelectCollection", oneof=0)
_field(command, "select_label", 14, T_MESSAGE, type_name=f"{_FQ}.SelectLabel", oneof=0)
_field(command, "set_capture", 15, T_MESSAGE, type_name=f"{_FQ}.SetCapture", oneof=0)
_field(command, "fit", 16, T_MESSAGE, type_name=f"{_FQ}.Fit", oneof=0)
_field(command, "flush", 17, T_MESSAGE, type_name=f"{_FQ}.Flush", oneof=0)

result = _fd.message_type.add(name="CommandResult")
_field(result, "protocol_version", 1, T_UINT32)
_field(result, "request_id", 2, T_STRING)
_field(result, "command", 3, T_ENUM, type_name=f"{_FQ}.CommandKind")
_field(result, "status", 4, T_ENUM, type_name=f"{_FQ}.ResultStatus")
_field(result, "state_version", 5, T_UINT64)
_field(result, "error", 6, T_MESSAGE, type_name=f"{_FQ}.Error")
_field(result, "progress", 7, T_MESSAGE, type_name=f"{_FQ}.FitProgress")

pipeline = _fd.message_type.add(name="PipelineStatus")
_field(pipeline, "state", 1, T_ENUM, type_name=f"{_FQ}.PipelineState")
_field(pipeline, "source_mode", 2, T_ENUM, type_name=f"{_FQ}.SourceMode")
active = _fd.message_type.add(name="ActiveTarget")
_field(active, "collection_id", 1, T_UINT32)
_field(active, "label", 2, T_UINT32)
_field(active, "capture_enabled", 3, T_BOOL)
label = _fd.message_type.add(name="LabelStatus")
_field(label, "label", 1, T_UINT32)
_field(label, "count", 2, T_UINT32)
_field(label, "capacity", 3, T_UINT32)
collection = _fd.message_type.add(name="CollectionStatus")
_field(collection, "collection_id", 1, T_UINT32)
_field(collection, "feature_dimension", 2, T_UINT32)
_field(collection, "data_generation", 3, T_UINT64)
_field(collection, "labels", 4, T_MESSAGE, label=L_REPEATED, type_name=f"{_FQ}.LabelStatus")
model = _fd.message_type.add(name="ModelStatus")
_field(model, "phase", 1, T_ENUM, type_name=f"{_FQ}.ModelPhase")
_field(model, "ready", 2, T_BOOL)
_field(model, "has_source_collection_id", 3, T_BOOL)
_field(model, "source_collection_id", 4, T_UINT32)
_field(model, "source_generation", 5, T_UINT64)
_field(model, "stale", 6, T_BOOL)
_field(model, "epoch", 7, T_UINT32)
_field(model, "total_epochs", 8, T_UINT32)
_field(model, "loss", 9, T_FLOAT)
_field(model, "accuracy", 10, T_FLOAT)
_field(model, "duration_ms", 11, T_UINT64)
state = _fd.message_type.add(name="StateSnapshot")
_field(state, "protocol_version", 1, T_UINT32)
_field(state, "state_version", 2, T_UINT64)
_field(state, "timestamp_ns", 3, T_UINT64)
_field(state, "pipeline", 4, T_MESSAGE, type_name=f"{_FQ}.PipelineStatus")
_field(state, "active", 5, T_MESSAGE, type_name=f"{_FQ}.ActiveTarget")
_field(state, "collections", 6, T_MESSAGE, label=L_REPEATED, type_name=f"{_FQ}.CollectionStatus")
_field(state, "model", 7, T_MESSAGE, type_name=f"{_FQ}.ModelStatus")
_field(state, "last_error", 8, T_MESSAGE, type_name=f"{_FQ}.Error")

_pool = descriptor_pool.DescriptorPool()
_pool.Add(_fd)


def _message(name):
    return message_factory.GetMessageClass(_pool.FindMessageTypeByName(f"{_PKG}.{name}"))


ControlCommand = _message("ControlCommand")
CommandResult = _message("CommandResult")
StateSnapshot = _message("StateSnapshot")

COMMAND = {
    "get_state": 1, "subscribe_state": 2, "prepare_capture": 3,
    "select_collection": 4, "select_label": 5, "set_capture": 6,
    "fit": 7, "flush": 8,
}
RESULT_ACCEPTED, RESULT_SUCCEEDED, RESULT_FAILED = 1, 2, 3
FLUSH_LABEL, FLUSH_COLLECTION, FLUSH_ALL = 1, 2, 3


def enum_name(message, field_name: str) -> str:
    field = message.DESCRIPTOR.fields_by_name[field_name]
    value = field.enum_type.values_by_number.get(getattr(message, field_name))
    return value.name.removeprefix("PIPELINE_").removeprefix("SOURCE_MODE_").removeprefix("MODEL_").removeprefix("COMMAND_").removeprefix("RESULT_").removeprefix("ERROR_").lower() if value else "unspecified"
