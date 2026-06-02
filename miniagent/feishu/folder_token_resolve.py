"""云盘父目录 ``folder_token`` 解析：工具参数（含飞书云盘链接）、JSON配置、可选根目录 API。"""

from __future__ import annotations

import re

from miniagent.feishu.types import FeishuConfig
from miniagent.infrastructure.env_parse import env_flag
from miniagent.infrastructure.json_config import get_config

# 飞书云盘文件夹路径常见形态：.../folder/<token>、.../drive/folder/<token>、...#/folder/<token>
_FOLDER_IN_PATH = re.compile(
    r"(?:^|[?#/])(?:/)?(?:drive/)?folder/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_FOLDER_TOKEN_QUERY = re.compile(r"[\?&]folder_token=([A-Za-z0-9_-]+)", re.IGNORECASE)


def extract_folder_token_from_url(s: str) -> str | None:
    """从飞书/Lark 云盘文件夹分享 URL 中提取 ``folder_token``；无法识别时返回 ``None``。"""
    normalized = (s or "").strip().replace("\\", "/")
    if not normalized:
        return None
    m = _FOLDER_TOKEN_QUERY.search(normalized)
    if m:
        return m.group(1)
    m = _FOLDER_IN_PATH.search(normalized)
    if m:
        return m.group(1)
    return None


def _looks_like_url_or_drive_link(s: str) -> bool:
    """判断字符串是否为 URL 或飞书/Lark 云盘链接格式。"""
    sl = s.lower()
    if s.strip().startswith("http://") or s.strip().startswith("https://"):
        return True
    if "feishu.cn" in sl or "feishu.com" in sl:
        return True
    if "larksuite.com" in sl or "larkoffice.com" in sl or "larkoffice.cn" in sl:
        return True
    if "/folder/" in sl:
        return True
    return False


def folder_token_from_tool_arg(raw: str | None) -> tuple[str, str | None]:
    """解析工具传入的 ``folder_token`` 参数。

    Returns:
        ``(token_or_empty, error_message)``。若入参像 URL 但解析失败，则 ``error_message`` 非空。
    """
    if raw is None:
        return "", None
    s = str(raw).strip()
    if not s:
        return "", None
    if _looks_like_url_or_drive_link(s):
        tok = extract_folder_token_from_url(s)
        if tok:
            return tok, None
        return (
            "",
            "未能从链接中解析出 folder_token；请使用云盘文件夹分享链接，或直接传入文件夹 token。",
        )
    return s, None


def default_doc_folder_token_from_env() -> str:
    """从JSON配置读取默认父目录folder_token。"""
    token = get_config("feishu.doc.folder_token", None)
    if token:
        return str(token)
    return ""


def root_meta_fallback_enabled() -> bool:
    """是否启用「根文件夹元数据」API 作为最后回退（默认开启）。"""
    return env_flag("FEISHU_DOC_FOLDER_FALLBACK_ROOT_META", default=True)


def format_missing_folder_token_message(
    *, tried: list[str], root_meta_error: str | None = None
) -> str:
    """统一缺省目录时的工具失败文案。"""
    tried_s = "、".join(tried) if tried else "（无）"
    lines = [
        "⚠️ 需要云盘父目录 folder_token。",
        f"已尝试：{tried_s}。",
        "请任选其一：在工具参数中传入文件夹 token 或飞书云盘文件夹完整链接；",
        "或配置环境变量 MINIAGENT_FEISHU_DOC_FOLDER_TOKEN；",
        "或设置 FEISHU_DOC_FOLDER_FALLBACK_ROOT_META=1 并确保应用具备云盘根元数据权限（见 docs/FEISHU.md）。",
    ]
    if root_meta_error:
        lines.append(f"根目录 API 回退失败：{root_meta_error}")
    return "\n".join(lines)


def resolve_parent_folder_token(
    folder_arg: str | None, *, cfg: FeishuConfig | None
) -> tuple[str | None, str | None]:
    """解析创建/列举云盘目录所用的父文件夹 token（同步版本）。

    顺序：工具参数（支持 URL）→ 环境默认 →（可选）根文件夹元数据 API。

    Returns:
        ``(token, error_message)``；成功时 ``error_message`` 为 ``None``。
    """
    tried: list[str] = []
    raw = (folder_arg or "").strip() if folder_arg is not None else ""
    token, url_err = folder_token_from_tool_arg(raw if raw else None)
    if url_err:
        return None, f"⚠️ {url_err}"
    if token:
        return token, None

    env_tok = default_doc_folder_token_from_env()
    if env_tok:
        return env_tok, None

    if root_meta_fallback_enabled() and cfg is not None:
        tried.append("FEISHU_DOC_FOLDER_FALLBACK_ROOT_META（drive/explorer/v2/root_folder/meta）")
        try:
            from miniagent.feishu.drive_client import get_root_folder_meta

            return get_root_folder_meta(cfg), None
        except Exception as e:
            return None, format_missing_folder_token_message(tried=tried, root_meta_error=str(e))

    if root_meta_fallback_enabled() and cfg is None:
        tried.append(
            "FEISHU_DOC_FOLDER_FALLBACK_ROOT_META 已开启，但未提供 FeishuConfig，无法调用根目录 API"
        )

    return None, format_missing_folder_token_message(tried=tried)


async def resolve_parent_folder_token_async(
    folder_arg: str | None, *, cfg: FeishuConfig | None
) -> tuple[str | None, str | None]:
    """解析创建/列举云盘目录所用的父文件夹 token（异步版本）。

    顺序：工具参数（支持 URL）→ 环境默认 →（可选）根文件夹元数据 API。

    Returns:
        ``(token, error_message)``；成功时 ``error_message`` 为 ``None``。
    """
    tried: list[str] = []
    raw = (folder_arg or "").strip() if folder_arg is not None else ""
    token, url_err = folder_token_from_tool_arg(raw if raw else None)
    if url_err:
        return None, f"⚠️ {url_err}"
    if token:
        return token, None

    env_tok = default_doc_folder_token_from_env()
    if env_tok:
        return env_tok, None

    if root_meta_fallback_enabled() and cfg is not None:
        tried.append("FEISHU_DOC_FOLDER_FALLBACK_ROOT_META（drive/explorer/v2/root_folder/meta）")
        try:
            from miniagent.feishu.drive_client import get_root_folder_meta_async

            return await get_root_folder_meta_async(cfg), None
        except Exception as e:
            return None, format_missing_folder_token_message(tried=tried, root_meta_error=str(e))

    if root_meta_fallback_enabled() and cfg is None:
        tried.append(
            "FEISHU_DOC_FOLDER_FALLBACK_ROOT_META 已开启，但未提供 FeishuConfig，无法调用根目录 API"
        )

    return None, format_missing_folder_token_message(tried=tried)


__all__ = [
    "default_doc_folder_token_from_env",
    "extract_folder_token_from_url",
    "folder_token_from_tool_arg",
    "format_missing_folder_token_message",
    "resolve_parent_folder_token",
    "resolve_parent_folder_token_async",
    "root_meta_fallback_enabled",
]
