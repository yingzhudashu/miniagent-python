"""Mini Agent Python — 统一启动器

同时启动 CLI + 飞书 WebSocket 服务器，共享同一套子系统。

用法:
    python -m src --unified        # CLI + 飞书同时运行
    python -m src --unified --cli  # 仅 CLI（默认）
    python -m src --unified --feishu  # 仅飞书

架构:
- 一套 registry / monitor / skill_registry / session_manager
- CLI 通过 stdin 交互，飞书通过 WebSocket 长轮询
- 共享 UnifiedEngine 管理思考回调和会话路由
- CLI 输入可注入到任意会话（包括飞书会话）
- 飞书思考过程实时显示在 CLI 终端
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import signal
from pathlib import Path
from typing import Any

# ── 核心子系统 ──
from src.core.agent import run_agent
from src.core.executor import MODEL
from src.core.config import MODEL_PROFILES
from src.core.instance_manager import try_acquire_instance, force_acquire_instance, release_instance, stop_instance
from src.core.registry import DefaultToolRegistry
from src.core.monitor import DefaultToolMonitor
from src.skills.registry import DefaultSkillRegistry
from src.skills.loader import discover_skill_packages
from src.skills.clawhub_client import create_clawhub_client, search_local_skills
from src.session.manager import DefaultSessionManager as SessionManager
from src.session.manager import SessionOptions
from src.tools.filesystem import filesystem_tools
from src.tools.exec import exec_tools
from src.tools.web import web_tools
from src.tools.skills import skills_tools
from src.tools.self_opt import self_opt_tools
from src.security.sandbox import get_default_workspace

# ── 全局状态 ──
registry = DefaultToolRegistry()
monitor = DefaultToolMonitor()
skill_registry = DefaultSkillRegistry()
clawhub = create_clawhub_client()
session_manager: SessionManager | None = None

# 注册内置工具
for name, tool in filesystem_tools.items():
    registry.register(name, tool)
for name, tool in exec_tools.items():
    registry.register(name, tool)
for name, tool in web_tools.items():
    registry.register(name, tool)
for name, tool in skills_tools.items():
    registry.register(name, tool)
for name, tool in self_opt_tools.items():
    registry.register(name, tool)

active_profile = os.environ.get("MODEL_PROFILE", "balanced")
active_session_id = "cli-interactive"
log_file: str | None = None


# ═══════════════════════════════════════════════════════════
# Unified Engine — 共享思考回调 + 会话路由
# ═══════════════════════════════════════════════════════════

class ThinkingDisplay:
    """终端思考过程显示（CLI + 飞书共享）"""

    def __init__(self) -> None:
        self._active_sessions: dict[str, bool] = {}

    def mark_active(self, chat_id: str, active: bool) -> None:
        if active:
            self._active_sessions[chat_id] = True
        else:
            self._active_sessions.pop(chat_id, None)

    def show(self, text: str, chat_id: str = "") -> None:
        prefix = f"[{chat_id[:8]}] " if chat_id else ""
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if i == 0:
                print(f"  💭 {prefix}{line}")
            else:
                print(f"     {prefix}{line}")


class UnifiedEngine:
    """统一管理引擎

    职责：
    - 共享思考回调：飞书处理时的思考实时显示在 CLI
    - 会话路由：CLI 可注入消息到任意会话
    - 会话管理：CLI 和飞书共享 session_manager
    """

    def __init__(self) -> None:
        self.thinking = ThinkingDisplay()
        self._feishu_sessions: dict[str, list[dict[str, str]]] = {}

    async def run_agent_with_thinking(
        self,
        user_input: str,
        chat_id: str,
        skill_toolboxes: list,
        skill_prompts: str | None,
        *,
        is_feishu: bool = False,
    ) -> str:
        """运行 agent 并实时显示思考过程"""
        history = self._get_or_create_history(chat_id)
        session_opts = SessionOptions(description=f"{'飞书' if is_feishu else 'CLI'}: {chat_id}")
        session_manager.get_or_create(chat_id, session_opts)

        self.thinking.mark_active(chat_id, True)

        on_thinking = None
        if is_feishu:
            cid = chat_id

            async def _thinking(text: str) -> None:
                self.thinking.show(text, cid)

            on_thinking = _thinking
        else:
            async def _cli_thinking(text: str) -> None:
                self.thinking.show(text)

            on_thinking = _cli_thinking

        reply = await run_agent(
            user_input,
            registry=registry,
            monitor=monitor,
            toolboxes=skill_toolboxes,
            skip_planning=False,
            agent_config={
                "session_key": chat_id,
                "conversation_history": history,
                "debug": False,
            },
            system_prompt=skill_prompts,
            on_thinking=on_thinking,
        )

        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": reply})
        if len(history) > 40:
            self._feishu_sessions[chat_id] = history[-40:]

        self.thinking.mark_active(chat_id, False)
        return reply

    def inject_message(self, chat_id: str, content: str) -> None:
        """向指定会话注入消息"""
        history = self._get_or_create_history(chat_id)
        history.append({"role": "user", "content": content, "_injected": True})

    def _get_or_create_history(self, chat_id: str) -> list[dict[str, str]]:
        if chat_id not in self._feishu_sessions:
            self._feishu_sessions[chat_id] = []
        return self._feishu_sessions[chat_id]


# 全局引擎实例
engine: UnifiedEngine | None = None


# ── 初始化 ──

async def init_subsystems():
    """初始化所有共享子系统。"""
    global session_manager, engine

    engine = UnifiedEngine()

    # 加载技能
    skills_root = os.environ.get(
        "MINI_AGENT_SKILLS",
        str(Path(__file__).parent / "skills"),
    )
    loaded_skills = []
    if os.path.isdir(skills_root):
        packages = await discover_skill_packages(skills_root)
        for pkg in packages:
            skill_registry.register_package(pkg)
            loaded_skills.extend(pkg.skills)
            for skill in pkg.skills:
                if skill.tools:
                    for name, tool in skill.tools.items():
                        try:
                            registry.register(name, tool)
                        except ValueError:
                            pass

    # 工具箱
    skill_toolboxes = skill_registry.get_all_toolboxes()
    skill_prompts = skill_registry.get_system_prompts()

    # 初始化 SessionManager
    session_manager = SessionManager(registry, skill_toolboxes, loaded_skills)
    session_manager.get_or_create(active_session_id, SessionOptions(description="CLI 交互会话"))

    # 关键词索引清理
    try:
        from src.core.keyword_index import KeywordIndex
        ki = KeywordIndex()
        ki.load()
        pruned = ki.prune_expired(30)
    except Exception:
        pass

    return loaded_skills, skill_toolboxes, skill_prompts


def print_welcome(feishu_enabled: bool = False):
    """显示启动信息。"""
    print(f"\n{'='*60}")
    print(f"🤖 Mini Agent 统一模式已启动")
    print(f"📡 模型: {MODEL} | 预设: {active_profile}")
    print(f"📂 工作空间: {get_default_workspace()}")
    print(f"🔧 工具: {', '.join(registry.list())}")
    print(f"📋 CLI 会话: {active_session_id}")
    if feishu_enabled:
        print(f"💬 飞书: 已启用（WebSocket 长轮询）")
        print(f"💡 飞书消息处理时的思考过程将在此终端显示")
    print(f"{'='*60}\n")


# ── CLI 交互 ──

async def run_cli_loop(skill_toolboxes, skill_prompts):
    """CLI 交互循环。"""
    global active_session_id, log_file, engine

    while True:
        try:
            user_input = await asyncio.to_thread(input, "> ")
        except (EOFError, KeyboardInterrupt):
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            break

        # ── 内置命令 ──
        if user_input == ".stop":
            result = stop_instance()
            if result.get("success"):
                print("✅ 实例已停止")
            sys.exit(0)

        if user_input == ".stats":
            print(f"\n{monitor.report()}")
            continue

        if user_input == ".sessions":
            sessions = session_manager.list()
            print("\n🧩 会话列表:")
            for s in sessions:
                marker = " ← 当前" if s.id == active_session_id else ""
                print(f"  - {s.id}{marker} | 工具: {s.turn_count} 轮")
            # 显示引擎中的飞书会话
            if engine and engine._feishu_sessions:
                print("\n📡 飞书会话:")
                for cid, hist in engine._feishu_sessions.items():
                    active = " (活跃)" if engine.thinking._active_sessions.get(cid) else ""
                    print(f"  - {cid}{active} | {len(hist)//2} 轮对话")
            continue

        if user_input.startswith(".session "):
            parts = user_input.split()
            sub_cmd = parts[1] if len(parts) > 1 else ""
            if sub_cmd == "switch" and len(parts) >= 3:
                target_id = parts[2]
                ctx = session_manager.get_or_create(target_id)
                if ctx:
                    active_session_id = target_id
                    print(f"🔄 已切换到会话: {target_id}")
                else:
                    print(f"❌ 会话不存在: {target_id}")
            elif sub_cmd == "list":
                sessions = session_manager.list()
                print("\n🧩 会话列表:")
                for s in sessions:
                    print(f"  - {s.id} ({s.description})")
            else:
                print("用法: .session switch <id> | .session list")
            continue

        # ── 注入消息到指定会话 ──
        if user_input.startswith(".send "):
            parts = user_input.split(" ", 2)
            if len(parts) >= 3:
                target_id = parts[1]
                message = parts[2]
                if engine:
                    engine.inject_message(target_id, message)
                    print(f"📤 已注入消息到 {target_id}")
                    # 如果目标是当前会话，直接处理
                    if target_id == active_session_id:
                        try:
                            reply = await engine.run_agent_with_thinking(
                                message, target_id, skill_toolboxes, skill_prompts
                            )
                            print(f"\n🦾 {reply}\n")
                        except Exception as e:
                            print(f"\n❌ 错误: {e}\n")
                else:
                    print("❌ 引擎未初始化")
            else:
                print("用法: .send <session_id> <message>")
            continue

        if user_input.startswith(".profile"):
            parts = user_input.split()
            if len(parts) >= 2 and parts[1] in MODEL_PROFILES:
                active_profile = parts[1]
                print(f"📡 已切换到预设: {parts[1]}")
            else:
                print(f"当前预设: {active_profile}")
                print("可用: " + ", ".join(MODEL_PROFILES.keys()))
            continue

        # ── Agent 执行 ──
        try:
            session_ctx = session_manager.get_or_create(active_session_id)
            print(f"\n👤 You {user_input}")

            reply = await engine.run_agent_with_thinking(
                user_input,
                active_session_id,
                skill_toolboxes,
                "\n\n".join(skill_prompts) if skill_prompts else None,
            )
            print(f"\n🦾 Agent\n  {reply}\n")

            # 同步到 session_manager（.sessions 等命令可见）
            session_ctx.conversation_history.append({"role": "user", "content": user_input})
            session_ctx.conversation_history.append({"role": "assistant", "content": reply})

        except Exception as e:
            print(f"\n❌ 错误: {e}\n")

    print("\n👋 bye")


# ── 飞书处理器 ──

def create_feishu_handler(skill_toolboxes, skill_prompts):
    """创建飞书消息处理器（共享引擎）。"""

    async def handler(content: str, chat_id: str, sender_id: str) -> str:
        if not engine:
            return "⚠️ 引擎未初始化"

        try:
            print(f"\n📨 [飞书 {chat_id[:8]}] {content}")
            reply = await engine.run_agent_with_thinking(
                content,
                chat_id,
                skill_toolboxes,
                "\n\n".join(skill_prompts) if skill_prompts else None,
                is_feishu=True,
            )
            return reply
        except Exception as e:
            return f"⚠️ 处理失败: {e}"

    return handler


# ── 统一启动 ──

async def unified_main():
    """统一启动：CLI + 飞书。"""
    # Windows UTF-8（必须在任何 emoji 输出之前）
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # 单实例检查
    force_mode = "--force" in sys.argv
    instance_result = force_acquire_instance() if force_mode else try_acquire_instance()
    if not instance_result.get("success"):
        if "existing_pid" in instance_result:
            print(f"❌ Mini Agent 已在运行 (PID={instance_result.get('existing_pid')})")
        sys.exit(1)

    def _on_exit():
        try:
            release_instance()
        except Exception:
            pass

    signal.signal(signal.SIGINT, lambda *_: (_on_exit(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (_on_exit(), sys.exit(0)))

    # 初始化
    loaded_skills, skill_toolboxes, skill_prompts = await init_subsystems()

    # Windows UTF-8
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    feishu_enabled = "--feishu" in sys.argv

    # 启动飞书服务器（如果启用）
    feishu_task = None
    if feishu_enabled:
        from src.feishu.poll_server import start_feishu_poll_server
        from src.feishu.types import FeishuConfig

        config = FeishuConfig(
            app_id=os.environ.get("FEISHU_APP_ID", ""),
            app_secret=os.environ.get("FEISHU_APP_SECRET", ""),
            verification_token=os.environ.get("FEISHU_VERIFICATION_TOKEN", ""),
        )

        handler = create_feishu_handler(skill_toolboxes, skill_prompts)

        async def _start_feishu():
            try:
                await start_feishu_poll_server(config, handler)
            except Exception as e:
                print(f"[飞书] 启动失败: {e}")

        feishu_task = asyncio.create_task(_start_feishu())

    print_welcome(feishu_enabled)

    # 运行 CLI 循环
    await run_cli_loop(skill_toolboxes, skill_prompts)

    # 清理
    if feishu_task:
        feishu_task.cancel()
    release_instance()


def unified_entry():
    """统一入口。"""
    try:
        from dotenv import load_dotenv
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path)
    except ImportError:
        pass

    asyncio.run(unified_main())
