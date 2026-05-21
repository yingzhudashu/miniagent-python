"""飞书云文档聚合工具 ``feishu_doc``（action 路由）。"""

from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any

from miniagent.feishu.folder_token_resolve import resolve_parent_folder_token
from miniagent.feishu.lark_client import config_from_env, require_lark_oapi
from miniagent.feishu.token_resolve import extract_doc_token
from miniagent.feishu.types import FeishuConfig
from miniagent.infrastructure.env_parse import env_str_legacy
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

FEISHU_DOC_TOOL_NAMES = frozenset({"feishu_doc"})

_SUPPORTED_ACTIONS = (
    "create",
    "get",
    "read",
    "write",
    "append",
    "delete",
    "list_blocks",
    "get_block",
    "update_block",
    "delete_block",
    "batch_update",
    "export_raw",
    "import_raw",
    "create_table",
    "write_table_cells",
    "create_table_with_values",
    "upload_image",
    "upload_file",
    "download_media",
    "upload_image_from_message",
    "copy",
    "move",
    "list_permissions",
    "add_permission",
    "remove_permission",
    "search",
)


def _docx_open_url(document_id: str) -> str | None:
    prefix = env_str_legacy(
        "MINIAGENT_FEISHU_DOCX_URL_PREFIX",
        "FEISHU_DOCX_URL_PREFIX",
        deprecate_msg="FEISHU_DOCX_URL_PREFIX 已弃用，请改用 MINIAGENT_FEISHU_DOCX_URL_PREFIX。",
    )
    if not prefix:
        return None
    did = (document_id or "").strip()
    return f"{prefix.rstrip('/')}/{did}" if did else None


def _resolve_under_workspace(workspace: str, rel: str) -> str:
    base = os.path.realpath(workspace)
    tail = (rel or "").strip().replace("\\", "/").lstrip("/")
    cand = os.path.realpath(os.path.join(base, tail))
    if cand != base and not cand.startswith(base + os.sep):
        raise ValueError("路径越出会话工作区")
    return cand


def _fmt_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


async def _feishu_doc(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    action = str(args.get("action") or "").strip().lower()
    if action not in _SUPPORTED_ACTIONS:
        return ToolResult(
            success=False,
            content=f"⚠️ 未知 action={action!r}。支持: {', '.join(_SUPPORTED_ACTIONS)}",
        )

    cfg = config_from_env()
    if cfg is None:
        return ToolResult(success=False, content="⚠️ 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET。")
    try:
        require_lark_oapi()
    except ImportError:
        return ToolResult(success=False, content="⚠️ 请安装 lark-oapi（pip install miniagent-python[feishu]）。")

    try:
        if action == "create":
            return await _action_create(args, ctx, cfg)
        if action == "get":
            return _action_get(args, cfg)
        if action == "read":
            return _action_read(args, cfg)
        if action in ("write", "append"):
            return _action_append(args, cfg, full_write=action == "write")
        if action == "delete":
            return _action_delete(args, cfg)
        if action == "list_blocks":
            return _action_list_blocks(args, cfg)
        if action == "get_block":
            return _action_get_block(args, cfg)
        if action == "update_block":
            return _action_update_block(args, cfg)
        if action == "delete_block":
            return _action_delete_block(args, cfg)
        if action == "batch_update":
            return _action_batch_update(args, cfg)
        if action == "export_raw":
            return _action_export_raw(args, ctx, cfg)
        if action == "import_raw":
            return _action_import_raw(args, ctx, cfg)
        if action == "create_table":
            return _action_create_table(args, cfg)
        if action == "write_table_cells":
            return _action_write_table_cells(args, cfg)
        if action == "create_table_with_values":
            return _action_create_table_with_values(args, cfg)
        if action == "upload_image":
            return _action_upload_image(args, ctx, cfg)
        if action == "upload_file":
            return _action_upload_file(args, ctx, cfg)
        if action == "download_media":
            return _action_download_media(args, ctx, cfg)
        if action == "upload_image_from_message":
            return await _action_upload_image_from_message(args, ctx, cfg)
        if action == "copy":
            return _action_copy(args, cfg)
        if action == "move":
            return _action_move(args, cfg)
        if action == "list_permissions":
            return _action_list_permissions(args, cfg)
        if action == "add_permission":
            return _action_add_permission(args, cfg)
        if action == "remove_permission":
            return _action_remove_permission(args, cfg)
        if action == "search":
            return _action_search(args, cfg)
    except Exception as e:
        return ToolResult(success=False, content=f"⚠️ feishu_doc.{action} 失败: {e}")

    return ToolResult(success=False, content="⚠️ 未处理的 action。")


async def _action_create(args: dict[str, Any], ctx: ToolContext, cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.client import create_document

    title = str(args.get("title") or "未命名文档").strip() or "未命名文档"
    folder_arg = str(args.get("folder_token") or "").strip()
    folder, folder_err = resolve_parent_folder_token(folder_arg, cfg=cfg)
    if folder_err or not folder:
        return ToolResult(success=False, content=folder_err or "⚠️ 缺少 folder_token。")
    doc_id, rev = create_document(cfg, folder_token=folder, title=title)
    url = _docx_open_url(doc_id)
    url_line = f"\n- url: {url}" if url else ""
    hint = "" if url else "\n（配置 MINIAGENT_FEISHU_DOCX_URL_PREFIX 可带可分享链接）"
    owner = str(args.get("owner_open_id") or getattr(ctx, "feishu_im_receive_id", None) or "").strip()
    owner_note = f"\n- owner_open_id: {owner}" if owner else ""
    return ToolResult(
        success=True,
        content=f"✅ 已创建云文档。\n- document_id: {doc_id}\n- revision_id: {rev}{url_line}{owner_note}{hint}",
    )


def _action_get(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.client import get_document

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    if not doc_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token 或 document_id。")
    meta = get_document(cfg, doc_id)
    url = _docx_open_url(doc_id)
    if url:
        meta["url"] = url
    return ToolResult(success=True, content=_fmt_json(meta))


def _action_read(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.blocks import list_document_blocks
    from miniagent.feishu.docx.client import get_document, get_document_raw_content

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    if not doc_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token 或 document_id。")
    meta = get_document(cfg, doc_id)
    text = get_document_raw_content(cfg, doc_id)
    blocks, _, _ = list_document_blocks(cfg, doc_id, page_size=200)
    types = Counter(b.get("block_type") for b in blocks)
    cap = 120_000
    if len(text) > cap:
        text = text[:cap] + "\n\n…（已截断）"
    hint = ""
    structured = {k for k, v in types.items() if v and k not in (1, 2)}
    if structured:
        hint = (
            "hint: 文档含表格/图片等结构化块，请用 action=list_blocks 或 get_block 读取详情；"
            "Markdown 表格请用 create_table / create_table_with_values / write_table_cells，"
            "或 batch_update；整篇替换用 write + mode=replace。"
        )
    payload = {
        "title": meta.get("title"),
        "document_id": doc_id,
        "revision_id": meta.get("revision_id"),
        "block_type_counts": dict(types),
        "content": text or "",
        "hint": hint or None,
    }
    return ToolResult(success=True, content=_fmt_json(payload))


def _action_append(args: dict[str, Any], cfg: FeishuConfig, *, full_write: bool) -> ToolResult:
    from miniagent.feishu.docx.blocks import (
        DOCX_APPEND_MAX_CHARS,
        append_plain_text_to_document,
        clear_document_content_blocks,
    )
    from miniagent.feishu.docx.markdown import markdown_to_plain_text

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    content = str(args.get("content") or args.get("text") or "")
    mode = str(args.get("mode") or "").strip().lower()
    if not doc_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token 或 document_id。")
    if not content.strip():
        return ToolResult(success=False, content="⚠️ content 为空。")
    if full_write and mode == "replace":
        removed, failed = clear_document_content_blocks(cfg, doc_id)
        plain = markdown_to_plain_text(content)
        n = append_plain_text_to_document(cfg, doc_id, plain)
        warn = f"（{failed} 个块删除失败，可能残留旧内容）" if failed else ""
        return ToolResult(
            success=True,
            content=f"✅ write(replace)：已清除 {removed} 个块并写入 {n} 个新段落。{warn}",
        )
    n = append_plain_text_to_document(cfg, doc_id, content)
    note = ""
    if full_write and mode != "replace":
        note = "\n提示：write 默认 append；整篇替换请设 mode=replace。"
    return ToolResult(
        success=True,
        content=f"✅ 已追加 {n} 个文本块（单次约 {DOCX_APPEND_MAX_CHARS} 字符上限）。{note}",
    )


def _action_delete(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.client import delete_document

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    if not doc_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token 或 document_id。")
    delete_document(cfg, doc_id)
    return ToolResult(success=True, content=f"✅ 已删除云文档（file_token={doc_id}）。")


def _action_list_blocks(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.blocks import list_document_blocks

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    if not doc_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token 或 document_id。")
    page_token = str(args.get("page_token") or "").strip() or None
    items, nxt, has_more = list_document_blocks(cfg, doc_id, page_token=page_token)
    return ToolResult(
        success=True,
        content=_fmt_json({"items": items, "has_more": has_more, "page_token": nxt}),
    )


def _action_get_block(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.blocks import get_block

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    block_id = str(args.get("block_id") or "").strip()
    if not doc_id or not block_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token 与 block_id。")
    return ToolResult(success=True, content=_fmt_json(get_block(cfg, doc_id, block_id)))


def _action_update_block(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.blocks import update_block_text

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    block_id = str(args.get("block_id") or "").strip()
    content = str(args.get("content") or "")
    if not doc_id or not block_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token 与 block_id。")
    update_block_text(cfg, doc_id, block_id, content)
    return ToolResult(success=True, content="✅ 已更新块文本。")


def _action_delete_block(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.blocks import delete_block

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    block_id = str(args.get("block_id") or "").strip()
    if not doc_id or not block_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token 与 block_id。")
    delete_block(cfg, doc_id, block_id)
    return ToolResult(success=True, content="✅ 已删除块。")


def _action_batch_update(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.blocks import batch_update_blocks

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    requests_raw = args.get("requests")
    if not doc_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token 或 document_id。")
    if requests_raw is None:
        return ToolResult(success=False, content="⚠️ 需要 requests（batch_update 请求数组）。")
    if isinstance(requests_raw, str):
        try:
            requests_payload = json.loads(requests_raw)
        except json.JSONDecodeError as e:
            return ToolResult(success=False, content=f"⚠️ requests JSON 无效: {e}")
    else:
        requests_payload = requests_raw
    if not isinstance(requests_payload, list):
        return ToolResult(success=False, content="⚠️ requests 须为数组。")
    out = batch_update_blocks(cfg, doc_id, requests_payload)
    return ToolResult(success=True, content=_fmt_json(out))


def _action_export_raw(args: dict[str, Any], ctx: ToolContext, cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.client import get_document_raw_content

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    rel = str(args.get("relative_path") or args.get("path") or "").strip()
    if not doc_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token 或 document_id。")
    ws = (ctx.cwd or "").strip()
    if not ws or not rel:
        return ToolResult(success=False, content="⚠️ 需要会话工作区与 relative_path。")
    try:
        path = _resolve_under_workspace(ws, rel)
    except ValueError as e:
        return ToolResult(success=False, content=f"⚠️ {e}")
    text = get_document_raw_content(cfg, doc_id)
    os.makedirs(os.path.dirname(path) or ws, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return ToolResult(success=True, content=f"✅ 已导出到工作区: {rel}（{len(text)} 字符）")


def _action_import_raw(args: dict[str, Any], ctx: ToolContext, cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.blocks import append_plain_text_to_document
    from miniagent.feishu.docx.markdown import markdown_to_plain_text

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    rel = str(args.get("relative_path") or args.get("path") or "").strip()
    if not doc_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token。")
    ws = (ctx.cwd or "").strip()
    if not ws or not rel:
        return ToolResult(success=False, content="⚠️ 需要 relative_path。")
    try:
        path = _resolve_under_workspace(ws, rel)
    except ValueError as e:
        return ToolResult(success=False, content=f"⚠️ {e}")
    with open(path, encoding="utf-8") as f:
        md = f.read()
    n = append_plain_text_to_document(cfg, doc_id, markdown_to_plain_text(md))
    return ToolResult(success=True, content=f"✅ import_raw：已追加 {n} 段（不含 MD 表格）。")


def _action_create_table(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.tables import create_table_block

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    if not doc_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token。")
    tid = create_table_block(
        cfg,
        doc_id,
        row_size=int(args.get("row_size") or 2),
        column_size=int(args.get("column_size") or 2),
        parent_block_id=str(args.get("parent_block_id") or "").strip() or None,
    )
    return ToolResult(success=True, content=f"✅ table_block_id: {tid}")


def _action_write_table_cells(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.tables import write_table_cells

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    tid = str(args.get("table_block_id") or "").strip()
    values = args.get("values")
    if not doc_id or not tid:
        return ToolResult(success=False, content="⚠️ 需要 doc_token 与 table_block_id。")
    if isinstance(values, str):
        values = json.loads(values)
    write_table_cells(cfg, doc_id, tid, values)
    return ToolResult(success=True, content="✅ 已写入表格单元格。")


def _action_create_table_with_values(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.tables import create_table_with_values

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    values = args.get("values")
    if isinstance(values, str):
        values = json.loads(values)
    if not doc_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token。")
    tid = create_table_with_values(
        cfg,
        doc_id,
        row_size=int(args.get("row_size") or 2),
        column_size=int(args.get("column_size") or 2),
        values=values or [],
    )
    return ToolResult(success=True, content=f"✅ table_block_id: {tid}")


def _action_upload_image(args: dict[str, Any], ctx: ToolContext, cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.media import upload_doc_image_from_path

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    rel = str(args.get("relative_path") or args.get("file_path") or "").strip()
    if not doc_id or not rel:
        return ToolResult(success=False, content="⚠️ 需要 doc_token 与 relative_path。")
    path = _resolve_under_workspace(ctx.cwd or "", rel)
    tok = upload_doc_image_from_path(cfg, doc_id, path)
    return ToolResult(success=True, content=f"✅ 已插入图片，file_token={tok}")


def _action_upload_file(args: dict[str, Any], ctx: ToolContext, cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.media import upload_doc_file_from_path

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    rel = str(args.get("relative_path") or args.get("file_path") or "").strip()
    if not doc_id or not rel:
        return ToolResult(success=False, content="⚠️ 需要 doc_token 与 relative_path。")
    path = _resolve_under_workspace(ctx.cwd or "", rel)
    tok = upload_doc_file_from_path(cfg, doc_id, path)
    return ToolResult(success=True, content=f"✅ 已上传附件素材，file_token={tok}")


def _action_download_media(args: dict[str, Any], ctx: ToolContext, cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.media import download_media_bytes

    tok = str(args.get("file_token") or args.get("token") or "").strip()
    rel = str(args.get("relative_path") or args.get("path") or "").strip()
    extra = str(args.get("extra") or "").strip() or None
    if not tok:
        return ToolResult(success=False, content="⚠️ 需要 file_token。")
    ws = (ctx.cwd or "").strip()
    if not ws or not rel:
        return ToolResult(success=False, content="⚠️ 需要 relative_path 写入工作区。")
    try:
        path = _resolve_under_workspace(ws, rel)
    except ValueError as e:
        return ToolResult(success=False, content=f"⚠️ {e}")
    data = download_media_bytes(cfg, tok, extra=extra)
    os.makedirs(os.path.dirname(path) or ws, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return ToolResult(success=True, content=f"✅ 已下载 {len(data)} 字节到 {rel}")


async def _action_upload_image_from_message(args: dict[str, Any], ctx: ToolContext, cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.docx.media import upload_doc_image_from_bytes
    from miniagent.feishu.resource_io import download_message_resource

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    mid = str(args.get("message_id") or "").strip()
    fk = str(args.get("file_key") or "").strip()
    if not doc_id or not mid or not fk:
        return ToolResult(success=False, content="⚠️ 需要 doc_token、message_id、file_key。")
    data, _ = await download_message_resource(cfg.app_id, cfg.app_secret, message_id=mid, file_key=fk, type_="image")
    tok = upload_doc_image_from_bytes(cfg, doc_id, data)
    return ToolResult(success=True, content=f"✅ 已从消息插入图片，file_token={tok}")


def _action_copy(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.drive_extra import copy_file
    from miniagent.feishu.folder_token_resolve import resolve_parent_folder_token

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    name = str(args.get("name") or "副本").strip()
    folder, err = resolve_parent_folder_token(str(args.get("folder_token") or ""), cfg=cfg)
    if err or not folder:
        return ToolResult(success=False, content=err or "⚠️ 需要 folder_token。")
    new_tok = copy_file(cfg, doc_id, name=name, folder_token=folder)
    return ToolResult(success=True, content=f"✅ 已复制，新 token: {new_tok}")


def _action_move(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.drive_extra import move_file
    from miniagent.feishu.folder_token_resolve import resolve_parent_folder_token

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    folder, err = resolve_parent_folder_token(str(args.get("folder_token") or ""), cfg=cfg)
    if err or not folder:
        return ToolResult(success=False, content=err or "⚠️ 需要 folder_token。")
    move_file(cfg, doc_id, folder_token=folder)
    return ToolResult(success=True, content="✅ 已移动文档。")


def _action_list_permissions(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.drive_extra import list_permissions

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    if not doc_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token。")
    items = list_permissions(cfg, doc_id)
    return ToolResult(success=True, content=_fmt_json({"permissions": items}))


def _action_add_permission(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.drive_extra import add_permission

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    member_type = str(args.get("member_type") or "").strip()
    member_id = str(args.get("member_id") or args.get("email") or args.get("open_id") or "").strip()
    perm = str(args.get("perm") or "view").strip()
    if not doc_id or not member_type or not member_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token、member_type、member_id（或 email/open_id）。")
    out = add_permission(cfg, doc_id, member_type=member_type, member_id=member_id, perm=perm)
    return ToolResult(success=True, content=_fmt_json(out))


def _action_remove_permission(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.drive_extra import remove_permission

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    member_type = str(args.get("member_type") or "").strip()
    member_id = str(args.get("member_id") or args.get("email") or args.get("open_id") or "").strip()
    if not doc_id or not member_type or not member_id:
        return ToolResult(success=False, content="⚠️ 需要 doc_token、member_type、member_id。")
    remove_permission(cfg, doc_id, member_type=member_type, member_id=member_id)
    return ToolResult(success=True, content="✅ 已移除协作者权限。")


def _action_search(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    from miniagent.feishu.drive_extra import (
        SearchApiError,
        SearchRequiresUserTokenError,
        search_docs,
    )

    q = str(args.get("query") or args.get("q") or "").strip()
    if not q:
        return ToolResult(success=False, content="⚠️ 需要 query。")
    try:
        items = search_docs(cfg, q)
        return ToolResult(success=True, content=_fmt_json({"ok": True, "results": items}))
    except SearchRequiresUserTokenError as e:
        return ToolResult(success=False, content=_fmt_json(e.to_payload()))
    except SearchApiError as e:
        return ToolResult(success=False, content=_fmt_json(e.to_payload()))


_feishu_doc_schema = {
    "type": "function",
    "function": {
        "name": "feishu_doc",
        "description": (
            "飞书云文档（docx）统一工具。action："
            "create/get/read/write/append/delete、list_blocks/get_block/update_block/delete_block/batch_update、"
            "export_raw/import_raw；表格 create_table/write_table_cells/create_table_with_values；"
            "媒体 upload_image/upload_file/download_media/upload_image_from_message；"
            "copy/move；list_permissions/add_permission/remove_permission；search（需 User Token）。"
            "write 默认 append；mode=replace 整篇替换。doc_token 可为 document_id 或 docx URL。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_SUPPORTED_ACTIONS),
                    "description": "操作类型",
                },
                "doc_token": {"type": "string", "description": "文档 ID 或 docx URL"},
                "document_id": {"type": "string", "description": "同 doc_token"},
                "title": {"type": "string"},
                "folder_token": {"type": "string"},
                "owner_open_id": {"type": "string", "description": "创建时建议传入用户 open_id"},
                "content": {"type": "string", "description": "write/append/update_block 正文"},
                "text": {"type": "string", "description": "append 别名"},
                "block_id": {"type": "string"},
                "page_token": {"type": "string"},
                "requests": {
                    "description": "batch_update 请求数组或 JSON 字符串",
                },
                "relative_path": {
                    "type": "string",
                    "description": "export_raw/import_raw/download_media/upload_image 等工作区相对路径",
                },
                "path": {"type": "string", "description": "relative_path 别名"},
                "mode": {"type": "string", "description": "write 时：replace 整篇替换，默认 append"},
                "table_block_id": {"type": "string", "description": "write_table_cells"},
                "values": {"description": "表格二维数组或 JSON 字符串"},
                "row_size": {"type": "integer", "description": "create_table"},
                "column_size": {"type": "integer", "description": "create_table"},
                "parent_block_id": {"type": "string", "description": "create_table 父块"},
                "file_token": {"type": "string", "description": "download_media"},
                "extra": {"type": "string", "description": "download_media 可选 extra"},
                "message_id": {"type": "string", "description": "upload_image_from_message"},
                "file_key": {"type": "string", "description": "upload_image_from_message"},
                "name": {"type": "string", "description": "copy 新文档名"},
                "member_type": {"type": "string", "description": "add/remove_permission"},
                "member_id": {"type": "string", "description": "协作者 ID 或 email"},
                "email": {"type": "string", "description": "add_permission 别名"},
                "open_id": {"type": "string", "description": "add_permission 别名"},
                "perm": {"type": "string", "description": "view/edit/full_access 等"},
                "query": {"type": "string", "description": "search 关键词"},
                "q": {"type": "string", "description": "query 别名"},
            },
            "required": ["action"],
        },
    },
}

feishu_doc_tools: dict[str, ToolDefinition] = {
    "feishu_doc": ToolDefinition(
        schema=_feishu_doc_schema,
        handler=_feishu_doc,
        permission="allowlist",
        help_text="飞书云文档（docx）读写与块操作",
        toolbox=None,
    ),
}

__all__ = ["FEISHU_DOC_TOOL_NAMES", "feishu_doc_tools"]
