"""Engine — 子系统初始化

由 ``run_runtime`` 早期调用 ``init_subsystems``，完成 **工具与技能**
的就绪后再进入 CLI 循环。

顺序要点（与注册覆盖语义一致）：

1. ``register_builtin_tools``：内置 ``ALL_TOOLS``（含 session_memory_tools）
2. 磁盘技能包发现并注册工具（同名冲突时跳过技能侧）
3. 可选 ``mcp.stdio_command`` / ``mcp.stdio_env``：stdio MCP 工具（未安装 ``mcp`` 包则打日志跳过）
4. ``build_skill_snapshots``（内含 ``BUILTIN_TOOLBOXES`` 合并）；若已注册 MCP 工具则追加 ``mcp`` 工具箱；创建 ``SessionManager``；默认会话加锁；
   ``KeywordIndex.prune_expired`` 清理过期索引项

配置见包内 ``miniagent/resources/config.defaults.json``；架构见 ``docs/ARCHITECTURE.md``。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import shutil
import tempfile
from pathlib import Path
from typing import Any

from miniagent.agent.logging import get_logger
from miniagent.assistant.infrastructure.env_parse import env_flag, env_str
from miniagent.assistant.infrastructure.json_config import get_config

_logger = get_logger(__name__)


async def init_subsystems(
    registry: Any,
    skill_registry: Any,
    SessionManager: Any,
    channel_router: Any,
    clawhub: Any | None = None,
    keyword_index: Any | None = None,
) -> tuple[list, list, list, str, Any]:
    """初始化所有共享子系统。

    Args:
        registry: 工具注册表
        skill_registry: 技能注册表
        SessionManager: SessionManager 类
        channel_router: 本进程的 :class:`~miniagent.assistant.infrastructure.channel_router.ChannelRouter` 实例
        clawhub: ClawHub 客户端，传入 :class:`~miniagent.assistant.session.manager.DefaultSessionManager`
        keyword_index: 关键词索引实例（清理过期条目）；未提供则新建默认实例

    Returns:
        (loaded_skills, skill_toolboxes, skill_prompts, active_session_id, session_manager)
    """
    # 0. 自动注册 trace 持久化钩子（如果设置了 MINIAGENT_TRACE_LOG_FILE）
    from miniagent.agent.observability import auto_register_trace_file_hook
    from miniagent.assistant.engine.builtin_tools import register_builtin_tools
    from miniagent.assistant.skills.load_runtime import bootstrap_skill_packages
    from miniagent.assistant.skills.snapshots import build_skill_snapshots

    auto_register_trace_file_hook()

    # 0.5. 检查并恢复 baseline skills（skill-vetter / skill-creator）
    _ensure_baseline_skills()

    # 1. 内置工具（ALL_TOOLS）先于技能包；同名时内置优先（技能注册遇 ValueError 则跳过）
    # session_memory_tools 已在 ALL_TOOLS 中统一注册
    reg_n = register_builtin_tools(registry)
    if reg_n:
        _logger.info("已注册 %d 个内置工具（ALL_TOOLS）", reg_n)

    from miniagent.agent.config import get_default_agent_config

    try:
        agent_cfg = get_default_agent_config()
    except Exception:
        agent_cfg = None

    loaded_skills, _added, _removed = await bootstrap_skill_packages(
        registry,
        skill_registry,
        config=agent_cfg,
    )

    await _register_mcp_tools_from_config(registry)

    # 2. 获取工具箱和系统提示（含 gating）；MCP 工具注册后追加 mcp 工具箱供规划器选用
    skill_toolboxes, skill_prompts = build_skill_snapshots(skill_registry, agent_cfg)
    from miniagent.assistant.mcp.toolbox import ensure_mcp_toolbox

    skill_toolboxes = ensure_mcp_toolbox(skill_toolboxes, registry)

    # 3. 创建 SessionManager
    session_manager = SessionManager(registry, skill_toolboxes, loaded_skills, clawhub=clawhub)

    # 4. 创建默认会话并加锁
    session_name = env_str("MINIAGENT_SESSION_NAME") or get_config("session.default_name", None)
    active_session_id = await _init_default_session_async(
        session_manager, channel_router, session_name=session_name
    )

    # 5. 清理过期关键词索引
    try:
        from miniagent.assistant.memory.keyword_index import KeywordIndex

        ki = keyword_index if keyword_index is not None else KeywordIndex()
        ki.load()
        ki.prune_expired()
    except Exception as e:
        _logger.debug("关键词索引初始化失败: %s", e)

    return loaded_skills, skill_toolboxes, skill_prompts, active_session_id, session_manager


def _resolve_continue_session_id(session_manager: Any, channel_router: Any) -> str:
    """在 ``--continue`` 模式下解析应恢复的会话 ID。"""
    from miniagent.assistant.session.manager import session_info_id

    existing_sessions = session_manager.list_all_sessions_with_info()
    existing_ids = {session_info_id(s) for s in existing_sessions if session_info_id(s)}

    last_state = channel_router.load_cli_session_state()
    if last_state:
        last_session_id = last_state.get("last_cli_session")
        if last_session_id:
            if last_session_id in existing_ids:
                return last_session_id
            _logger.info("上次会话 %s 已删除，尝试其它回退", last_session_id)

    primary = channel_router.primary
    if primary and primary in existing_ids:
        return primary

    cli_bound = channel_router.resolve("__cli__")
    if cli_bound != "__cli__" and cli_bound in existing_ids:
        return cli_bound

    return "default"


def _resolve_startup_session_id(
    session_manager: Any,
    channel_router: Any,
    *,
    session_name: str | None = None,
) -> str:
    """解析启动时应使用的会话 ID（不含 get_or_create / 加锁）。"""
    continue_mode = get_config("session.continue_mode", False) or env_flag(
        "MINIAGENT_CONTINUE_SESSION"
    )

    if continue_mode and not session_name:
        return _resolve_continue_session_id(session_manager, channel_router)
    if session_name:
        return session_name
    return "default"


async def _init_default_session_async(
    session_manager: Any,
    channel_router: Any,
    *,
    session_name: str | None = None,
) -> str:
    """创建默认会话并加锁（磁盘恢复在后台线程执行，避免阻塞事件循环）。"""
    from miniagent.assistant.engine.session_lock import try_lock_session
    from miniagent.assistant.session.manager import SessionOptions

    session_id = _resolve_startup_session_id(
        session_manager, channel_router, session_name=session_name
    )

    await asyncio.to_thread(
        session_manager.get_or_create,
        session_id,
        SessionOptions(description="默认会话"),
    )
    channel_router.bind("__cli__", session_id)
    channel_router.set_primary(session_id)

    ok, reason = try_lock_session(session_id)
    if ok:
        return session_id

    fallback = f"{session_id}-{random.randint(1000, 9999)}"
    _logger.info(
        "会话 %s 加锁失败%s，回退到 %s",
        session_id,
        f" ({reason})" if reason else "",
        fallback,
    )
    await asyncio.to_thread(
        session_manager.get_or_create,
        fallback,
        SessionOptions(description="默认会话（回退）"),
    )
    channel_router.bind("__cli__", fallback)
    channel_router.set_primary(fallback)
    fb_ok, fb_reason = try_lock_session(fallback)
    if not fb_ok:
        _logger.warning("回退会话 %s 加锁失败: %s", fallback, fb_reason)
    return fallback


def _init_default_session(
    session_manager: Any, channel_router: Any, *, session_name: str | None = None
) -> str:
    """创建默认会话并加锁（同步；供测试与非 async 调用方）。"""
    from miniagent.assistant.engine.session_lock import try_lock_session
    from miniagent.assistant.session.manager import SessionOptions

    session_id = _resolve_startup_session_id(
        session_manager, channel_router, session_name=session_name
    )

    session_manager.get_or_create(session_id, SessionOptions(description="默认会话"))
    channel_router.bind("__cli__", session_id)
    channel_router.set_primary(session_id)

    ok, reason = try_lock_session(session_id)
    if ok:
        return session_id

    fallback = f"{session_id}-{random.randint(1000, 9999)}"
    _logger.info(
        "会话 %s 加锁失败%s，回退到 %s",
        session_id,
        f" ({reason})" if reason else "",
        fallback,
    )
    session_manager.get_or_create(fallback, SessionOptions(description="默认会话（回退）"))
    channel_router.bind("__cli__", fallback)
    channel_router.set_primary(fallback)
    fb_ok, fb_reason = try_lock_session(fallback)
    if not fb_ok:
        _logger.warning("回退会话 %s 加锁失败: %s", fallback, fb_reason)
    return fallback


_BASELINE_SKILLS = (
    "skill-vetter",
    "skill-creator",
    "builtin-web",
    "builtin-stackexchange",
)

# Git blob IDs of every previously shipped canonical builtin-web tools.py.
# Identity comparison is migration gating, not a cryptographic trust decision:
# any local edit changes the blob and is therefore preserved.
_BUILTIN_WEB_MANAGED_TOOL_BLOBS = frozenset(
    {
        "59b3cc3306e0db6ec8aa3e6606de7c1299900556",
        "09cef9c23b475c64e24f579e8aaabe8784d6e421",
        "85cab56011efc29acc233e26b10f1311dbdd0ef8",
        "cffd180e02b5ba5c53d4841482a4cd4012c956f9",
        "1f8cf9491a97f73a0f7ea44452e16c554997baed",
        "edd2fb1ad86456def31d59b3ad3707dcee2532d5",
        "3d5e3bc01b9dc9fa03ca4902957f8d7a1888b967",
        "4745e4a43aefa76d4e9c3ed2847147ff1f8b49fd",
        "49c3e931cb69e174a8138d4376c94f853b5bf1f0",
        "89c3aaf272578c4f09c519f40517646bc283dd7f",
        "cabf3502d203924308ecdadfb57ef2640d8b93c7",
        "c03e2aeb0db97e38d195043e51ae4a1e9e3641d5",
        "873be94bdea9f5cf9744d2209b101ebb1a350414",
    }
)


def _git_blob_id(path: str | os.PathLike[str]) -> str:
    """Compute Git's content identity without requiring a Git executable."""
    payload = Path(path).read_bytes()
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(f"blob {len(payload)}\0".encode())
    digest.update(payload)
    return digest.hexdigest()


def _replace_known_managed_file(
    source: str | os.PathLike[str],
    target: str | os.PathLike[str],
    known_blobs: frozenset[str],
) -> bool:
    """Atomically upgrade an unchanged historical template; preserve custom files."""
    source_path = Path(source)
    target_path = Path(target)
    if not source_path.is_file() or not target_path.is_file():
        return False
    try:
        target_blob = _git_blob_id(target_path)
        if target_blob not in known_blobs or _git_blob_id(source_path) == target_blob:
            return False
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target_path.parent,
                prefix=f".{target_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
            shutil.copy2(source_path, temp_path)
            os.replace(temp_path, target_path)
            temp_path = None
            return True
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
    except OSError as error:
        _logger.debug("托管 baseline skill 文件升级失败 %s: %s", target_path, error)
        return False


def _ensure_baseline_skills() -> None:
    """检查 baseline skills 是否存在，缺失则从模板恢复。

    若技能根目录不存在，会自动创建后再恢复。
    """
    skills_root = _get_skills_root_for_baseline()
    if not skills_root:
        return

    os.makedirs(skills_root, exist_ok=True)

    templates_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "templates"
    )
    if not os.path.isdir(templates_dir):
        return

    for name in _BASELINE_SKILLS:
        target = os.path.join(skills_root, name)
        src = os.path.join(templates_dir, name)
        if not os.path.isdir(target):
            if os.path.isdir(src):
                try:
                    shutil.copytree(src, target)
                    _logger.info("已恢复 baseline skill: %s", name)
                except OSError as e:
                    _logger.warning("恢复 baseline skill %s 失败: %s", name, e)
        elif name == "builtin-web":
            relative_tools = os.path.join("skills", "web-tools", "tools.py")
            if _replace_known_managed_file(
                os.path.join(src, relative_tools),
                os.path.join(target, relative_tools),
                _BUILTIN_WEB_MANAGED_TOOL_BLOBS,
            ):
                _logger.info("已升级未修改的托管 baseline skill: builtin-web/tools.py")


def _parse_mcp_stdio_command(raw: Any) -> list[str] | None:
    """解析 ``mcp.stdio_command``（config.user.json 原生数组或 JSON 字符串）。"""
    if not raw:
        return None
    spec: Any
    if isinstance(raw, list):
        spec = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        spec = json.loads(text)
    else:
        return None
    if isinstance(spec, list) and len(spec) >= 1:
        return [str(x) for x in spec]
    return None


def _parse_mcp_stdio_env(raw: Any) -> dict[str, str] | None:
    """解析 ``mcp.stdio_env``（原生对象或 JSON 字符串）为 ``str -> str`` 环境变量表。"""
    if not raw:
        return None
    spec: Any
    if isinstance(raw, dict):
        spec = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        spec = json.loads(text)
    else:
        return None
    if not isinstance(spec, dict):
        return None
    return {str(k): str(v) for k, v in spec.items()}


def _is_mcp_missing_error(exc: BaseException) -> bool:
    """是否为「未安装 mcp 包」类错误（``ImportError`` 或包装的 ``RuntimeError``）。"""
    if isinstance(exc, ImportError):
        return True
    return isinstance(exc, RuntimeError) and "未安装 mcp" in str(exc)


async def _register_mcp_tools_from_config(registry: Any) -> int:
    """若配置了 ``mcp.stdio_command``，连接 MCP stdio 并注册工具。

    Returns:
        成功注册的 MCP 工具数量（未配置或失败时为 0）
    """
    mcp_raw = get_config("mcp.stdio_command", "")
    if not mcp_raw:
        return 0
    try:
        spec = _parse_mcp_stdio_command(mcp_raw)
        if not spec:
            _logger.warning("mcp.stdio_command: 无效格式，需为非空 JSON 数组 [command, arg1, ...]")
            return 0
        env_raw = get_config("mcp.stdio_env", None)
        try:
            stdio_env = _parse_mcp_stdio_env(env_raw)
        except json.JSONDecodeError as e:
            _logger.warning("mcp.stdio_env: JSON 解析失败: %s", e)
            return 0
        if env_raw and stdio_env is None:
            _logger.warning('mcp.stdio_env: 无效格式，需为 JSON 对象 {"KEY": "value", ...}')
            return 0

        from miniagent.assistant.mcp.runtime import register_mcp_stdio_tools

        mcp_n = await register_mcp_stdio_tools(
            registry,
            spec[0],
            spec[1:],
            env=stdio_env,
        )
        _logger.info("mcp.stdio_command: 已注册 %d 个 MCP 工具", mcp_n)
        return mcp_n
    except json.JSONDecodeError as e:
        _logger.warning("mcp.stdio_command: JSON 解析失败: %s", e)
    except RuntimeError as e:
        if _is_mcp_missing_error(e):
            _logger.warning(
                "mcp.stdio_command: 未安装 mcp 包，跳过（pip install miniagent-python[mcp]）"
            )
        else:
            _logger.warning("mcp.stdio_command: %s", e)
    except Exception as e:
        _logger.warning("mcp.stdio_command: %s", e)
    return 0


def _get_skills_root_for_baseline() -> str | None:
    """获取技能根目录路径（用于基线比较）；失败时返回 None。"""
    try:
        from miniagent.assistant.skills.paths import get_skills_root

        return get_skills_root()
    except Exception as e:
        _logger.debug("无法解析技能根目录（跳过 baseline 恢复）: %s", e)
        return None


__all__ = ["init_subsystems"]
