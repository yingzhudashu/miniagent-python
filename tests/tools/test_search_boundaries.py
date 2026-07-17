"""Focused regressions migrated from test_type_boundary_regressions.py."""

from __future__ import annotations

import pytest

from miniagent.assistant.feishu.drive_extra import SearchApiError, search_docs
from miniagent.ui.feishu.types import FeishuConfig


def test_search_docs_preserves_numeric_api_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.assistant.feishu.drive_client as drive_client

    monkeypatch.setattr(
        drive_client,
        "_http_request",
        lambda *_args, **_kwargs: {"code": "123", "msg": "denied"},
    )
    with pytest.raises(SearchApiError) as caught:
        search_docs(FeishuConfig("id", "secret"), "query", user_token="token")
    assert caught.value.code == 123
