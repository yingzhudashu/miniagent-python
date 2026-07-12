"""CLI 与飞书共享的顶层命令元数据注册表。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

CommandChannel = Literal["cli", "feishu"]
CommandHandler = Callable[..., Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """描述一个顶层命令的解析、权限、帮助和处理器身份。"""

    name: str
    handler_key: str
    summary: str
    usage: str
    aliases: tuple[str, ...] = ()
    channels: frozenset[CommandChannel] = frozenset({"cli", "feishu"})
    mutates_state: bool = False

    def __post_init__(self) -> None:
        """在注册阶段拒绝含糊或不可解析的命令元数据。"""
        names = (self.name, *self.aliases)
        if any(not value.startswith("/") or " " in value for value in names):
            raise ValueError(f"命令名必须是无空格的 / 前缀标识: {names!r}")
        if not self.handler_key.strip() or not self.summary.strip() or not self.usage.strip():
            raise ValueError(f"命令元数据不完整: {self.name}")


class CommandRegistry:
    """保持声明顺序的不可变命令索引。"""

    def __init__(self, specs: tuple[CommandSpec, ...]) -> None:
        """建立名称和别名索引，并拒绝重复声明。"""
        by_name: dict[str, CommandSpec] = {}
        for spec in specs:
            for command_name in (spec.name, *spec.aliases):
                normalized = command_name.lower()
                if normalized in by_name:
                    raise ValueError(f"重复命令名或别名: {command_name}")
                by_name[normalized] = spec
        self._specs = specs
        self._by_name = by_name

    @property
    def specs(self) -> tuple[CommandSpec, ...]:
        """返回按补全和前缀匹配优先级排序的声明。"""
        return self._specs

    @property
    def names(self) -> tuple[str, ...]:
        """返回规范命令名，不包含别名。"""
        return tuple(spec.name for spec in self._specs)

    def resolve(self, command_name: str) -> CommandSpec | None:
        """按规范名或别名解析命令。"""
        return self._by_name.get(command_name.lower())

    def first_prefix_match(self, prefix: str) -> CommandSpec | None:
        """按声明顺序返回第一个规范名匹配项。"""
        lowered = prefix.lower()
        return next((spec for spec in self._specs if spec.name.lower().startswith(lowered)), None)

    def bind_handlers(self, handlers: Mapping[str, CommandHandler]) -> BoundCommandRegistry:
        """校验并冻结处理器映射，防止注册表与实际分派能力漂移。

        绑定必须一次性覆盖每个 ``handler_key``，同时拒绝无对应命令的多余处理器。
        该方法返回新视图，不修改命令元数据注册表本身。
        """
        expected = {spec.handler_key for spec in self._specs}
        actual = set(handlers)
        if expected != actual:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(f"命令处理器绑定不完整: missing={missing}, extra={extra}")
        if any(not callable(handler) for handler in handlers.values()):
            raise TypeError("命令处理器必须是可调用对象")
        return BoundCommandRegistry(self, MappingProxyType(dict(handlers)))


@dataclass(frozen=True, slots=True)
class BoundCommandRegistry:
    """命令元数据与异步处理器的一次性、只读绑定。"""

    registry: CommandRegistry
    handlers: Mapping[str, CommandHandler]

    def handler_for(self, command_name: str) -> CommandHandler | None:
        """按规范命令名或别名返回处理器；未知命令返回 ``None``。"""
        spec = self.registry.resolve(command_name)
        return self.handlers.get(spec.handler_key) if spec is not None else None


COMMAND_REGISTRY = CommandRegistry(
    (
        CommandSpec("/help", "help", "显示命令帮助", "/help"),
        CommandSpec("/session", "session", "管理会话", "/session <list|switch|create|rename|delete>", mutates_state=True),
        CommandSpec("/instance", "instance", "查看或停止实例", "/instance <list|stop>", mutates_state=True),
        CommandSpec("/feishu", "feishu", "管理飞书连接", "/feishu <status|start|stop>", mutates_state=True),
        CommandSpec("/queue", "queue", "管理消息队列", "/queue <status|mode|abort>", mutates_state=True),
        CommandSpec("/abort", "abort", "中止当前任务", "/abort", mutates_state=True),
        CommandSpec("/query", "query", "查询活动日志", "/query [条件]"),
        CommandSpec("/btw", "background_task", "管理后台任务", "/btw <start|status|result|cancel|clear>", mutates_state=True),
        CommandSpec("/schedule", "schedule", "管理定时任务", "/schedule <list|show|add|remove|enable|disable>", mutates_state=True),
        CommandSpec("/self-opt", "self_opt", "管理自优化提案", "/self-opt <status|proposals|show|approve|reject|apply|analyze|report>", mutates_state=True),
        CommandSpec("/kb", "knowledge", "管理知识库", "/kb <list|mount|unmount|search|reload>", mutates_state=True),
        CommandSpec("/model", "model", "查看或切换模型", "/model [模型]", mutates_state=True),
        CommandSpec("/config", "config", "查看有效配置", "/config [节]"),
        CommandSpec("/doctor", "doctor", "运行环境诊断", "/doctor"),
        CommandSpec("/stats", "stats", "显示运行统计", "/stats"),
        CommandSpec("/status", "status", "显示 Agent 状态", "/status"),
        CommandSpec("/stop", "stop", "停止当前进程", "/stop", channels=frozenset({"cli"}), mutates_state=True),
        CommandSpec("/confirm", "confirm", "确认待执行操作", "/confirm", mutates_state=True),
        CommandSpec("/adjust", "adjust", "调整待执行计划", "/adjust <说明>", mutates_state=True),
        CommandSpec("/reject", "reject", "拒绝待执行操作", "/reject", mutates_state=True),
        CommandSpec("/review", "review", "复核上一轮结果", "/review [要求]"),
        CommandSpec("/improve", "improve", "改进上一轮结果", "/improve [要求]"),
        CommandSpec("/test", "test", "运行评测样例", "/test <run|list|status>"),
        CommandSpec("/reload-skills", "reload_skills", "重新加载技能", "/reload-skills", mutates_state=True),
        CommandSpec("/reload-config", "reload_config", "重新加载配置", "/reload-config", mutates_state=True),
    )
)

__all__ = [
    "COMMAND_REGISTRY",
    "BoundCommandRegistry",
    "CommandChannel",
    "CommandHandler",
    "CommandRegistry",
    "CommandSpec",
]
