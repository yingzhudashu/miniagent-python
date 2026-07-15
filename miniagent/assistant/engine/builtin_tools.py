"""将 ALL_TOOLS 注册到主 ToolRegistry（内置工具先于技能包加载）。

在 ``init_subsystems`` 中**先于技能包**调用 :func:`register_builtin_tools`，保证内置定义优先占用工具名。

同名冲突策略：**内置优先**。注册表已有同名工具时 catch ``ValueError`` 并 debug 日志跳过；
技能包侧注册遇同名亦跳过（见 ``engine.init``）。

可选注册开关（配置键 → 跳过的工具集合）：

+-------------------------------+---------------------------+--------------------------------+
| 配置键                        | 默认值                    | 跳过的工具名                   |
+===============================+===========================+================================+
| ``cli.dot_tools_enabled``     | ``true``                  | ``CLI_DOT_TOOL_NAMES``         |
| ``scheduled_tools.enabled``   | ``true``                  | ``SCHEDULE_TOOL_NAMES``        |
| ``feishu.tools_explicit``     | ``null``（见下）          | ``FEISHU_EXT_TOOL_NAMES``      |
| ``feishu.tools_auto``         | ``true``                  | （与 explicit 组合判定）       |
+-------------------------------+---------------------------+--------------------------------+

飞书扩展工具是否注册由 :func:`feishu_im_tools_should_register` 判定：
``tools_explicit=true`` 强制开启；``false`` 强制关闭；未设置时在 ``tools_auto`` 为真且已配置
``FEISHU_APP_ID`` / ``FEISHU_APP_SECRET`` 时开启。

环境变量收敛暴露面（点命令 / 定时任务工具）的说明见 ``README`` 与 ``docs/SECURITY.md``。
"""

from __future__ import annotations

from miniagent.agent.logging import get_logger
from miniagent.agent.types.tool import ToolRegistryProtocol
from miniagent.assistant.feishu.feishu_tool_policy import FEISHU_EXT_TOOL_NAMES
from miniagent.assistant.feishu.im_tool_policy import feishu_im_tools_should_register
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.assistant.tools import ALL_TOOLS
from miniagent.assistant.tools.cli_dispatch_tools import CLI_DOT_TOOL_NAMES
from miniagent.assistant.tools.knowledge_tools import (
    KNOWLEDGE_TOOL_NAMES,
    apply_knowledge_toolbox_policy,
)
from miniagent.assistant.tools.schedule_tools import SCHEDULE_TOOL_NAMES

_logger = get_logger(__name__)


def _cli_dot_tools_registration_enabled() -> bool:
    """``cli.dot_tools_enabled`` 为真时注册 ``run_dot_command``（默认开启）。"""
    return bool(get_config("cli.dot_tools_enabled", True))


def _schedule_tools_registration_enabled() -> bool:
    """``scheduled_tools.enabled`` 为真时注册 ``manage_scheduled_task``（默认开启）。"""
    return bool(get_config("scheduled_tools.enabled", True))


def _feishu_im_tools_registration_enabled() -> bool:
    """飞书扩展内置工具；见 ``feishu.tools_explicit`` / ``feishu.tools_auto`` 与凭证。"""
    return feishu_im_tools_should_register()


def register_builtin_tools(registry: ToolRegistryProtocol) -> int:
    """将 ``ALL_TOOLS`` 写入主注册表。

    在 ``init_subsystems`` 第一步调用，早于 ``bootstrap_skill_packages``。
    按配置跳过 CLI 点命令、定时任务与飞书扩展工具；其余条目逐个 ``registry.register``。

    Args:
        registry: 进程主工具注册表（通常为 ``DefaultToolRegistry``）。

    Returns:
        本次成功注册的数量。以下情况**不计入**返回值：

        - 因配置开关被 skip 的工具；
        - 注册表已有同名工具而 catch ``ValueError`` 跳过的条目。

    Note:
        返回值小于 ``len(ALL_TOOLS)`` 并不表示异常，可能由上述 skip 导致。
        重复调用时，已注册名称会被跳过且返回值可能为 0。
    """
    skip_cli_dot = not _cli_dot_tools_registration_enabled()
    skip_schedule = not _schedule_tools_registration_enabled()
    skip_feishu_im = not _feishu_im_tools_registration_enabled()
    n = 0
    for name, tool in ALL_TOOLS.items():
        if skip_cli_dot and name in CLI_DOT_TOOL_NAMES:
            continue
        if skip_schedule and name in SCHEDULE_TOOL_NAMES:
            continue
        if skip_feishu_im and name in FEISHU_EXT_TOOL_NAMES:
            continue
        try:
            if name in KNOWLEDGE_TOOL_NAMES:
                tool = apply_knowledge_toolbox_policy(tool)
            registry.register(name, tool)
            n += 1
        except ValueError:
            _logger.debug(
                '注册表已有同名工具 "%s"，跳过内置定义（内置优先策略）',
                name,
            )
    return n


__all__ = ["register_builtin_tools"]
