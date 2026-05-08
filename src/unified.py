"""Mini Agent Python — 核心引擎

多实例支持 + 会话级锁 + 运行时飞书插件。

架构:
    ┌─────────────┐
    │  Core Agent  │
    │  run_agent() │
    └──────┬───────┘
           │
    ┌──────┼──────────┐
    │      │          │
┌───▼──┐ ┌─▼────┐ ┌──▼──────┐
│ CLI  │ │Feishu│ │ Future  │
│stdin │ │ (WS) │ │ (API?)  │
└──────┘ └──────┘ └─────────┘

特性:
- 多实例并行：每个实例独立运行，通过会话级 .lock 文件隔离
- 会话编号：自动编号 #1, #2, #3... 标题可重命名
- 飞书思考：实时推送思考过程到飞书卡片
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import signal
import time
import subprocess
from pathlib import Path
from typing import Any

# ── 核心子系统 ──
from src.core.agent import run_agent
from src.core.executor import MODEL
from src.core.config import MODEL_PROFILES
from src.core.registry import DefaultToolRegistry
from src.core.monitor import DefaultToolMonitor
from src.skills.registry import DefaultSkillRegistry
from src.skills.loader import discover_skill_packages
from src.skills.clawhub_client import create_clawhub_client
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
active_session_id: str = ""
log_file: str | None = None

# ── 飞书运行时状态 ──
feishu_task: asyncio.Task | None = None
feishu_running = False
feishu_config: Any = None  # 保存 FeishuConfig 引用，供思考回调使用

# ── 会话历史持久化计数器 ──
_conversation_counter = 0


# ═══════════════════════════════════════════════════════════
# 会话级锁管理
# ═══════════════════════════════════════════════════════════

def _get_lock_path(session_id: str) -> str:
    """获取会话锁文件路径。"""
    from src.session.manager import _get_workspaces_dir
    safe = session_id.replace("/", "_").replace("\\", "_")
    return os.path.join(_get_workspaces_dir(), safe, ".lock")


def _is_process_running(pid: int) -> bool:
    """检测 PID 是否存活。"""
    try:
        if sys.platform == "win32":
            output = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                timeout=5, text=True,
            )
            return f'"{pid}"' in output
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def try_lock_session(session_id: str) -> tuple[bool, str]:
    """尝试获取会话锁。

    Returns:
        (success, reason) — success=True 表示锁获取成功
    """
    lock_path = _get_lock_path(session_id)
    my_pid = os.getpid()

    # 确保目录存在
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)

    # 如果锁已存在，检查所有者
    if os.path.exists(lock_path):
        try:
            with open(lock_path, "r") as f:
                locked_pid = int(f.read().strip())
            if locked_pid == my_pid:
                return True, ""  # 我自己锁的
            if _is_process_running(locked_pid):
                return False, f"被其他实例占用 (PID={locked_pid})"
            # 进程已死，清理过期锁
            try:
                os.unlink(lock_path)
            except OSError:
                pass
        except (ValueError, OSError):
            try:
                os.unlink(lock_path)
            except OSError:
                pass

    with open(lock_path, "w") as f:
        f.write(str(my_pid))
    return True, ""


def release_session_lock(session_id: str) -> None:
    """释放会话锁。"""
    lock_path = _get_lock_path(session_id)
    try:
        if os.path.exists(lock_path):
            with open(lock_path, "r") as f:
                locked_pid = int(f.read().strip())
            if locked_pid == os.getpid():
                os.unlink(lock_path)
    except Exception:
        pass


def is_session_locked(session_id: str) -> int | None:
    """检查会话是否被其他实例锁定。

    Returns:
        占用者的 PID，或 None 表示未被锁定
    """
    lock_path = _get_lock_path(session_id)
    if not os.path.exists(lock_path):
        return None
    try:
        with open(lock_path, "r") as f:
            locked_pid = int(f.read().strip())
        if locked_pid == os.getpid():
            return None
        if _is_process_running(locked_pid):
            return locked_pid
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════
# ThinkingDisplay — 带序号的思考过程显示
# ═══════════════════════════════════════════════════════════

class ThinkingDisplay:
    """思考过程显示（CLI 终端 + 飞书缓冲）"""

    def __init__(self) -> None:
        self._step_counter: int = 0
        self._buffer: list[str] = []
        self._buffer_enabled: bool = False

    def reset_counter(self) -> None:
        self._step_counter = 0
        self._buffer.clear()

    def enable_buffer(self) -> None:
        """启用缓冲模式（飞书用）。"""
        self._buffer_enabled = True
        self._buffer.clear()

    def disable_buffer(self) -> None:
        """禁用缓冲模式（CLI 用）。"""
        self._buffer_enabled = False
        self._buffer.clear()

    def get_buffered(self) -> str:
        """获取缓冲的思考内容。"""
        return "\n".join(self._buffer)

    def _next_step(self) -> int:
        step = self._step_counter
        self._step_counter += 1
        return step

    def show(self, text: str, chat_id: str = "") -> None:
        step = self._next_step()
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if i == 0:
                entry = f"💭 [{step}] {line}"
            else:
                entry = f"     {line}"
            if self._buffer_enabled:
                self._buffer.append(entry)
            else:
                prefix = f"[{chat_id[:8]}] " if chat_id else ""
                print(f"  {prefix}{entry}")


# ═══════════════════════════════════════════════════════════
# Unified Engine
# ═══════════════════════════════════════════════════════════

class UnifiedEngine:
    """统一管理引擎"""

    def __init__(self) -> None:
        self.thinking = ThinkingDisplay()
        self._feishu_sessions: dict[str, list[dict[str, str]]] = {}

    async def run_agent_with_thinking(
        self,
        user_input: str,
        session_key: str,
        skill_toolboxes: list,
        skill_prompts: str | None,
        *,
        is_feishu: bool = False,
    ) -> str:
        """运行 agent 并显示思考过程。

        CLI: 终端实时显示
        飞书: 缓冲思考步骤，完成后发送
        """
        global _conversation_counter

        # 1. 获取会话
        session_opts = SessionOptions(
            description=f"{'飞书' if is_feishu else 'CLI'}: {session_key}"
        )
        session_ctx = session_manager.get_or_create(session_key, session_opts)
        history = session_ctx.conversation_history
        self._feishu_sessions[session_key] = history

        # 2. 重置思考计数器
        self.thinking.reset_counter()

        # 3. 飞书模式启用缓冲
        if is_feishu:
            self.thinking.enable_buffer()

        # 4. 思考回调
        async def _thinking(text: str) -> None:
            self.thinking.show(text, session_key if is_feishu else "")

        # 5. 调用 Agent
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
            system_prompt=skill_prompts,
            on_thinking=_thinking,
        )

        # 6. 飞书：发送思考过程
        if is_feishu:
            thinking_text = self.thinking.get_buffered()
            self.thinking.disable_buffer()
            if thinking_text and feishu_config:
                try:
                    from src.feishu.poll_server import _send_thinking
                    await _send_thinking(feishu_config, session_key, thinking_text)
                except Exception:
                    pass  # 静默失败
        else:
            self.thinking.disable_buffer()

        # 7. 更新历史
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": reply})
        if len(history) > 40:
            del history[:len(history) - 40]

        # 8. 持久化
        _conversation_counter += 1
        if session_manager:
            session_manager.save_session_history(session_key)
            self._save_numbered_history(session_key, history)

        return reply

    def _save_numbered_history(self, session_key: str, history: list[dict]) -> None:
        """保存带编号的会话历史。"""
        try:
            state_dir = os.path.join(os.getcwd(), "state", "sessions", session_key)
            os.makedirs(state_dir, exist_ok=True)
            seq = _conversation_counter
            filename = f"{seq:04d}_{int(time.time())}.json"
            path = os.path.join(state_dir, filename)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(history[-2:], f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def inject_message(self, chat_id: str, content: str) -> None:
        """向指定会话注入消息。"""
        history = self._get_or_create_history(chat_id)
        history.append({"role": "user", "content": content, "_injected": True})

    def _get_or_create_history(self, chat_id: str) -> list[dict[str, str]]:
        if chat_id not in self._feishu_sessions:
            if session_manager:
                ctx = session_manager.get_or_create(chat_id)
                self._feishu_sessions[chat_id] = ctx.conversation_history
                return ctx.conversation_history
            self._feishu_sessions[chat_id] = []
        return self._feishu_sessions[chat_id]


engine: UnifiedEngine | None = None


# ═══════════════════════════════════════════════════════════
# 初始化
# ═══════════════════════════════════════════════════════════

async def init_subsystems():
    """初始化所有共享子系统。"""
    global session_manager, engine

    engine = UnifiedEngine()

    # 1. 加载技能包
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

    # 2. 获取工具箱和系统提示
    skill_toolboxes = skill_registry.get_all_toolboxes()
    skill_prompts = skill_registry.get_system_prompts()

    # 3. 创建 SessionManager
    session_manager = SessionManager(registry, skill_toolboxes, loaded_skills)

    # 4. 创建默认会话并加锁
    _init_default_session()

    # 5. 清理过期关键词索引
    try:
        from src.core.keyword_index import KeywordIndex
        ki = KeywordIndex()
        ki.load()
        ki.prune_expired(30)
    except Exception:
        pass

    return loaded_skills, skill_toolboxes, skill_prompts


def _init_default_session() -> None:
    """创建默认会话并加锁。"""
    global active_session_id

    # 使用统一命名：每个实例的第一个会话都叫 default
    session_id = "default"
    session_manager.get_or_create(session_id, SessionOptions(description="默认会话"))

    # 尝试加锁
    ok, reason = try_lock_session(session_id)
    if ok:
        active_session_id = session_id
    else:
        # 被其他实例占用，创建新会话
        import random
        session_id = f"default-{random.randint(1000, 9999)}"
        session_manager.get_or_create(session_id, SessionOptions(description="默认会话"))
        try_lock_session(session_id)
        active_session_id = session_id


# ═══════════════════════════════════════════════════════════
# 飞书运行时控制
# ═══════════════════════════════════════════════════════════

async def feishu_start(skill_toolboxes, skill_prompts):
    """启动飞书连接。"""
    global feishu_task, feishu_running, feishu_config

    if feishu_running:
        print("ℹ️ 飞书已在运行")
        return

    try:
        from src.feishu.poll_server import start_feishu_poll_server
        from src.feishu.types import FeishuConfig
    except ImportError:
        print("❌ 飞书模块未安装")
        return

    config = FeishuConfig(
        app_id=os.environ.get("FEISHU_APP_ID", ""),
        app_secret=os.environ.get("FEISHU_APP_SECRET", ""),
        verification_token=os.environ.get("FEISHU_VERIFICATION_TOKEN", ""),
    )

    if not config.app_id:
        print("❌ 未配置飞书凭证")
        return

    feishu_config = config  # 保存引用，供思考回调使用
    handler = create_feishu_handler(skill_toolboxes, skill_prompts)

    async def _start_feishu():
        try:
            print("[飞书] 正在启动 WebSocket 长轮询...")
            await start_feishu_poll_server(config, handler)
        except asyncio.CancelledError:
            print("[飞书] 已停止")
        except Exception as e:
            print(f"[飞书] 运行异常: {e}")
            feishu_running = False

    feishu_task = asyncio.create_task(_start_feishu())
    feishu_running = True
    print("✅ 飞书已启动")


def feishu_stop():
    """停止飞书连接。"""
    global feishu_task, feishu_running

    if not feishu_running:
        print("ℹ️ 飞书未运行")
        return

    feishu_running = False
    if feishu_task:
        feishu_task.cancel()
        feishu_task = None
    print("✅ 飞书已停止")


def feishu_status():
    """显示飞书状态。"""
    if feishu_running:
        print("🟢 飞书: 运行中")
    else:
        print("⚪ 飞书: 未启用")


# ═══════════════════════════════════════════════════════════
# 欢迎信息
# ═══════════════════════════════════════════════════════════

def _get_version() -> str:
    """从 pyproject.toml 读取版本号。"""
    try:
        import tomllib
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version", "0.1.0")
    except Exception:
        return "0.1.0"


def print_welcome(feishu_enabled: bool = False):
    """简洁美观的启动欢迎界面。"""
    version = _get_version()
    tool_count = len(registry.list())
    skill_count = len(skill_registry.get_all())
    feishu_label = "飞书" if feishu_enabled else "待命"
    display_name = _get_session_display()

    print()
    print(f"  🤖 Mini Agent  v{version}")
    print(f"  📡 {MODEL}  ({active_profile})")
    print(f"  🔧 {tool_count} tools  ·  📦 {skill_count} skills  ·  {feishu_label}")
    print(f"  💼 {display_name}")
    print()


def _get_session_display() -> str:
    """获取当前会话显示名称。"""
    if not session_manager or not active_session_id:
        return "未初始化"
    return session_manager.get_session_display_name(active_session_id)


# ═══════════════════════════════════════════════════════════
# CLI 交互循环
# ═══════════════════════════════════════════════════════════

async def run_cli_loop(skill_toolboxes, skill_prompts):
    """CLI 交互循环。"""
    global active_session_id, engine, feishu_task

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
            try:
                from src.core.instance_manager import stop_instance
                result = stop_instance()
                if result.get("success"):
                    print("✅ 实例已停止")
            except Exception:
                pass
            release_session_lock(active_session_id)
            sys.exit(0)

        # ── 会话管理 ──

        if user_input.startswith(".session "):
            parts = user_input.split()
            sub_cmd = parts[1] if len(parts) > 1 else ""

            if sub_cmd == "list":
                _cmd_session_list()
            elif sub_cmd == "switch" and len(parts) >= 3:
                await _cmd_session_switch(parts[2])
            elif sub_cmd == "create" and len(parts) >= 3:
                await _cmd_session_create(parts[2], parts[3] if len(parts) > 3 else None)
            elif sub_cmd == "rename" and len(parts) >= 4:
                _cmd_session_rename(parts[2], " ".join(parts[3:]))
            else:
                print("\n用法:")
                print("  .session list                  列出所有会话")
                print("  .session switch <id>           切换到指定会话")
                print("  .session create <id> [title]   创建新会话，可指定标题")
                print("  .session rename <id> <title>   重命名会话")
                print()
            continue

        # ── 飞书控制 ──
        if user_input.startswith(".feishu"):
            if user_input == ".feishu start":
                await feishu_start(skill_toolboxes, skill_prompts)
            elif user_input == ".feishu stop":
                feishu_stop()
            else:
                feishu_status()
            continue

        if user_input == ".stats":
            print(f"\n{monitor.report()}")
            continue

        if user_input.startswith(".profile"):
            parts = user_input.split()
            if len(parts) >= 2 and parts[1] in MODEL_PROFILES:
                global active_profile
                active_profile = parts[1]
                print(f"📡 已切换到预设: {parts[1]}")
            else:
                print(f"当前预设: {active_profile}")
                print("可用: " + ", ".join(MODEL_PROFILES.keys()))
            continue

        if user_input == ".help":
            _cmd_help()
            continue

        # ── Agent 执行 ──
        try:
            print(f"\n👤 You: {user_input}")

            reply = await engine.run_agent_with_thinking(
                user_input,
                active_session_id,
                skill_toolboxes,
                "\n\n".join(skill_prompts) if skill_prompts else None,
            )
            print(f"\n🦾 Agent\n  {reply}\n")

        except Exception as e:
            print(f"\n❌ 错误: {e}\n")

    # 清理会话锁
    release_session_lock(active_session_id)
    print("\n👋 bye")


# ═══════════════════════════════════════════════════════════
# 帮助命令
# ═══════════════════════════════════════════════════════════

def _cmd_help() -> None:
    """显示分类帮助信息。"""
    profiles = ", ".join(MODEL_PROFILES.keys())

    print()
    print("  ╭─── Mini Agent 命令手册 ─────────────────────────────────╮")
    print()

    # ── 启动命令 ──
    print("  🚀 启动命令（终端）")
    print("    python -m src                     启动 CLI 模式")
    print("    python -m src --feishu            启动 CLI + 飞书")
    print("    python -m src --stop              停止运行中的实例")
    print()

    # ── 会话管理 ──
    print("  📁 会话管理")
    print("    .session list                   列出所有会话")
    print("    .session switch <id>            切换到指定会话")
    print("    .session create <id> [标题]     创建新会话，可指定标题")
    print("    .session rename <id> <新标题>   重命名会话")
    print()

    # ── 飞书控制 ──
    print("  💬 飞书控制")
    print("    .feishu start                   启动飞书 WebSocket 连接")
    print("    .feishu stop                    停止飞书连接")
    print("    .feishu status                  查看飞书运行状态")
    print()

    # ── 模型预设 ──
    print("  📡 模型预设")
    print(f"    .profile <名称>                 切换模型预设")
    print(f"    可用预设: {profiles}")
    print(f"    当前预设: {active_profile}")
    print()

    # ── 工具与统计 ──
    print("  📊 工具与统计")
    print("    .stats                          查看工具调用统计")
    print()

    # ── 实例控制 ──
    print("  ⚙️ 实例控制")
    print("    .stop                           停止当前实例并退出")
    print()

    # ── 其他 ──
    print("  📖 其他")
    print("    .help                           显示本帮助")
    print("    quit / exit                     退出程序")
    print()

    # ── 提示 ──
    print("  💡 提示: 直接输入文字即可与 Agent 对话")
    print("  ╰─────────────────────────────────────────────────────╯")
    print()


# ═══════════════════════════════════════════════════════════
# 会话管理命令
# ═══════════════════════════════════════════════════════════

def _cmd_session_list() -> None:
    """列出所有会话。"""
    if not session_manager:
        print("❌ 会话管理器未初始化")
        return

    sessions = session_manager.list_all_sessions_with_info()
    my_pid = os.getpid()

    if not sessions:
        print("📭 暂无会话")
        return

    print("\n📋 会话列表:")
    for s in sessions:
        marker = " ← 当前" if s["id"] == active_session_id else ""
        lock_info = ""
        if s["locked"]:
            if s["lock_pid"] == my_pid:
                lock_info = " 🔒 (本实例)"
            else:
                lock_info = f" 🔒 (PID={s['lock_pid']})"
        display = f"#{s['number']} {s['title']}"
        print(f"  - {display}{marker} | {s['turn_count']} 轮{lock_info}")
    print()


async def _cmd_session_switch(session_id: str) -> None:
    """切换到指定会话。"""
    global active_session_id

    if not session_manager:
        print("❌ 会话管理器未初始化")
        return

    # 释放当前会话锁
    release_session_lock(active_session_id)

    # 检查目标会话是否被其他实例锁定
    lock_pid = is_session_locked(session_id)
    if lock_pid is not None:
        # 被其他实例锁定，尝试恢复已存在的会话
        try:
            session_manager.get_or_create(session_id)
        except Exception:
            pass

        locked_sessions = [
            s for s in session_manager.list_all_sessions_with_info()
            if s["id"] == session_id and s["locked"]
        ]
        if locked_sessions:
            print(f"❌ 会话 #{locked_sessions[0]['number']} {locked_sessions[0]['title']} 被其他实例占用 (PID={lock_pid})")
            # 恢复当前会话锁
            try_lock_session(active_session_id)
            return

    # 获取或创建目标会话
    try:
        session_manager.get_or_create(session_id)
    except Exception:
        pass

    # 尝试加锁
    ok, reason = try_lock_session(session_id)
    if not ok:
        print(f"❌ 无法切换: {reason}")
        try_lock_session(active_session_id)  # 恢复当前锁
        return

    active_session_id = session_id
    display = session_manager.get_session_display_name(session_id)
    print(f"🔄 已切换到会话: {display}")


async def _cmd_session_create(session_id: str, title: str | None = None) -> None:
    """创建新会话。"""
    if not session_manager:
        print("❌ 会话管理器未初始化")
        return

    session_opts = SessionOptions(title=title or "", description=title or session_id)
    session_manager.get_or_create(session_id, session_opts)

    # 加锁
    try_lock_session(session_id)

    display = session_manager.get_session_display_name(session_id)
    print(f"✅ 已创建会话: {display}")


def _cmd_session_rename(session_id: str, new_title: str) -> None:
    """重命名会话。"""
    if not session_manager:
        print("❌ 会话管理器未初始化")
        return

    ok = session_manager.rename_session(session_id, new_title)
    if ok:
        display = session_manager.get_session_display_name(session_id)
        print(f"✅ 已重命名: {display}")
    else:
        print(f"❌ 会话不存在: {session_id}")


# ═══════════════════════════════════════════════════════════
# 飞书处理器
# ═══════════════════════════════════════════════════════════

def create_feishu_handler(skill_toolboxes, skill_prompts):
    """创建飞书消息处理器。

    飞书消息使用 active_session_id，与 CLI 共享会话。
    思考过程会实时推送到飞书。
    """

    async def handler(content: str, chat_id: str, sender_id: str) -> str:
        if not engine:
            return "⚠️ 引擎未初始化"
        try:
            print(f"\n📨 [飞书 {chat_id[:8]}] {content}")
            reply = await engine.run_agent_with_thinking(
                content,
                active_session_id,
                skill_toolboxes,
                "\n\n".join(skill_prompts) if skill_prompts else None,
                is_feishu=True,
            )
            return reply
        except Exception as e:
            return f"⚠️ 处理失败: {e}"

    return handler


# ═══════════════════════════════════════════════════════════
# 启动入口
# ═══════════════════════════════════════════════════════════

async def unified_main():
    """主启动流程。

    不再检查全局单实例 — 支持多实例并行。
    每个实例通过会话级 .lock 文件隔离。
    """
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # 注册信号处理器
    def _on_exit(*_):
        if active_session_id:
            release_session_lock(active_session_id)
        if feishu_task:
            feishu_task.cancel()
        sys.exit(0)

    signal.signal(signal.SIGINT, _on_exit)
    signal.signal(signal.SIGTERM, _on_exit)

    # 初始化子系统
    loaded_skills, skill_toolboxes, skill_prompts = await init_subsystems()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # --feishu: 启动时自动开启飞书
    feishu_enabled = "--feishu" in sys.argv
    if feishu_enabled:
        await feishu_start(skill_toolboxes, skill_prompts)

    print_welcome(feishu_enabled)

    # 运行 CLI 循环
    await run_cli_loop(skill_toolboxes, skill_prompts)

    # 清理
    if feishu_task:
        feishu_task.cancel()
    release_session_lock(active_session_id)


def unified_entry():
    """统一入口点。"""
    try:
        from dotenv import load_dotenv
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path)
    except ImportError:
        pass

    asyncio.run(unified_main())
