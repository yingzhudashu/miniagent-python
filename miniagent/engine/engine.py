"""Engine — UnifiedEngine 核心引擎

拆分自 unified.py。

职责：
- 会话上下文管理
- Agent 执行编排（含思考回调）
- 会话历史持久化
- 飞书思考推送
- 集成：context_manager、memory_store、activity_log
"""

from __future__ import annotations

from typing import Any

from miniagent.core.agent import run_agent
from miniagent.memory.context import DefaultContextManager
from miniagent.memory.defaults import resolve_memory_dependencies
from miniagent.memory.store import format_memory_for_prompt
from miniagent.session.manager import SessionOptions


class UnifiedEngine:
    """统一管理引擎。

    将用户输入传递给 Agent，管理会话历史和思考显示。
    集成上下文管理、跨会话记忆、活动日志。

    依赖注入：
    - registry: 工具注册表（运行时注入）
    - monitor: 性能监控器（运行时注入）
    - session_manager: 会话管理器（运行时注入）
    - feishu_config: 飞书配置（运行时注入）
    """

    def __init__(self) -> None:
        """初始化引擎：创建思考显示器、计数器、上下文管理器字典。"""
        from miniagent.engine.thinking import ThinkingDisplay

        self.thinking = ThinkingDisplay()
        self._conversation_counter: int = 0
        self._context_managers: dict[str, DefaultContextManager] = {}

    async def run_agent_with_thinking(
        self,
        user_input: str,
        session_key: str,
        skill_toolboxes: list,
        skill_prompts: str | None,
        *,
        is_feishu: bool = False,
        registry: Any = None,
        monitor: Any = None,
        session_manager: Any = None,
        feishu_config: Any = None,
        channel_router: Any = None,
        clawhub: Any | None = None,
        memory_store: Any | None = None,
        activity_log: Any | None = None,
        keyword_index: Any | None = None,
        client: Any | None = None,
    ) -> str:
        """运行 agent 并显示思考过程。

        CLI: 终端实时显示
        飞书: 缓冲思考步骤，完成后发送

        Args:
            user_input: 用户输入
            session_key: 会话标识符
            skill_toolboxes: 可用工具箱
            skill_prompts: 技能系统提示词
            is_feishu: 当前请求是否来自飞书通道（非独立启动形态；进程始终带 CLI）
            registry: 工具注册表（注入）
            monitor: 性能监控器（注入）
            session_manager: 会话管理器（注入）
            feishu_config: 飞书配置（注入）
            channel_router: 通道路由器（飞书思考多通道回调时使用）
            clawhub: ClawHub 客户端（注入至工具上下文，技能搜索/安装复用）
            memory_store: 记忆存储（默认与 ``MINI_AGENT_STATE`` 进程 bundle 一致）
            activity_log: 活动日志（同上）
            keyword_index: 关键词索引（同上）
            client: LLM 客户端（``None`` 时由 ``run_agent`` 回落到共享工厂）
        """
        ms, al, ki = resolve_memory_dependencies(memory_store, activity_log, keyword_index)

        # 1. 获取会话
        session_opts = SessionOptions(
            description=f"{'飞书' if is_feishu else 'CLI'}: {session_key}"
        )
        ctx = session_manager.get_or_create(session_key, session_opts)
        history = ctx.conversation_history

        # 2. 加载跨会话记忆
        memory = await ms.load(session_key)
        memory_text = format_memory_for_prompt(memory)

        # 3. 重置该会话的思考计数器（每个会话独立计数，多群并发安全）
        self.thinking.reset_counter(session_key)

        # 4. 飞书通道：启用飞书思考回调（与 CLI 终端展示并行）
        #    每个会话独立注册回调，多群聊并发时互不覆盖
        #    如果该会话有多个绑定通道（如 CLI 绑定到此），思考内容同时发送到所有通道
        if is_feishu and feishu_config:
            router = channel_router
            if router is None:
                raise ValueError("channel_router 为必填（飞书会话且提供 feishu_config 时）")
            bound_channels = router.get_bound_channels(session_key)

            async def _feishu_send(chat_id: str, text: str, template: str) -> None:
                """飞书思考消息发送回调。"""
                from miniagent.feishu.poll_server import _send_thinking
                await _send_thinking(feishu_config, chat_id, text, template)

            # 如果 CLI 也绑定到此会话，注册双回调（终端 + 飞书）
            if router.CLI_CHANNEL in bound_channels:
                async def _dual_send(chat_id: str, text: str, template: str) -> None:
                    """双通道：飞书 + CLI transcript（由 ThinkingDisplay._output_sink 镜像）。"""
                    from miniagent.feishu.poll_server import _send_thinking
                    await _send_thinking(feishu_config, chat_id, text, template)

                self.thinking.enable_feishu(session_key, session_key, _dual_send)
            else:
                self.thinking.enable_feishu(session_key, session_key, _feishu_send)

        # 5. 思考回调（支持流式更新）
        async def _thinking(text: str, streaming: bool = False, header: str = "") -> None:
            await self.thinking.show(text, session_key if is_feishu else "", streaming=streaming, header=header)

        # 6. 调用 Agent
        reply = await run_agent(
            user_input,
            registry=registry,
            monitor=monitor,
            toolboxes=skill_toolboxes,
            skip_planning=False,
            agent_config={
                "session_key": session_key,
                "conversation_history": history,
                "debug": False,
            },
            system_prompt=(f"{skill_prompts}\n\n{memory_text}" if memory_text else skill_prompts),
            on_thinking=_thinking,
            clawhub=clawhub,
            memory_store=ms,
            activity_log=al,
            keyword_index=ki,
            client=client,
        )
        # 流式思考最后一 chunk 往往不以换行结束；否则下一区块（分隔线/回复）会黏在同一行。
        self.thinking.end_thinking()

        # 7. 飞书：思考已实时发送，清理该会话的思考状态
        if is_feishu:
            self.thinking.disable_buffer(session_key)

        # 8. 更新历史
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": reply})
        if len(history) > 40:
            del history[: len(history) - 40]

        # 9. 活动日志
        al.log_session_start(
            session_key, user_input, source="feishu" if is_feishu else "cli"
        )
        al.log_final_reply(session_key, reply)

        # 10. 持久化
        self._conversation_counter += 1
        if session_manager:
            session_manager.save_session_history(session_key)
            self._save_numbered_history(session_key, history)

        # 11. 更新记忆存储
        try:
            from miniagent.memory.store import extract_facts, generate_turn_summary

            summary = generate_turn_summary(user_input, [], reply)
            facts = extract_facts(reply)
            await ms.update_summary(session_key, summary, facts)
        except Exception:
            pass

        return reply

    def _save_numbered_history(self, session_key: str, history: list[dict]) -> None:
        """保存带编号的会话历史（已废弃）。

        不再每轮创建独立文件，改为仅更新 history.json。
        保留此方法避免旧代码调用报错。
        """
        pass

    def inject_message(self, session_key: str, content: str, *, session_manager: Any) -> None:
        """向指定会话注入消息。

        Args:
            session_key: 会话标识符
            content: 消息内容
            session_manager: 当前进程的会话管理器（由 ``RuntimeContext`` / 启动流程持有）
        """
        if session_manager:
            ctx = session_manager.get_or_create(session_key)
            ctx.conversation_history.append(
                {"role": "user", "content": content, "_injected": True}
            )

    def get_context_manager(self, session_key: str) -> DefaultContextManager | None:
        """获取会话的上下文管理器（用于 token 估算）。"""
        return self._context_managers.get(session_key)


__all__ = ["UnifiedEngine"]
