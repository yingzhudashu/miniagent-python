"""llm_json 共享工具单元测试。

验证 JSON 解析、空响应回退和 Mock 客户端路径。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from miniagent.core.llm_json import llm_json


class TestLlmJson:
    """llm_json() 行为测试。"""

    def _make_mock_client(self, json_str: str) -> MagicMock:
        """构造返回指定 JSON 字符串的 Mock LLM client。"""
        mock_choice = MagicMock()
        mock_choice.message.content = json_str
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        async def fake_create(**kw):
            return mock_response

        mock_client = MagicMock()
        mock_client.chat.completions.create = fake_create
        return mock_client

    @pytest.mark.asyncio
    async def test_valid_json_parsed(self) -> None:
        """有效 JSON 应正确解析为字典。"""
        client = self._make_mock_client('{"key": "value", "num": 42}')
        result = await llm_json("test", "system", client=client)
        assert result == {"key": "value", "num": 42}

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty_dict(self) -> None:
        """无效 JSON 应回退为空字典。"""
        client = self._make_mock_client("not valid json {{{")
        result = await llm_json("test", "system", client=client)
        assert result == {}

    @pytest.mark.asyncio
    async def test_null_content_returns_empty_dict(self) -> None:
        """空 content 应回退为空字典。"""
        mock_choice = MagicMock()
        mock_choice.message.content = None
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        async def fake_create(**kw):
            return mock_response

        client = MagicMock()
        client.chat.completions.create = fake_create
        result = await llm_json("test", "system", client=client)
        assert result == {}

    @pytest.mark.asyncio
    async def test_nested_json_preserved(self) -> None:
        """嵌套 JSON 结构应完整保留。"""
        client = self._make_mock_client('{"outer": {"inner": [1, 2, 3]}, "flag": true}')
        result = await llm_json("test", "system", client=client)
        assert result["outer"]["inner"] == [1, 2, 3]
        assert result["flag"] is True

    @pytest.mark.asyncio
    async def test_empty_object_parsed(self) -> None:
        """空对象 {} 应解析为空字典。"""
        client = self._make_mock_client("{}")
        result = await llm_json("test", "system", client=client)
        assert result == {}

    @pytest.mark.asyncio
    async def test_chinese_content(self) -> None:
        """含中文内容的 JSON 应正确解析。"""
        client = self._make_mock_client('{"目标": "获取天气", "城市": ["北京", "上海"]}')
        result = await llm_json("测试", "系统", client=client)
        assert result["目标"] == "获取天气"
        assert "北京" in result["城市"]
