"""CLI 与命令调度共享的运行时状态（类型收窄）。

``unified_main`` 在进程启动时构造 :class:`CliLoopState` 字典，经闭包传递给 CLI 主循环、
``dispatch_command``、飞书 handler 与定时任务（``scheduled_tasks.runner`` / ``ticker``）。
该结构在**单进程内共享**，非线程安全；修改 ``active_session_id`` 等字段时应与会话锁、
``session_manager`` 保持一致。

写入 ``AgentConfig.cli_loop_state`` 后由 ``ToolContext`` 注入点命令工具，详见
``docs/ARCHITECTURE.md``。

Example::

    state: CliLoopState = {
        "active_session_id": "default",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": session_manager,
        "instance_id": 1,
        "runtime_ctx": ctx,
        "feishu_p2p_synced_senders": set(),
    }
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from miniagent.runtime.context import RuntimeContext
    from miniagent.types.memory import SessionManagerProtocol
    from miniagent.types.tool import Toolbox


class _CliLoopStateRequired(TypedDict):
    """``CliLoopState`` 必填键（``unified_main`` 启动时写入）。"""

    active_session_id: str  # 当前活跃会话 ID（与 UnifiedEngine 的 session_key 对齐）
    skill_toolboxes: list[Toolbox]  # 技能工具箱快照；热加载后由 apply_skill_snapshots_to_state 更新
    skill_prompts: list[str]  # 技能系统提示词快照（字符串列表，合并后注入 Agent）
    feishu_enabled: bool  # 是否启用飞书 WebSocket 模式
    session_manager: SessionManagerProtocol | None  # 会话管理器；启动完成前为 None
    instance_id: int  # 多实例注册 ID
    runtime_ctx: RuntimeContext  # 进程级组合根（message_queue、channel_router、engine 等）
    feishu_p2p_synced_senders: set[str]  # 飞书私聊 sender_id；随 /session switch 重绑


class CliLoopState(_CliLoopStateRequired, total=False):
    """主循环与 ``dispatch_command`` 使用的状态键（与 ``main.unified_main`` 一致）。

    ``last_feishu_receive_chat_id`` 在飞书入站时按需写入（``feishu_handler``）。
    ``cli_render_width`` / ``cli_markdown_width`` 由全屏 ``run_cli_loop`` 注册，供
    ``cli_format`` 与飞书 CLI 镜像对齐视口宽度。
    """

    last_feishu_receive_chat_id: str  # 最近飞书 chat_id；定时任务镜像投递回退
    cli_render_width: object  # ``Callable[[], int]``；全屏分隔线宽度
    cli_markdown_width: object  # ``Callable[[], int]``；全屏 Markdown 渲染宽度


__all__ = ["CliLoopState"]
