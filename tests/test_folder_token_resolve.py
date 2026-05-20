"""``folder_token_resolve``：URL 提取、解析优先级、根目录回退（mock）。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from miniagent.feishu.folder_token_resolve import (
    extract_folder_token_from_url,
    folder_token_from_tool_arg,
    resolve_parent_folder_token,
    root_meta_fallback_enabled,
)
from miniagent.feishu.types import FeishuConfig


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://bytedance.feishu.cn/drive/folder/fldcnxxxxxxxxxxxx",
            "fldcnxxxxxxxxxxxx",
        ),
        (
            "https://example.larkoffice.com/folder/fldcnAbCdEf123",
            "fldcnAbCdEf123",
        ),
        (
            "https://x.feishu.cn/wiki/space/token?folder_token=fldcnFromQuery",
            "fldcnFromQuery",
        ),
    ],
)
def test_extract_folder_token_from_url(url: str, expected: str) -> None:
    assert extract_folder_token_from_url(url) == expected


def test_extract_folder_token_from_url_returns_none_for_docx() -> None:
    assert extract_folder_token_from_url("https://x.feishu.cn/docx/doccn123") is None


def test_extract_folder_token_from_url_fragment_path() -> None:
    u = "https://x.feishu.cn/drive/home#/folder/fldcnFragment"
    assert extract_folder_token_from_url(u) == "fldcnFragment"


def test_folder_token_from_tool_arg_plain_token() -> None:
    tok, err = folder_token_from_tool_arg("fldcnPlain")
    assert tok == "fldcnPlain" and err is None


def test_folder_token_from_tool_arg_bad_url() -> None:
    tok, err = folder_token_from_tool_arg("https://x.feishu.cn/docx/doccnNoFolder")
    assert tok == "" and err is not None


def test_resolve_parent_folder_token_arg_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_FEISHU_DOC_FOLDER_TOKEN", "fld_env")
    monkeypatch.setenv("FEISHU_DOC_FOLDER_FALLBACK_ROOT_META", "0")
    cfg = FeishuConfig(app_id="a", app_secret="b")
    tok, err = resolve_parent_folder_token("fld_arg", cfg=cfg)
    assert err is None and tok == "fld_arg"


def test_resolve_parent_folder_token_env_when_arg_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_FEISHU_DOC_FOLDER_TOKEN", "fld_env")
    monkeypatch.setenv("FEISHU_DOC_FOLDER_FALLBACK_ROOT_META", "0")
    cfg = FeishuConfig(app_id="a", app_secret="b")
    tok, err = resolve_parent_folder_token("", cfg=cfg)
    assert err is None and tok == "fld_env"


def test_resolve_parent_folder_token_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIAGENT_FEISHU_DOC_FOLDER_TOKEN", raising=False)
    monkeypatch.setenv("FEISHU_DOC_FOLDER_FALLBACK_ROOT_META", "0")
    cfg = FeishuConfig(app_id="a", app_secret="b")
    u = "https://t.feishu.cn/drive/folder/fldcnFromUrl"
    tok, err = resolve_parent_folder_token(u, cfg=cfg)
    assert err is None and tok == "fldcnFromUrl"


def test_resolve_parent_folder_token_root_meta_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MINIAGENT_FEISHU_DOC_FOLDER_TOKEN", raising=False)
    monkeypatch.delenv("FEISHU_DOC_FOLDER_FALLBACK_ROOT_META", raising=False)
    cfg = FeishuConfig(app_id="a", app_secret="b")
    with patch(
        "miniagent.feishu.drive_client.get_root_folder_meta",
        return_value="fld_root",
    ):
        tok, err = resolve_parent_folder_token("", cfg=cfg)
    assert err is None and tok == "fld_root"


def test_resolve_parent_folder_token_root_meta_cfg_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIAGENT_FEISHU_DOC_FOLDER_TOKEN", raising=False)
    monkeypatch.delenv("FEISHU_DOC_FOLDER_FALLBACK_ROOT_META", raising=False)
    tok, err = resolve_parent_folder_token("", cfg=None)
    assert tok is None and err is not None
    assert "FeishuConfig" in err


def test_root_meta_fallback_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEISHU_DOC_FOLDER_FALLBACK_ROOT_META", raising=False)
    assert root_meta_fallback_enabled() is True
    monkeypatch.setenv("FEISHU_DOC_FOLDER_FALLBACK_ROOT_META", "true")
    assert root_meta_fallback_enabled() is True
    monkeypatch.setenv("FEISHU_DOC_FOLDER_FALLBACK_ROOT_META", "0")
    assert root_meta_fallback_enabled() is False
