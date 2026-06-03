"""Mini Agent Python — 统一错误消息常量

定义项目中使用的统一错误消息，便于：
- 保持消息一致性
- 支持国际化准备
- 减少硬编码字符串

分类：
- 配置相关：CONFIG_*
- 依赖相关：DEPENDENCY_*
- 操作相关：OPERATION_*
- 飞书相关：FEISHU_*
"""

from __future__ import annotations

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
FEISHU_WEBsocket_DISCONNECTED = "飞书 WebSocket 连接已断开"
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
SANDBOX_COMMAND_BLOCKED = "命令不在白名单中"

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
    # 飞书
    "FEISHU_CONFIG_MISSING",
    "FEISHU_APP_ID_MISSING",
    "FEISHU_APP_SECRET_MISSING",
    "FEISHU_WEBsocket_DISCONNECTED",
    "FEISHU_RATE_LIMITED",
    "FEISHU_PERMISSION_DENIED",
    # 文件
    "FILE_NOT_FOUND",
    "FILE_READ_ERROR",
    "FILE_WRITE_ERROR",
    "FILE_PATH_INVALID",
    # 会话
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
    "SANDBOX_COMMAND_BLOCKED",
]