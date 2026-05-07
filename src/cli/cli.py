"""Mini Agent Python — CLI 交互入口 (Phase 7)

Mini Agent 的用户界面层，负责初始化所有子系统并启动交互循环。

职责：
1. 初始化核心子系统（ToolRegistry、ToolMonitor、SkillRegistry、OutputManager）
2. 自动发现并加载 skills/ 目录下的技能包
3. 注册所有工具（内置 + 技能贡献 + self-opt）
4. 显示欢迎信息和工作空间概览
5. 启动交互式循环，处理用户输入
6. 处理内置命令：.stats, .skills, .profile, .skill, .plan, .log, .optimize, quit

内置命令：
- `.stats`        — 查看工具使用统计
- `.skills`       — 查看已加载技能
- `.sessions`     — 查看会话列表
- `.profile <name>` — 切换模型预设
- `.skill search <query>` — 搜索 ClawHub 技能
- `.skill install <slug>` — 安装技能
- `.skill list`   — 列出已安装技能
- `.plan <内容>`  — 跳过规划直接执行
- `.log <路径>`   — 开启增量日志
- `.optimize`     — 自我优化
- `.session new/switch/destroy` — 会话管理
- `.promote`      — 升维工具
- `.demote`       — 降维工具
- quit / exit     — 退出程序
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import signal
from pathlib import Path
from typing import Any

# ── 初始化核心子系统 ──

from src.core.agent import run_agent
from src.core.executor import MODEL
from src.core.config import MODEL_PROFILES, get_default_agent_config
from src.core.instance_manager import try_acquire_instance, force_acquire_instance, release_instance, stop_instance
from src.core.process_tracker import cleanup_all_processes, get_active_processes
from src.core.registry import DefaultToolRegistry
from src.core.monitor import DefaultToolMonitor
from src.skills.registry import DefaultSkillRegistry
from src.skills.loader import discover_skill_packages
from src.skills.clawhub_client import create_clawhub_client, search_local_skills
from src.session.manager import DefaultSessionManager as SessionManager
from src.types.memory import SessionOptions
from src.tools.filesystem import filesystem_tools
from src.tools.exec import exec_tools
from src.tools.web import web_tools
from src.tools.skills import skills_tools
from src.tools.self_opt import self_opt_tools
from src.security.sandbox import get_default_workspace
from src.cli.display_manager import DisplayManager

# ── 全局状态 ──

registry = DefaultToolRegistry()
monitor = DefaultToolMonitor()
skill_registry = DefaultSkillRegistry()
clawhub = create_clawhub_client()
dm = DisplayManager(prompt="> ")


async def _on_thinking_cli(text: str, display: DisplayManager) -> None:
    """CLI 思考回调：显示 LLM 思考内容。"""
    display.show_thinking_content(text)

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
session_manager: SessionManager | None = None
active_session_id = "cli-interactive"
log_file: str | None = None


# ── 技能加载 ──

async def load_skills() -> list:
    """自动发现并加载技能包。"""
    skills_root = os.environ.get(
        "MINI_AGENT_SKILLS",
        str(Path(__file__).parent.parent / "skills"),
    )

    if not os.path.isdir(skills_root):
        print(f"ℹ️ 技能目录不存在: {skills_root}")
        return []

    packages = await discover_skill_packages(skills_root)
    for pkg in packages:
        skill_registry.register_package(pkg)
        print(f"📦 已加载技能包: {pkg.name} ({len(pkg.skills)} 个技能)")
        for skill in pkg.skills:
            if skill.tools:
                for name, tool in skill.tools.items():
                    try:
                        registry.register(name, tool)
                    except ValueError:
                        print(f"⚠️ 工具 '{name}' 已存在，跳过")

    return packages


# ── 欢迎信息 ──

def _get_version() -> str:
    """从 pyproject.toml 读取版本号。"""
    try:
        import tomllib
        pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version", "unknown")
    except Exception:
        return "0.1.0"


def print_welcome(dm, all_toolboxes, loaded_skills):
    """显示欢迎信息。"""
    version = _get_version()
    toolbox_names = [tb.name for tb in all_toolboxes]
    tool_names = registry.list()
    skill_names = [s.name for s in loaded_skills] if loaded_skills else None

    dm.print_welcome(
        version=version,
        model=MODEL,
        profile=active_profile,
        workspace=get_default_workspace(),
        tools=tool_names,
        toolboxes=toolbox_names,
        skills=skill_names,
    )


# ── CLI 主循环 ──

async def main():
    global active_session_id, log_file, session_manager

    # Windows 控制台 UTF-8 输出支持
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # 单实例检查
    if "--stop" in sys.argv:
        result = stop_instance()
        if result.get("success"):
            dm.show_info("Mini Agent 已停止")
        else:
            dm.show_info(result.get("reason", ""))
        sys.exit(0)

    force_mode = "--force" in sys.argv
    instance_result = force_acquire_instance() if force_mode else try_acquire_instance()
    if not instance_result.get("success"):
        if "existing_pid" in instance_result:
            dm.show_error(f"Mini Agent 已在运行 (PID={instance_result.get('existing_pid')})")
            dm.show_info("强制启动: python -m src --force")
        else:
            dm.show_error(f"无法获取实例锁: {instance_result.get('reason', '')}")
        sys.exit(1)

    # 注册退出钩（信号 → 优雅退出）
    async def _graceful_exit():
        try:
            await cleanup_all_processes()
        except Exception:
            pass
        try:
            release_instance()
        except Exception:
            pass
        sys.exit(0)

    def _on_signal(*_):
        asyncio.create_task(_graceful_exit())

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    # 加载技能
    loaded_skills = await load_skills()

    # 工具箱
    skill_toolboxes = skill_registry.get_all_toolboxes()
    all_toolboxes = skill_toolboxes  # 所有工具箱由 skill_registry 统一管理
    skill_prompts = skill_registry.get_system_prompts()

    # 初始化 SessionManager
    session_manager = SessionManager(registry, all_toolboxes, loaded_skills)
    session_manager.get_or_create(active_session_id, SessionOptions(description="CLI 交互会话"))
    dm.show_info("多会话管理已初始化")

    # 欢迎信息
    print_welcome(dm, all_toolboxes, loaded_skills)

    # 关键词索引清理（启动时）
    try:
        from src.core.keyword_index import KeywordIndex
        ki = KeywordIndex()
        ki.load()
        pruned = ki.prune_expired(30)
        if pruned > 0:
            dm.show_info(f"已清理 {pruned} 条过期索引")
    except Exception:
        pass

    # ── Agent 执行 ──
    while True:
        try:
            user_input = await asyncio.to_thread(dm.prompt)
        except EOFError:
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            break

        # 显示用户输入（到历史流）
        dm.show_user_input(user_input)

        # ── 内置命令处理 ──

        if user_input == ".stop":
            result = stop_instance()
            if result.get("success"):
                dm.show_info("实例已停止")
                sys.exit(0)
            else:
                dm.show_info(result.get("reason", ""))
            continue

        if user_input == ".stats":
            dm.show_command_result("📊 工具统计", monitor.report())
            continue

        if user_input == ".skills":
            skills = skill_registry.get_all()
            if not skills:
                dm.show_command_result("🎯 技能", "暂无已加载的技能")
            else:
                lines = [f"{s.name} ({s.id}): {s.description}" for s in skills]
                dm.show_command_result("🎯 已加载技能", "\n".join(lines))
            continue

        if user_input == ".sessions":
            sessions = session_manager.list()
            main_tools = session_manager.get_main_tools()
            lines = [
                f"活跃会话: {len(sessions)}",
                f"主空间工具: {len(main_tools)} 个",
                f"当前会话: {active_session_id}",
            ]
            for s in sessions:
                marker = " ← 当前" if s.session_id == active_session_id else ""
                lines.append(f"  - {s.session_id}{marker} | 工具:{s.tool_count} 技能:{s.skill_count}")
            dm.show_command_result("🧩 多会话管理", "\n".join(lines))
            continue

        # .session new/switch/destroy
        if user_input.startswith(".session "):
            parts = user_input.split()
            sub_cmd = parts[1] if len(parts) > 1 else ""

            if sub_cmd == "new":
                new_id = parts[2] if len(parts) > 2 else f"cli-{int(_time.time())}"
                desc = " ".join(parts[3:]) if len(parts) > 3 else "CLI 会话"
                ctx = session_manager.get_or_create(new_id, SessionOptions(description=desc))
                active_session_id = new_id
                dm.show_info(f"会话已创建并切换: {new_id}")
            elif sub_cmd == "switch" and len(parts) >= 3:
                target_id = parts[2]
                ctx = session_manager.get_or_create(target_id)
                if ctx:
                    active_session_id = target_id
                    dm.show_info(f"已切换到会话: {target_id}")
                else:
                    dm.show_error(f"会话不存在: {target_id}")
            elif sub_cmd == "destroy" and len(parts) >= 3:
                target_id = parts[2]
                if target_id == active_session_id:
                    dm.show_warning("无法销毁当前活跃会话，请先 .session switch 到其他会话")
                else:
                    ok = session_manager.destroy(target_id)
                    if ok:
                        dm.show_info(f"会话已销毁: {target_id}")
                    else:
                        dm.show_error(f"会话不存在: {target_id}")
            else:
                dm.show_error("未知 .session 命令")
                dm.show_info("用法: .session new [id] [desc] | .session switch <id> | .session destroy <id>")
            continue

        if user_input.startswith(".promote "):
            parts = user_input.split()
            target = parts[1]
            if target == "all":
                if session_manager:
                    results = session_manager.promote_all_tools(active_session_id)
                    dm.show_command_result("🚀 批量升维", "\n".join(str(r) for r in results))
            else:
                if session_manager:
                    result = session_manager.promote_tool(active_session_id, target)
                    dm.show_info(result)
            continue

        if user_input.startswith(".demote "):
            tool_name = user_input.split()[1]
            if session_manager:
                result = session_manager.demote_tool(tool_name)
                dm.show_info(result)
            continue

        if user_input.startswith(".profile"):
            parts = user_input.split()
            if len(parts) < 2:
                lines = [f"当前预设: {active_profile}", "可用预设:"]
                for name, profile in MODEL_PROFILES.items():
                    marker = " ← 当前" if name == active_profile else ""
                    lines.append(f"  - {name}: {profile.description}{marker}")
                dm.show_command_result("📡 模型预设", "\n".join(lines))
            else:
                profile_name = parts[1]
                if profile_name in MODEL_PROFILES:
                    active_profile = profile_name
                    dm.show_info(f"模型预设已切换到: {profile_name}")
                else:
                    dm.show_error(f"未知预设: {profile_name}")
                    dm.show_info("使用 .profile 查看可用列表")
            continue

        if user_input.startswith(".skill "):
            parts = user_input.split()
            sub_cmd = parts[1] if len(parts) > 1 else ""

            if sub_cmd == "search" and len(parts) >= 3:
                query = " ".join(parts[2:])
                dm.show_info(f"搜索技能: '{query}'...")
                try:
                    skills_root = os.environ.get(
                        "MINI_AGENT_SKILLS",
                        str(Path(__file__).parent.parent / "skills"),
                    )
                    local_results = search_local_skills(skills_root, query)
                    if local_results:
                        lines = [f"{s['name']} ({s['slug']}): {s['description']}" for s in local_results]
                        dm.show_command_result("🔍 本地技能", "\n".join(lines))
                    else:
                        dm.show_info("未找到匹配的本地技能")
                except Exception as e:
                    dm.show_error(f"搜索失败: {e}")
            elif sub_cmd == "install" and len(parts) >= 3:
                slug = parts[2]
                dm.show_info(f"安装技能: {slug}...")
                try:
                    result = await clawhub.download(slug)
                    dm.show_info(f"已安装到: {result['path']}")
                except Exception as e:
                    dm.show_error(f"安装失败: {e}")
            elif sub_cmd == "list":
                skills_root = os.environ.get(
                    "MINI_AGENT_SKILLS",
                    str(Path(__file__).parent.parent / "skills"),
                )
                local_results = search_local_skills(skills_root, "")
                if local_results:
                    lines = [f"{s['name']} ({s['slug']}): {s['description']}" for s in local_results]
                    dm.show_command_result("📦 已安装技能", "\n".join(lines))
                else:
                    dm.show_info("暂无已安装的技能")
            else:
                dm.show_error("未知 .skill 命令")
                dm.show_info("用法: .skill search <query> | .skill install <slug> | .skill list")
            continue

        if user_input.startswith(".log "):
            log_file = user_input[5:].strip() or None
            if log_file:
                dm.show_info(f"增量日志已开启: {log_file}")
            else:
                dm.show_info("增量日志已关闭")
            continue

        if user_input.startswith(".optimize"):
            sub_cmd = user_input.split()[1] if len(user_input.split()) > 1 else ""
            project_root = str(Path(__file__).parent.parent)
            src_dir = os.path.join(project_root, "src")

            try:
                if sub_cmd == "inspect":
                    dm.show_info("启动自我审视...")
                    total_lines = 0
                    py_files = 0
                    for root, _, files in os.walk(src_dir):
                        for f in files:
                            if f.endswith(".py"):
                                py_files += 1
                                total_lines += len(Path(os.path.join(root, f)).read_text().splitlines())
                    dm.show_command_result("📦 代码统计", f"Python 文件: {py_files}, 总代码行数: {total_lines}")
                elif sub_cmd == "status":
                    lines = [
                        f"工具调用次数: {monitor._total_calls}",
                        f"成功率: {monitor._success_count}/{monitor._total_calls}",
                    ]
                    dm.show_command_result("📊 优化仪表盘", "\n".join(lines))
                else:
                    dm.show_command_result("🚀 自我优化", "子命令: .optimize inspect | .optimize status | .optimize auto")
            except Exception as e:
                dm.show_error(f"自我优化失败: {e}")
            continue

        if user_input == ".help":
            dm.show_command_result("💡 可用命令",
                ".stats    — 工具统计\n"
                ".skills   — 已加载技能\n"
                ".sessions — 会话列表\n"
                ".profile  — 模型预设\n"
                ".skill    — 技能管理\n"
                ".session  — 会话操作\n"
                ".plan     — 跳过规划直接执行\n"
                ".log      — 增量日志\n"
                ".optimize — 自我优化\n"
                ".promote  — 升维工具\n"
                ".demote   — 降维工具\n"
                ".help     — 此帮助\n"
                "quit/exit — 退出")
            continue

        # ── Agent 执行 ──
        skip_planning = user_input.startswith(".plan ")
        actual_input = user_input[6:] if skip_planning else user_input

        try:
            session_ctx = session_manager.get_or_create(active_session_id)

            # 显示思考状态
            dm.show_thinking()

            # 调用 Agent
            reply = await run_agent(
                actual_input,
                registry=registry,
                monitor=monitor,
                toolboxes=all_toolboxes,
                skip_planning=skip_planning,
                agent_config={
                    "debug": False,  # 关闭 debug 避免刷屏
                    "log_file": log_file,
                    "session_key": active_session_id,
                    "conversation_history": session_ctx.conversation_history,
                },
                system_prompt="\n\n".join(skill_prompts) if skill_prompts else None,
                on_thinking=lambda text: _on_thinking_cli(text, dm),
            )
            # 显示最终回复
            dm.show_reply(reply)

            # 更新对话历史
            session_ctx.conversation_history.append({"role": "user", "content": actual_input})
            session_ctx.conversation_history.append({"role": "assistant", "content": reply})

            # 限制历史长度（保留最近 40 条）
            if len(session_ctx.conversation_history) > 40:
                session_ctx.conversation_history = session_ctx.conversation_history[-40:]

        except Exception as e:
            dm.show_error(str(e))

    # 退出
    dm.farewell(monitor.report())

    # 子进程清理
    active = get_active_processes()
    if active:
        dm.show_info(f"正在清理 {len(active)} 个子进程...")
    await cleanup_all_processes()

    release_instance()


if __name__ == "__main__":
    asyncio.run(main())
