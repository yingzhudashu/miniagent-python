"""Mini Agent Python — 自定义异常类

定义项目特定的异常类型，增强错误语义，便于错误处理和日志追踪。

与 ``error_messages`` 的分工：
- 本模块：可 ``raise`` / ``except`` 的异常类型（可携带结构化字段）
- ``error_messages``：用户可见文案常量；异常消息应引用这些常量或格式化函数

异常分类：
- 安全相关：``SandboxViolationError``
- 配置相关：``FeishuConfigMissingError``
- 依赖相关：``LarkOapiMissingError``

工具层通常在边界将异常映射为 ``ToolResult``（见 ``feishu_utils``、``path_utils``）。
"""

from __future__ import annotations

from miniagent.types.error_messages import (
    DEPENDENCY_LARK_OAPI_MISSING,
    FEISHU_CONFIG_MISSING,
    format_sandbox_path_violation,
)


class SandboxViolationError(Exception):
    """路径超出沙箱范围异常。

    当尝试访问不在 ``allowed_dirs`` 白名单内的路径时抛出。
    由 ``miniagent.security.sandbox.resolve_sandbox_path`` 在路径校验失败时抛出。

    Attributes:
        path: 尝试访问的路径（用户原始输入或解析前路径）。
        allowed_dirs: 允许访问的目录列表。

    Example:
        >>> try:
        ...     raise SandboxViolationError("/etc/passwd", ["/workspace"])
        ... except SandboxViolationError as e:
        ...     assert e.path == "/etc/passwd"
        ...     assert "/workspace" in e.allowed_dirs
    """

    def __init__(self, path: str, allowed_dirs: list[str]) -> None:
        """初始化沙箱违规异常。

        Args:
            path: 尝试访问的路径。
            allowed_dirs: 允许的目录列表。
        """
        self.path = path
        self.allowed_dirs = list(allowed_dirs)
        super().__init__(format_sandbox_path_violation(path, allowed_dirs))


class FeishuConfigMissingError(Exception):
    """飞书配置缺失异常。

    当尝试使用飞书相关功能但未配置必要的环境变量时抛出。
    必要的环境变量：``FEISHU_APP_ID``、``FEISHU_APP_SECRET``。

    工具层通常通过 ``feishu_utils.check_feishu_config`` 映射为 ``ToolResult``，
    非工具代码可直接 ``raise`` 或 ``except`` 本异常。

    See also:
        ``miniagent.tools.feishu_utils.require_feishu_config``
    """

    def __init__(self) -> None:
        super().__init__(FEISHU_CONFIG_MISSING)


class LarkOapiMissingError(Exception):
    """lark-oapi 依赖缺失异常。

    当尝试使用飞书相关功能但未安装 lark-oapi SDK 时抛出。

    安装方式::

        pip install miniagent-python[feishu]
        # 或
        pip install lark-oapi

    工具层通常通过 ``feishu_utils.check_lark_oapi`` 映射为 ``ToolResult``。

    See also:
        ``miniagent.tools.feishu_utils.require_lark_oapi_installed``
    """

    def __init__(self) -> None:
        super().__init__(DEPENDENCY_LARK_OAPI_MISSING)


__all__ = [
    "SandboxViolationError",
    "FeishuConfigMissingError",
    "LarkOapiMissingError",
]
