"""飞书工具默认 receive_id 与 receive_id_type 对齐。"""

from __future__ import annotations

from miniagent.feishu.receive_id import default_receive_id_for_send, effective_receive_id_type
from miniagent.types.tool import ToolContext
from tests.config_helpers import install_test_config

_default_receive_id_for_send = default_receive_id_for_send
_effective_receive_id_type = effective_receive_id_type


def test_default_receive_id_chat_id_uses_message_queue() -> None:
    ctx = ToolContext(cwd="/tmp", message_queue_abort_chat_id="oc_abc")
    rid, err = _default_receive_id_for_send({}, ctx)
    assert err is None and rid == "oc_abc"


def test_default_receive_id_open_id_uses_injected_sender(tmp_path) -> None:
    install_test_config(tmp_path, {"feishu": {"receive_id_type": "open_id"}})
    ctx = ToolContext(
        cwd="/tmp",
        message_queue_abort_chat_id="oc_grp",
        feishu_im_receive_id="ou_user_open",
    )
    rid, err = _default_receive_id_for_send({}, ctx)
    assert err is None and rid == "ou_user_open"


def test_default_receive_id_open_id_missing_sender_errors(tmp_path) -> None:
    install_test_config(tmp_path, {"feishu": {"receive_id_type": "open_id"}})
    ctx = ToolContext(cwd="/tmp", message_queue_abort_chat_id="oc_grp", feishu_im_receive_id=None)
    rid, err = _default_receive_id_for_send({}, ctx)
    assert rid is None and err and "feishu_im_receive_id" in err


def test_default_receive_id_explicit_arg_overrides(tmp_path) -> None:
    install_test_config(tmp_path, {"feishu": {"receive_id_type": "open_id"}})
    ctx = ToolContext(
        cwd="/tmp",
        message_queue_abort_chat_id="oc_1",
        feishu_im_receive_id="ou_a",
    )
    rid, err = _default_receive_id_for_send({"receive_id": "custom_rid"}, ctx)
    assert err is None and rid == "custom_rid"


def test_effective_receive_id_type_arg_over_env(tmp_path) -> None:
    install_test_config(tmp_path, {"feishu": {"receive_id_type": "open_id"}})
    ctx = ToolContext(cwd="/tmp", feishu_im_receive_id_type=None)
    assert _effective_receive_id_type({"receive_id_type": "union_id"}, ctx) == "union_id"
