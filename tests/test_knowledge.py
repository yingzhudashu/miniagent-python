"""Tests for knowledge base module."""

from __future__ import annotations

import os
import tempfile

import pytest

from miniagent.knowledge import (
    KnowledgeRegistry,
)
from miniagent.knowledge.base import KBConfig, KnowledgeBase, load_kb_config


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
        from miniagent.tools.knowledge_tools import knowledge_tools

        # Get the tool definition
        tool = knowledge_tools.get("search_knowledge")
        assert tool is not None
        assert tool.schema["function"]["name"] == "search_knowledge"

    @pytest.mark.asyncio
    async def test_kb_list_tool(self):
        """Test kb_list_tool."""
        from miniagent.tools.knowledge_tools import knowledge_tools

        tool = knowledge_tools.get("kb_list")
        assert tool is not None
        assert tool.schema["function"]["name"] == "kb_list"

    @pytest.mark.asyncio
    async def test_read_knowledge_file_tool(self):
        """Test read_knowledge_file_tool."""
        from miniagent.tools.knowledge_tools import knowledge_tools

        tool = knowledge_tools.get("read_knowledge_file")
        assert tool is not None
        assert tool.schema["function"]["name"] == "read_knowledge_file"