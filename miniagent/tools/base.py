"""Mini Agent Python — 工具构建基础设施

提供 ToolBuilder 类简化 ToolDefinition 创建，减少重复代码。

使用示例：

    from miniagent.tools.base import tool

    filesystem_tools = {
        "read_file": tool("read_file", "读取文件内容")
            .param("path", "string", "文件路径")
            .optional("offset", "number", "起始行号")
            .sandbox()
            .toolbox("file_read")
            .handler(_read_file_handler)
            .build(),
    }

迁移说明：
- 原定义模式：_xxx_schema (15行) + _xxx_handler + ToolDefinition
- 新定义模式：tool() + .param() + .build() (约5行)
- 代码量减少约 67%
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from miniagent.types.tool import ToolContext, ToolResult

from miniagent.types.tool import ToolDefinition

# 工具处理器签名
ToolHandler = Callable[[dict[str, Any], "ToolContext"], "ToolResult"]


def build_schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    """构建 OpenAI 兼容的 tool schema。

    Args:
        name: 工具名称
        description: 工具描述
        properties: 参数属性字典
        required: 必需参数列表

    Returns:
        OpenAI tool schema 格式的字典
    """
    params: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        params["required"] = required
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": params,
        }
    }


class ToolBuilder:
    """工具构建器：简化 ToolDefinition 创建。

    使用链式调用风格，每个方法返回 self 以支持连续配置。

    Example:
        >>> tool("read_file", "读取文件内容")
        ...     .param("path", "string", "文件路径")
        ...     .optional("limit", "number", "最大读取行数")
        ...     .sandbox()
        ...     .toolbox("file_read")
        ...     .help("读取文件内容")
        ...     .handler(_read_file_handler)
        ...     .build()
    """

    def __init__(self, name: str, description: str):
        """初始化工具构建器。

        Args:
            name: 工具名称（如 "read_file"）
            description: 工具描述（用于 LLM 理解工具用途）
        """
        self._name = name
        self._description = description
        self._properties: dict[str, Any] = {}
        self._required: list[str] = []
        self._permission: str = "sandbox"
        self._toolbox: str | None = None
        self._help_text: str = description
        self._handler: ToolHandler | None = None

    def param(self, name: str, type: str, desc: str, required: bool = True) -> ToolBuilder:
        """添加参数。

        Args:
            name: 参数名称
            type: 参数类型（"string" | "number" | "integer" | "boolean" | "array" | "object"）
            desc: 参数描述
            required: 是否必需（默认 True）

        Returns:
            self（支持链式调用）
        """
        self._properties[name] = {"type": type, "description": desc}
        if required:
            self._required.append(name)
        return self

    def optional(self, name: str, type: str, desc: str) -> ToolBuilder:
        """添加可选参数（required=False 的快捷方式）。

        Args:
            name: 参数名称
            type: 参数类型
            desc: 参数描述

        Returns:
            self（支持链式调用）
        """
        return self.param(name, type, desc, required=False)

    def enum_param(
        self,
        name: str,
        desc: str,
        values: list[str],
        required: bool = True
    ) -> ToolBuilder:
        """添加枚举参数。

        Args:
            name: 参数名称
            desc: 参数描述
            values: 枚举值列表
            required: 是否必需

        Returns:
            self（支持链式调用）
        """
        self._properties[name] = {
            "type": "string",
            "enum": values,
            "description": desc,
        }
        if required:
            self._required.append(name)
        return self

    def array_param(
        self,
        name: str,
        desc: str,
        item_type: str = "string",
        required: bool = True
    ) -> ToolBuilder:
        """添加数组参数。

        Args:
            name: 参数名称
            desc: 参数描述
            item_type: 数组元素类型（默认 "string"）
            required: 是否必需

        Returns:
            self（支持链式调用）
        """
        self._properties[name] = {
            "type": "array",
            "items": {"type": item_type},
            "description": desc,
        }
        if required:
            self._required.append(name)
        return self

    def object_param(
        self,
        name: str,
        desc: str,
        properties: dict[str, Any] | None = None,
        required: bool = True
    ) -> ToolBuilder:
        """添加对象参数。

        Args:
            name: 参数名称
            desc: 参数描述
            properties: 对象属性（可选）
            required: 是否必需

        Returns:
            self（支持链式调用）
        """
        param_def: dict[str, Any] = {"type": "object", "description": desc}
        if properties:
            param_def["properties"] = properties
        self._properties[name] = param_def
        if required:
            self._required.append(name)
        return self

    def any_param(self, name: str, desc: str, required: bool = True) -> ToolBuilder:
        """添加任意类型参数（不指定 type，用于 JSON 字符串等）。

        Args:
            name: 参数名称
            desc: 参数描述
            required: 是否必需

        Returns:
            self（支持链式调用）
        """
        self._properties[name] = {"description": desc}
        if required:
            self._required.append(name)
        return self

    def sandbox(self) -> ToolBuilder:
        """设置为 sandbox 权限（默认，ToolDefinition.permission 元数据）。

        标记为路径/文件类工具；运行时沙箱仍由 ``ToolContext.permission`` 与
        ``resolve_path_for_tool`` 控制。

        Returns:
            self（支持链式调用）
        """
        self._permission = "sandbox"
        return self

    def allowlist(self) -> ToolBuilder:
        """设置为 allowlist 权限（ToolDefinition.permission 元数据）。

        标记为不依赖路径沙箱的外部 API / 只读工具；**不会**自动改写
        ``ToolContext.permission`` 或 ``exec_command`` 的命令 allowlist 行为。

        Returns:
            self（支持链式调用）
        """
        self._permission = "allowlist"
        return self

    def require_confirm(self) -> ToolBuilder:
        """设置为 require-confirm 权限。

        executor 在调用 handler 前经 ``ConfirmationChannel`` 等待用户
        ``/confirm``（``AgentConfig.auto_execute_confirmed=True`` 时可跳过）。

        Returns:
            self（支持链式调用）
        """
        self._permission = "require-confirm"
        return self

    def toolbox(self, toolbox_id: str) -> ToolBuilder:
        """设置工具箱 ID。

        Args:
            toolbox_id: 工具箱 ID（如 "file_read", "exec"）

        Returns:
            self（支持链式调用）
        """
        self._toolbox = toolbox_id
        return self

    def core(self) -> ToolBuilder:
        """设置为核心工具箱（toolbox=None，始终可用）。

        Returns:
            self（支持链式调用）
        """
        self._toolbox = None
        return self

    def help(self, text: str) -> ToolBuilder:
        """设置帮助文本。

        Args:
            text: 帮助文本

        Returns:
            self（支持链式调用）
        """
        self._help_text = text
        return self

    def handler(self, fn: ToolHandler) -> ToolBuilder:
        """设置工具处理器函数。

        Args:
            fn: 异步处理函数，签名：async def fn(args, ctx) -> ToolResult

        Returns:
            self（支持链式调用）
        """
        self._handler = fn
        return self

    def build(self) -> ToolDefinition:
        """构建 ToolDefinition 对象。

        Returns:
            完整的 ToolDefinition 对象

        Raises:
            ValueError: 如果未设置 handler
        """
        if self._handler is None:
            raise ValueError(f"Tool '{self._name}' missing handler - call .handler(fn) before .build()")

        schema = build_schema(
            self._name,
            self._description,
            self._properties,
            self._required if self._required else None,
        )

        return ToolDefinition(
            schema=schema,
            handler=self._handler,
            permission=self._permission,
            help_text=self._help_text,
            toolbox=self._toolbox,
        )


def tool(name: str, description: str) -> ToolBuilder:
    """创建工具构建器的快捷函数。

    这是 ToolBuilder(name, description) 的快捷方式。

    Args:
        name: 工具名称
        description: 工具描述

    Returns:
        ToolBuilder 对象

    Example:
        >>> my_tool = tool("my_tool", "我的工具描述")
        ...     .param("input", "string", "输入内容")
        ...     .sandbox()
        ...     .handler(_my_handler)
        ...     .build()
    """
    return ToolBuilder(name, description)


__all__ = [
    "ToolBuilder",
    "tool",
    "build_schema",
    "ToolHandler",
]