"""MiniAgent Internal 层配置常量（写死，不可通过 JSON/ENV 覆盖）

本模块定义系统内部常量，这些值在运行时不可更改，用于：
- 性能优化参数（缓存大小、回调频率等）
- 安全边界（并发上限、超时底线等）
- UI 渲染参数（终端宽度、边距等）

配置分层说明：
┌─────────────────────────────────────────────────────────────┐
│ Internal 层（本模块）— 写死常量，不可覆盖                      │
│ ├── 性能：回调频率、缓存大小、并发上限                         │
│ ├── 安全：超时底线、最大轮数限制                               │
│ └── 渲染：终端宽度范围、边距                                   │
├─────────────────────────────────────────────────────────────┤
│ Advanced 层（config.defaults.json）— 运维参数，可 JSON 覆盖   │
│ ├── 超时：http_timeout、tool_timeout                          │
│ ├── 并发：max_concurrent_tools                                │
│ └── 记忆：max_history_messages                                │
├─────────────────────────────────────────────────────────────┤
│ User 层（config.user.json）— 用户可见配置                      │
│ ├── 模型：model、temperature、thinking_level                   │
│ ├── Agent：max_turns、context_reserve_ratio                   │
│ └── 路径：state_dir、allowed_paths                            │
└─────────────────────────────────────────────────────────────┘

修改 Internal 常量的唯一方式是修改本模块源码并重新部署。

设计背景见 docs/ENGINEERING.md §1.1 配置分层。
"""

from __future__ import annotations

# ─── Agent 遗留常量（未走 JsonConfigLoader 的路径）───

# Agent 全局最大轮数（防止无限循环）
DEFAULT_AGENT_MAX_TURNS = 400  # 默认值，可通过 config.user.json 覆盖
DEFAULT_AGENT_TOOL_TIMEOUT = 60  # 单工具执行超时（秒）

# 向后兼容别名（推荐直接使用 DEFAULT_* 常量）
AGENT_MAX_TURNS = DEFAULT_AGENT_MAX_TURNS
AGENT_TOOL_TIMEOUT = DEFAULT_AGENT_TOOL_TIMEOUT
AGENT_HISTORY_SIZE = 50  # 历史消息保留上限

# ─── Execution ───

# 任务难度分类（是否启用 Phase 0 预分类）
EXECUTION_ANNOUNCE_DIFFICULTY = True  # 在执行前显示难度公告
EXECUTION_TASK_CLASSIFIER_ENABLED = True  # 启用任务难度分类器

# 分步执行模式（Phase 1 规划后按步骤分子循环）
EXECUTION_PHASED_ENABLED = True  # 开启分步执行
EXECUTION_STEP_MAX_TURNS = 48  # 单步骤最大轮数（防止某步骤卡死）
STEP_MAX_TURNS = EXECUTION_STEP_MAX_TURNS  # 向后兼容别名

# 思考输出格式
EXECUTION_THINKING_SEPARATOR = ""  # 思考块分隔符
EXECUTION_TOOL_INTENT_MAX_CHARS = 4000  # 工具意图截断上限
EXECUTION_TOOL_INTENT_IN_THINKING = False  # 是否在 thinking 中显示工具意图

# 工具执行行为
EXECUTION_TOOL_FINISH_VERBOSE = False  # 工具完成时详细输出
EXECUTION_THINKING_MERGE_TOOLS = True  # 合并同轮多工具输出
EXECUTION_TERMINAL_WIDTH_CACHE_TTL = 2.0  # 终端宽度缓存 TTL（秒）
TERMINAL_WIDTH_CACHE_TTL = EXECUTION_TERMINAL_WIDTH_CACHE_TTL  # 向后兼容别名
EXECUTION_MAX_CONCURRENT_TOOLS = 5  # 最大并发工具数（防止资源耗尽）

# 回调频率控制（防止 UI 过载）
EXECUTION_CALLBACK_MIN_INTERVAL_MS = 50  # 回调最小间隔（毫秒）
EXECUTION_CALLBACK_MIN_CHARS = 100  # 回调最小字符增量

# ─── Render ───

# 终端渲染宽度限制（防止窄屏/宽屏显示问题）
RENDER_MIN_WIDTH = 40  # 最小有效宽度（字符）
RENDER_MAX_WIDTH = 500  # 最大有效宽度（字符）
RENDER_WIDTH_MARGIN = 4  # 边距宽度（字符）

# ─── CLI（Internal 实现细节）───

# Markdown 渲染模式
CLI_RAW_MARKDOWN = False  # 是否直接输出原始 Markdown
CLI_THINKING_RICH = False  # 是否使用富文本思考显示

# 终端布局参数
CLI_WIDTH_MARGIN = 1  # CLI 边距
CLI_WRAP_THRESHOLD = 40  # 文本换行阈值
CLI_BASH_TIMEOUT = 60  # Bash 命令超时（秒）
CLI_RENDER_CACHE_MAX_SIZE = 100  # 渲染缓存大小上限
RENDER_CACHE_MAX_SIZE = CLI_RENDER_CACHE_MAX_SIZE  # 向后兼容别名

# 思考输出样式（ANSI 颜色）
CLI_STYLE_THINK_HEAD = "ansibrightcyan"  # 思考标题颜色
CLI_STYLE_THINK_BODY = "ansibrightcyan"  # 思考正文颜色

# 自我优化工具
CLI_SELF_OPT_TOOLS = True  # 是否启用自我优化工具

# ─── 规划与澄清 ───

# 规划器重试上限
PLANNER_MAX_RETRIES = 3  # 规划失败时最大重试次数

# 澄清器最大追问数（按难度分档）
CLARIFIER_MAX_QUESTIONS_SIMPLE = 0  # 简单任务不追问
CLARIFIER_MAX_QUESTIONS_NORMAL = 1  # 普通任务最多1个问题
CLARIFIER_MAX_QUESTIONS_COMPLEX = 3  # 复杂任务最多3个问题

# ─── 缓存与日志限制 ───

# 飞书卡片缓存（防止重复操作）
CARD_DEDUPE_MAX_SIZE = 256  # 卡片去重缓存大小
CARD_EXTRACT_MAX_NODES = 400  # 卡片内容提取最大节点数
CARD_EXTRACT_MAX_DEPTH = 12  # 卡片内容提取最大深度

# 日志截断限制（防止日志膨胀）
MAX_ARGS_LOG_LEN = 500  # 工具参数日志截断长度
MAX_TRANSCRIPT_CHARS = 400000  # Transcript 最大字符数

# ─── 飞书 Internal ───

FEISHU_PATCH_INTERVAL_S = 0.08
FEISHU_PATCH_CHAR_DELTA = 20
FEISHU_PATCH_BUDGET = 60
FEISHU_PATCH_TIMEOUT_S = 10.0
FEISHU_PATCH_QUEUE_ENABLED = False
FEISHU_PATCH_QUEUE_MAX_SIZE = 10
FEISHU_PATCH_IMPORTANT_CONTENT_IMMEDIATE = True
FEISHU_VISION_MAX_BYTES = 20971520
FEISHU_API_URL_TENANT_TOKEN = (
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
)
FEISHU_API_URL_ROOT_FOLDER_META = (
    "https://open.feishu.cn/open-apis/drive/explorer/v2/root_folder/meta"
)
BITABLE_LIST_RECORDS_MAX = 500
BITABLE_DEFAULT_PAGE_SIZE = 100
DEDUP_FLUSH_INTERVAL = 60
DEDUP_FLUSH_THRESHOLD = 1000
LIST_FILE_PAGE_SIZE = 50
DOCX_APPEND_MAX_BLOCKS = 30
DOCX_LIST_BLOCKS_MAX = 200
FEISHU_SEND_TIMEOUT = 30.0

# ─── 记忆 Internal ───

MEMORY_MAINTENANCE_MAX_ITERS = 3
HISTORY_ARCHIVE_MAX_MESSAGES = 120
IMPROVE_MAX_ITERATIONS = 3
KNOWLEDGE_MAX_FILE_CHARS = 50000

# ─── 浏览器 ───

BROWSER_IDLE_TIMEOUT_SECONDS = 300
BROWSER_TIMEOUT_SECONDS = 60
BROWSER_DISABLE_IMAGES = True
BROWSER_DISABLE_STYLES = True

# ─── Web 搜索 ───

WEB_SEARCH_TAVILY_URL = "https://api.tavily.com/search"
WEB_SEARCH_TAVILY_TIMEOUT = 45.0

# ─── ClawHub ───

CLAWHUB_API_URL = "https://clawhub.ai/api/v1"

# ─── 性能缓存 ───

PERF_JSON_CACHE_MAX_SIZE = 500

# ─── 实例管理 ───

INSTANCE_HEARTBEAT_TIMEOUT = 30
HEARTBEAT_TIMEOUT = INSTANCE_HEARTBEAT_TIMEOUT
INSTANCE_CACHE_TTL = 30.0

# ─── 循环检测 ───

ARGS_CACHE_MAX_SIZE = 100

# ─── 关键词索引 ───

KEYWORD_INDEX_MAX_KEYWORDS = 20
KEYWORD_INDEX_MIN_KEYWORD_LEN = 2
KEYWORD_EXTRACT_MAX = 50
KEYWORD_PRUNE_DAYS = 30

# ─── 会话管理 ───

SESSION_MANAGER_MAX_SESSIONS = 50

# ─── 后台任务 ───

BACKGROUND_TASKS_MAX_CONCURRENT = 4
BACKGROUND_TASKS_TASK_TTL_SECONDS = 3600


__all__ = [
    "DEFAULT_AGENT_MAX_TURNS",
    "DEFAULT_AGENT_TOOL_TIMEOUT",
    "AGENT_MAX_TURNS",
    "AGENT_TOOL_TIMEOUT",
    "AGENT_HISTORY_SIZE",
    "EXECUTION_ANNOUNCE_DIFFICULTY",
    "EXECUTION_TASK_CLASSIFIER_ENABLED",
    "EXECUTION_PHASED_ENABLED",
    "EXECUTION_STEP_MAX_TURNS",
    "STEP_MAX_TURNS",
    "EXECUTION_THINKING_SEPARATOR",
    "EXECUTION_TOOL_INTENT_MAX_CHARS",
    "EXECUTION_TOOL_INTENT_IN_THINKING",
    "EXECUTION_TOOL_FINISH_VERBOSE",
    "EXECUTION_THINKING_MERGE_TOOLS",
    "EXECUTION_TERMINAL_WIDTH_CACHE_TTL",
    "TERMINAL_WIDTH_CACHE_TTL",
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
    "RENDER_CACHE_MAX_SIZE",
    "CLI_STYLE_THINK_HEAD",
    "CLI_STYLE_THINK_BODY",
    "CLI_SELF_OPT_TOOLS",
    "PLANNER_MAX_RETRIES",
    "CLARIFIER_MAX_QUESTIONS_SIMPLE",
    "CLARIFIER_MAX_QUESTIONS_NORMAL",
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
    "FEISHU_PATCH_QUEUE_ENABLED",
    "FEISHU_PATCH_QUEUE_MAX_SIZE",
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
    "HEARTBEAT_TIMEOUT",
    "INSTANCE_CACHE_TTL",
    "ARGS_CACHE_MAX_SIZE",
    "KEYWORD_INDEX_MAX_KEYWORDS",
    "KEYWORD_INDEX_MIN_KEYWORD_LEN",
    "KEYWORD_EXTRACT_MAX",
    "KEYWORD_PRUNE_DAYS",
    "SESSION_MANAGER_MAX_SESSIONS",
    "BACKGROUND_TASKS_MAX_CONCURRENT",
    "BACKGROUND_TASKS_TASK_TTL_SECONDS",
]
