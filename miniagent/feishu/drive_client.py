"""飞书云盘：drive v1 列举、根文件夹元数据（HTTP）、及 ``lark-oapi`` 列举封装。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from miniagent.feishu.lark_response import format_lark_response_error
from miniagent.feishu.types import FeishuConfig

# 单次列举上限（与工具层一致）
LIST_FILE_PAGE_SIZE = 50

_TENANT_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_ROOT_FOLDER_META_URL = "https://open.feishu.cn/open-apis/drive/explorer/v2/root_folder/meta"


def _parse_feishu_json_code(raw: Any) -> int | None:
    """开放平台 JSON 体中的 ``code`` 字段；无法解析为整数时返回 ``None``。"""
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _http_post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, method="POST", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:500]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error: {e}") from e
    try:
        out: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"invalid JSON from Feishu POST (len={len(body)}): {body[:300]!r}…"
        ) from e
    return out


def _http_get_json(url: str, *, headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:500]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error: {e}") from e
    try:
        out: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"invalid JSON from Feishu GET (len={len(body)}): {body[:300]!r}…"
        ) from e
    return out


def _fetch_tenant_access_token(config: FeishuConfig) -> str:
    js = _http_post_json(
        _TENANT_TOKEN_URL,
        {"app_id": config.app_id, "app_secret": config.app_secret},
    )
    code = _parse_feishu_json_code(js.get("code"))
    if code is None or code != 0:
        raise RuntimeError(f"tenant_access_token: code={js.get('code')!r} msg={js.get('msg')}")
    tok = str(js.get("tenant_access_token") or "").strip()
    if not tok:
        raise RuntimeError("tenant_access_token: empty tenant_access_token in response")
    return tok


def get_root_folder_meta(config: FeishuConfig) -> str:
    """调用「获取根文件夹元数据」接口，返回根目录 ``folder_token``。

    需应用具备云盘相关权限（参见开放平台文档）；常用错误码 ``91204`` 表示无权限。

    Note:
        使用 ``open.feishu.cn`` 域名；国际版租户若不可用，请改用手动配置 ``folder_token``。
    """
    tenant = _fetch_tenant_access_token(config)
    js = _http_get_json(
        _ROOT_FOLDER_META_URL,
        headers={"Authorization": f"Bearer {tenant}"},
    )
    code = _parse_feishu_json_code(js.get("code"))
    if code is None or code != 0:
        raise RuntimeError(
            f"root_folder/meta failed: code={js.get('code')!r} msg={js.get('msg')} "
            f"(若 code=91204 多为无 drive 权限；请为应用开通云盘元数据/读写权限或关闭 FEISHU_DOC_FOLDER_FALLBACK_ROOT_META)"
        )
    data = js.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("root_folder/meta: missing data object")
    token = str(data.get("token") or "").strip()
    if not token:
        raise RuntimeError("root_folder/meta: empty token in response")
    return token


def list_folder_files_page(
    config: FeishuConfig,
    *,
    folder_token: str,
    page_token: str | None = None,
    page_size: int = LIST_FILE_PAGE_SIZE,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    """列举某文件夹下一页文件/子文件夹。

    Returns:
        ``(entries, next_page_token, has_more)``；每个 entry 含 ``name``、``token``、``type``（开放平台原始 type 字符串）。
    """
    import lark_oapi as lark
    from lark_oapi.api.drive.v1 import ListFileRequest

    client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
    b = ListFileRequest.builder().folder_token(folder_token).page_size(min(page_size, 200))
    if page_token:
        b = b.page_token(page_token)
    resp = client.drive.v1.file.list(b.build())
    if not resp.success() or not resp.data:
        raise RuntimeError(f"Feishu drive list_file failed: {format_lark_response_error(resp)}")
    files = getattr(resp.data, "files", None) or []
    out: list[dict[str, Any]] = []
    for f in files:
        out.append(
            {
                "name": getattr(f, "name", None) or "",
                "token": getattr(f, "token", None) or "",
                "type": getattr(f, "type", None) or "",
            }
        )
    next_tok = getattr(resp.data, "next_page_token", None)
    has_more = bool(getattr(resp.data, "has_more", False))
    return out, (str(next_tok) if next_tok else None), has_more
