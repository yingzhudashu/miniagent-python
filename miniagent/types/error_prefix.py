"""Mini Agent Python — 输出前缀常量

统一工具输出、CLI提示、飞书回复等场景的错误/警告/成功前缀。
使用常量而非硬编码 emoji，便于：
- 批量替换/国际化
- 日志系统统一过滤（如「⚠️」前缀不入磁盘去重）
- 风格一致性

**约定**：
- ``ERROR_PREFIX``：操作失败（权限拒绝、文件不存在、API 错误等）
- ``WARNING_PREFIX``：提示/警告（配置缺失、建议、需确认等）
- ``SUCCESS_PREFIX``：操作成功（文件写入、发送完成等）

**配置**：
- 从JSON配置加载默认值，环境变量可覆盖
- MINIAGENT_UI_ERROR_PREFIX, MINIAGENT_UI_WARNING_PREFIX, MINIAGENT_UI_SUCCESS_PREFIX

所有工具返回 ``ToolResult(success=False, content=...)`` 应使用 ``ERROR_PREFIX`` 或 ``WARNING_PREFIX``。
"""

from __future__ import annotations

import os

from miniagent.infrastructure.json_config import get_config

# 从JSON配置加载默认值
ERROR_PREFIX = get_config("ui.error_prefix", "❌")
WARNING_PREFIX = get_config("ui.warning_prefix", "⚠️")
SUCCESS_PREFIX = get_config("ui.success_prefix", "✅")

# 环境变量覆盖支持
if os.environ.get("MINIAGENT_UI_ERROR_PREFIX"):
    ERROR_PREFIX = os.environ.get("MINIAGENT_UI_ERROR_PREFIX") or ERROR_PREFIX
if os.environ.get("MINIAGENT_UI_WARNING_PREFIX"):
    WARNING_PREFIX = os.environ.get("MINIAGENT_UI_WARNING_PREFIX") or WARNING_PREFIX
if os.environ.get("MINIAGENT_UI_SUCCESS_PREFIX"):
    SUCCESS_PREFIX = os.environ.get("MINIAGENT_UI_SUCCESS_PREFIX") or SUCCESS_PREFIX

__all__ = ["ERROR_PREFIX", "WARNING_PREFIX", "SUCCESS_PREFIX"]