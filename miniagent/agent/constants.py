"""MiniAgent 配置常量（Internal 层 + JSON 默认值种子）

本模块集中存放**编译期常量**与**JSON 配置的默认值种子**，分三类：

1. **纯 Internal** — 运行时不可通过 JSON/ENV 覆盖（如飞书 patch 节流、渲染边距、API 端点）。
2. **JSON 默认值种子** — 以 ``DEFAULT_*`` 命名；``config.py`` / ``get_config(key, constant)`` 将其作为
   包内 defaults 未显式设置时的回退值（如 ``DEFAULT_AGENT_MAX_TURNS``）。
3. **JSON 可覆盖的运行时回退** — 常量提供硬编码默认，但对应 JSON 键存在时以配置为准
   （如 ``HISTORY_ARCHIVE_MAX_MESSAGES`` ↔ ``memory.history_max_messages``）。

配置分层说明：
┌─────────────────────────────────────────────────────────────┐
│ Internal 层（纯常量，不可覆盖）                                │
│ ├── 性能：回调频率、缓存大小、工具并发上限                     │
│ ├── 安全：单步轮数上限、渲染宽度夹紧                         │
│ └── 集成：第三方 API 端点、飞书 patch 节流                     │
├─────────────────────────────────────────────────────────────┤
│ Advanced 层（包内 defaults）— 运维参数，可 JSON 覆盖         │
│ ├── 超时：agent.http_timeout、agent.tool_timeout              │
│ ├── 记忆：memory.max_history_messages、memory.maintenance_*   │
│ └── 渠道：feishu.websocket.*、feishu.card.*                 │
├─────────────────────────────────────────────────────────────┤
│ User 层（config.user.json）— 用户可见配置                      │
│ ├── 模型：model、temperature、thinking_level                   │
│ ├── Agent：agent.max_turns、agent.max_questions               │
│ └── 路径：paths.state_dir、paths.allowed_paths                  │
└─────────────────────────────────────────────────────────────┘

修改纯 Internal 常量的唯一方式是修改本模块源码并重新部署。
JSON 默认值种子应与本文件及包内 defaults 保持同步。

设计背景见 docs/ENGINEERING.md §1.1 配置分层。
"""

from __future__ import annotations

# ─── Agent JSON 默认值种子（可被 config.user.json 覆盖）───

# Agent 全局最大轮数（防止无限循环）；对应 agent.max_turns
DEFAULT_AGENT_MAX_TURNS = 400
# 单工具执行超时（秒）；对应 agent.tool_timeout
DEFAULT_AGENT_TOOL_TIMEOUT = 60
AGENT_HISTORY_SIZE_DEFAULT = 50

# ─── Execution（纯 Internal）───

# 任务难度分类（是否启用 Phase 0 预分类）
EXECUTION_ANNOUNCE_DIFFICULTY = True  # 在执行前显示难度公告
EXECUTION_TASK_CLASSIFIER_ENABLED = True  # 启用任务难度分类器

# 分步执行模式（Phase 1 规划后按步骤分子循环）
EXECUTION_PHASED_ENABLED = True  # 开启分步执行
EXECUTION_STEP_MAX_TURNS = 48  # 单步骤最大轮数（防止某步骤卡死）
EXECUTION_MAX_PLAN_CONFIRM_ROUNDS = 3  # 高风险计划被要求调整后的最大重新规划轮数

# 思考输出格式
EXECUTION_THINKING_SEPARATOR = ""  # 思考块分隔符
EXECUTION_TOOL_INTENT_MAX_CHARS = 4000  # 工具意图截断上限（字符）
EXECUTION_TOOL_INTENT_IN_THINKING = False  # 是否在 thinking 中显示工具意图

# 工具执行行为
EXECUTION_TOOL_FINISH_VERBOSE = False  # 工具完成时详细输出
EXECUTION_THINKING_MERGE_TOOLS = True  # 合并同轮多工具输出
EXECUTION_TERMINAL_WIDTH_CACHE_TTL = 2.0  # 终端宽度缓存 TTL（秒）
EXECUTION_MAX_CONCURRENT_TOOLS = 5  # 最大并发工具数（硬上限，无 JSON 覆盖）

# 回调频率控制（防止 UI 过载）
EXECUTION_CALLBACK_MIN_INTERVAL_MS = 50  # 回调最小间隔（毫秒）
EXECUTION_CALLBACK_MIN_CHARS = 100  # 回调最小字符增量

# ─── Render（纯 Internal）───

# 终端渲染宽度限制（防止窄屏/宽屏显示问题）
RENDER_MIN_WIDTH = 40  # 最小有效宽度（字符）
RENDER_MAX_WIDTH = 500  # 最大有效宽度（字符）
RENDER_WIDTH_MARGIN = 4  # 边距宽度（字符）

# ─── CLI（Internal 实现细节；部分可被 ENV/JSON 覆盖）───

# Markdown 渲染模式（可被 MINIAGENT_CLI_RAW_MARKDOWN / cli.raw_markdown 覆盖）
CLI_RAW_MARKDOWN = False
# 思考 Rich 渲染（可被 MINIAGENT_CLI_THINKING_RICH / cli.thinking_rich 覆盖）
CLI_THINKING_RICH = False

# 终端布局参数
CLI_WIDTH_MARGIN = 1  # CLI 边距（字符）
CLI_WRAP_THRESHOLD = 40  # 文本换行阈值（字符）
CLI_BASH_TIMEOUT = 60  # Bash 命令超时（秒）
CLI_RENDER_CACHE_MAX_SIZE = 100  # Markdown 渲染 LRU 缓存条目上限

# 思考输出样式（ANSI 颜色）
CLI_STYLE_THINK_HEAD = "ansibrightcyan"  # 思考标题颜色
CLI_STYLE_THINK_BODY = "ansibrightcyan"  # 思考正文颜色

# 自我优化工具
CLI_SELF_OPT_TOOLS = True  # 是否启用自我优化 CLI 工具

# ─── 规划与澄清（纯 Internal；agent.max_questions 为全局上限）───

PLANNER_MAX_RETRIES = 3  # 规划失败时最大重试次数
CLARIFIER_MAX_QUESTIONS_SIMPLE = 0  # 简单任务不追问
CLARIFIER_MAX_QUESTIONS_NORMAL = 1  # 一般任务最多 1 个问题
CLARIFIER_MAX_QUESTIONS_MEDIUM = 2  # 中等任务最多 2 个问题
CLARIFIER_MAX_QUESTIONS_COMPLEX = 3  # 复杂任务最多 3 个问题

# ─── 缓存与日志限制 ───

# 飞书卡片缓存（防止重复操作）
CARD_DEDUPE_MAX_SIZE = 256  # 卡片去重 LRU 缓存大小（条）
CARD_EXTRACT_MAX_NODES = 400  # 卡片内容提取最大节点数
CARD_EXTRACT_MAX_DEPTH = 12  # 卡片内容提取最大递归深度

# 日志截断限制（防止日志膨胀）
MAX_ARGS_LOG_LEN = 500  # 工具参数日志截断长度（字符）
# Transcript 默认上限；可被 memory.max_transcript_chars 覆盖
MAX_TRANSCRIPT_CHARS = 400000

# ─── 飞书 Internal（纯 Internal）───

FEISHU_PATCH_INTERVAL_S = 0.08  # 流式卡片 patch 最小间隔（秒）
FEISHU_PATCH_CHAR_DELTA = 20  # 触发 patch 的最小字符增量
FEISHU_PATCH_BUDGET = 60  # 单轮流式输出最大 patch 次数
FEISHU_PATCH_TIMEOUT_S = 10.0  # 单次 patch HTTP 超时（秒）
FEISHU_PATCH_IMPORTANT_CONTENT_IMMEDIATE = True  # 重要内容是否立即 patch
FEISHU_VISION_MAX_BYTES = 20971520  # 飞书图片 vision 最大字节数（20 MiB）
FEISHU_API_URL_TENANT_TOKEN = (
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
)
FEISHU_API_URL_ROOT_FOLDER_META = (
    "https://open.feishu.cn/open-apis/drive/explorer/v2/root_folder/meta"
)
BITABLE_LIST_RECORDS_MAX = 500  # Bitable 单次列举记录硬上限
BITABLE_DEFAULT_PAGE_SIZE = 100  # Bitable 分页默认 page_size
DEDUP_FLUSH_INTERVAL = 60  # 飞书入站去重刷盘间隔（秒）
DEDUP_FLUSH_THRESHOLD = 1000  # 触发去重刷盘的内存条目阈值
LIST_FILE_PAGE_SIZE = 50  # 云盘文件列表默认分页大小
DOCX_APPEND_MAX_BLOCKS = 30  # Docx 单次追加块数上限
DOCX_LIST_BLOCKS_MAX = 200  # Docx 列举块数上限
FEISHU_SEND_TIMEOUT = 30.0  # 飞书 IM 发送超时（秒）
FEISHU_SDK_CLIENT_CACHE_MAX_SIZE = 8  # Lark SDK 客户端缓存硬上限

# ─── 记忆 Internal ───

MEMORY_MAINTENANCE_MAX_ITERS = 3  # 渐进压缩维护最大迭代；可被 memory.maintenance_max_iters 覆盖
# 历史归档消息数阈值；可被 memory.history_max_messages 覆盖
HISTORY_ARCHIVE_MAX_MESSAGES = 120
MEMORY_SESSION_LOCKS_MAX = 2048  # 单进程缓存的会话锁上限，防止长期运行时无界增长
MEMORY_STORE_CACHE_CLEANUP_INTERVAL_S = 60  # 记忆缓存 TTL 全表清理的最小间隔
IMPROVE_MAX_ITERATIONS = 3  # /review 自我反驳式优化最大轮数
KNOWLEDGE_MAX_FILE_CHARS = 50000  # 知识库单文件读取字符上限

# ─── 浏览器（纯 Internal）───

BROWSER_IDLE_TIMEOUT_SECONDS = 300  # 浏览器空闲回收超时（秒）
BROWSER_TIMEOUT_SECONDS = 60  # 浏览器单次操作超时（秒）
BROWSER_DISABLE_IMAGES = True  # 默认禁用图片加载以提速
BROWSER_DISABLE_STYLES = True  # 默认禁用样式表以提速

# ─── Web 搜索（纯 Internal）───

WEB_SEARCH_TAVILY_URL = "https://api.tavily.com/search"
WEB_SEARCH_TAVILY_TIMEOUT = 45.0  # Tavily 请求超时（秒）

# ─── ClawHub（纯 Internal）───

CLAWHUB_API_URL = "https://clawhub.ai/api/v1"

# ─── 性能缓存（纯 Internal）───

PERF_JSON_CACHE_MAX_SIZE = 500  # JSON 解析 LRU 缓存条目上限

# ─── 实例管理（纯 Internal）───

INSTANCE_HEARTBEAT_TIMEOUT = 30  # 实例心跳超时（秒）
INSTANCE_CACHE_TTL = 30.0  # 实例元数据缓存 TTL（秒）

# ─── 循环检测（纯 Internal）───

ARGS_CACHE_MAX_SIZE = 100  # 工具参数指纹 LRU 缓存大小

# ─── 关键词索引 ───

KEYWORD_INDEX_MAX_KEYWORDS = 20  # 单条记忆索引存储的关键词上限
KEYWORD_INDEX_MIN_KEYWORD_LEN = 2  # 最短有效关键词长度（字符）
KEYWORD_EXTRACT_MAX = 50  # 从文本提取关键词时的默认上限
KEYWORD_PRUNE_DAYS = 30  # 关键词索引自动清理天数阈值

# ─── 会话管理（纯 Internal）───

SESSION_MANAGER_MAX_SESSIONS = 50  # 会话管理器内存中保留的最大会话数
SESSION_CONFIG_CACHE_MAX_SIZE = 2048  # 会话 config.json 指纹缓存上限

# ─── 后台任务（纯 Internal）───

BACKGROUND_TASKS_MAX_CONCURRENT = 4  # 后台任务默认并行上限
BACKGROUND_TASKS_TASK_TTL_SECONDS = 3600  # 已完成任务状态保留 TTL（秒）


__all__ = [
    "DEFAULT_AGENT_MAX_TURNS",
    "DEFAULT_AGENT_TOOL_TIMEOUT",
    "AGENT_HISTORY_SIZE_DEFAULT",
    "EXECUTION_ANNOUNCE_DIFFICULTY",
    "EXECUTION_TASK_CLASSIFIER_ENABLED",
    "EXECUTION_PHASED_ENABLED",
    "EXECUTION_STEP_MAX_TURNS",
    "EXECUTION_MAX_PLAN_CONFIRM_ROUNDS",
    "EXECUTION_THINKING_SEPARATOR",
    "EXECUTION_TOOL_INTENT_MAX_CHARS",
    "EXECUTION_TOOL_INTENT_IN_THINKING",
    "EXECUTION_TOOL_FINISH_VERBOSE",
    "EXECUTION_THINKING_MERGE_TOOLS",
    "EXECUTION_TERMINAL_WIDTH_CACHE_TTL",
    "EXECUTION_MAX_CONCURRENT_TOOLS",
    "EXECUTION_CALLBACK_MIN_INTERVAL_MS",
    "EXECUTION_CALLBACK_MIN_CHARS",
    "RENDER_MIN_WIDTH",
    "RENDER_MAX_WIDTH",
    "RENDER_WIDTH_MARGIN",
    "CLI_RAW_MARKDOWN",
    "CLI_THINKING_RICH",
    "CLI_WIDTH_MARGIN",
    "CLI_WRAP_THRESHOLD",
    "CLI_BASH_TIMEOUT",
    "CLI_RENDER_CACHE_MAX_SIZE",
    "CLI_STYLE_THINK_HEAD",
    "CLI_STYLE_THINK_BODY",
    "CLI_SELF_OPT_TOOLS",
    "PLANNER_MAX_RETRIES",
    "CLARIFIER_MAX_QUESTIONS_SIMPLE",
    "CLARIFIER_MAX_QUESTIONS_NORMAL",
    "CLARIFIER_MAX_QUESTIONS_MEDIUM",
    "CLARIFIER_MAX_QUESTIONS_COMPLEX",
    "CARD_DEDUPE_MAX_SIZE",
    "CARD_EXTRACT_MAX_NODES",
    "CARD_EXTRACT_MAX_DEPTH",
    "MAX_ARGS_LOG_LEN",
    "MAX_TRANSCRIPT_CHARS",
    "FEISHU_PATCH_INTERVAL_S",
    "FEISHU_PATCH_CHAR_DELTA",
    "FEISHU_PATCH_BUDGET",
    "FEISHU_PATCH_TIMEOUT_S",
    "FEISHU_PATCH_IMPORTANT_CONTENT_IMMEDIATE",
    "FEISHU_VISION_MAX_BYTES",
    "FEISHU_API_URL_TENANT_TOKEN",
    "FEISHU_API_URL_ROOT_FOLDER_META",
    "BITABLE_LIST_RECORDS_MAX",
    "BITABLE_DEFAULT_PAGE_SIZE",
    "DEDUP_FLUSH_INTERVAL",
    "DEDUP_FLUSH_THRESHOLD",
    "LIST_FILE_PAGE_SIZE",
    "DOCX_APPEND_MAX_BLOCKS",
    "DOCX_LIST_BLOCKS_MAX",
    "FEISHU_SEND_TIMEOUT",
    "FEISHU_SDK_CLIENT_CACHE_MAX_SIZE",
    "MEMORY_MAINTENANCE_MAX_ITERS",
    "HISTORY_ARCHIVE_MAX_MESSAGES",
    "IMPROVE_MAX_ITERATIONS",
    "KNOWLEDGE_MAX_FILE_CHARS",
    "BROWSER_IDLE_TIMEOUT_SECONDS",
    "BROWSER_TIMEOUT_SECONDS",
    "BROWSER_DISABLE_IMAGES",
    "BROWSER_DISABLE_STYLES",
    "WEB_SEARCH_TAVILY_URL",
    "WEB_SEARCH_TAVILY_TIMEOUT",
    "CLAWHUB_API_URL",
    "PERF_JSON_CACHE_MAX_SIZE",
    "INSTANCE_HEARTBEAT_TIMEOUT",
    "INSTANCE_CACHE_TTL",
    "ARGS_CACHE_MAX_SIZE",
    "KEYWORD_INDEX_MAX_KEYWORDS",
    "KEYWORD_INDEX_MIN_KEYWORD_LEN",
    "KEYWORD_EXTRACT_MAX",
    "KEYWORD_PRUNE_DAYS",
    "SESSION_MANAGER_MAX_SESSIONS",
    "SESSION_CONFIG_CACHE_MAX_SIZE",
    "BACKGROUND_TASKS_MAX_CONCURRENT",
    "BACKGROUND_TASKS_TASK_TTL_SECONDS",
]
