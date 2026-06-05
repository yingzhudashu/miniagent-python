"""Mini Agent Python — 工具系统类型测试

测试 miniagent/types/tool.py 中定义的核心类型：
- ToolPermission 权限级别
- Toolbox 工具箱
- ToolContext 工具执行上下文
- ToolResult 工具执行结果
- ToolDefinition 工具定义
- RegisteredTool 已注册工具
- TokenEstimate token估算
- ContextState 上下文状态
"""

from __future__ import annotations

from miniagent.types.tool import (
    ContextState,
    RegisteredTool,
    TokenEstimate,
    Toolbox,
    ToolContext,
    ToolDefinition,
    ToolPermission,
    ToolResult,
)


class TestToolPermission:
    """测试工具权限级别类型"""

    def test_permission_values(self) -> None:
        """权限级别应为允许的字符串值"""
        valid_permissions: list[ToolPermission] = [
            "sandbox",
            "allowlist",
            "require-confirm",
        ]
        for p in valid_permissions:
            assert isinstance(p, str)

    def test_permission_type_annotation(self) -> None:
        """权限级别类型检查"""
        # 类型注解应接受有效值
        perm: ToolPermission = "sandbox"
        assert perm == "sandbox"


class TestToolbox:
    """测试工具箱类型"""

    def test_toolbox_creation(self) -> None:
        """工具箱创建"""
        toolbox = Toolbox(
            id="filesystem",
            name="文件系统",
            description="文件读写操作",
            keywords=["file", "read", "write"],
        )
        assert toolbox.id == "filesystem"
        assert toolbox.name == "文件系统"
        assert toolbox.description == "文件读写操作"
        assert toolbox.keywords == ["file", "read", "write"]

    def test_toolbox_default_keywords(self) -> None:
        """工具箱默认关键词为空列表"""
        toolbox = Toolbox(
            id="test",
            name="Test",
            description="Test toolbox",
        )
        assert toolbox.keywords == []

    def test_toolbox_keywords_mutable(self) -> None:
        """工具箱关键词可修改"""
        toolbox = Toolbox(
            id="test",
            name="Test",
            description="Test",
            keywords=["a"],
        )
        toolbox.keywords.append("b")
        assert toolbox.keywords == ["a", "b"]


class TestToolContext:
    """测试工具执行上下文"""

    def test_tool_context_creation(self) -> None:
        """工具上下文创建"""
        ctx = ToolContext(
            cwd="/home/user",
            allowed_paths=["/home/user/docs"],
            permission="sandbox",
        )
        assert ctx.cwd == "/home/user"
        assert ctx.allowed_paths == ["/home/user/docs"]
        assert ctx.permission == "sandbox"

    def test_tool_context_defaults(self) -> None:
        """工具上下文默认值"""
        ctx = ToolContext(cwd="/tmp")
        assert ctx.allowed_paths == []
        assert ctx.permission == "sandbox"
        assert ctx.clawhub is None
        assert ctx.session_key is None
        assert ctx.cli_loop_state is None
        assert ctx.cli_dispatch_allow_mutations is True
        assert ctx.message_queue_abort_chat_id is None
        assert ctx.feishu_im_receive_id_type is None
        assert ctx.feishu_im_receive_id is None

    def test_tool_context_all_fields(self) -> None:
        """工具上下文所有字段"""
        ctx = ToolContext(
            cwd="/test",
            allowed_paths=["/a", "/b"],
            permission="allowlist",
            session_key="session-123",
            cli_dispatch_allow_mutations=False,
            message_queue_abort_chat_id="chat_x",
            feishu_im_receive_id_type="open_id",
            feishu_im_receive_id="user_y",
        )
        assert ctx.cwd == "/test"
        assert ctx.permission == "allowlist"
        assert ctx.session_key == "session-123"
        assert ctx.cli_dispatch_allow_mutations is False
        assert ctx.message_queue_abort_chat_id == "chat_x"
        assert ctx.feishu_im_receive_id_type == "open_id"
        assert ctx.feishu_im_receive_id == "user_y"


class TestToolResult:
    """测试工具执行结果"""

    def test_tool_result_success(self) -> None:
        """成功结果"""
        result = ToolResult(success=True, content="操作成功")
        assert result.success is True
        assert result.content == "操作成功"
        assert result.meta == {}

    def test_tool_result_failure(self) -> None:
        """失败结果"""
        result = ToolResult(success=False, content="操作失败: 文件不存在")
        assert result.success is False
        assert "文件不存在" in result.content

    def test_tool_result_with_meta(self) -> None:
        """带元数据的结果"""
        result = ToolResult(
            success=True,
            content="读取完成",
            meta={"bytes": 1024, "lines": 50},
        )
        assert result.meta["bytes"] == 1024
        assert result.meta["lines"] == 50

    def test_tool_result_meta_mutable(self) -> None:
        """元数据可修改"""
        result = ToolResult(success=True, content="ok")
        result.meta["key"] = "value"
        assert result.meta["key"] == "value"


class TestTokenEstimate:
    """测试 token 估算"""

    def test_token_estimate_creation(self) -> None:
        """token 估算创建"""
        estimate = TokenEstimate(tokens=100, char_length=500)
        assert estimate.tokens == 100
        assert estimate.char_length == 500

    def test_token_estimate_ratio(self) -> None:
        """token 与字符比例"""
        estimate = TokenEstimate(tokens=100, char_length=400)
        ratio = estimate.char_length / estimate.tokens
        assert ratio == 4.0


class TestContextState:
    """测试上下文状态"""

    def test_context_state_creation(self) -> None:
        """上下文状态创建"""
        state = ContextState(
            messages=[{"role": "user", "content": "hello"}],
            total_tokens=10,
            compressed=False,
        )
        assert len(state.messages) == 1
        assert state.total_tokens == 10
        assert state.compressed is False

    def test_context_state_messages_mutable(self) -> None:
        """消息列表可追加"""
        state = ContextState(
            messages=[{"role": "user", "content": "hello"}],
            total_tokens=10,
            compressed=False,
        )
        state.messages.append({"role": "assistant", "content": "hi"})
        assert len(state.messages) == 2


class TestToolDefinition:
    """测试工具定义"""

    def test_tool_definition_creation(self) -> None:
        """工具定义创建"""
        async def handler(args: dict, ctx: ToolContext) -> ToolResult:
            return ToolResult(success=True, content="done")

        tool = ToolDefinition(
            schema={"type": "function", "function": {"name": "test"}},
            handler=handler,
            permission="sandbox",
            help_text="测试工具",
        )
        assert tool.schema["type"] == "function"
        assert tool.permission == "sandbox"
        assert tool.help_text == "测试工具"
        assert tool.toolbox is None

    def test_tool_definition_with_toolbox(self) -> None:
        """带工具箱的工具定义"""
        async def handler(args: dict, ctx: ToolContext) -> ToolResult:
            return ToolResult(success=True, content="ok")

        tool = ToolDefinition(
            schema={"type": "function", "function": {"name": "read"}},
            handler=handler,
            permission="sandbox",
            help_text="读取文件",
            toolbox="filesystem",
        )
        assert tool.toolbox == "filesystem"


class TestRegisteredTool:
    """测试已注册工具"""

    def test_registered_tool_creation(self) -> None:
        """已注册工具创建"""
        async def handler(args: dict, ctx: ToolContext) -> ToolResult:
            return ToolResult(success=True, content="ok")

        tool = RegisteredTool(
            schema={"type": "function", "function": {"name": "test"}},
            handler=handler,
            permission="sandbox",
            help_text="测试工具",
            name="test_tool",
        )
        assert tool.name == "test_tool"

    def test_registered_tool_inherits_tool_definition(self) -> None:
        """已注册工具继承工具定义"""
        async def handler(args: dict, ctx: ToolContext) -> ToolResult:
            return ToolResult(success=True, content="ok")

        tool = RegisteredTool(
            schema={"type": "function", "function": {"name": "x"}},
            handler=handler,
            permission="allowlist",
            help_text="help",
            name="x",
            toolbox="core",
        )
        assert tool.toolbox == "core"
        assert tool.permission == "allowlist"