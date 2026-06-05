"""Mini Agent Python — 记忆类型测试

测试 miniagent/types/memory.py 中定义的核心类型：
- MemoryEntry 记忆条目
- MemoryEntryInput 记忆条目输入
- FileMetadata 文件元数据
- SessionMemory 会话记忆
- SessionOptions 会话配置选项
- Session 会话
"""

from __future__ import annotations

from miniagent.types.memory import (
    FileMetadata,
    MemoryEntry,
    MemoryEntryInput,
    Session,
    SessionMemory,
    SessionOptions,
)


class TestMemoryEntry:
    """测试记忆条目"""

    def test_memory_entry_creation(self) -> None:
        """记忆条目创建"""
        entry = MemoryEntry(
            timestamp="2026-06-03T12:00:00Z",
            user_snippet="用户询问天气",
            summary="讨论了天气情况",
            facts=["用户在北京", "今天是晴天"],
        )
        assert entry.timestamp == "2026-06-03T12:00:00Z"
        assert entry.user_snippet == "用户询问天气"
        assert entry.summary == "讨论了天气情况"
        assert len(entry.facts) == 2

    def test_memory_entry_default_facts(self) -> None:
        """默认事实为空列表"""
        entry = MemoryEntry(
            timestamp="2026-06-03T12:00:00Z",
            user_snippet="test",
            summary="test summary",
        )
        assert entry.facts == []

    def test_memory_entry_facts_mutable(self) -> None:
        """事实列表可追加"""
        entry = MemoryEntry(
            timestamp="2026-06-03T12:00:00Z",
            user_snippet="test",
            summary="test",
            facts=["fact1"],
        )
        entry.facts.append("fact2")
        assert len(entry.facts) == 2


class TestMemoryEntryInput:
    """测试记忆条目输入"""

    def test_memory_entry_input_creation(self) -> None:
        """记忆条目输入创建"""
        entry = MemoryEntryInput(
            timestamp="2026-06-03T12:00:00Z",
            user_snippet="用户消息",
            summary="摘要",
            facts=["事实1", "事实2"],
        )
        assert entry.timestamp == "2026-06-03T12:00:00Z"
        assert entry.facts == ["事实1", "事实2"]

    def test_memory_entry_input_optional_facts(self) -> None:
        """事实可选"""
        entry = MemoryEntryInput(
            timestamp="2026-06-03T12:00:00Z",
            user_snippet="test",
            summary="test",
        )
        assert entry.facts is None

    def test_memory_entry_input_none_facts(self) -> None:
        """事实可以显式设为 None"""
        entry = MemoryEntryInput(
            timestamp="2026-06-03T12:00:00Z",
            user_snippet="test",
            summary="test",
            facts=None,
        )
        assert entry.facts is None


class TestFileMetadata:
    """测试文件元数据"""

    def test_file_metadata_creation(self) -> None:
        """文件元数据创建"""
        meta = FileMetadata(
            name="report.pdf",
            path="uploads/report.pdf",
            size=102400,
            mime_type="application/pdf",
            type="binary",
        )
        assert meta.name == "report.pdf"
        assert meta.path == "uploads/report.pdf"
        assert meta.size == 102400
        assert meta.mime_type == "application/pdf"
        assert meta.type == "binary"

    def test_file_metadata_defaults(self) -> None:
        """默认值"""
        meta = FileMetadata(
            name="test.txt",
            path="test.txt",
            size=100,
            mime_type="text/plain",
            type="text",
        )
        assert meta.description == ""
        assert meta.timestamp == ""
        assert meta.source == "cli"

    def test_file_metadata_all_fields(self) -> None:
        """所有字段"""
        meta = FileMetadata(
            name="image.png",
            path="images/image.png",
            size=50000,
            mime_type="image/png",
            type="image",
            description="产品图片",
            timestamp="2026-06-03T12:00:00Z",
            source="feishu",
        )
        assert meta.description == "产品图片"
        assert meta.source == "feishu"

    def test_file_metadata_types(self) -> None:
        """文件类型值"""
        valid_types = ["image", "text", "binary"]
        for t in valid_types:
            meta = FileMetadata(
                name="file",
                path="file",
                size=100,
                mime_type="application/octet-stream",
                type=t,
            )
            assert meta.type == t


class TestSessionMemory:
    """测试会话记忆"""

    def test_session_memory_creation(self) -> None:
        """会话记忆创建"""
        memory = SessionMemory(
            session_id="session-001",
            cumulative_summary="用户偏好分析",
            key_facts=["用户喜欢Python", "用户是开发者"],
        )
        assert memory.session_id == "session-001"
        assert memory.cumulative_summary == "用户偏好分析"
        assert len(memory.key_facts) == 2

    def test_session_memory_defaults(self) -> None:
        """默认值"""
        memory = SessionMemory(session_id="test")
        assert memory.cumulative_summary == ""
        assert memory.key_facts == []
        assert memory.entries == []
        assert memory.uploaded_files == []
        assert memory.total_turns == 0
        assert memory.first_seen == ""
        assert memory.last_active == ""
        assert memory.chat_id is None
        assert memory.sender_id is None

    def test_session_memory_entries(self) -> None:
        """记忆条目列表"""
        entry = MemoryEntry(
            timestamp="2026-06-03T12:00:00Z",
            user_snippet="test",
            summary="test",
        )
        memory = SessionMemory(
            session_id="test",
            entries=[entry],
        )
        assert len(memory.entries) == 1
        assert memory.entries[0].user_snippet == "test"

    def test_session_memory_uploaded_files(self) -> None:
        """上传文件列表"""
        file_meta = FileMetadata(
            name="doc.pdf",
            path="doc.pdf",
            size=1000,
            mime_type="application/pdf",
            type="binary",
        )
        memory = SessionMemory(
            session_id="test",
            uploaded_files=[file_meta],
        )
        assert len(memory.uploaded_files) == 1

    def test_session_memory_feishu_binding(self) -> None:
        """飞书绑定字段"""
        memory = SessionMemory(
            session_id="test",
            chat_id="oc_x",
            sender_id="ou_y",
        )
        assert memory.chat_id == "oc_x"
        assert memory.sender_id == "ou_y"


class TestSessionOptions:
    """测试会话配置选项"""

    def test_session_options_defaults(self) -> None:
        """默认值"""
        options = SessionOptions()
        assert options.title == ""
        assert options.description is None
        assert options.parent_session_id is None
        assert options.workspace_path is None
        assert options.allowed_tools is None
        assert options.toolboxes is None

    def test_session_options_creation(self) -> None:
        """创建带值的选项"""
        options = SessionOptions(
            title="新会话",
            description="测试会话",
            parent_session_id="parent-001",
            workspace_path="/tmp/test",
            allowed_tools=["read_file", "write_file"],
        )
        assert options.title == "新会话"
        assert options.parent_session_id == "parent-001"
        assert options.allowed_tools == ["read_file", "write_file"]


class TestSession:
    """测试会话"""

    def test_session_creation(self) -> None:
        """会话创建"""
        session = Session(
            id="session-001",
            description="测试会话",
            created_at="2026-06-03T12:00:00Z",
        )
        assert session.id == "session-001"
        assert session.description == "测试会话"
        assert session.created_at == "2026-06-03T12:00:00Z"

    def test_session_defaults(self) -> None:
        """默认值"""
        session = Session(id="test")
        assert session.description == ""
        assert session.created_at == ""
        assert session.last_active_at == ""
        assert session.turn_count == 0
        assert session.workspace_path is None
        assert session.config_overrides == {}
        assert session.destroyed is False
        assert session.conversation_history == []

    def test_session_files_path_property(self) -> None:
        """files_path 属性"""
        session = Session(
            id="test",
            workspace_path="/sessions/test/files",
        )
        assert session.files_path == "/sessions/test/files"

    def test_session_files_path_none(self) -> None:
        """workspace_path 为 None 时 files_path 也为 None"""
        session = Session(id="test")
        assert session.files_path is None

    def test_session_config_overrides(self) -> None:
        """配置覆盖"""
        session = Session(
            id="test",
            config_overrides={"model": "gpt-4", "max_tokens": 4000},
        )
        assert session.config_overrides["model"] == "gpt-4"

    def test_session_conversation_history(self) -> None:
        """对话历史"""
        session = Session(
            id="test",
            conversation_history=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
        )
        assert len(session.conversation_history) == 2

    def test_session_destroyed_flag(self) -> None:
        """销毁标志"""
        session = Session(id="test", destroyed=True)
        assert session.destroyed is True


class TestMemoryEntryToInputConversion:
    """测试记忆条目转换"""

    def test_memory_entry_to_dict(self) -> None:
        """记忆条目转字典"""
        entry = MemoryEntry(
            timestamp="2026-06-03T12:00:00Z",
            user_snippet="test",
            summary="summary",
            facts=["fact1"],
        )
        # dataclasses 可以转换为 dict
        from dataclasses import asdict
        d = asdict(entry)
        assert d["timestamp"] == "2026-06-03T12:00:00Z"
        assert d["facts"] == ["fact1"]

    def test_memory_entry_input_to_dict(self) -> None:
        """记忆条目输入转字典"""
        entry = MemoryEntryInput(
            timestamp="2026-06-03T12:00:00Z",
            user_snippet="test",
            summary="summary",
            facts=["fact1"],
        )
        from dataclasses import asdict
        d = asdict(entry)
        assert d["timestamp"] == "2026-06-03T12:00:00Z"
        assert d["facts"] == ["fact1"]