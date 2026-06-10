"""基础设施 — 通道与会话绑定路由器（ChannelRouter）

将不同输入通道（CLI、飞书群聊、飞书私聊）映射到同一个主会话。
所有通道的消息都路由到主会话，记忆/文件/工具全部共享。

未绑定时：每个通道独立会话（保持当前行为）。
绑定后：多通道共享同一主会话。

用户可见行为与命令说明见 ``docs/CHANNEL_BINDING.md``。

Example:
    router = ChannelRouter()
    router.resolve("__cli__")          # → "__cli__"（未绑定，独立会话）
    router.bind(ChannelRouter.CLI_CHANNEL, "oc_xxx")
    router.resolve("__cli__")          # → "oc_xxx"（已绑定到飞书会话）
    router.bind("feishu:ou_abc", "***")
    router.resolve("feishu:ou_abc")    # → "***"（飞书私聊绑定到 CLI 会话）
"""

from __future__ import annotations

import json
import os
from typing import Any


class ChannelRouter:
    """通道-会话路由器。

    维护 channel_id → primary_session_id 的映射关系。
    CLI 和飞书私聊可以互相绑定到对方的主会话。
    群聊始终保持独立会话（不参与绑定）。
    """

    # 内置通道标识
    CLI_CHANNEL = "__cli__"
    FEISHU_P2P_PREFIX = "feishu_p2p:"  # 飞书私聊通道前缀 + sender_id
    FEISHU_GROUP_PREFIX = "feishu:"  # 飞书群聊通道前缀 + chat_id

    def __init__(self) -> None:
        """初始化空绑定表与主会话指针。"""
        # channel_id → primary_session_id
        self._bindings: dict[str, str] = {}
        # primary_session_id → [channel_id, ...]  反向索引
        self._reverse: dict[str, list[str]] = {}
        # 当前主会话（通过 set_primary 设置）
        self._primary: str | None = None
        # CLI 上次会话状态（--continue 功能）
        self._last_cli_session: str | None = None
        self._last_cli_session_number: int = 0
        self._last_cli_session_title: str = ""
        self._last_cli_exit_time: str = ""

    # -----------------------------------------------------------------------
    # 绑定/解绑
    # -----------------------------------------------------------------------

    def _detach_reverse(self, channel_id: str, session_id: str | None) -> None:
        """从反向索引中移除 channel_id → session_id 绑定，并清理空条目。"""
        if not session_id or session_id not in self._reverse:
            return
        if channel_id in self._reverse[session_id]:
            self._reverse[session_id].remove(channel_id)
        if not self._reverse[session_id]:
            del self._reverse[session_id]

    def bind(self, channel_id: str, session_id: str) -> str:
        """绑定通道到指定会话。

        Args:
            channel_id: 通道标识（如 "__cli__" 或 "feishu_p2p:ou_xxx"）
            session_id: 目标主会话 ID

        Returns:
            之前绑定的会话 ID（如果存在）
        """
        old = self._bindings.get(channel_id)

        # 从反向索引中移除旧绑定
        self._detach_reverse(channel_id, old)

        # 添加新绑定
        self._bindings[channel_id] = session_id
        if session_id not in self._reverse:
            self._reverse[session_id] = []
        if channel_id not in self._reverse[session_id]:
            self._reverse[session_id].append(channel_id)

        # 自动设置主会话
        if self._primary is None:
            self._primary = session_id

        self._auto_save()
        return old or ""

    def unbind(self, channel_id: str) -> str:
        """解除通道绑定，恢复独立会话。

        Args:
            channel_id: 通道标识

        Returns:
            之前绑定的会话 ID（如果存在）
        """
        old = self._bindings.pop(channel_id, "")

        self._detach_reverse(channel_id, old)

        # 如果没有更多绑定了，清除主会话
        if not self._bindings:
            self._primary = None

        self._auto_save()
        return old

    def unbind_all(self) -> None:
        """解除所有通道绑定，恢复为各通道独立会话。"""
        self._bindings.clear()
        self._reverse.clear()
        self._primary = None

    # -----------------------------------------------------------------------
    # 解析
    # -----------------------------------------------------------------------

    def resolve(self, channel_id: str) -> str:
        """解析通道对应的会话 ID。

        未绑定时返回 channel_id 本身（保持当前独立会话行为）。

        Args:
            channel_id: 通道标识

        Returns:
            会话 ID
        """
        return self._bindings.get(channel_id, channel_id)

    def resolve_feishu_message(self, chat_id: str, sender_id: str, chat_type: str = "group") -> str:
        """从飞书消息解析会话 ID。

        - 群聊消息: 始终返回 "feishu:chat_id"（独立会话，不参与绑定）
        - 私聊消息: 返回 "feishu_p2p:sender_id"，检查是否有绑定

        Args:
            chat_id: 飞书聊天室 ID
            sender_id: 发送者 ID
            chat_type: "group" 或 "p2p"

        Returns:
            会话 ID
        """
        if chat_type == "p2p":
            channel_id = f"{self.FEISHU_P2P_PREFIX}{sender_id}"
            return self.resolve(channel_id)
        # 群聊：固定 feishu:<chat_id>，不查 _bindings（与 CHANNEL_BINDING 文档一致）
        return f"{self.FEISHU_GROUP_PREFIX}{chat_id}"

    # -----------------------------------------------------------------------
    # 主会话
    # -----------------------------------------------------------------------

    def set_primary(self, session_id: str) -> None:
        """设置主会话。

        后续调用 bind() 但未显式指定主会话时，默认绑定到此会话。

        Args:
            session_id: 主会话 ID
        """
        self._primary = session_id
        self._auto_save()

    @property
    def primary(self) -> str | None:
        """当前主会话 ID。"""
        return self._primary

    # -----------------------------------------------------------------------
    # 查询
    # -----------------------------------------------------------------------

    def get_bound_channels(self, session_id: str) -> list[str]:
        """获取绑定到某会话的所有通道。

        Args:
            session_id: 会话 ID

        Returns:
            通道标识列表
        """
        return list(self._reverse.get(session_id, []))

    def get_all_bindings(self) -> dict[str, str]:
        """获取所有绑定关系。

        Returns:
            {channel_id: session_id}
        """
        return dict(self._bindings)

    def is_bound(self, channel_id: str) -> bool:
        """检查通道是否已绑定。

        Args:
            channel_id: 通道标识

        Returns:
            True 如果已绑定
        """
        return channel_id in self._bindings

    def status(self) -> str:
        """返回绑定状态的人类可读描述。

        Returns:
            状态字符串
        """
        lines = ["📡 通道绑定状态", "=" * 30]

        if not self._bindings:
            lines.append("  未绑定任何通道，各通道独立会话")
        else:
            lines.append(f"  主会话: {self._primary or '未设置'}")
            lines.append("")
            for channel_id, session_id in sorted(self._bindings.items()):
                display = self._format_channel(channel_id)
                lines.append(f"  {display} → {session_id}")

        from miniagent.infrastructure.cli_feishu_policy import focus_mode_status_line

        lines.append("")
        lines.append(focus_mode_status_line(self))

        return "\n".join(lines)

    @staticmethod
    def _format_channel(channel_id: str) -> str:
        """格式化通道标识为可读文本。"""
        if channel_id == ChannelRouter.CLI_CHANNEL:
            return "💻 CLI"
        if channel_id.startswith("feishu_p2p:"):
            sender = channel_id.split(":", 1)[1]
            return f"💬 飞书私聊 ({sender[:8]}...)"
        if channel_id.startswith("feishu:"):
            chat = channel_id.split(":", 1)[1]
            return f"💬 飞书群聊 ({chat[:8]}...)"
        return channel_id

    # -----------------------------------------------------------------------
    # 持久化（可选）
    # -------------------------------------------------------------------

    def _state_dir(self) -> str:
        """返回状态目录路径。"""
        from miniagent.infrastructure.paths import resolve_state_dir

        return resolve_state_dir()

    def _state_file(self) -> str | None:
        """返回持久化文件路径；未配置 paths.state_dir 时返回 None。"""
        d = self._state_dir()
        if not d:
            return None
        return os.path.join(d, "channel-router.json")

    def _auto_save(self) -> None:
        """若设置了状态目录则写入磁盘。"""
        p = self._state_file()
        if p:
            self.save(path=p)

    def save(self, path: str | None = None) -> str:
        """将绑定状态写入磁盘 JSON 文件。

        Args:
            path: 可选的完整路径；默认使用 `{paths.state_dir}/channel-router.json`

        Returns:
            写入的文件路径
        """
        if path is None:
            p = self._state_file()
            if p is None:
                raise ValueError("未配置 paths.state_dir，需传入 path 参数")
        else:
            p = path

        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return p

    def load(self, path: str | None = None) -> bool:
        """从磁盘加载绑定状态。

        Args:
            path: 可选的完整路径；默认使用 `{paths.state_dir}/channel-router.json`

        Returns:
            True 如果成功加载，False 如果文件不存在
        """
        if path is None:
            p = self._state_file()
            if p is None or not os.path.isfile(p):
                return False
        else:
            p = path
            if not os.path.isfile(p):
                return False

        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        self.from_dict(data)
        return True

    def to_dict(self) -> dict[str, Any]:
        """序列化绑定状态。

        Returns:
            可 JSON 序列化的字典
        """
        return {
            "bindings": dict(self._bindings),
            "reverse": {k: list(v) for k, v in self._reverse.items()},
            "primary": self._primary,
            "last_cli_session": self._last_cli_session,
            "last_cli_session_number": self._last_cli_session_number,
            "last_cli_session_title": self._last_cli_session_title,
            "last_cli_exit_time": self._last_cli_exit_time,
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        """从字典恢复绑定状态。

        Args:
            data: 之前 to_dict() 的输出
        """
        self._bindings = dict(data.get("bindings", {}))
        self._reverse = {k: list(v) for k, v in data.get("reverse", {}).items()}
        self._primary = data.get("primary")
        self._last_cli_session = data.get("last_cli_session")
        self._last_cli_session_number = data.get("last_cli_session_number", 0)
        self._last_cli_session_title = data.get("last_cli_session_title", "")
        self._last_cli_exit_time = data.get("last_cli_exit_time", "")

    # -----------------------------------------------------------------------
    # CLI 会话状态持久化（--continue 功能）
    # -----------------------------------------------------------------------

    def save_cli_session_state(
        self,
        session_id: str,
        session_number: int,
        session_title: str,
        exit_time: str | None = None,
    ) -> None:
        """保存 CLI 上次活跃会话状态。

        Args:
            session_id: 会话 ID
            session_number: 会话编号
            session_title: 会话标题
            exit_time: 退出时间（ISO 格式）；默认当前时间
        """
        from datetime import datetime, timezone

        self._last_cli_session = session_id
        self._last_cli_session_number = session_number
        self._last_cli_session_title = session_title
        self._last_cli_exit_time = exit_time or datetime.now(timezone.utc).isoformat()
        self._auto_save()

    def load_cli_session_state(self) -> dict[str, Any]:
        """加载 CLI 上次活跃会话状态。

        Returns:
            包含 last_cli_session, last_cli_session_number, last_cli_session_title,
            last_cli_exit_time 的字典；无记录时返回空字典
        """
        if not self._last_cli_session:
            return {}
        return {
            "last_cli_session": self._last_cli_session,
            "last_cli_session_number": self._last_cli_session_number,
            "last_cli_session_title": self._last_cli_session_title,
            "last_cli_exit_time": self._last_cli_exit_time,
        }


# ============================================================================
# 测试辅助函数
# ============================================================================

_default_router: ChannelRouter | None = None


def get_channel_router() -> ChannelRouter | None:
    """获取进程级 ChannelRouter 单例（如果已初始化）。"""
    return _default_router


def set_channel_router(router: ChannelRouter) -> None:
    """设置进程级 ChannelRouter 单例。"""
    global _default_router
    _default_router = router


def reset_channel_router_for_tests() -> None:
    """清空 ChannelRouter 缓存，仅供测试使用。"""
    global _default_router
    _default_router = None


__all__ = [
    "ChannelRouter",
    "get_channel_router",
    "set_channel_router",
    "reset_channel_router_for_tests",
]
