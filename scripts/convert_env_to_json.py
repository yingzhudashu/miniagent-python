"""将 .env 配置转换为 JSON 格式

此脚本将现有的 .env 文件转换为：
1. workspaces/config.user.json - 非敏感的用户配置
2. .env.secrets - 敏感凭据（API密钥等）
3. .env.backup - 原.env备份

使用方法：
    python scripts/convert_env_to_json.py [--dry-run] [--env-path PATH]

选项：
    --dry-run    仅显示转换结果，不写入文件
    --env-path   指定.env文件路径（默认项目根目录）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime
from typing import Any


# ============================================================================
# 敏感配置清单（分离到 .env.secrets）
# ============================================================================
SENSITIVE_KEYS = frozenset([
    # API密钥
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "WEB_SEARCH_API_KEY",
    "MINIAGENT_EMBED_API_KEY",
    # 飞书凭据
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_VERIFICATION_TOKEN",
    "FEISHU_ENCRYPT_KEY",
    "MINIAGENT_FEISHU_USER_ACCESS_TOKEN",
    # GitHub令牌
    "GITHUB_TOKEN",
])


# ============================================================================
# 环境变量到JSON路径映射
# ============================================================================
ENV_TO_JSON_MAPPING: dict[str, str] = {
    # === 模型配置 ===
    "OPENAI_BASE_URL": "model.base_url",
    "OPENAI_MODEL": "model.model",
    "AGENT_TEMPERATURE": "model.temperature",
    "AGENT_TOP_P": "model.top_p",
    "OPENAI_MAX_TOKENS": "model.max_tokens",
    "AGENT_THINKING_DEFAULT": "model.thinking_level",
    "OPENAI_THINKING_BUDGET": "model.thinking_budget",
    "AGENT_CONTEXT_WINDOW": "model.context_window",

    # === Agent配置 ===
    "AGENT_MAX_TURNS": "agent.max_turns",
    "AGENT_TOOL_TIMEOUT": "agent.tool_timeout",
    "AGENT_HTTP_TIMEOUT": "agent.http_timeout",
    "AGENT_CONTEXT_RESERVE": "agent.context_reserve_ratio",
    "AGENT_CONTEXT_COMPRESS_THRESHOLD": "agent.context_compress_threshold",
    "AGENT_DEBUG": "agent.debug",
    "AGENT_LOG_TOKEN_USAGE": "agent.log_token_usage",
    "AGENT_ALLOW_PARALLEL_TOOLS": "agent.allow_parallel_tools",

    # === 循环检测配置 ===
    "LOOP_DETECTION_ENABLED": "agent.loop_detection.enabled",
    "LOOP_HISTORY_SIZE": "agent.loop_detection.history_size",
    "LOOP_WARNING_THRESHOLD": "agent.loop_detection.warning_threshold",
    "LOOP_CRITICAL_THRESHOLD": "agent.loop_detection.critical_threshold",

    # === 执行配置 ===
    "MINIAGENT_ANNOUNCE_DIFFICULTY_AND_PLAN": "execution.announce_difficulty",
    "MINIAGENT_TASK_CLASSIFIER": "execution.task_classifier_enabled",
    "MINIAGENT_PHASED_EXECUTION": "execution.phased_enabled",
    "MINIAGENT_STEP_MAX_TURNS": "execution.step_max_turns",
    "MINIAGENT_THINKING_SEGMENT_SEPARATOR": "execution.thinking_separator",
    "MINIAGENT_TOOL_INTENT_MAX_CHARS": "execution.tool_intent_max_chars",
    "MINIAGENT_TOOL_INTENT_IN_THINKING": "execution.tool_intent_in_thinking",
    "MINIAGENT_TOOL_FINISH_VERBOSE": "execution.tool_finish_verbose",
    "MINIAGENT_THINKING_MERGE_TOOLS": "execution.thinking_merge_tools",
    "MINIAGENT_TERMINAL_WIDTH_CACHE_TTL": "execution.terminal_width_cache_ttl",

    # === CLI配置 ===
    "MINIAGENT_CLI_DOT_TOOLS": "cli.dot_tools_enabled",
    "MINIAGENT_CLI_RAW_MARKDOWN": "cli.raw_markdown",
    "MINIAGENT_CLI_THINKING_RICH": "cli.thinking_rich",
    "MINIAGENT_CLI_WIDTH_MARGIN": "cli.width_margin",
    "MINIAGENT_CLI_WRAP_THRESHOLD": "cli.wrap_threshold",
    "MINIAGENT_CLI_FILE_VISION_DESC": "cli.file_vision_desc",
    "MINIAGENT_WELCOME_CLI_HINT": "cli.welcome_hint",
    "MINIAGENT_SELF_OPT_TOOLS": "cli.self_opt_tools",

    # === 飞书配置（非凭据）===
    "MINIAGENT_FEISHU_REPLY_PLAIN": "feishu.reply_plain",
    "MINIAGENT_FEISHU_REPLY_TARGET": "feishu.reply_target",
    "MINIAGENT_FEISHU_CARD_ACTION_ROUTER": "feishu.card_action_router",
    "MINIAGENT_FEISHU_TOOLS_AUTO": "feishu.tools_auto",
    "MINIAGENT_FEISHU_TOOLS": "feishu.tools_explicit",
    "MINIAGENT_FEISHU_DOT_COMMANDS_FULL": "feishu.dot_commands_full",
    "MINIAGENT_FEISHU_CARD_EXTRACT_INBOUND": "feishu.card_extract_inbound",
    "MINIAGENT_FEISHU_MARKDOWN_COMMANDS": "feishu.markdown_commands",
    "MINIAGENT_FEISHU_REPLY_IN_THREAD": "feishu.reply_in_thread",
    "MINIAGENT_FEISHU_RECEIVE_ID_TYPE": "feishu.receive_id_type",
    "MINIAGENT_FEISHU_MAX_MESSAGE_AGE_S": "feishu.max_message_age",
    "MINIAGENT_FEISHU_MEDIA_RUN_AGENT": "feishu.media.run_agent",
    "MINIAGENT_FEISHU_MEDIA_VISION_DESC": "feishu.media.vision_desc",
    "MINIAGENT_FEISHU_MEDIA_SILENT_REPLY": "feishu.media.silent_reply",
    "MINIAGENT_FEISHU_DOC_FOLDER_TOKEN": "feishu.doc.folder_token",
    "MINIAGENT_FEISHU_DOCX_URL_PREFIX": "feishu.doc.docx_url_prefix",
    "FEISHU_DOC_FOLDER_FALLBACK_ROOT_META": "feishu.doc.folder_fallback_root_meta",

    # === 飞书WebSocket配置 ===
    "MINIAGENT_FEISHU_WS_AUTO_RECONNECT": "feishu.websocket.auto_reconnect",
    "MINIAGENT_FEISHU_WS_WATCHDOG_INTERVAL_S": "feishu.websocket.watchdog_interval",
    "MINIAGENT_FEISHU_WS_DEAD_CONN_GRACE_S": "feishu.websocket.dead_conn_grace",
    "MINIAGENT_FEISHU_WS_RECONNECT_GRACE_S": "feishu.websocket.reconnect_grace",
    "MINIAGENT_FEISHU_WS_REFRESH_INTERVAL_S": "feishu.websocket.refresh_interval",
    "MINIAGENT_FEISHU_WS_IDLE_REFRESH_S": "feishu.websocket.idle_refresh",

    # === 飞书卡片配置 ===
    "MINI_AGENT_THINKING_FOR_LLM_MAX_CHARS": "feishu.card.thinking_max_chars",
    "MINI_AGENT_FEISHU_CARD_BODY_MAX": "feishu.card.body_max_chars",

    # === 定时任务配置 ===
    "MINIAGENT_DISABLE_SCHEDULED_TASKS": "scheduled_tasks.disabled",
    "MINIAGENT_SCHEDULE_DISPATCH_BACKOFF": "scheduled_tasks.dispatch_backoff",
    "MINIAGENT_SCHEDULE_TIMEZONE": "scheduled_tasks.timezone",
    "MINIAGENT_SCHEDULE_FEISHU_MIRROR": "scheduled_tasks.feishu_mirror",
    "MINIAGENT_SCHEDULE_FEISHU_LAST_CHAT": "scheduled_tasks.feishu_last_chat",
    "MINIAGENT_SCHEDULE_TOOLS": "scheduled_tools.enabled",

    # === 时区配置 ===
    "MINIAGENT_TIMEZONE": "timezone.default",

    # === 嵌入搜索配置（非密钥）===
    "MINIAGENT_EMBED_SEARCH": "embedding.enabled",
    "MINIAGENT_EMBED_BASE_URL": "embedding.base_url",
    "MINIAGENT_EMBED_MODEL": "embedding.model",
    "MINIAGENT_EMBED_DIM": "embedding.dimension",
    "MINIAGENT_EMBED_TOP_K": "embedding.top_k",
    "MINIAGENT_EMBED_MIN_SCORE": "embedding.min_score",
    "MINIAGENT_EMBED_MAX_ENTRIES": "embedding.max_entries",

    # === 内存配置 ===
    "MINI_AGENT_HISTORY_PROGRESSIVE": "memory.history_progressive",
    "MINI_AGENT_HISTORY_MAINTENANCE_MAX_ITERS": "memory.maintenance_max_iters",
    "MINI_AGENT_HISTORY_TAIL_MESSAGES": "memory.history_tail_messages",
    "MINI_AGENT_HISTORY_MAX_MESSAGES": "memory.history_max_messages",
    "MINI_AGENT_HISTORY_ARCHIVE_TOKEN_HINT": "memory.archive_token_hint",
    "MINIAGENT_MAX_HISTORY_MESSAGES": "memory.max_history_messages",
    "MINIAGENT_MEMORY_STORE_CACHE_MAX": "memory.store_cache_max",
    "MINIAGENT_REGISTRY_MAX_ENTRIES": "memory.registry_max_entries",
    "MINIAGENT_KEYWORD_INDEX_MAX": "memory.keyword_index_max",
    "MINI_AGENT_CONTEXT_TOOL_REDACT": "memory.context_tool_redact",
    "MINI_AGENT_LAYERED_MEMORY_INJECT": "memory.layered_inject",
    "MINI_AGENT_LAYERED_MEMORY_MAX_CHARS": "memory.layered_max_chars",
    "MINI_AGENT_DIARY_PREVIEW_CHARS": "memory.diary_preview_chars",
    "MINI_AGENT_LAYERED_MEMORY_SESSION_LT": "memory.layered_session_lt",
    "MINI_AGENT_LAYERED_MEMORY_AGENT_LT": "memory.layered_agent_lt",

    # === 路径配置 ===
    "MINI_AGENT_STATE": "paths.state_dir",
    "MINI_AGENT_WORKSPACE": "paths.workspace",
    "MINI_AGENT_SKILLS": "paths.skills_dir",

    # === 会话配置 ===
    "MINIAGENT_SESSION_NAME": "session.default_name",
    "MINIAGENT_CONTINUE_SESSION": "session.continue_mode",

    # === MCP配置 ===
    "MINIAGENT_MCP_STDIO": "mcp.stdio_command",

    # === 调试配置 ===
    "MINIAGENT_DEBUG_SESSION_ID": "debug.session_id",
    "MINIAGENT_DEBUG_LOG_PATH": "debug.log_path",
    "MINIAGENT_PERF_METRICS": "debug.perf_metrics",

    # === 知识库配置 ===
    "MINIAGENT_KB_ROOT": "knowledge.root",
    "MINIAGENT_KB_AUTO_MOUNT": "knowledge.auto_mount",
    "MINIAGENT_KB_MAX_CHARS": "knowledge.max_chars",

    # === Web搜索配置（非密钥）===
    "TAVILY_TIMEOUT": "web_search.tavily_timeout",
    "BROWSER_TOOL_TIMEOUT": "web_search.browser_timeout",

    # === 安全配置 ===
    "MINIAGENT_ALLOWED_COMMANDS": "security.allowed_commands",

    # === 功能开关 ===
    "MINIAGENT_REQUIREMENT_CLARIFY": "features.requirement_clarify",
    "MINIAGENT_REFLECTION": "features.reflection",
    "MINIAGENT_SKILLS_WATCH": "features.skills_watch",
    "MINI_AGENT_TUI_VERBOSE_LOG": "features.tui_verbose_log",
}


# ============================================================================
# 类型转换函数
# ============================================================================
def parse_value(value: str) -> Any:
    """将字符串值转换为适当的类型"""
    if value == "" or value.lower() == "null":
        return None

    # 布尔值
    if value.lower() in ("true", "1", "yes", "on"):
        return True
    if value.lower() in ("false", "0", "no", "off"):
        return False

    # 整数
    try:
        return int(value)
    except ValueError:
        pass

    # 浮点数
    try:
        return float(value)
    except ValueError:
        pass

    # JSON数组/对象（如MCP配置）
    if value.startswith("[") or value.startswith("{"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass

    # 字符串
    return value


def set_nested_value(config: dict[str, Any], path: str, value: Any) -> None:
    """设置嵌套字典中的值"""
    keys = path.split(".")
    current = config
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


# ============================================================================
# 主转换函数
# ============================================================================
def convert_env_to_json(env_path: str) -> tuple[dict[str, Any], dict[str, str]]:
    """将.env文件转换为JSON配置和敏感凭据

    Returns:
        (json_config, secrets) - JSON配置字典和敏感凭据字典
    """
    json_config: dict[str, Any] = {}
    secrets: dict[str, str] = {}

    # 解析.env文件
    env_values: dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    # 移除引号
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    env_values[key] = value

    # 分类处理
    for key, value in env_values.items():
        if key in SENSITIVE_KEYS:
            # 敏感配置 → secrets
            secrets[key] = value
        elif key in ENV_TO_JSON_MAPPING:
            # 有映射的配置 → JSON
            json_path = ENV_TO_JSON_MAPPING[key]
            parsed_value = parse_value(value)
            if parsed_value is not None:  # 忽略空值
                set_nested_value(json_config, json_path, parsed_value)
        else:
            # 未映射的配置，尝试自动映射（MINIAGENT_*前缀）
            if key.startswith("MINIAGENT_"):
                # 自动生成JSON路径
                auto_path = key[11:].lower().replace("_", ".")
                parsed_value = parse_value(value)
                if parsed_value is not None:
                    set_nested_value(json_config, auto_path, parsed_value)
                    print(f"  [自动映射] {key} → {auto_path}")

    return json_config, secrets


def main() -> None:
    parser = argparse.ArgumentParser(description="将.env转换为JSON配置")
    parser.add_argument("--dry-run", action="store_true", help="仅显示转换结果，不写入文件")
    parser.add_argument("--env-path", default=None, help=".env文件路径")
    args = parser.parse_args()

    # 确定项目根目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)

    # 确定.env路径
    env_path = args.env_path or os.path.join(project_root, ".env")
    workspaces_dir = os.path.join(project_root, "workspaces")

    print(f"项目根目录: {project_root}")
    print(f".env路径: {env_path}")
    print(f"输出目录: {workspaces_dir}")
    print()

    if not os.path.exists(env_path):
        print("❌ .env文件不存在")
        print("   请先创建.env文件或使用 --env-path 指定路径")
        return

    # 执行转换
    print("📖 读取.env文件...")
    json_config, secrets = convert_env_to_json(env_path)

    # 显示结果
    print()
    print("=" * 60)
    print("📋 JSON配置 (config.user.json):")
    print("=" * 60)
    print(json.dumps(json_config, indent=2, ensure_ascii=False))

    print()
    print("=" * 60)
    print("🔐 敏感凭据 (.env.secrets):")
    print("=" * 60)
    for key, value in secrets.items():
        # 显示值时部分隐藏
        display_value = value if len(value) < 8 else value[:4] + "****"
        print(f"{key}={display_value}")

    print()
    print("=" * 60)
    print("📊 统计:")
    print("=" * 60)
    print(f"  JSON配置项: {len(json_config)} 个顶级配置节")
    print(f"  敏感凭据: {len(secrets)} 个")

    if args.dry_run:
        print()
        print("⚠️  DRY-RUN模式 - 文件未写入")
        return

    # 确保workspaces目录存在
    if not os.path.exists(workspaces_dir):
        os.makedirs(workspaces_dir)
        print(f"✅ 创建目录: {workspaces_dir}")

    # 写入文件
    config_json_path = os.path.join(workspaces_dir, "config.user.json")
    secrets_path = os.path.join(project_root, ".env.secrets")
    backup_path = os.path.join(project_root, ".env.backup")

    # 写入config.user.json
    with open(config_json_path, "w", encoding="utf-8") as f:
        json.dump(json_config, f, indent=2, ensure_ascii=False)
    print(f"✅ 写入: {config_json_path}")

    # 写入.env.secrets
    with open(secrets_path, "w", encoding="utf-8") as f:
        f.write("# MiniAgent 敏感凭据（自动生成于 " + datetime.now().isoformat() + "）\n")
        f.write("# ⚠️ 此文件包含敏感信息，切勿提交到git\n\n")
        for key, value in secrets.items():
            f.write(f"{key}={value}\n")
    print(f"✅ 写入: {secrets_path}")

    # 备份原.env
    if os.path.exists(env_path):
        shutil.copy(env_path, backup_path)
        print(f"✅ 备份: {backup_path}")

    print()
    print("=" * 60)
    print("✅ 转换完成!")
    print("=" * 60)
    print()
    print("下一步:")
    print("  1. 检查 workspaces/config.user.json 确认配置正确")
    print("  2. 检查 .env.secrets 确认敏感凭据正确")
    print("  3. 将 .env.secrets 添加到 .gitignore（如果尚未添加）")
    print("  4. 原.env已备份到 .env.backup，可安全删除")


if __name__ == "__main__":
    main()