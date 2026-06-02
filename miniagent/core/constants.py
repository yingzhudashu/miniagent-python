"""MiniAgent 配置常量模块

集中管理分散在各模块中的硬编码配置值，提供：
- 默认值定义
- 环境变量覆盖支持
- 配置文档说明

配置优先级：
1. 环境变量（MINIAGENT_*）
2. JSON 配置文件（config.user.json）
3. 本模块定义的默认值

相关文档：docs/ENV_REFERENCE.md
"""

from __future__ import annotations

import os

# ─── 执行限制 ───

# Agent 最大执行轮数（环境变量 AGENT_MAX_TURNS 或 MINIAGENT_AGENT_MAX_TURNS）
AGENT_MAX_TURNS = int(os.environ.get("MINIAGENT_AGENT_MAX_TURNS", os.environ.get("AGENT_MAX_TURNS", "400")))

# 分步执行单步最大轮数
STEP_MAX_TURNS = int(os.environ.get("MINIAGENT_STEP_MAX_TURNS", "48"))

# 规划器最大重试次数
PLANNER_MAX_RETRIES = 3

# 需求澄清最大问题数（按难度分级）
CLARIFIER_MAX_QUESTIONS_SIMPLE = 0
CLARIFIER_MAX_QUESTIONS_NORMAL = 1
CLARIFIER_MAX_QUESTIONS_COMPLEX = 3

# ─── 缓存大小 ───

# Markdown 渲染缓存大小
RENDER_CACHE_MAX_SIZE = int(os.environ.get("MINIAGENT_RENDER_CACHE_MAX_SIZE", "100"))

# 卡片去重缓存大小
CARD_DEDUPE_MAX_SIZE = int(os.environ.get("MINIAGENT_CARD_DEDUPE_MAX_SIZE", "256"))

# 卡片内容提取最大节点数
CARD_EXTRACT_MAX_NODES = int(os.environ.get("MINIAGENT_CARD_EXTRACT_MAX_NODES", "400"))

# 卡片内容提取最大深度
CARD_EXTRACT_MAX_DEPTH = 12

# ─── 日志与输出限制 ───

# 工具参数日志最大长度
MAX_ARGS_LOG_LEN = int(os.environ.get("MINIAGENT_MAX_ARGS_LOG_LEN", "500"))

# Transcript 最大字符数
MAX_TRANSCRIPT_CHARS = int(os.environ.get("MINIAGENT_MAX_TRANSCRIPT_CHARS", "400000"))

# ─── 飞书配置 ───

# 多维表格记录列表单页最大数
BITABLE_LIST_RECORDS_MAX = int(os.environ.get("MINIAGENT_BITABLE_LIST_RECORDS_MAX", "500"))

# 多维表格记录列表默认页大小
BITABLE_DEFAULT_PAGE_SIZE = int(os.environ.get("MINIAGENT_BITABLE_DEFAULT_PAGE_SIZE", "100"))

# ─── 记忆配置 ───

# 归档消息数阈值
HISTORY_ARCHIVE_MAX_MESSAGES = int(os.environ.get("MINIAGENT_HISTORY_ARCHIVE_MAX_MESSAGES", "120"))

# ─── 答案改进配置 ───

# 答案改进最大迭代次数
IMPROVE_MAX_ITERATIONS = int(os.environ.get("MINIAGENT_IMPROVE_MAX_ITERATIONS", "3"))

# ─── 飞书去重配置 ───

# 去重刷盘间隔（秒）
DEDUP_FLUSH_INTERVAL = int(os.environ.get("MINIAGENT_DEDUP_FLUSH_INTERVAL", "60"))

# 去重刷盘阈值（条数）
DEDUP_FLUSH_THRESHOLD = int(os.environ.get("MINIAGENT_DEDUP_FLUSH_THRESHOLD", "1000"))

# ─── 飞书云盘配置 ───

# 云盘文件列表页大小
LIST_FILE_PAGE_SIZE = int(os.environ.get("MINIAGENT_LIST_FILE_PAGE_SIZE", "50"))

# ─── 飞书Docx配置 ───

# Docx 批量追加最大块数
DOCX_APPEND_MAX_BLOCKS = int(os.environ.get("MINIAGENT_DOCX_APPEND_MAX_BLOCKS", "30"))

# Docx 列举块最大数
DOCX_LIST_BLOCKS_MAX = int(os.environ.get("MINIAGENT_DOCX_LIST_BLOCKS_MAX", "200"))

# ─── 实例管理配置 ───

# 实例心跳超时（秒）
HEARTBEAT_TIMEOUT = int(os.environ.get("MINIAGENT_HEARTBEAT_TIMEOUT", "30"))

# 实例缓存 TTL（秒）
INSTANCE_CACHE_TTL = float(os.environ.get("MINIAGENT_INSTANCE_CACHE_TTL", "30.0"))

# ─── 循环检测配置 ───

# 循环检测参数缓存大小
ARGS_CACHE_MAX_SIZE = int(os.environ.get("MINIAGENT_ARGS_CACHE_MAX_SIZE", "100"))

# ─── 关键词索引配置 ───

# 关键词提取最大数
KEYWORD_EXTRACT_MAX = int(os.environ.get("MINIAGENT_KEYWORD_EXTRACT_MAX", "50"))

# 关键词索引过期天数
KEYWORD_PRUNE_DAYS = int(os.environ.get("MINIAGENT_KEYWORD_PRUNE_DAYS", "30"))

# ─── 会话管理配置 ───

# 会话管理器最大会话数
SESSION_MANAGER_MAX_SESSIONS = int(os.environ.get("MINIAGENT_SESSION_MANAGER_MAX_SESSIONS", "50"))

# ─── Agent 配置默认值 ───

# 默认历史大小
AGENT_HISTORY_SIZE = int(os.environ.get("MINIAGENT_HISTORY_SIZE", "50"))

# 默认工具超时（秒）
AGENT_TOOL_TIMEOUT = int(os.environ.get("MINIAGENT_TOOL_TIMEOUT", "60"))

# ─── 飞书消息发送配置 ───

# 飞书消息发送超时（秒）
FEISHU_SEND_TIMEOUT = float(os.environ.get("MINIAGENT_FEISHU_SEND_TIMEOUT", "30.0"))


__all__ = [
    "AGENT_MAX_TURNS",
    "STEP_MAX_TURNS",
    "PLANNER_MAX_RETRIES",
    "CLARIFIER_MAX_QUESTIONS_SIMPLE",
    "CLARIFIER_MAX_QUESTIONS_NORMAL",
    "CLARIFIER_MAX_QUESTIONS_COMPLEX",
    "RENDER_CACHE_MAX_SIZE",
    "CARD_DEDUPE_MAX_SIZE",
    "CARD_EXTRACT_MAX_NODES",
    "CARD_EXTRACT_MAX_DEPTH",
    "MAX_ARGS_LOG_LEN",
    "MAX_TRANSCRIPT_CHARS",
    "BITABLE_LIST_RECORDS_MAX",
    "BITABLE_DEFAULT_PAGE_SIZE",
    "HISTORY_ARCHIVE_MAX_MESSAGES",
    "IMPROVE_MAX_ITERATIONS",
    "DEDUP_FLUSH_INTERVAL",
    "DEDUP_FLUSH_THRESHOLD",
    "LIST_FILE_PAGE_SIZE",
    "DOCX_APPEND_MAX_BLOCKS",
    "DOCX_LIST_BLOCKS_MAX",
    "HEARTBEAT_TIMEOUT",
    "INSTANCE_CACHE_TTL",
    "ARGS_CACHE_MAX_SIZE",
    "KEYWORD_EXTRACT_MAX",
    "KEYWORD_PRUNE_DAYS",
    "SESSION_MANAGER_MAX_SESSIONS",
    "AGENT_HISTORY_SIZE",
    "AGENT_TOOL_TIMEOUT",
    "FEISHU_SEND_TIMEOUT",
]