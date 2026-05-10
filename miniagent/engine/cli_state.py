"""CLI 与命令调度共享的运行时状态（类型收窄）。

``unified_main`` 构造的字典与此 :class:`CliLoopState` 对齐；飞书 handler 闭包捕获同一结构。
"""

from __future__ import annotations

from typing import Any, TypedDict


class CliLoopState(TypedDict):
    """主循环与 ``dispatch_command`` 使用的状态键（与 ``main.unified_main`` 一致）。"""

    active_session_id: str
    skill_toolboxes: list[Any]
    skill_prompts: list[Any]
    feishu_enabled: bool
    session_manager: Any | None
    instance_id: int
    runtime_ctx: Any
    #: 飞书私聊 sender_id，首次私聊自动绑到当前活跃会话；随 ``.session switch`` 重绑
    feishu_p2p_synced_senders: set[str]


__all__ = ["CliLoopState"]
