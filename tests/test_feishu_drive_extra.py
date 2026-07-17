"""drive_extra 权限与搜索。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("lark_oapi")


def test_search_docs_requires_user_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.assistant.feishu.drive_extra import SearchRequiresUserTokenError, search_docs
    from miniagent.ui.feishu.types import FeishuConfig

    monkeypatch.delenv("MINIAGENT_FEISHU_USER_ACCESS_TOKEN", raising=False)
    cfg = FeishuConfig(app_id="a", app_secret="b")
    with pytest.raises(SearchRequiresUserTokenError) as exc:
        search_docs(cfg, "q")
    payload = exc.value.to_payload()
    assert payload["requires_user_token"] is True


@pytest.mark.asyncio
async def test_feishu_doc_search_no_token_json(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.agent.types.tool import ToolContext
    from miniagent.assistant.tools.feishu_doc_tools import _feishu_doc

    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    monkeypatch.delenv("MINIAGENT_FEISHU_USER_ACCESS_TOKEN", raising=False)
    r = await _feishu_doc({"action": "search", "query": "test"}, ToolContext(cwd="/tmp"))
    assert r.success is False
    assert "requires_user_token" in r.content


def test_add_permission_mock() -> None:
    from miniagent.assistant.feishu.drive_extra import add_permission
    from miniagent.ui.feishu.types import FeishuConfig

    cfg = FeishuConfig(app_id="a", app_secret="b")
    mock_resp = MagicMock()
    mock_resp.success.return_value = True
    mock_resp.data = MagicMock(
        member=MagicMock(member_type="email", member_id="u@x.com", perm="view")
    )
    with patch("miniagent.assistant.feishu.drive_extra.build_client") as bc:
        bc.return_value.drive.v1.permission_member.create.return_value = mock_resp
        out = add_permission(cfg, "doc_tok", member_type="email", member_id="u@x.com")
    assert out["member_id"] == "u@x.com"
