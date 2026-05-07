"""Mini Agent Python — 安全模块

提供路径沙箱验证，防止目录穿越攻击。
所有文件操作必须通过 resolve_sandbox_path() 验证路径合法性。
"""

from src.security.sandbox import resolve_sandbox_path, get_default_workspace

__all__ = ["resolve_sandbox_path", "get_default_workspace"]
