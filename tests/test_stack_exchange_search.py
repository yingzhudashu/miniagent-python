"""Contracts for the packaged Stack Exchange troubleshooting skill."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from miniagent.core.prompts.planner import PLAN_SYSTEM_PROMPT
from miniagent.skills.loader import load_skill_package
from miniagent.types.tool import ToolContext

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = (
    PROJECT_ROOT / "miniagent" / "skills" / "templates" / "builtin-stackexchange"
)
TOOLS_PATH = PACKAGE_DIR / "skills" / "stackexchange-tools" / "tools.py"

spec = importlib.util.spec_from_file_location("_builtin_stackexchange_tools", TOOLS_PATH)
assert spec is not None and spec.loader is not None
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


@pytest.fixture(autouse=True)
def _reset_runtime_state(monkeypatch: pytest.MonkeyPatch) -> None:
    tools._cache.clear()
    tools._backoff_until.clear()
    monkeypatch.delenv("STACK_EXCHANGE_KEY", raising=False)


def _ctx() -> ToolContext:
    return ToolContext(cwd=".", allowed_paths=["."], permission="sandbox")


def _question_payload(*, quota: int = 299) -> dict:
    return {
        "items": [
            {
                "question_id": 101,
                "title": "RuntimeError: event loop is closed",
                "link": "https://stackoverflow.com/questions/101/event-loop-closed",
                "body": "<p>Fails on Python 3.12</p><pre><code>RuntimeError: closed</code></pre>",
                "score": 7,
                "answer_count": 3,
                "creation_date": 1_700_000_000,
                "last_activity_date": 1_710_000_000,
                "accepted_answer_id": 201,
                "tags": ["python", "python-asyncio"],
                "owner": {"display_name": "Question Author"},
                "content_license": "CC BY-SA 4.0",
            }
        ],
        "quota_remaining": quota,
    }


def _answer_payload(*, quota: int = 298, backoff: int | None = None) -> dict:
    payload = {
        "items": [
            {
                "answer_id": 201,
                "question_id": 101,
                "is_accepted": True,
                "score": 5,
                "last_activity_date": 1_705_000_000,
                "body": "<p>Use <code>asyncio.run(main())</code>.</p>",
                "owner": {"display_name": "Accepted Author"},
                "content_license": "CC BY-SA 4.0",
            },
            {
                "answer_id": 202,
                "question_id": 101,
                "is_accepted": False,
                "score": 12,
                "last_activity_date": 1_715_000_000,
                "body": "<p>Upgrade the integration before changing loop policy.</p>",
                "owner": {"display_name": "Top Author"},
                "content_license": "CC BY-SA 4.0",
            },
        ],
        "quota_remaining": quota,
    }
    if backoff is not None:
        payload["backoff"] = backoff
    return payload


@pytest.mark.asyncio
async def test_search_without_key_returns_accepted_and_highest_voted_answers() -> None:
    client = SimpleNamespace(
        get=AsyncMock(side_effect=[_Response(_question_payload()), _Response(_answer_payload())])
    )
    with patch.object(tools, "get_shared_httpx_client", AsyncMock(return_value=client)):
        result = await tools._stack_exchange_search_handler(
            {
                "query": "python 3.12 event loop closed",
                "sites": ["stackoverflow"],
                "tags": ["python", "python-asyncio"],
                "maxResults": 3,
            },
            _ctx(),
        )

    assert result.success
    assert "Accepted answer: score=5" in result.content
    assert "Highest-voted answer: score=12" in result.content
    assert "Accepted Author" in result.content and "Top Author" in result.content
    assert "```" in result.content and "`asyncio.run(main())`" in result.content
    assert "https://stackoverflow.com/questions/101/event-loop-closed/201#answer-201" in result.content
    assert "CC BY-SA 4.0" in result.content
    assert result.meta["quota_remaining"] == 298
    assert result.meta["result_count"] == 1
    first_params = client.get.await_args_list[0].kwargs["params"]
    assert first_params["tagged"] == "python;python-asyncio"
    assert "key" not in first_params


@pytest.mark.asyncio
async def test_optional_key_is_sent_and_cache_avoids_repeat_calls(monkeypatch) -> None:
    monkeypatch.setenv("STACK_EXCHANGE_KEY", "stack-key")
    client = SimpleNamespace(
        get=AsyncMock(side_effect=[_Response(_question_payload()), _Response(_answer_payload())])
    )
    args = {"query": "event loop closed", "sites": ["stackoverflow"]}
    with patch.object(tools, "get_shared_httpx_client", AsyncMock(return_value=client)):
        first = await tools._stack_exchange_search_handler(args, _ctx())
        second = await tools._stack_exchange_search_handler(args, _ctx())

    assert first.success and second.success
    assert client.get.await_count == 2
    assert client.get.await_args_list[0].kwargs["params"]["key"] == "stack-key"
    assert first.meta["cache_hit"] is False
    assert second.meta["cache_hit"] is True


def test_query_sanitization_preserves_error_and_removes_private_data() -> None:
    query, changed = tools._sanitize_query(
        "RuntimeError token=secret-value user@example.com "
        "C:\\Users\\alice\\project\\app.py /home/alice/app.py "
        "https://build.internal/job/7 192.168.1.20 Python 3.12"
    )
    assert changed
    assert "RuntimeError" in query and "Python 3.12" in query
    assert "secret-value" not in query
    assert "user@example.com" not in query
    assert "alice" not in query
    assert "build.internal" not in query
    assert "192.168.1.20" not in query
    assert "[credential]" in query and "[local-path]" in query


@pytest.mark.asyncio
async def test_partial_site_failure_keeps_results_and_reports_failed_site() -> None:
    async def fake_search(site: str, *_args) -> dict:
        if site == "superuser":
            raise tools._StackExchangeError("temporary failure")
        return {
            "site": site,
            "questions": _question_payload()["items"],
            "answers": _answer_payload()["items"],
            "quota_remaining": 42,
        }

    with patch.object(tools, "_search_site", side_effect=fake_search):
        result = await tools._stack_exchange_search_handler(
            {"query": "driver crash", "sites": ["stackoverflow", "superuser"]}, _ctx()
        )

    assert result.success
    assert result.meta["failed_sites"] == ["superuser"]
    assert "Unavailable sites: superuser" in result.content
    assert "## stackoverflow" in result.content


@pytest.mark.asyncio
async def test_all_sites_failure_returns_actionable_error() -> None:
    with patch.object(
        tools,
        "_search_site",
        AsyncMock(side_effect=tools._StackExchangeError("offline")),
    ):
        result = await tools._stack_exchange_search_handler(
            {"query": "driver crash", "sites": ["superuser"]}, _ctx()
        )
    assert not result.success
    assert result.meta["failed_sites"] == ["superuser"]
    assert "external verification" in result.content


@pytest.mark.asyncio
async def test_empty_results_are_successful_and_quota_zero_is_exposed() -> None:
    client = SimpleNamespace(
        get=AsyncMock(return_value=_Response({"items": [], "quota_remaining": 0}))
    )
    with patch.object(tools, "get_shared_httpx_client", AsyncMock(return_value=client)):
        result = await tools._stack_exchange_search_handler(
            {"query": "unlikely exact error", "sites": ["stackoverflow"]}, _ctx()
        )
    assert result.success
    assert result.meta["result_count"] == 0
    assert result.meta["quota_remaining"] == 0
    assert "No matching questions" in result.content


@pytest.mark.asyncio
async def test_api_backoff_is_recorded_without_sleeping() -> None:
    client = SimpleNamespace(get=AsyncMock(return_value=_Response({"items": [], "backoff": 30})))
    with patch.object(tools, "get_shared_httpx_client", AsyncMock(return_value=client)):
        await tools._api_get("/search/advanced", params={}, method_key="search:test")
        with pytest.raises(tools._StackExchangeError, match="backoff active"):
            await tools._api_get("/search/advanced", params={}, method_key="search:test")
    assert client.get.await_count == 1


def test_site_and_tag_limits_and_planner_policy() -> None:
    assert tools._normalize_sites(["stackoverflow", "superuser", "bad site", "unix", "apple"]) == [
        "stackoverflow",
        "superuser",
        "unix",
    ]
    assert tools._normalize_tags(["python", "bad tag!", "python", "c++"]) == [
        "python",
        "badtag",
        "c++",
    ]
    assert "Stack Overflow" in PLAN_SYSTEM_PROMPT
    assert "普通概念问题不因此添加联网步骤" in PLAN_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_packaged_skill_loads_tool_and_system_prompt() -> None:
    package = await load_skill_package(str(PACKAGE_DIR))
    assert package is not None
    assert package.id == "builtin-stackexchange"
    assert len(package.skills) == 1
    skill = package.skills[0]
    assert "stack_exchange_search" in skill.tools
    assert skill.tools["stack_exchange_search"].toolbox == "web"
    assert skill.system_prompt and "superuser" in skill.system_prompt


def test_config_secret_bridge_sets_stack_exchange_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.infrastructure import env_loader

    monkeypatch.delenv("STACK_EXCHANGE_KEY", raising=False)
    with patch.object(
        env_loader,
        "get_config_section",
        return_value={"stack_exchange_key": "configured-key"},
    ):
        env_loader.load_secrets_from_config()
    assert os.environ["STACK_EXCHANGE_KEY"] == "configured-key"

