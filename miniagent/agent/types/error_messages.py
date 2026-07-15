"""Mini Agent Python — 统一错误消息常量

定义项目中使用的统一用户可见消息，便于保持文案一致、减少硬编码，并为后续 i18n 预留入口。

**两类常量**：

- **简单常量**（如 ``FILE_NOT_FOUND``）：纯文本，无前缀；适用于 CLI 打印、异常消息等。
- **模板常量**（如 ``FILE_NOT_FOUND_WITH_PATH``）：已含 ``ERROR_PREFIX`` / ``WARNING_PREFIX`` /
  ``SUCCESS_PREFIX``，占位符为 ``{key}``；须通过 :func:`format_message` 填充后返回给用户或
  ``ToolResult.content``。

前缀语义见 ``miniagent/types/error_prefix.py``；输出约定见 ``docs/OUTPUT_FORMAT.md``。

**分类前缀**：

- 配置：``CONFIG_*``
- 依赖：``DEPENDENCY_*``
- 通用操作：``OPERATION_*``
- 飞书：``FEISHU_*``（配置级简单常量 + 工具级模板常量）
- 文件：``FILE_*``、``DIRECTORY_*``、``DIR_*``、``TEXT_*``
- 命令：``COMMAND_*``
- 数据：``JSON_*``、``CSV_*``、``DATA_*``
- 视觉：``IMAGE_*``、``LLM_*``、``MODEL_*``
- 知识库：``KB_*``
- 技能：``SKILL_*``
- CLI 点命令：``DOT_*``
- 定时任务：``SCHEDULE_*``
- 会话：``SESSION_*``（通用简单常量 + 记忆/日记模板常量）
- 工具：``TOOL_*``
- 沙箱：``SANDBOX_*``
- 成功反馈：``FILE_WRITTEN``、``RECORD_*``、``SCHEDULE_TASK_*`` 等（``SUCCESS_PREFIX`` 模板）
"""

from __future__ import annotations

from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX

# ============================================================================
# 配置相关错误消息
# ============================================================================

CONFIG_ENV_MISSING = "未配置必要的环境变量"
CONFIG_JSON_INVALID = "配置文件格式无效"
CONFIG_PATH_NOT_FOUND = "配置路径不存在"

# ============================================================================
# 依赖相关错误消息
# ============================================================================

DEPENDENCY_LARK_OAPI_MISSING = "请安装 lark-oapi（pip install miniagent-python[feishu]）"
DEPENDENCY_PLAYWRIGHT_MISSING = "请安装 playwright（pip install miniagent-python[browser] && playwright install chromium）"
DEPENDENCY_RICH_MISSING = "请安装 rich（pip install miniagent-python[cli]）"
DEPENDENCY_MCP_MISSING = "请安装 mcp（pip install miniagent-python[mcp]）"

# ============================================================================
# 操作相关错误消息
# ============================================================================

OPERATION_TIMEOUT = "操作超时"
OPERATION_CANCELLED = "操作被取消"
OPERATION_FAILED = "操作失败"

# ============================================================================
# 飞书相关错误消息
# ============================================================================

FEISHU_CONFIG_MISSING = "未配置 FEISHU_APP_ID / FEISHU_APP_SECRET"
FEISHU_APP_ID_MISSING = "未配置 FEISHU_APP_ID"
FEISHU_APP_SECRET_MISSING = "未配置 FEISHU_APP_SECRET"
FEISHU_WEBSOCKET_DISCONNECTED = "飞书 WebSocket 连接已断开"
FEISHU_RATE_LIMITED = "飞书 API 调用频率超限"
FEISHU_PERMISSION_DENIED = "飞书操作权限不足"

# ============================================================================
# 文件相关错误消息
# ============================================================================

FILE_NOT_FOUND = "文件不存在"
FILE_READ_ERROR = "文件读取失败"
FILE_WRITE_ERROR = "文件写入失败"
FILE_PATH_INVALID = "路径无效"

# ============================================================================
# 会话相关错误消息
# ============================================================================

SESSION_NOT_FOUND = "会话不存在"
SESSION_LOCKED = "会话被锁定"
SESSION_EXPIRED = "会话已过期"

# ============================================================================
# 工具相关错误消息
# ============================================================================

TOOL_NOT_FOUND = "工具不存在"
TOOL_EXECUTION_FAILED = "工具执行失败"
TOOL_TIMEOUT = "工具执行超时"
TOOL_PERMISSION_DENIED = "工具操作权限不足"

# ============================================================================
# 沙箱相关错误消息
# ============================================================================

SANDBOX_PATH_VIOLATION = "路径超出沙箱允许范围"
SANDBOX_PATH_VIOLATION_DETAIL = '路径越权: "{path}" 超出允许的范围: {allowed_dirs}'
SANDBOX_COMMAND_BLOCKED = "命令不在白名单中"

# ============================================================================
# 文件操作详细消息（带前缀模板；简单 FILE_* 见上方）
# ============================================================================

FILE_NOT_FOUND_WITH_PATH = f"{ERROR_PREFIX} 文件不存在: {{path}}"
FILE_PERMISSION_READ_DENIED = f"{ERROR_PREFIX} 权限不足，无法读取: {{path}}"
FILE_PERMISSION_WRITE_DENIED = f"{ERROR_PREFIX} 权限不足，无法写入: {{path}}"
FILE_IS_DIRECTORY = f"{ERROR_PREFIX} 路径是目录而非文件: {{path}}"
DIRECTORY_NOT_FOUND = f"{ERROR_PREFIX} 目录不存在: {{path}}"
FILE_SOURCE_NOT_FOUND = f"{ERROR_PREFIX} 源文件不存在: {{src}}"
FILE_DELETE_RECURSIVE_REQUIRED = f"{ERROR_PREFIX} 删除目录需设置 recursive=true"
TEXT_NOT_FOUND = f"{ERROR_PREFIX} 未找到匹配的文本: \"{{text}}...\""
TEXT_MULTIPLE_MATCHES = f"{ERROR_PREFIX} 找到 {{count}} 处匹配，请提供更精确的 oldText"
FILE_READ_FAILED = f"{ERROR_PREFIX} 读取文件失败: {{error}}"
FILE_WRITE_FAILED = f"{ERROR_PREFIX} 写入文件失败: {{error}}"
FILE_DELETE_FAILED = f"{ERROR_PREFIX} 删除失败: {{error}}"
FILE_MOVE_FAILED = f"{ERROR_PREFIX} 移动失败: {{error}}"
FILE_COPY_FAILED = f"{ERROR_PREFIX} 复制失败: {{error}}"

# ============================================================================
# 命令执行详细消息
# ============================================================================

COMMAND_EMPTY = f"{ERROR_PREFIX} 命令不能为空"
COMMAND_SYNTAX_INVALID = f"{ERROR_PREFIX} 命令语法无效"
COMMAND_BLOCKED = f"{ERROR_PREFIX} 命令被拒绝: {{reason}}"
COMMAND_BLOCKED_DANGER = f"{ERROR_PREFIX} 包含危险操作 \"{{pattern}}\""
COMMAND_BLOCKED_INJECTION = f"{ERROR_PREFIX} 检测到可能的 shell 注入模式"
COMMAND_BLOCKED_NOT_ALLOWED = f"{ERROR_PREFIX} '{{cmd}}' 不在允许的命令列表中"
COMMAND_EXECUTION_FAILED = f"{ERROR_PREFIX} 执行失败: {{error}}"
COMMAND_TIMEOUT = f"{ERROR_PREFIX} 命令执行超时 ({{timeout}}s)"

# ============================================================================
# 数据处理消息
# ============================================================================

JSON_PARSE_FAILED = f"{ERROR_PREFIX} JSON 解析失败: {{error}}"
JSON_NOT_VALID = f"{ERROR_PREFIX} data 不是有效 JSON: {{error}}"
CSV_READ_FAILED = f"{ERROR_PREFIX} CSV 读取失败: {{error}}"
CSV_WRITE_FAILED = f"{ERROR_PREFIX} CSV 写入失败: {{error}}"
DATA_EMPTY = f"{ERROR_PREFIX} data 必须是非空数组"
DATA_PARSE_FAILED = f"{ERROR_PREFIX} 数据解析失败: {{error}}"

# ============================================================================
# 视觉工具消息
# ============================================================================

IMAGE_NOT_FOUND = f"{ERROR_PREFIX} 图片文件不存在: {{path}}"
IMAGE_TOO_LARGE = f"{ERROR_PREFIX} 图片文件过大 ({{size}}MB)，上限 {{max}}MB"
IMAGE_ANALYSIS_FAILED = f"{ERROR_PREFIX} 图片分析失败"
LLM_CLIENT_NOT_CONFIGURED = f"{ERROR_PREFIX} LLM 客户端未配置: {{error}}"
MODEL_NOT_CONFIGURED = f"{ERROR_PREFIX} 未配置模型"

# ============================================================================
# 知识库消息
# ============================================================================

KB_QUERY_EMPTY = f"{WARNING_PREFIX} query 参数不能为空"
KB_NOT_FOUND = f"{WARNING_PREFIX} 未找到相关内容"
KB_NOT_MOUNTED = f"{WARNING_PREFIX} 知识库 '{{name}}' 未挂载"
KB_FILE_NOT_FOUND = f"{WARNING_PREFIX} 文件不存在: {{path}}"
KB_READ_FAILED = f"{ERROR_PREFIX} 读取知识库文件失败: {{error}}"
KB_SEARCH_FAILED = f"{ERROR_PREFIX} 检索失败: {{error}}"
KB_LIST_FAILED = f"{ERROR_PREFIX} 获取知识库列表失败: {{error}}"

# ============================================================================
# 技能管理消息
# ============================================================================

SKILL_ALREADY_INSTALLED = f"{WARNING_PREFIX} 技能 \"{{slug}}\" 已安装在 {{path}}"
SKILL_INSTALL_FAILED = f"{ERROR_PREFIX} 安装技能 \"{{slug}}\" 失败: {{error}}"
SKILL_NOT_INSTALLED = f"{WARNING_PREFIX} 技能 \"{{slug}}\" 未安装在 {{path}}"
SKILL_UNINSTALL_FAILED = f"{ERROR_PREFIX} 卸载技能 \"{{slug}}\" 失败: {{error}}"

# ============================================================================
# 飞书工具消息（带前缀模板；简单 FEISHU_* 见上方）
# ============================================================================

FEISHU_RECEIVE_ID_MISSING = f"{WARNING_PREFIX} 缺少 receive_id"
FEISHU_MESSAGE_ID_MISSING = f"{WARNING_PREFIX} 需要 message_id"
FEISHU_SEND_FAILED = f"{WARNING_PREFIX} 飞书发送失败: {{error}}"
# 含 {error}；无异常详情时用 FEISHU_DELETE_FAILED_MSG
FEISHU_RECALL_FAILED = f"{WARNING_PREFIX} 飞书删除消息 API 失败: {{error}}"
FEISHU_FILE_NOT_FOUND = f"{WARNING_PREFIX} 文件不存在: {{path}}"
FEISHU_FOLDER_TOKEN_MISSING = f"{WARNING_PREFIX} 缺少 folder_token"
FEISHU_DOC_TOKEN_MISSING = f"{WARNING_PREFIX} 需要 doc_token 或 document_id"
FEISHU_TABLE_ID_MISSING = f"{WARNING_PREFIX} 需要 app_token 与 table_id"
FEISHU_BLOCK_ID_MISSING = f"{WARNING_PREFIX} 需要 doc_token 与 block_id"
FEISHU_ACTION_UNKNOWN = f"{WARNING_PREFIX} 未知 action={{action}}"
FEISHU_CONTENT_EMPTY = f"{WARNING_PREFIX} content 为空"
FEISHU_WORKSPACE_MISSING = f"{WARNING_PREFIX} 缺少工作区路径或 relative_path"
FEISHU_UPLOAD_FAILED = f"{WARNING_PREFIX} 上传或发送失败: {{error}}"
FEISHU_LIST_FAILED = f"{WARNING_PREFIX} 列举失败: {{error}}"
FEISHU_UPDATE_FAILED = f"{WARNING_PREFIX} 更新失败: {{error}}"
FEISHU_DELETE_FAILED_MSG = f"{WARNING_PREFIX} 飞书删除消息 API 失败"

# ============================================================================
# CLI 点命令（/help 等）消息
# ============================================================================

DOT_COMMAND_INVALID = f"{WARNING_PREFIX} 参数 line 必须以 / 开头（与终端命令一致）"
DOT_COMMAND_CONTEXT_MISSING = f"{WARNING_PREFIX} 命令工具仅在完整进程集成（含 runtime_ctx）中可用"
DOT_COMMAND_UNKNOWN = f"{WARNING_PREFIX} 未识别的命令；请使用 /help 查看列表"
DOT_COMMAND_MUTATION_BLOCKED = f"{WARNING_PREFIX} 当前渠道不允许修改定时任务（飞书场景）"

# ============================================================================
# 定时任务消息
# ============================================================================

SCHEDULE_ACTION_MISSING = f"{ERROR_PREFIX} 缺少 action"
SCHEDULE_TASK_ID_MISSING = f"{ERROR_PREFIX} {{action}} 需要 task_id"
SCHEDULE_TASK_NOT_FOUND = f"{ERROR_PREFIX} 未找到任务: {{tid}}"
SCHEDULE_TASK_ID_EXISTS = f"{ERROR_PREFIX} 任务 ID 已存在: {{tid}}"
SCHEDULE_INTERVAL_INVALID = f"{ERROR_PREFIX} add_interval 需要 task_id、prompt、interval_seconds（正整数）"
SCHEDULE_ONCE_INVALID = f"{ERROR_PREFIX} add_once 需要 task_id、prompt、once_iso（ISO8601）"
SCHEDULE_CRON_INVALID = f"{ERROR_PREFIX} add_cron 需要 task_id、prompt、cron_expr（5 段 Unix cron）"
SCHEDULE_UPDATE_INVALID = f"{ERROR_PREFIX} update 需要 task_id、prompt"
SCHEDULE_ONCE_PARSE_FAILED = f"{ERROR_PREFIX} 无法解析 once_iso，请使用 ISO8601"
SCHEDULE_CRON_TIME_PAST = f"{ERROR_PREFIX} 一次性任务时间已在过去"
SCHEDULE_CRON_NO_NEXT = f"{ERROR_PREFIX} 无法计算下次触发时间"
SCHEDULE_ENABLED_INVALID = f"{ERROR_PREFIX} set_enabled 需要 enabled 布尔值"
SCHEDULE_ACTION_UNKNOWN = f"{ERROR_PREFIX} 未知 action: {{action}}"

# ============================================================================
# 会话记忆消息（带前缀模板；通用 SESSION_* 见上方）
# ============================================================================

SESSION_KEY_MISSING = f"{ERROR_PREFIX} 当前无 session_key，无法定位会话日记"
SESSION_QUERY_EMPTY = f"{ERROR_PREFIX} query 不能为空"
SESSION_DIARY_READ_FAILED = f"{ERROR_PREFIX} 读取失败: {{error}}"
SESSION_DIARY_NOT_FOUND = f"{ERROR_PREFIX} 未找到日记文件: {{path}}（日期 {{day}}）"

# ============================================================================
# 成功消息
# ============================================================================

FILE_WRITTEN = f"{SUCCESS_PREFIX} 已写入 {{path}} ({{size}} 字节)"
FILE_WRITTEN_SHORT = f"{SUCCESS_PREFIX} 已写入 {{path}}"
FILE_DELETED = f"{SUCCESS_PREFIX} 已删除: {{path}}"
FILE_MOVED = f"{SUCCESS_PREFIX} 已移动: {{src}} → {{dst}}"
FILE_COPIED = f"{SUCCESS_PREFIX} 已复制: {{src}} → {{dst}}"
FILE_REPLACED = f"{SUCCESS_PREFIX} 已替换 1 处 ({{old}} → {{new}} 字符)"
DIR_CREATED = f"{SUCCESS_PREFIX} 已创建目录: {{path}}"
RECORD_DELETED = f"{SUCCESS_PREFIX} 已删除记录 {{rid}}"
RECORD_DELETED_BATCH = f"{SUCCESS_PREFIX} 已批量删除 {{count}} 条记录"
SKILL_INSTALLED = f"{SUCCESS_PREFIX} 技能 \"{{slug}}\" 安装成功"
SKILL_UNINSTALLED = f"{SUCCESS_PREFIX} 技能 \"{{slug}}\" 已卸载"
FEISHU_SEND_SUCCESS = f"{SUCCESS_PREFIX} 已发送到当前飞书会话"
FEISHU_RECALL_SUCCESS = f"{SUCCESS_PREFIX} 已请求撤回该消息"
FEISHU_CARD_SENT = f"{SUCCESS_PREFIX} 已发送交互卡片"
FEISHU_DOC_CREATED = f"{SUCCESS_PREFIX} 已创建云文档"
SCHEDULE_TASK_ADDED = f"{SUCCESS_PREFIX} 已添加 {{kind}} 任务 {{tid}}"
SCHEDULE_TASK_REMOVED = f"{SUCCESS_PREFIX} 已删除任务 {{tid}}"
SCHEDULE_TASK_UPDATED = f"{SUCCESS_PREFIX} 已更新 {{tid}}"
DOT_COMMAND_EXIT = f"{SUCCESS_PREFIX} 实例已停止"

# ============================================================================
# 辅助函数
# ============================================================================


def format_message(template: str, **kwargs: str | int | float | bool) -> str:
    """将模板中的 ``{key}`` 占位符替换为对应值。

    模板常量在本模块中通过 f-string 定义（如 ``{{path}}``），运行时字面量为 ``{path}``。
    本函数按关键字名做简单 ``str.replace``，不使用 ``str.format``，避免与 JSON 花括号冲突。

    Args:
        template: 含 ``{key}`` 占位符的消息模板（通常为模块级模板常量）。
        **kwargs: 占位符名到替换值的映射；值会经 ``str()`` 转为字符串。

    Returns:
        替换后的完整消息。

    Note:
        - 未传入的占位符会原样保留。
        - 多余的 ``kwargs`` 会被忽略。
        - 若替换值本身含 ``{other_key}`` 形态的文本，可能被后续轮次误替换（当前场景极少见）。

    Example:
        >>> format_message(FILE_NOT_FOUND_WITH_PATH, path="/test.txt")
        '❌ 文件不存在: /test.txt'
    """
    result = template
    for key, value in kwargs.items():
        result = result.replace("{" + key + "}", str(value))
    return result


def format_sandbox_path_violation(path: str, allowed_dirs: list[str]) -> str:
    """生成沙箱路径越界的详细错误消息。

    Args:
        path: 尝试访问的路径。
        allowed_dirs: 允许的目录列表。

    Returns:
        与 ``SandboxViolationError`` 及 ``resolve_sandbox_path`` 一致的消息文本。
    """
    return format_message(
        SANDBOX_PATH_VIOLATION_DETAIL,
        path=path,
        allowed_dirs=", ".join(allowed_dirs),
    )


__all__ = [
    # 配置
    "CONFIG_ENV_MISSING",
    "CONFIG_JSON_INVALID",
    "CONFIG_PATH_NOT_FOUND",
    # 依赖
    "DEPENDENCY_LARK_OAPI_MISSING",
    "DEPENDENCY_PLAYWRIGHT_MISSING",
    "DEPENDENCY_RICH_MISSING",
    "DEPENDENCY_MCP_MISSING",
    # 操作
    "OPERATION_TIMEOUT",
    "OPERATION_CANCELLED",
    "OPERATION_FAILED",
    # 飞书配置
    "FEISHU_CONFIG_MISSING",
    "FEISHU_APP_ID_MISSING",
    "FEISHU_APP_SECRET_MISSING",
    "FEISHU_WEBSOCKET_DISCONNECTED",
    "FEISHU_RATE_LIMITED",
    "FEISHU_PERMISSION_DENIED",
    # 文件（简单）
    "FILE_NOT_FOUND",
    "FILE_READ_ERROR",
    "FILE_WRITE_ERROR",
    "FILE_PATH_INVALID",
    # 会话（简单）
    "SESSION_NOT_FOUND",
    "SESSION_LOCKED",
    "SESSION_EXPIRED",
    # 工具
    "TOOL_NOT_FOUND",
    "TOOL_EXECUTION_FAILED",
    "TOOL_TIMEOUT",
    "TOOL_PERMISSION_DENIED",
    # 沙箱
    "SANDBOX_PATH_VIOLATION",
    "SANDBOX_PATH_VIOLATION_DETAIL",
    "SANDBOX_COMMAND_BLOCKED",
    # 文件操作（模板）
    "FILE_NOT_FOUND_WITH_PATH",
    "FILE_PERMISSION_READ_DENIED",
    "FILE_PERMISSION_WRITE_DENIED",
    "FILE_IS_DIRECTORY",
    "DIRECTORY_NOT_FOUND",
    "FILE_SOURCE_NOT_FOUND",
    "FILE_DELETE_RECURSIVE_REQUIRED",
    "TEXT_NOT_FOUND",
    "TEXT_MULTIPLE_MATCHES",
    "FILE_READ_FAILED",
    "FILE_WRITE_FAILED",
    "FILE_DELETE_FAILED",
    "FILE_MOVE_FAILED",
    "FILE_COPY_FAILED",
    # 命令执行（模板）
    "COMMAND_EMPTY",
    "COMMAND_SYNTAX_INVALID",
    "COMMAND_BLOCKED",
    "COMMAND_BLOCKED_DANGER",
    "COMMAND_BLOCKED_INJECTION",
    "COMMAND_BLOCKED_NOT_ALLOWED",
    "COMMAND_EXECUTION_FAILED",
    "COMMAND_TIMEOUT",
    # 数据处理（模板）
    "JSON_PARSE_FAILED",
    "JSON_NOT_VALID",
    "CSV_READ_FAILED",
    "CSV_WRITE_FAILED",
    "DATA_EMPTY",
    "DATA_PARSE_FAILED",
    # 视觉（模板）
    "IMAGE_NOT_FOUND",
    "IMAGE_TOO_LARGE",
    "IMAGE_ANALYSIS_FAILED",
    "LLM_CLIENT_NOT_CONFIGURED",
    "MODEL_NOT_CONFIGURED",
    # 知识库（模板）
    "KB_QUERY_EMPTY",
    "KB_NOT_FOUND",
    "KB_NOT_MOUNTED",
    "KB_FILE_NOT_FOUND",
    "KB_READ_FAILED",
    "KB_SEARCH_FAILED",
    "KB_LIST_FAILED",
    # 技能（模板）
    "SKILL_ALREADY_INSTALLED",
    "SKILL_INSTALL_FAILED",
    "SKILL_NOT_INSTALLED",
    "SKILL_UNINSTALL_FAILED",
    # 飞书工具（模板）
    "FEISHU_RECEIVE_ID_MISSING",
    "FEISHU_MESSAGE_ID_MISSING",
    "FEISHU_SEND_FAILED",
    "FEISHU_RECALL_FAILED",
    "FEISHU_FILE_NOT_FOUND",
    "FEISHU_FOLDER_TOKEN_MISSING",
    "FEISHU_DOC_TOKEN_MISSING",
    "FEISHU_TABLE_ID_MISSING",
    "FEISHU_BLOCK_ID_MISSING",
    "FEISHU_ACTION_UNKNOWN",
    "FEISHU_CONTENT_EMPTY",
    "FEISHU_WORKSPACE_MISSING",
    "FEISHU_UPLOAD_FAILED",
    "FEISHU_LIST_FAILED",
    "FEISHU_UPDATE_FAILED",
    "FEISHU_DELETE_FAILED_MSG",
    # CLI 点命令（模板）
    "DOT_COMMAND_INVALID",
    "DOT_COMMAND_CONTEXT_MISSING",
    "DOT_COMMAND_UNKNOWN",
    "DOT_COMMAND_MUTATION_BLOCKED",
    # 定时任务（模板）
    "SCHEDULE_ACTION_MISSING",
    "SCHEDULE_TASK_ID_MISSING",
    "SCHEDULE_TASK_NOT_FOUND",
    "SCHEDULE_TASK_ID_EXISTS",
    "SCHEDULE_INTERVAL_INVALID",
    "SCHEDULE_ONCE_INVALID",
    "SCHEDULE_CRON_INVALID",
    "SCHEDULE_UPDATE_INVALID",
    "SCHEDULE_ONCE_PARSE_FAILED",
    "SCHEDULE_CRON_TIME_PAST",
    "SCHEDULE_CRON_NO_NEXT",
    "SCHEDULE_ENABLED_INVALID",
    "SCHEDULE_ACTION_UNKNOWN",
    # 会话记忆（模板）
    "SESSION_KEY_MISSING",
    "SESSION_QUERY_EMPTY",
    "SESSION_DIARY_READ_FAILED",
    "SESSION_DIARY_NOT_FOUND",
    # 成功消息（模板）
    "FILE_WRITTEN",
    "FILE_WRITTEN_SHORT",
    "FILE_DELETED",
    "FILE_MOVED",
    "FILE_COPIED",
    "FILE_REPLACED",
    "DIR_CREATED",
    "RECORD_DELETED",
    "RECORD_DELETED_BATCH",
    "SKILL_INSTALLED",
    "SKILL_UNINSTALLED",
    "FEISHU_SEND_SUCCESS",
    "FEISHU_RECALL_SUCCESS",
    "FEISHU_CARD_SENT",
    "FEISHU_DOC_CREATED",
    "SCHEDULE_TASK_ADDED",
    "SCHEDULE_TASK_REMOVED",
    "SCHEDULE_TASK_UPDATED",
    "DOT_COMMAND_EXIT",
    # 辅助函数
    "format_message",
    "format_sandbox_path_violation",
]