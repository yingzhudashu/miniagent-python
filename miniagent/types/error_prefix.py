"""Mini Agent Python — 输出前缀常量

统一工具输出、CLI提示、飞书回复等场景的错误/警告/成功前缀。

**约定**：
- ``ERROR_PREFIX``：操作失败（权限拒绝、文件不存在、API 错误等）
- ``WARNING_PREFIX``：提示/警告（配置缺失、建议、需确认等）
- ``SUCCESS_PREFIX``：操作成功（文件写入、发送完成等）

所有工具返回 ``ToolResult(success=False, content=...)`` 应使用 ``ERROR_PREFIX`` 或 ``WARNING_PREFIX``。
"""

from __future__ import annotations

from typing import Final

ERROR_PREFIX: Final[str] = "❌"
"""操作失败前缀（权限拒绝、文件不存在、API 错误等）。"""

WARNING_PREFIX: Final[str] = "⚠️"
"""提示/警告前缀（配置缺失、建议、需确认等非致命情况）。"""

SUCCESS_PREFIX: Final[str] = "✅"
"""操作成功前缀（文件写入、发送完成等）。"""

__all__ = ["ERROR_PREFIX", "WARNING_PREFIX", "SUCCESS_PREFIX"]
