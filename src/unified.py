"""Mini Agent Python — 统一启动器

同时启动 CLI + 飞书 WebSocket 服务器，共享同一套子系统。

用法:
    python -m src --unified        # CLI + 飞书同时运行
    python -m src --unified --cli  # 仅 CLI（默认）
    python -m src --unified --feishu  # 仅飞书

架构:
- 一套 registry / monitor / skill_registry / session_manager
- CLI 通过 stdin 交互，飞书通过 WebSocket 长轮询
- 两边通过 session_key 路由到同一个 SessionManager
- CLI 默认使用 "cli-interactive" 会话
- 飞书每个 chat_id 自动创建/获取独立会话
- CLI 用户可以通过 .session switch 切换到飞书会话查看/操作
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


# ── 初始化 ──

async def init_subsystems():
    """初始化所有共享子系统。"""
    global session_manager

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
    print(f"{'='*60}\n")


# ── CLI 交互 ──

async def run_cli_loop(skill_toolboxes, skill_prompts):
    """CLI 交互循环。"""
    global active_session_id, log_file

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
        skip_planning = user_input.startswith(".plan ")
        actual_input = user_input[6:] if skip_planning else user_input

        try:
            session_ctx = session_manager.get_or_create(active_session_id)

            reply = await run_agent(
                actual_input,
                registry=registry,
                monitor=monitor,
                toolboxes=skill_toolboxes,
                skip_planning=skip_planning,
                agent_config={
                    "debug": False,
                    "log_file": log_file,
                    "session_key": active_session_id,
                    "conversation_history": session_ctx.conversation_history,
                },
                system_prompt="\n\n".join(skill_prompts) if skill_prompts else None,
            )
            print(f"\n🦾 {reply}\n")

            session_ctx.conversation_history.append({"role": "user", "content": actual_input})
            session_ctx.conversation_history.append({"role": "assistant", "content": reply})

        except Exception as e:
            print(f"\n❌ 错误: {e}\n")

    print("\n👋 bye")


# ── 飞书处理器 ──

def create_feishu_handler(skill_toolboxes, skill_prompts):
    """创建飞书消息处理器。"""
    _histories: dict[str, list[dict[str, str]]] = {}

    async def handler(content: str, chat_id: str, sender_id: str) -> str:
        if chat_id not in _histories:
            _histories[chat_id] = []
            # 通过 session_manager 也会创建对应会话
            session_manager.get_or_create(chat_id, SessionOptions(description=f"飞书: {chat_id}"))

        history = _histories[chat_id]

        try:
            reply = await run_agent(
                content,
                registry=registry,
                monitor=monitor,
                toolboxes=skill_toolboxes,
                skip_planning=False,
                agent_config={
                    "session_key": chat_id,
                    "conversation_history": history,
                    "debug": False,
                },
                system_prompt="\n\n".join(skill_prompts) if skill_prompts else None,
            )

            history.append({"role": "user", "content": content})
            history.append({"role": "assistant", "content": reply})
            if len(history) > 40:
                _histories[chat_id] = history[-40:]

            return reply
        except Exception as e:
            return f"⚠️ 处理失败: {e}"

    return handler


# ── 统一启动 ──

async def unified_main():
    """统一启动：CLI + 飞书。"""
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
