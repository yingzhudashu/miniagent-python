"""ClawHub 客户端搜索、详情、安装和路径安全契约测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from miniagent.assistant.skills.clawhub_client import _ClawHubClientImpl


@pytest.mark.asyncio
async def test_search_primary_fallback_and_empty(monkeypatch) -> None:
    client = _ClawHubClientImpl("https://hub.test")
    responses = iter(
        [
            {"items": [{"slug": "a", "name": "A"}, "skip"]},
            {},
            {"results": [{"slug": "b", "name": "B"}]},
            {},
            {},
        ]
    )

    async def fetch(_url):
        return next(responses)

    monkeypatch.setattr(client, "_fetch_json", fetch)
    assert [item.slug for item in await client.search("query", 3)] == ["a"]
    assert [item.slug for item in await client.search("query", 3)] == ["b"]
    assert await client.search("query", 3) == []


@pytest.mark.asyncio
async def test_detail_invalid_payload_and_close_idempotent(monkeypatch) -> None:
    client = _ClawHubClientImpl("https://hub.test")

    async def invalid(_url):
        return []

    monkeypatch.setattr(client, "_fetch_json", invalid)
    detail = await client.get_detail("author/skill")
    assert detail.slug == "author/skill" and detail.name == ""

    class Pool:
        def __init__(self):
            self.closed = 0

        async def aclose(self):
            self.closed += 1

    pool = Pool()
    client._http_client = pool
    await client.close()
    await client.close()
    assert pool.closed == 1


@pytest.mark.asyncio
async def test_download_installs_files_and_metadata(tmp_path: Path, monkeypatch) -> None:
    client = _ClawHubClientImpl("https://hub.test")

    async def detail(_slug):
        from miniagent.agent.types.skill import ClawHubSkillDetail

        return ClawHubSkillDetail(
            slug="author/example",
            name="Example",
            description="desc",
            version="1.2.3",
            files=[{"path": "SKILL.md", "content": "# Skill"}, {"path": "tools/x.py", "content": "x=1"}],
        )

    monkeypatch.setattr(client, "get_detail", detail)
    result = await client.download("author/example", skills_root=str(tmp_path))
    target = tmp_path / "example"
    assert result["path"] == str(target)
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "# Skill"
    metadata = json.loads((target / ".clawhub.json").read_text(encoding="utf-8"))
    assert metadata["slug"] == "author/example" and metadata["version"] == "1.2.3"


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe", ["../escape", "/absolute", "nested/../../escape"])
async def test_download_rejects_unsafe_paths(tmp_path: Path, monkeypatch, unsafe: str) -> None:
    client = _ClawHubClientImpl("https://hub.test")

    async def detail(_slug):
        from miniagent.agent.types.skill import ClawHubSkillDetail

        return ClawHubSkillDetail(
            slug="example",
            name="Example",
            description="desc",
            version="1",
            files=[{"path": unsafe, "content": "bad"}],
        )

    monkeypatch.setattr(client, "get_detail", detail)
    with pytest.raises(RuntimeError, match="不安全|跳出"):
        await client.download("example", skills_root=str(tmp_path))


@pytest.mark.asyncio
async def test_download_endpoint_fallback_and_missing_files(tmp_path: Path, monkeypatch) -> None:
    client = _ClawHubClientImpl("https://hub.test")

    async def fetch(url):
        if "download" in url:
            return {"files": [{"path": "SKILL.md", "content": "fallback"}]}
        return {"slug": "example", "name": "Example", "description": "", "version": "1"}

    monkeypatch.setattr(client, "_fetch_json", fetch)
    result = await client.download("example", version="1", skills_root=str(tmp_path))
    assert Path(result["path"], "SKILL.md").exists()

    async def no_files(_url):
        return {}

    monkeypatch.setattr(client, "_fetch_json", no_files)
    with pytest.raises(RuntimeError, match="未返回"):
        await client.download("empty", skills_root=str(tmp_path))

