"""Tests for knowledge base module."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from miniagent.agent.types.tool import ToolContext
from miniagent.assistant.knowledge import (
    KnowledgeRegistry,
    retrieve_knowledge_context,
)
from miniagent.assistant.knowledge.base import (
    KBConfig,
    KnowledgeBase,
    load_kb_config,
    resolve_kb_file_path,
)
from miniagent.assistant.tools.knowledge_tools import (
    KNOWLEDGE_TOOLBOX,
    apply_knowledge_toolbox_policy,
    knowledge_tools,
)


class TestKnowledgeBase:
    """Test KnowledgeBase class."""

    def test_kb_config_defaults(self):
        """Test default KBConfig values."""
        config = KBConfig()
        assert config.name == "default"
        assert config.retriever == "keyword"
        assert config.max_chars == 8000
        assert config.top_k == 5
        assert "*.md" in config.file_patterns

    def test_load_kb_config_missing_file(self):
        """Test loading KB.yaml from non-existent path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "KB.yaml")
            config = load_kb_config(config_path)
            # Should use directory name (basename) as default
            assert config.name == os.path.basename(tmpdir)

    def test_knowledge_base_load_file(self):
        """Test loading a single file."""
        # Use delete=False and close before loading
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        try:
            f.write("# Test Document\n\nThis is a test about API design.")
            f.close()  # Close before loading to avoid permission error on Windows
            kb = KnowledgeBase(f.name)
            kb.load()
            assert len(kb._entries) == 1
            assert kb.name == os.path.basename(f.name)
        finally:
            os.unlink(f.name)

    def test_knowledge_base_search(self):
        """Test searching in knowledge base."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        try:
            f.write("# API Guide\n\nThis document describes the API endpoints.")
            f.close()  # Close before loading
            kb = KnowledgeBase(f.name)
            kb.load()
            result = kb.search("API")
            assert "API Guide" in result or "API" in result
        finally:
            os.unlink(f.name)

    def test_knowledge_base_search_shows_source_metadata(self):
        """Auto-ingested KB search results should expose the original source path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            files_dir = os.path.join(tmpdir, "files")
            os.makedirs(files_dir)
            with open(os.path.join(tmpdir, "KB.yaml"), "w", encoding="utf-8") as f:
                f.write("name: auto\nfile_patterns:\n  - '*.md'\n")
            with open(os.path.join(files_dir, "abc.md"), "w", encoding="utf-8") as f:
                f.write("Alpha source marker")
            metadata = {
                "/source/abc.md": {
                    "source_path": "/source/abc.md",
                    "file_path": "abc.md",
                    "source_hash": "abcdef1234567890",
                    "size": 19,
                    "ingested_at": 123.0,
                }
            }
            with open(os.path.join(tmpdir, "source-metadata.json"), "w", encoding="utf-8") as f:
                import json

                json.dump(metadata, f)

            kb = KnowledgeBase(tmpdir)
            result = kb.search("Alpha")

            assert "/source/abc.md" in result
            assert "abcdef123456" in result


class TestKnowledgeRegistry:
    """Test KnowledgeRegistry class."""

    def test_mount_unmount(self):
        """Test mounting and unmounting knowledge bases."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test file
            files_dir = os.path.join(tmpdir, "files")
            os.makedirs(files_dir)
            with open(os.path.join(files_dir, "test.md"), "w", encoding="utf-8") as f:
                f.write("# Test\n\nContent here.")

            # Use a fresh registry (not global singleton)
            registry = KnowledgeRegistry(state_dir=tmpdir)
            registry._mounted.clear()  # Clear any auto-mounted

            result = registry.mount(tmpdir, "test_kb")
            assert result["success"]
            assert "test_kb" in registry._mounted

            result = registry.unmount("test_kb")
            assert result["success"]
            assert "test_kb" not in registry._mounted

    def test_search_empty_registry(self):
        """Test searching with no mounted KBs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = KnowledgeRegistry(state_dir=tmpdir)
            registry._mounted.clear()
            result = registry.search("test query")
            assert result == ""

    def test_list_empty(self):
        """Test listing with no mounted KBs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = KnowledgeRegistry(state_dir=tmpdir)
            registry._mounted.clear()
            kb_list = registry.list()
            assert kb_list == []


class TestKnowledgeTools:
    """Test knowledge tools."""

    @pytest.mark.asyncio
    async def test_search_knowledge_tool(self):
        """Test search_knowledge_tool."""
        from miniagent.assistant.tools.knowledge_tools import knowledge_tools

        # Get the tool definition
        tool = knowledge_tools.get("search_knowledge")
        assert tool is not None
        assert tool.schema["function"]["name"] == "search_knowledge"

    @pytest.mark.asyncio
    async def test_kb_list_tool(self):
        """Test kb_list_tool."""
        from miniagent.assistant.tools.knowledge_tools import knowledge_tools

        tool = knowledge_tools.get("kb_list")
        assert tool is not None
        assert tool.schema["function"]["name"] == "kb_list"

    @pytest.mark.asyncio
    async def test_read_knowledge_file_tool(self):
        """Test read_knowledge_file_tool."""
        from miniagent.assistant.tools.knowledge_tools import knowledge_tools

        tool = knowledge_tools.get("read_knowledge_file")
        assert tool is not None
        assert tool.schema["function"]["name"] == "read_knowledge_file"


class TestKnowledgeBaseExtended:
    def test_scan_files_deduplicates_overlapping_patterns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "doc.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("overlap")
            kb = KnowledgeBase(tmpdir, config=KBConfig(file_patterns=["*.md", "*.*"]))
            kb.load()
            assert len(kb._entries) == 1

    def test_fulltext_retriever_matches_substrings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            files_dir = os.path.join(tmpdir, "files")
            os.makedirs(files_dir)
            with open(os.path.join(files_dir, "a.md"), "w", encoding="utf-8") as f:
                f.write("UniquePhraseOnlyHere")
            kb = KnowledgeBase(tmpdir, config=KBConfig(name="ft", retriever="fulltext"))
            result = kb.search("UniquePhraseOnlyHere")
            assert "UniquePhraseOnlyHere" in result

    def test_resolve_kb_file_path_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            files_dir = os.path.join(tmpdir, "files")
            os.makedirs(files_dir)
            safe = os.path.join(files_dir, "safe.md")
            with open(safe, "w", encoding="utf-8") as f:
                f.write("ok")
            resolved = resolve_kb_file_path(tmpdir, "safe.md")
            assert resolved == os.path.abspath(safe)
            assert resolve_kb_file_path(tmpdir, "../../../etc/passwd") is None


class TestKnowledgeRegistryExtended:
    def test_cross_kb_search_uses_global_top_k(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = KnowledgeRegistry(state_dir=tmpdir)
            registry._mounted.clear()

            for label, keyword in (("kb_a", "alpha"), ("kb_b", "beta")):
                kb_dir = os.path.join(tmpdir, label)
                files_dir = os.path.join(kb_dir, "files")
                os.makedirs(files_dir)
                with open(os.path.join(files_dir, "doc.md"), "w", encoding="utf-8") as f:
                    f.write(f"content about {keyword}")
                registry.mount(kb_dir, label)

            result = registry.search("alpha beta", top_k=1)
            assert result.count("###") == 1

    def test_mount_custom_name_persisted_in_registry_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            kb_root = os.path.join(tmpdir, "kb_root")
            os.makedirs(kb_root)
            kb_dir = os.path.join(kb_root, "docs")
            files_dir = os.path.join(kb_dir, "files")
            os.makedirs(files_dir)
            with open(os.path.join(files_dir, "x.md"), "w", encoding="utf-8") as f:
                f.write("hello")

            with patch("miniagent.assistant.knowledge.registry.get_config", return_value=kb_root):
                registry = KnowledgeRegistry(state_dir=tmpdir)
                registry._mounted.clear()
                registry.mount(kb_dir, "alias_name")

                registry_path = os.path.join(kb_root, "kb_registry.json")
                data = json.loads(open(registry_path, encoding="utf-8").read())
                assert data["mounted"][0]["name"] == "alias_name"

                registry2 = KnowledgeRegistry(state_dir=tmpdir)
                assert "alias_name" in registry2._mounted

    def test_refresh_auto_file_kb_reloads_existing_mount(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            kb_dir = os.path.join(tmpdir, "auto")
            files_dir = os.path.join(kb_dir, "files")
            os.makedirs(files_dir)
            doc = os.path.join(files_dir, "a.md")
            with open(doc, "w", encoding="utf-8") as f:
                f.write("version-one")

            registry = KnowledgeRegistry(state_dir=tmpdir)
            registry._mounted.clear()
            registry.mount(kb_dir, "_auto_file_analysis")

            with open(doc, "w", encoding="utf-8") as f:
                f.write("version-two keyword")

            result = registry.refresh_auto_file_kb(kb_dir, "_auto_file_analysis")
            assert result["success"]
            search = registry.search("version-two", kb_name="_auto_file_analysis")
            assert "version-two" in search


class TestRetrieveKnowledgeContext:
    def test_returns_empty_when_disabled(self):
        registry = MagicMock()
        with patch("miniagent.assistant.knowledge.get_config", return_value=False):
            assert retrieve_knowledge_context(registry, "query", phase="executor") == ""

    def test_returns_markdown_when_results_exist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = KnowledgeRegistry(state_dir=tmpdir)
            registry._mounted.clear()
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
                f.write("# Doc\n\nAPI endpoint guide")
                f.close()
                try:
                    registry.mount(f.name, "doc_kb")
                    with patch("miniagent.assistant.knowledge.get_config") as mock_cfg:
                        mock_cfg.side_effect = lambda key, default=None: {
                            "knowledge.executor_enabled": True,
                            "knowledge.executor_top_k": 3,
                            "knowledge.executor_max_chars": 4000,
                        }.get(key, default)
                        out = retrieve_knowledge_context(registry, "API", phase="executor")
                    assert "## 相关知识库摘要" in out
                    assert "API" in out
                finally:
                    os.unlink(f.name)


class TestKnowledgeToolHandlers:
    @pytest.mark.asyncio
    async def test_search_knowledge_handler_empty_query(self):
        handler = knowledge_tools["search_knowledge"].handler
        result = await handler(
            {}, ToolContext(cwd=os.getcwd(), knowledge_registry=MagicMock())
        )
        assert not result.success

    @pytest.mark.asyncio
    async def test_read_knowledge_file_handler_blocks_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            files_dir = os.path.join(tmpdir, "files")
            os.makedirs(files_dir)
            with open(os.path.join(files_dir, "inside.md"), "w", encoding="utf-8") as f:
                f.write("secret")

            registry = KnowledgeRegistry(state_dir=tmpdir)
            registry._mounted.clear()
            registry.mount(tmpdir, "kb")

            handler = knowledge_tools["read_knowledge_file"].handler
            ctx = ToolContext(cwd=tmpdir, knowledge_registry=registry)
            bad = await handler({"kb_name": "kb", "file_path": "../outside.md"}, ctx)
            good = await handler({"kb_name": "kb", "file_path": "inside.md"}, ctx)

            assert not bad.success
            assert good.success
            assert "secret" in good.content

    @pytest.mark.asyncio
    async def test_kb_list_handler_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = KnowledgeRegistry(state_dir=tmpdir)
            registry._mounted.clear()
            handler = knowledge_tools["kb_list"].handler
            result = await handler(
                {}, ToolContext(cwd=tmpdir, knowledge_registry=registry)
            )
            assert result.success
            assert "未挂载" in result.content


class TestKnowledgeToolboxPolicy:
    def test_apply_knowledge_toolbox_policy_respects_as_core(self):
        tool = knowledge_tools["search_knowledge"]
        with patch("miniagent.assistant.tools.knowledge_tools.get_config", return_value=True):
            assert apply_knowledge_toolbox_policy(tool).toolbox is None
        with patch("miniagent.assistant.tools.knowledge_tools.get_config", return_value=False):
            assert apply_knowledge_toolbox_policy(tool).toolbox == KNOWLEDGE_TOOLBOX
