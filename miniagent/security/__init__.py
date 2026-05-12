"""Mini Agent Python — 安全模块

路径沙箱：工具层在读写文件前应通过 ``resolve_sandbox_path`` 校验目标路径落在
``ToolContext.allowed_paths``（及工作区默认根，见 ``get_default_workspace``）内，
以降低 prompt injection 导致的越权读写的风险。威胁模型与解析规则见 ``sandbox`` 模块文档。
"""

from miniagent.security.sandbox import get_default_workspace, resolve_sandbox_path

__all__ = ["resolve_sandbox_path", "get_default_workspace"]
