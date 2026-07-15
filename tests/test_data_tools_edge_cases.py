"""Tests for miniagent/tools/data_tools.py edge cases."""

import json
import os
import tempfile

import pytest

from miniagent.agent.types.tool import ToolContext
from miniagent.assistant.tools.data_tools import (
    _json_read_handler,
    _json_write_handler,
    _read_csv_handler,
    _write_csv_handler,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for file tests."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def ctx(temp_dir):
    """Create a tool context with temp directory as allowed path."""
    return ToolContext(cwd=temp_dir, allowed_paths=[temp_dir])


class TestReadCSVEdgeCases:
    """Edge case tests for read_csv."""

    @pytest.mark.asyncio
    async def test_file_not_found(self, ctx):
        """Non-existent file returns error."""
        result = await _read_csv_handler({"path": "nonexistent.csv"}, ctx)
        assert result.success is False
        assert "文件不存在" in result.content

    @pytest.mark.asyncio
    async def test_empty_file(self, ctx, temp_dir):
        """Empty CSV file handled."""
        path = os.path.join(temp_dir, "empty.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
        result = await _read_csv_handler({"path": path}, ctx)
        assert result.success is True
        assert "空文件" in result.content

    @pytest.mark.asyncio
    async def test_tsv_auto_detection(self, ctx, temp_dir):
        """TSV file auto-detected by tab count."""
        path = os.path.join(temp_dir, "data.tsv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("name\tvalue\na\t1\nb\t2\n")
        result = await _read_csv_handler({"path": path}, ctx)
        assert result.success is True
        # Should detect tab delimiter

    @pytest.mark.asyncio
    async def test_csv_auto_detection(self, ctx, temp_dir):
        """CSV file auto-detected by comma count."""
        path = os.path.join(temp_dir, "data.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("name,value\na,1\nb,2\n")
        result = await _read_csv_handler({"path": path}, ctx)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_max_rows_limit(self, ctx, temp_dir):
        """maxRows limits returned rows."""
        path = os.path.join(temp_dir, "large.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("id\n")
            for i in range(200):
                f.write(f"{i}\n")
        result = await _read_csv_handler({"path": path, "maxRows": 10}, ctx)
        assert result.success is True
        # Should only have header + 10 rows
        lines = result.content.strip().split("\n")
        assert len(lines) == 11

    @pytest.mark.asyncio
    async def test_encoding_utf8(self, ctx, temp_dir):
        """UTF-8 encoding handled."""
        path = os.path.join(temp_dir, "utf8.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("name\n中文测试\n")
        result = await _read_csv_handler({"path": path}, ctx)
        assert result.success is True
        assert "中文测试" in result.content

    @pytest.mark.asyncio
    async def test_encoding_gbk(self, ctx, temp_dir):
        """GBK encoding handled."""
        path = os.path.join(temp_dir, "gbk.csv")
        with open(path, "w", encoding="gbk") as f:
            f.write("name\n中文\n")
        result = await _read_csv_handler({"path": path, "encoding": "gbk"}, ctx)
        assert result.success is True
        assert "中文" in result.content

    @pytest.mark.asyncio
    async def test_custom_delimiter(self, ctx, temp_dir):
        """Custom delimiter works."""
        path = os.path.join(temp_dir, "pipe.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("name|value\na|1\n")
        result = await _read_csv_handler({"path": path, "delimiter": "|"}, ctx)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_no_header(self, ctx, temp_dir):
        """File without header row."""
        path = os.path.join(temp_dir, "noheader.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("a,1\nb,2\n")
        result = await _read_csv_handler({"path": path}, ctx)
        # csv.DictReader treats first row as header
        assert result.success is True


class TestWriteCSVEdgeCases:
    """Edge case tests for write_csv."""

    @pytest.mark.asyncio
    async def test_invalid_json(self, ctx):
        """Invalid JSON data returns error."""
        result = await _write_csv_handler({"path": "out.csv", "data": "not json"}, ctx)
        assert result.success is False
        assert "不是有效 JSON" in result.content

    @pytest.mark.asyncio
    async def test_empty_array(self, ctx):
        """Empty array returns error."""
        result = await _write_csv_handler({"path": "out.csv", "data": "[]"}, ctx)
        assert result.success is False
        assert "非空数组" in result.content

    @pytest.mark.asyncio
    async def test_object_array(self, ctx, temp_dir):
        """Object array written correctly."""
        path = os.path.join(temp_dir, "obj.csv")
        data = json.dumps([{"name": "a", "value": 1}, {"name": "b", "value": 2}])
        result = await _write_csv_handler({"path": path, "data": data}, ctx)
        assert result.success is True
        assert "已写入 2 行" in result.content
        # Verify file content
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "name,value" in content

    @pytest.mark.asyncio
    async def test_list_array(self, ctx, temp_dir):
        """List array written correctly."""
        path = os.path.join(temp_dir, "list.csv")
        data = json.dumps([["a", 1], ["b", 2]])
        result = await _write_csv_handler({"path": path, "data": data}, ctx)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_custom_delimiter(self, ctx, temp_dir):
        """Custom delimiter in output."""
        path = os.path.join(temp_dir, "pipe.csv")
        data = json.dumps([{"a": 1, "b": 2}])
        result = await _write_csv_handler({"path": path, "data": data, "delimiter": "|"}, ctx)
        assert result.success is True
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "|" in content

    @pytest.mark.asyncio
    async def test_nested_object(self, ctx, temp_dir):
        """Nested objects flattened as string."""
        path = os.path.join(temp_dir, "nested.csv")
        data = json.dumps([{"name": "a", "nested": {"x": 1}}])
        result = await _write_csv_handler({"path": path, "data": data}, ctx)
        # Should still write, nested becomes dict repr
        assert result.success is True


class TestJsonReadEdgeCases:
    """Edge case tests for json_read."""

    @pytest.mark.asyncio
    async def test_file_not_found(self, ctx):
        """Non-existent file returns error."""
        result = await _json_read_handler({"path": "nonexistent.json"}, ctx)
        assert result.success is False
        assert "文件不存在" in result.content

    @pytest.mark.asyncio
    async def test_jsonl_file(self, ctx, temp_dir):
        """JSONL file parsed as array."""
        path = os.path.join(temp_dir, "data.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"name": "a"}\n')
            f.write('{"name": "b"}\n')
        result = await _json_read_handler({"path": path}, ctx)
        assert result.success is True
        # Should be formatted as JSON array
        parsed = json.loads(result.content)
        assert isinstance(parsed, list)
        assert len(parsed) == 2

    @pytest.mark.asyncio
    async def test_json_file(self, ctx, temp_dir):
        """Regular JSON file parsed."""
        path = os.path.join(temp_dir, "data.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"key": "value"}, f)
        result = await _json_read_handler({"path": path}, ctx)
        assert result.success is True
        assert "key" in result.content

    @pytest.mark.asyncio
    async def test_invalid_json(self, ctx, temp_dir):
        """Invalid JSON returns error."""
        path = os.path.join(temp_dir, "invalid.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("not valid json {{{")
        result = await _json_read_handler({"path": path}, ctx)
        assert result.success is False
        assert "JSON 解析失败" in result.content

    @pytest.mark.asyncio
    async def test_max_chars_truncation(self, ctx, temp_dir):
        """maxChars truncates output."""
        path = os.path.join(temp_dir, "large.json")
        data = {"content": "x" * 100000}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        result = await _json_read_handler({"path": path, "maxChars": 1000}, ctx)
        assert result.success is True
        assert "已截断" in result.content


class TestJsonWriteEdgeCases:
    """Edge case tests for json_write."""

    @pytest.mark.asyncio
    async def test_invalid_json(self, ctx):
        """Invalid JSON data returns error."""
        result = await _json_write_handler({"path": "out.json", "data": "not json"}, ctx)
        assert result.success is False
        assert "不是有效 JSON" in result.content

    @pytest.mark.asyncio
    async def test_pretty_output(self, ctx, temp_dir):
        """Pretty output has indentation."""
        path = os.path.join(temp_dir, "pretty.json")
        data = json.dumps({"key": "value"})
        result = await _json_write_handler({"path": path, "data": data, "pretty": True}, ctx)
        assert result.success is True
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "\n" in content  # Pretty format has newlines

    @pytest.mark.asyncio
    async def test_compact_output(self, ctx, temp_dir):
        """Compact output has no indentation."""
        path = os.path.join(temp_dir, "compact.json")
        data = json.dumps({"key": "value"})
        result = await _json_write_handler({"path": path, "data": data, "pretty": False}, ctx)
        assert result.success is True
        with open(path, encoding="utf-8") as f:
            content = f.read()
        # Compact format: separators without spaces
        assert content == '{"key": "value"}'

    @pytest.mark.asyncio
    async def test_unicode_preserved(self, ctx, temp_dir):
        """Unicode characters preserved."""
        path = os.path.join(temp_dir, "unicode.json")
        data = json.dumps({"name": "中文测试"})
        result = await _json_write_handler({"path": path, "data": data}, ctx)
        assert result.success is True
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "中文测试" in content

    @pytest.mark.asyncio
    async def test_nested_structure(self, ctx, temp_dir):
        """Nested structure written."""
        path = os.path.join(temp_dir, "nested.json")
        data = json.dumps({"level1": {"level2": {"level3": "value"}}})
        result = await _json_write_handler({"path": path, "data": data}, ctx)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_array_data(self, ctx, temp_dir):
        """Array data written."""
        path = os.path.join(temp_dir, "array.json")
        data = json.dumps([1, 2, 3, 4, 5])
        result = await _json_write_handler({"path": path, "data": data}, ctx)
        assert result.success is True