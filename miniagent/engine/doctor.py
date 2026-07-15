"""Mini Agent Python — 环境诊断

生成 Markdown 风格文本报告，供 CLI ``/doctor`` 与 ``python -m miniagent --doctor`` 使用。

检查范围：

- Python 运行时（版本、平台、解释器路径）
- 必需与可选 Python 依赖（对齐 ``pyproject.toml`` 与 extras）
- 包内默认配置 / ``config.user.json`` 与 API 密钥（JSON 与环境变量）
- 项目状态目录（``sessions``、``memory``）与知识库根目录（``knowledge.root``）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from miniagent.infrastructure.json_config import (
    get_config,
    get_config_paths,
    get_user_config_section,
)

# 与 pyproject.toml [project.dependencies] 对齐（import 名可能与包名不同）
REQUIRED_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("openai", "OpenAI SDK"),
    ("aiohttp", "异步 HTTP (aiohttp)"),
    ("httpx", "HTTP 客户端 (httpx)"),
    ("pydantic", "数据校验 (pydantic)"),
    ("yaml", "YAML 配置 (PyYAML)"),
    ("croniter", "定时表达式 (croniter)"),
    ("tzdata", "时区数据库 (tzdata)"),
    ("typing_extensions", "类型兼容层 (typing-extensions)"),
)

# 与 pyproject.toml optional-dependencies 对齐
OPTIONAL_DEPENDENCY_GROUPS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "cli",
        "CLI 交互界面（``pip install -e '.[cli]'``）",
        (
            ("prompt_toolkit", "CLI 界面 (prompt-toolkit)"),
            ("rich", "Markdown 渲染 (rich)"),
        ),
    ),
    (
        "feishu",
        "飞书集成（``pip install -e '.[feishu]'``）",
        (
            ("lark_oapi", "飞书 SDK (lark-oapi)"),
            ("mistune", "Markdown 解析 (mistune)"),
            ("websockets", "WebSocket (websockets)"),
        ),
    ),
    (
        "browser",
        "浏览器工具（``pip install -e '.[browser]'``）",
        (("playwright", "浏览器自动化 (playwright)"),),
    ),
    (
        "mcp",
        "MCP 集成（``pip install -e '.[mcp]'``）",
        (("mcp", "Model Context Protocol (mcp)"),),
    ),
    (
        "providers",
        "Anthropic / Google provider（``pip install -e '.[providers]'``）",
        (
            ("anthropic", "Anthropic SDK"),
            ("google.genai", "Google Gen AI SDK"),
        ),
    ),
)

_DEFAULT_KNOWLEDGE_ROOT = "workspaces/knowledge"


def _is_module_available(module_name: str) -> bool:
    """探测 Python 模块是否可 import。"""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def _format_masked_secret(value: str) -> str:
    """脱敏显示密钥（保留前 8 个字符）。"""
    text = value.strip()
    if len(text) > 8:
        return text[:8] + "..."
    return "***"


def _resolve_api_key() -> tuple[str | None, str]:
    """解析 OpenAI API 密钥。

    Returns:
        ``(密钥值或 None, 来源标签)``；来源为 ``json``、``env`` 或空串。
    """
    json_key = get_config("secrets.llm.openai.api_key", "") or get_config(
        "secrets.openai_api_key", ""
    )
    if json_key and str(json_key).strip():
        return str(json_key).strip(), "json"

    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        return env_key, "env"

    return None, ""


def _config_file_paths() -> tuple[Path, Path]:
    """返回 defaults 与 user 配置文件的绝对路径。"""
    return get_config_paths()


def _resolve_knowledge_root() -> str:
    """解析知识库根目录（与 ``KnowledgeRegistry`` 读取配置的方式一致）。"""
    raw = get_config("knowledge.root") or get_config(
        "knowledge.default_root", _DEFAULT_KNOWLEDGE_ROOT
    )
    raw = str(raw).strip() or _DEFAULT_KNOWLEDGE_ROOT
    if os.path.isabs(raw):
        return raw
    return os.path.abspath(raw)


def _append_dependency_section(
    lines: list[str],
    *,
    missing_required: list[str],
    missing_optional: list[str],
) -> None:
    lines.append("### 必需依赖")
    for module_name, display_name in REQUIRED_DEPENDENCIES:
        if _is_module_available(module_name):
            lines.append(f"- ✅ {display_name}: 已安装")
        else:
            lines.append(f"- ❌ {display_name}: 未安装")
            missing_required.append(display_name)
    lines.append("")

    lines.append("### 可选依赖")
    for _group_id, group_hint, modules in OPTIONAL_DEPENDENCY_GROUPS:
        lines.append(f"- {group_hint}")
        for module_name, display_name in modules:
            if _is_module_available(module_name):
                lines.append(f"  - ✅ {display_name}: 已安装")
            else:
                lines.append(f"  - ⚠️ {display_name}: 未安装")
                missing_optional.append(f"{display_name}（{group_hint.split('（')[0]}）")
    lines.append("")


def _append_config_diagnostics(lines: list[str]) -> tuple[Path, bool]:
    """追加配置文件诊断并返回用户配置路径及存在状态。"""
    defaults_path, user_path = _config_file_paths()
    lines.append("### 配置文件")
    lines.append(
        f"- ✅ 默认配置: {defaults_path}"
        if defaults_path.is_file()
        else f"- ❌ 默认配置缺失: {defaults_path}"
    )
    user_exists = user_path.is_file()
    lines.append(
        f"- ✅ 用户配置: {user_path}"
        if user_exists
        else f"- ⚠️ 用户配置缺失: {user_path}（将仅使用 defaults）"
    )
    lines.append("")
    return user_path, user_exists


def _append_api_diagnostics(lines: list[str]) -> bool:
    """追加 API/模型配置诊断，返回是否存在 API 密钥。"""
    api_key, api_source = _resolve_api_key()
    lines.append("### API 与模型配置")
    if api_key:
        source_label = {
            "json": "config.user.json secrets.llm.openai.api_key",
            "env": "环境变量 OPENAI_API_KEY",
        }.get(api_source, "未知来源")
        lines.append(f"- ✅ API 密钥 ({source_label}): {_format_masked_secret(api_key)}")
    else:
        lines.append("- ❌ API 密钥: 未设置（需 secrets.llm 或 provider 环境变量）")
    from miniagent.infrastructure.llm.factory import effective_llm_config

    llm = effective_llm_config(get_config, get_user_config_section)
    roles_raw = llm.get("roles")
    models_raw = llm.get("models")
    providers_raw = llm.get("providers")
    roles = roles_raw if isinstance(roles_raw, dict) else {}
    models = models_raw if isinstance(models_raw, dict) else {}
    providers = providers_raw if isinstance(providers_raw, dict) else {}
    default_profile = str(roles.get("default") or "primary")
    default_model = models.get(default_profile, {})
    if not isinstance(default_model, dict):
        default_model = {}
    provider_entry = providers.get(str(default_model.get("provider") or ""), {})
    if not isinstance(provider_entry, dict):
        provider_entry = {}
    headers = provider_entry.get("headers")
    has_user_agent = isinstance(headers, dict) and any(
        str(key).lower() == "user-agent" and bool(str(value).strip())
        for key, value in headers.items()
    )
    api = str(default_model.get("api", "未配置"))
    api_display = {
        "openai_responses": "responses",
        "openai_chat": "chat_completions",
    }.get(api, api)
    lines.extend(
        [
            f"- 默认 profile: {default_profile}",
            f"- Provider: {default_model.get('provider', '未配置')}",
            f"- 模型: {default_model.get('model', '未配置')}",
            f"- 传输协议: {api_display}",
            f"- 自定义 User-Agent: {'已设置' if has_user_agent else '未设置'}",
            "",
        ]
    )
    return bool(api_key)


def _append_storage_diagnostics(lines: list[str]) -> None:
    """追加状态目录与知识库目录诊断。"""
    from miniagent.infrastructure.paths import resolve_state_dir

    state_dir = resolve_state_dir()
    lines.append("### 状态目录")
    if os.path.isdir(state_dir):
        lines.append(f"- ✅ 状态目录存在: {state_dir}")
        for subdir in ("sessions", "memory"):
            path = os.path.join(state_dir, subdir)
            status = "✅" if os.path.isdir(path) else "ℹ️"
            suffix = "" if os.path.isdir(path) else " (尚未创建，首次使用时自动创建)"
            lines.append(f"  - {status} {subdir}/{suffix}")
    else:
        lines.extend([f"- ℹ️ 状态目录尚未创建: {state_dir}", "  - 首次启动会话时将自动创建"])
    knowledge_root = _resolve_knowledge_root()
    lines.extend(["", "### 知识库", f"- 配置路径: {knowledge_root}"])
    lines.append(
        "- ✅ 知识库根目录存在"
        if os.path.isdir(knowledge_root)
        else "- ℹ️ 知识库根目录不存在（未启用知识库或尚未挂载时为正常情况）"
    )
    lines.append("")


def diagnose_environment() -> str:
    """诊断安装与配置环境。

    Returns:
        Markdown 风格的多行诊断报告。
    """
    lines = ["## MiniAgent 环境诊断", ""]

    lines.append("### Python 环境")
    lines.append(f"- 版本: {sys.version}")
    lines.append(f"- 平台: {sys.platform}")
    lines.append(f"- 可执行文件: {sys.executable}")
    lines.append("")

    missing_required: list[str] = []
    missing_optional: list[str] = []
    _append_dependency_section(
        lines,
        missing_required=missing_required,
        missing_optional=missing_optional,
    )

    user_path, user_exists = _append_config_diagnostics(lines)
    has_api_key = _append_api_diagnostics(lines)
    _append_storage_diagnostics(lines)

    lines.append("### 建议")
    issues: list[str] = []
    tips: list[str] = []

    if missing_required:
        issues.append(f"缺少 {len(missing_required)} 个必需依赖: {', '.join(missing_required)}")
        tips.append("重新安装: pip install -e .")

    if not has_api_key:
        issues.append("未配置 OpenAI API 密钥")
        tips.append(
            "运行 `python -m miniagent` 生成 config.user.json，并在 secrets 中填写 openai_api_key"
        )
        tips.append("或设置环境变量 OPENAI_API_KEY")

    if not user_exists:
        issues.append("config.user.json 不存在")
        if not tips or "生成 config.user.json" not in tips[0]:
            tips.append("运行 `python -m miniagent` 生成 config.user.json 并按需覆盖配置")

    if missing_optional:
        tips.append(
            "可选组件未装全时，相关功能可能不可用；按需安装 cli、feishu、browser 或 mcp extra"
        )

    if issues:
        lines.append("⚠️ 发现以下问题:")
        for issue in issues:
            lines.append(f"  - {issue}")
        lines.append("")
        lines.append("建议操作:")
        for i, tip in enumerate(tips, start=1):
            lines.append(f"{i}. {tip}")
    else:
        lines.append("✅ 关键配置检查通过")
        if missing_optional:
            lines.append("")
            lines.append("ℹ️ 部分可选依赖未安装，不影响基本 Agent 运行。")

    return "\n".join(lines)


def _configure_stdout_encoding() -> None:
    """尽力将 stdout 设为 UTF-8，避免 Windows 控制台输出 emoji 失败。"""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def print_diagnose_report() -> None:
    """将 :func:`diagnose_environment` 的报告打印到 stdout。"""
    _configure_stdout_encoding()
    print(diagnose_environment())


__all__ = [
    "REQUIRED_DEPENDENCIES",
    "OPTIONAL_DEPENDENCY_GROUPS",
    "diagnose_environment",
    "print_diagnose_report",
    "_format_masked_secret",
    "_is_module_available",
    "_resolve_api_key",
    "_resolve_knowledge_root",
]
