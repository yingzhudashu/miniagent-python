"""token_resolve URL 解析。"""

from miniagent.feishu.token_resolve import (
    extract_bitable_app_token,
    extract_doc_token,
    extract_table_id,
)


def test_extract_doc_token_from_url() -> None:
    assert extract_doc_token("https://x.feishu.cn/docx/doxcnABC") == "doxcnABC"


def test_extract_bitable_app_and_table() -> None:
    url = "https://x.feishu.cn/base/appXYZ?table=tbl123"
    assert extract_bitable_app_token(url) == "appXYZ"
    assert extract_table_id(None, url_hint=url) == "tbl123"
