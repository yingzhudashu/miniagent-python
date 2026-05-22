"""Tests for skills tools ClawHub injection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.tools import skills as skills_module
from miniagent.types.tool import ToolContext


@pytest.mark.asyncio
async def test_search_skills_clawhub_uses_injected_client() -> None:
    fake = MagicMock()
    fake.search = AsyncMock(
        return_value=[{"slug": "x", "name": "X", "description": "d", "stars": 1, "downloads": 2}]
    )
    ctx = ToolContext(cwd=".", allowed_paths=["."], permission="allowlist", clawhub=fake)

    with patch("miniagent.skills.clawhub_client.create_clawhub_client") as mock_create:
        result = await skills_module._search_handler(
            {"query": "q", "source": "clawhub", "limit": 5},
            ctx,
        )
        mock_create.assert_not_called()

    fake.search.assert_awaited_once()
    assert result.success is True
    assert "X" in result.content
