from unittest.mock import Mock
import pytest
from scifi2_hub_manager import proto
from scifi2_hub_manager.controller import BroadbandController
from scifi2_hub_manager.model import state_from_proto, state_to_json
from scifi2_hub_manager.service import ControlService


def test_bridge_serializes_exo_commands():
    controller = BroadbandController("unused")
    controller.execute = Mock(side_effect=lambda command: command)
    service = ControlService(controller)
    mode = service._run_command("set_exo_mode", {"mode": "connected"}, "check")
    assert mode.set_exo_mode.mode == proto.EXO_MODE_CONNECTED
    assert mode.request_id == "check"
    query = service._run_command("query_exo", {"query": "check_limits"}, "limits")
    assert query.command == proto.COMMAND_QUERY_EXO
    assert query.query_exo.query == "check_limits"
    pose = service._run_command("set_exo_pose", {"joints": {"index": -10}}, "move")
    assert pose.set_exo_pose.joints[0].joint == proto.EXO_JOINT_INDEX
    assert pose.set_exo_pose.joints[0].value == -10


@pytest.mark.parametrize("query", ["enable:all", "version\nenable:all", "status:all", ""])
def test_query_allowlist(query):
    controller = BroadbandController("unused")
    controller.execute = Mock()
    with pytest.raises(ValueError):
        controller.query_exo(query)
    controller.execute.assert_not_called()


@pytest.mark.parametrize("joints", [{"index": True}, {"index": 101}, {"index": 1.5}, {}, [], {"elbow": 10}])
def test_invalid_pose_does_not_send(joints):
    controller = BroadbandController("unused")
    controller.execute = Mock()
    with pytest.raises(ValueError):
        controller.set_exo_pose(joints)
    controller.execute.assert_not_called()


def test_usb_status_survives_wire_and_json():
    message = proto.StateSnapshot(protocol_version=1)
    message.pipeline.state = proto.PIPELINE_READY
    message.pipeline.source_mode = proto.SOURCE_MODE_SAMPLING
    message.active.collection_id = 0
    message.model.phase = proto.MODEL_IDLE
    message.exo.configured = True
    message.exo.mode = proto.EXO_MODE_CONNECTED
    message.exo.link_open = True
    message.exo.firmware = "0.6.4"
    message.exo.last_reply = "Limit check:\nMotor 1: OK"
    message.exo.transport = "libusb CDC control=0 data=1"
    message.exo.watchdog_tripped = True
    message.exo.last_commanded.add(joint=proto.EXO_JOINT_INDEX, value=10)
    state = state_to_json(state_from_proto(message))["exo"]
    assert state["mode"] == "connected"
    assert state["last_commanded"] == {"index": 10}
    assert state["watchdog_tripped"] and state["link_open"]
    assert state["last_reply"] == message.exo.last_reply
    assert not state["armed"]
