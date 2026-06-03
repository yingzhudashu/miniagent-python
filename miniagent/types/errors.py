"""Mini Agent Python — 自定义异常类

定义项目特定的异常类型，增强错误语义，便于错误处理和日志追踪。

异常分类：
- 安全相关：SandboxViolationError
- 配置相关：FeishuConfigMissingError
- 依赖相关：LarkOapiMissingError
"""

from __future__ import annotations


class SandboxViolationError(Exception):
    """路径超出沙箱范围异常

    当尝试访问不在 allowed_paths 白名单内的路径时抛出。

    Attributes:
        path: 尝试访问的路径
        allowed_dirs: 允许的目录列表
    """

    def __init__(self, path: str, allowed_dirs: list[str]) -> None:
        """初始化沙箱违规异常

        Args:
            path: 尝试访问的路径
            allowed_dirs: 允许的目录列表
        """
        self.path = path
        self.allowed_dirs = allowed_dirs
        super().__init__(
            f'路径 "{path}" 超出允许的范围: {", ".join(allowed_dirs)}'
        )


class FeishuConfigMissingError(Exception):
    """飞书配置缺失异常

    当尝试使用飞书相关工具但未配置必要的环境变量时抛出。

    必要的环境变量：
    - FEISHU_APP_ID
    - FEISHU_APP_SECRET
    """

    def __init__(self) -> None:
        """初始化飞书配置缺失异常"""
        super().__init__(
            "未配置飞书必要的环境变量：FEISHU_APP_ID 和 FEISHU_APP_SECRET"
        )


class LarkOapiMissingError(Exception):
    """lark-oapi 依赖缺失异常

    当尝试使用飞书相关功能但未安装 lark-oapi SDK 时抛出。

    安装方式：
    pip install miniagent-python[feishu]
    或
    pip install lark-oapi
    """

    def __init__(self) -> None:
        """初始化 lark-oapi 依赖缺失异常"""
        super().__init__(
            "请安装 lark-oapi SDK（pip install miniagent-python[feishu]）"
        )


__all__ = [
    "SandboxViolationError",
    "FeishuConfigMissingError",
    "LarkOapiMissingError",
]