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
    FEISHU_GROUP_PREFIX = "feishu:"     # 飞书群聊通道前缀 + chat_id

    def __init__(self) -> None:
        """初始化空绑定表与主会话指针。"""
        # channel_id → primary_session_id
        self._bindings: dict[str, str] = {}
        # primary_session_id → [channel_id, ...]  反向索引
        self._reverse: dict[str, list[str]] = {}
        # 当前主会话（通过 set_primary 设置）
        self._primary: str | None = None

    # -----------------------------------------------------------------------
    # 绑定/解绑
    # -----------------------------------------------------------------------

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
        if old and old in self._reverse:
            if channel_id in self._reverse[old]:
                self._reverse[old].remove(channel_id)
            if not self._reverse[old]:
                del self._reverse[old]

        # 添加新绑定
        self._bindings[channel_id] = session_id
        if session_id not in self._reverse:
            self._reverse[session_id] = []
        if channel_id not in self._reverse[session_id]:
            self._reverse[session_id].append(channel_id)

        # 自动设置主会话
        if self._primary is None:
            self._primary = session_id

        return old or ""

    def unbind(self, channel_id: str) -> str:
        """解除通道绑定，恢复独立会话。

        Args:
            channel_id: 通道标识

        Returns:
            之前绑定的会话 ID（如果存在）
        """
        old = self._bindings.pop(channel_id, "")

        if old and old in self._reverse:
            if channel_id in self._reverse[old]:
                self._reverse[old].remove(channel_id)
            if not self._reverse[old]:
                del self._reverse[old]

        # 如果没有更多绑定了，清除主会话
        if not self._bindings:
            self._primary = None

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

    def resolve_feishu_message(
        self, chat_id: str, sender_id: str, chat_type: str = "group"
    ) -> str:
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
        else:
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
        lines = []
        lines.append("📡 通道绑定状态")
        lines.append("=" * 30)

        if not self._bindings:
            lines.append("  未绑定任何通道，各通道独立会话")
        else:
            lines.append(f"  主会话: {self._primary or '未设置'}")
            lines.append("")
            for channel_id, session_id in sorted(self._bindings.items()):
                display = self._format_channel(channel_id)
                lines.append(f"  {display} → {session_id}")

        return "\n".join(lines)

    @staticmethod
    def _format_channel(channel_id: str) -> str:
        """格式化通道标识为可读文本。"""
        if channel_id == ChannelRouter.CLI_CHANNEL:
            return "💻 CLI"
        elif channel_id.startswith("feishu_p2p:"):
            sender = channel_id.split(":", 1)[1]
            return f"💬 飞书私聊 ({sender[:8]}...)"
        elif channel_id.startswith("feishu:"):
            chat = channel_id.split(":", 1)[1]
            return f"💬 飞书群聊 ({chat[:8]}...)"
        return channel_id

    # -----------------------------------------------------------------------
    # 持久化（可选）
    # -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """序列化绑定状态。

        Returns:
            可 JSON 序列化的字典
        """
        return {
            "bindings": dict(self._bindings),
            "reverse": {k: list(v) for k, v in self._reverse.items()},
            "primary": self._primary,
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        """从字典恢复绑定状态。

        Args:
            data: 之前 to_dict() 的输出
        """
        self._bindings = dict(data.get("bindings", {}))
        self._reverse = {k: list(v) for k, v in data.get("reverse", {}).items()}
        self._primary = data.get("primary")
