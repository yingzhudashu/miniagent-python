"""飞书云文档聚合工具 ``feishu_doc``（action 路由）。

提供飞书云文档的多种操作，包括：
- 文档管理：create、get、read、write、append、delete
- 块操作：list_blocks、get_block、update_block、delete_block、batch_update
- 导入导出：export_raw、import_raw
- 表格操作：create_table、write_table_cells、create_table_with_values
- 媒体操作：upload_image、upload_file、download_media、upload_image_from_message
- 文件管理：copy、move
- 权限管理：list_permissions、add_permission、remove_permission
- 发现：search

所有操作通过 ``action`` 参数路由，使用统一的 ``feishu_doc`` 工具名。

相关文档：docs/FEISHU.md

**重构说明**：配置检查使用 miniagent/tools/feishu_utils.py 的共享函数。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from typing import Any

from miniagent.feishu._utils import fmt_json, resolve_under_workspace
from miniagent.feishu.folder_token_resolve import resolve_parent_folder_token
from miniagent.feishu.lark_client import config_from_env
from miniagent.feishu.token_resolve import extract_doc_token
from miniagent.feishu.types import FeishuConfig
from miniagent.infrastructure.json_config import get_config
from miniagent.tools.feishu_doc_schema import build_feishu_doc_schema
from miniagent.tools.feishu_utils import check_lark_oapi
from miniagent.types.error_prefix import SUCCESS_PREFIX, WARNING_PREFIX
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
    """构造云文档打开 URL（需配置 feishu.doc.docx_url_prefix）。"""
    prefix = get_config("feishu.doc.docx_url_prefix", None)
    if not prefix:
        return None
    did = (document_id or "").strip()
    return f"{prefix.rstrip('/')}/{did}" if did else None


def _trace_docx_render(
    action: str, render_mode: str, stats: dict[str, Any], warnings: list[str]
) -> None:
    """Emit metrics-only Docx render diagnostics without document body or tokens."""
    from miniagent.infrastructure.trace_events import EVENT_FEISHU_DOCX_RENDER
    from miniagent.infrastructure.tracing import emit_trace

    joined = "\n".join(warnings)
    emit_trace(
        {
            "type": EVENT_FEISHU_DOCX_RENDER,
            "action": action,
            "render_mode": render_mode,
            "written_blocks": int(stats.get("written_blocks") or 0),
            "fallback_count": int(stats.get("fallback_count") or 0),
            "warning_count": len(warnings),
            "validation_error": (
                "1770001" in joined
                or "99992402" in joined
                or "field validation failed" in joined
                or "invalid param" in joined
            ),
            "success": True,
        }
    )


async def _feishu_doc(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """飞书云文档聚合工具处理函数。

    根据 ``args["action"]`` 路由到具体的操作处理函数。支持 24 种操作。

    Args:
        args: 工具参数字典，必须包含 ``action`` 键指定操作类型
            - action: 操作类型（create/read/write/append/delete 等）
            - 其他参数由具体 action 处理函数定义
        ctx: 工具上下文，提供 cwd（工作目录）等信息

    Returns:
        ToolResult: 操作结果，包含 success（成功/失败）和 content（结果内容）

    Raises:
        无直接抛出异常，所有错误通过 ToolResult(success=False) 返回

    Example:
        >>> await _feishu_doc({"action": "create", "title": "新文档"}, ctx)
        ToolResult(success=True, content="已创建文档 doc_xxx")
    """
    action = str(args.get("action") or "").strip().lower()
    if action not in _SUPPORTED_ACTIONS:
        return ToolResult(
            success=False,
            content=f"{WARNING_PREFIX} 未知 action={action!r}。支持: {', '.join(_SUPPORTED_ACTIONS)}",
        )

    cfg = config_from_env()
    if cfg is None:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET。"
        )
    dep_err = check_lark_oapi()
    if dep_err:
        return dep_err

    try:
        if action == "upload_image_from_message":
            return await _action_upload_image_from_message(args, ctx, cfg)
        return await asyncio.to_thread(
            _dispatch_sync_doc_action,
            action,
            args,
            ctx,
            cfg,
        )
    except Exception as e:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} feishu_doc.{action} 失败: {e}")


def _dispatch_sync_doc_action(
    action: str,
    args: dict[str, Any],
    ctx: ToolContext,
    cfg: FeishuConfig,
) -> ToolResult:
    """Dispatch synchronous lark-oapi and filesystem actions in a worker thread."""
    if action in {"write", "append"}:
        return _action_append(args, cfg, full_write=action == "write")
    context_handlers = {
        "create": _action_create,
        "export_raw": _action_export_raw,
        "import_raw": _action_import_raw,
        "upload_image": _action_upload_image,
        "upload_file": _action_upload_file,
        "download_media": _action_download_media,
    }
    if context_handler := context_handlers.get(action):
        return context_handler(args, ctx, cfg)
    handlers = {
        "get": _action_get,
        "read": _action_read,
        "delete": _action_delete,
        "list_blocks": _action_list_blocks,
        "get_block": _action_get_block,
        "update_block": _action_update_block,
        "delete_block": _action_delete_block,
        "batch_update": _action_batch_update,
        "create_table": _action_create_table,
        "write_table_cells": _action_write_table_cells,
        "create_table_with_values": _action_create_table_with_values,
        "copy": _action_copy,
        "move": _action_move,
        "list_permissions": _action_list_permissions,
        "add_permission": _action_add_permission,
        "remove_permission": _action_remove_permission,
        "search": _action_search,
    }
    config_handler = handlers.get(action)
    return (
        config_handler(args, cfg)
        if config_handler
        else ToolResult(success=False, content=f"{WARNING_PREFIX} 未处理的 action。")
    )


def _action_create(args: dict[str, Any], ctx: ToolContext, cfg: FeishuConfig) -> ToolResult:
    """创建飞书云文档。

    Args:
        args: 参数字典
            - title: 文档标题（默认"未命名文档"）
            - folder_token: 父文件夹 token 或 URL（必填）
            - owner_open_id: 文档所有者 open_id（可选）
        ctx: 工具上下文
        cfg: 飞书配置

    Returns:
        ToolResult: 包含 document_id、revision_id 和可选的 URL
    """
    from miniagent.feishu.docx.client import create_document

    title = str(args.get("title") or "未命名文档").strip() or "未命名文档"
    folder_arg = str(args.get("folder_token") or "").strip()
    folder, folder_err = resolve_parent_folder_token(folder_arg, cfg=cfg)
    if folder_err or not folder:
        return ToolResult(
            success=False, content=folder_err or f"{WARNING_PREFIX} 缺少 folder_token。"
        )
    doc_id, rev = create_document(cfg, folder_token=folder, title=title)
    url = _docx_open_url(doc_id)
    url_line = f"\n- url: {url}" if url else ""
    hint = "" if url else "\n（配置 feishu.doc.docx_url_prefix 可带可分享链接）"
    owner = str(
        args.get("owner_open_id") or getattr(ctx, "feishu_im_receive_id", None) or ""
    ).strip()
    owner_note = f"\n- owner_open_id: {owner}" if owner else ""
    return ToolResult(
        success=True,
        content=f"{SUCCESS_PREFIX} 已创建云文档。\n- document_id: {doc_id}\n- revision_id: {rev}{url_line}{owner_note}{hint}",
    )


def _action_get(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """获取云文档元数据（标题、revision_id 等）。"""
    from miniagent.feishu.docx.client import get_document

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    if not doc_id:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 需要 doc_token 或 document_id。"
        )
    meta = get_document(cfg, doc_id)
    url = _docx_open_url(doc_id)
    if url:
        meta["url"] = url
    return ToolResult(success=True, content=fmt_json(meta))


def _action_read(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """读取云文档正文内容与块类型统计。"""
    from miniagent.feishu.docx.blocks import list_document_blocks
    from miniagent.feishu.docx.client import get_document, get_document_raw_content

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    if not doc_id:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 需要 doc_token 或 document_id。"
        )
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
    return ToolResult(success=True, content=fmt_json(payload))


def _action_append(args: dict[str, Any], cfg: FeishuConfig, *, full_write: bool) -> ToolResult:
    """Append or replace Docx content with diagnostics for rich-render fallback."""
    from miniagent.feishu.docx.blocks import (
        DOCX_APPEND_MAX_CHARS,
        append_markdown_to_document_with_stats,
        append_plain_text_to_document,
        clear_document_content_blocks,
    )
    from miniagent.feishu.docx.markdown import markdown_to_plain_text

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    content = str(args.get("content") or args.get("text") or "")
    mode = str(args.get("mode") or "").strip().lower()
    render_mode = str(args.get("render_mode") or "rich").strip().lower()

    if not doc_id:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} requires doc_token or document_id."
        )
    if not content.strip():
        return ToolResult(success=False, content=f"{WARNING_PREFIX} content is empty.")

    use_rich = render_mode == "rich"
    removed = failed = 0
    if full_write and mode == "replace":
        removed, failed = clear_document_content_blocks(cfg, doc_id)

    op = "write(replace)" if full_write and mode == "replace" else "append"
    if use_rich:
        n, warnings, stats = append_markdown_to_document_with_stats(
            cfg, doc_id, content, use_renderer=True
        )
        _trace_docx_render(op, render_mode, stats, warnings)
        warn_text = "\nWARNING: " + "\n".join(warnings) if warnings else ""
        clear_text = (
            f" cleared={removed} delete_failed={failed};"
            if full_write and mode == "replace"
            else ""
        )
        delete_warn = f" ({failed} 个块删除失败; {failed} delete failed)" if failed else ""
        return ToolResult(
            success=True,
            content=(
                f"{SUCCESS_PREFIX} {op} rich Markdown;{clear_text} written_blocks={n}; "
                f"fallback_count={stats.get('fallback_count', 0)}.{delete_warn}{warn_text}"
            ),
            meta={"render_mode": render_mode, "render_stats": stats, "warnings": warnings},
        )

    plain = markdown_to_plain_text(content) if full_write and mode == "replace" else content
    n = append_plain_text_to_document(cfg, doc_id, plain)
    stats = {"written_blocks": n, "fallback_count": 0}
    _trace_docx_render(op, render_mode, stats, [])
    note = ""
    if full_write and mode != "replace":
        note = "\nNote: write defaults to append; use mode=replace to replace the document body."
    if full_write and mode == "replace":
        note = f"\nCleared {removed} existing blocks; delete_failed={failed}."
    return ToolResult(
        success=True,
        content=(
            f"{SUCCESS_PREFIX} appended {n} plain-text blocks "
            f"(limit {DOCX_APPEND_MAX_CHARS} chars).{note}"
        ),
        meta={"render_mode": render_mode, "render_stats": stats, "warnings": []},
    )


def _action_delete(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """删除云文档。"""
    from miniagent.feishu.docx.client import delete_document

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    if not doc_id:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 需要 doc_token 或 document_id。"
        )
    delete_document(cfg, doc_id)
    return ToolResult(
        success=True, content=f"{SUCCESS_PREFIX} 已删除云文档（file_token={doc_id}）。"
    )


def _action_list_blocks(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """列出云文档内容块（分页）。"""
    from miniagent.feishu.docx.blocks import list_document_blocks

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    if not doc_id:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 需要 doc_token 或 document_id。"
        )
    page_token = str(args.get("page_token") or "").strip() or None
    items, nxt, has_more = list_document_blocks(cfg, doc_id, page_token=page_token)
    return ToolResult(
        success=True,
        content=fmt_json({"items": items, "has_more": has_more, "page_token": nxt}),
    )


def _action_get_block(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """获取单个内容块详情。"""
    from miniagent.feishu.docx.blocks import get_block

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    block_id = str(args.get("block_id") or "").strip()
    if not doc_id or not block_id:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 doc_token 与 block_id。")
    return ToolResult(success=True, content=fmt_json(get_block(cfg, doc_id, block_id)))


def _action_update_block(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """更新单个内容块的文本内容。"""
    from miniagent.feishu.docx.blocks import update_block_text

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    block_id = str(args.get("block_id") or "").strip()
    content = str(args.get("content") or "")
    if not doc_id or not block_id:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 doc_token 与 block_id。")
    update_block_text(cfg, doc_id, block_id, content)
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已更新块文本。")


def _action_delete_block(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """删除单个内容块。"""
    from miniagent.feishu.docx.blocks import delete_block

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    block_id = str(args.get("block_id") or "").strip()
    if not doc_id or not block_id:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 doc_token 与 block_id。")
    delete_block(cfg, doc_id, block_id)
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已删除块。")


def _action_batch_update(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """批量更新内容块（支持多种操作类型）。"""
    from miniagent.feishu.docx.blocks import batch_update_blocks

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    requests_raw = args.get("requests")
    if not doc_id:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 需要 doc_token 或 document_id。"
        )
    if requests_raw is None:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 需要 requests（batch_update 请求数组）。"
        )
    if isinstance(requests_raw, str):
        try:
            requests_payload = json.loads(requests_raw)
        except json.JSONDecodeError as e:
            return ToolResult(success=False, content=f"{WARNING_PREFIX} requests JSON 无效: {e}")
    else:
        requests_payload = requests_raw
    if not isinstance(requests_payload, list):
        return ToolResult(success=False, content=f"{WARNING_PREFIX} requests 须为数组。")
    out = batch_update_blocks(cfg, doc_id, requests_payload)
    return ToolResult(success=True, content=fmt_json(out))


def _action_export_raw(args: dict[str, Any], ctx: ToolContext, cfg: FeishuConfig) -> ToolResult:
    """导出云文档正文到本地文件。"""
    from miniagent.feishu.docx.client import get_document_raw_content

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    rel = str(args.get("relative_path") or args.get("path") or "").strip()
    if not doc_id:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 需要 doc_token 或 document_id。"
        )
    ws = (ctx.cwd or "").strip()
    if not ws or not rel:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 需要会话工作区与 relative_path。"
        )
    try:
        path = resolve_under_workspace(ws, rel)
    except ValueError as e:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} {e}")
    text = get_document_raw_content(cfg, doc_id)
    os.makedirs(os.path.dirname(path) or ws, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return ToolResult(
        success=True, content=f"{SUCCESS_PREFIX} 已导出到工作区: {rel}（{len(text)} 字符）"
    )


def _action_import_raw(args: dict[str, Any], ctx: ToolContext, cfg: FeishuConfig) -> ToolResult:
    """从本地文件导入 Markdown 内容到云文档（支持富文本渲染）。

    支持两种渲染模式：
    - render_mode="rich": 富文本渲染（标题、粗体、列表、代码块等）【默认】
    - render_mode="plain": 纯文本

    Markdown 文件将自动转换为飞书文档的 Block 结构，保留格式信息。
    """
    from miniagent.feishu.docx.blocks import (
        append_markdown_to_document_with_stats,
        append_plain_text_to_document,
    )
    from miniagent.feishu.docx.markdown import markdown_to_plain_text

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    rel = str(args.get("relative_path") or args.get("path") or "").strip()
    render_mode = str(args.get("render_mode") or "rich").strip().lower()

    if not doc_id:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 doc_token。")
    ws = (ctx.cwd or "").strip()
    if not ws or not rel:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 relative_path。")
    try:
        path = resolve_under_workspace(ws, rel)
    except ValueError as e:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} {e}")

    with open(path, encoding="utf-8") as f:
        md = f.read()

    # 根据渲染模式选择写入方式
    use_rich = render_mode == "rich"

    if use_rich:
        n, warnings, stats = append_markdown_to_document_with_stats(
            cfg, doc_id, md, use_renderer=True
        )
        _trace_docx_render("import_raw", render_mode, stats, warnings)
        warn_text = "\nWARNING: " + "\n".join(warnings) if warnings else ""
        return ToolResult(
            success=True,
            content=(
                f"{SUCCESS_PREFIX} import_raw rich Markdown; written_blocks={n}; "
                f"fallback_count={stats.get('fallback_count', 0)}.{warn_text}"
            ),
            meta={"render_mode": render_mode, "render_stats": stats, "warnings": warnings},
        )
    n = append_plain_text_to_document(cfg, doc_id, markdown_to_plain_text(md))
    stats = {"written_blocks": n, "fallback_count": 0}
    _trace_docx_render("import_raw", render_mode, stats, [])
    return ToolResult(
        success=True,
        content=f"{SUCCESS_PREFIX} import_raw plain text; written_blocks={n}.",
        meta={"render_mode": render_mode, "render_stats": stats, "warnings": []},
    )


def _action_create_table(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """在云文档中创建空白表格块。"""
    from miniagent.feishu.docx.tables import create_table_block

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    if not doc_id:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 doc_token。")
    tid = create_table_block(
        cfg,
        doc_id,
        row_size=int(args.get("row_size") or 2),
        column_size=int(args.get("column_size") or 2),
        parent_block_id=str(args.get("parent_block_id") or "").strip() or None,
    )
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} table_block_id: {tid}")


def _action_write_table_cells(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """写入表格单元格内容。"""
    from miniagent.feishu.docx.tables import write_table_cells

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    tid = str(args.get("table_block_id") or "").strip()
    values = args.get("values")
    if not doc_id or not tid:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 需要 doc_token 与 table_block_id。"
        )
    if isinstance(values, str):
        values = json.loads(values)
    if not isinstance(values, list) or not all(isinstance(row, list) for row in values):
        return ToolResult(success=False, content=f"{WARNING_PREFIX} values 必须是二维数组。")
    normalized_values = [[str(cell) for cell in row] for row in values]
    write_table_cells(cfg, doc_id, tid, normalized_values)
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已写入表格单元格。")


def _action_create_table_with_values(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """创建表格块并直接填充数据。"""
    from miniagent.feishu.docx.tables import create_table_with_values

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    values = args.get("values")
    if isinstance(values, str):
        values = json.loads(values)
    if not doc_id:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 doc_token。")
    tid = create_table_with_values(
        cfg,
        doc_id,
        row_size=int(args.get("row_size") or 2),
        column_size=int(args.get("column_size") or 2),
        values=values or [],
    )
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} table_block_id: {tid}")


def _action_upload_image(args: dict[str, Any], ctx: ToolContext, cfg: FeishuConfig) -> ToolResult:
    """上传本地图片到云文档并插入。"""
    from miniagent.feishu.docx.media import upload_doc_image_from_path

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    rel = str(args.get("relative_path") or args.get("file_path") or "").strip()
    if not doc_id or not rel:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 需要 doc_token 与 relative_path。"
        )
    path = resolve_under_workspace(ctx.cwd or "", rel)
    tok = upload_doc_image_from_path(cfg, doc_id, path)
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已插入图片，file_token={tok}")


def _action_upload_file(args: dict[str, Any], ctx: ToolContext, cfg: FeishuConfig) -> ToolResult:
    """上传本地文件作为云文档附件素材。"""
    from miniagent.feishu.docx.media import upload_doc_file_from_path

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    rel = str(args.get("relative_path") or args.get("file_path") or "").strip()
    if not doc_id or not rel:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 需要 doc_token 与 relative_path。"
        )
    path = resolve_under_workspace(ctx.cwd or "", rel)
    tok = upload_doc_file_from_path(cfg, doc_id, path)
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已上传附件素材，file_token={tok}")


def _action_download_media(args: dict[str, Any], ctx: ToolContext, cfg: FeishuConfig) -> ToolResult:
    """下载云文档素材（图片/附件）到本地文件。"""
    from miniagent.feishu.docx.media import download_media_bytes

    tok = str(args.get("file_token") or args.get("token") or "").strip()
    rel = str(args.get("relative_path") or args.get("path") or "").strip()
    extra = str(args.get("extra") or "").strip() or None
    if not tok:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 file_token。")
    ws = (ctx.cwd or "").strip()
    if not ws or not rel:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 需要 relative_path 写入工作区。"
        )
    try:
        path = resolve_under_workspace(ws, rel)
    except ValueError as e:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} {e}")
    data = download_media_bytes(cfg, tok, extra=extra)
    os.makedirs(os.path.dirname(path) or ws, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已下载 {len(data)} 字节到 {rel}")


async def _action_upload_image_from_message(
    args: dict[str, Any], ctx: ToolContext, cfg: FeishuConfig
) -> ToolResult:
    """从飞书消息中的图片上传到云文档。

    Args:
        args: 参数字典
            - doc_token: 目标文档 token 或 URL（必填）
            - message_id: 源消息 ID（必填）
            - file_key: 图片 file_key（必填）
        ctx: 工具上下文
        cfg: 飞书配置

    Returns:
        ToolResult: 包含上传后的 file_token
    """
    from miniagent.feishu.docx.media import upload_doc_image_from_bytes
    from miniagent.feishu.resource_io import download_message_resource

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    mid = str(args.get("message_id") or "").strip()
    fk = str(args.get("file_key") or "").strip()
    if not doc_id or not mid or not fk:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 需要 doc_token、message_id、file_key。"
        )
    data, _ = await download_message_resource(
        cfg.app_id, cfg.app_secret, message_id=mid, file_key=fk, type_="image"
    )
    tok = await asyncio.to_thread(upload_doc_image_from_bytes, cfg, doc_id, data)
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已从消息插入图片，file_token={tok}")


def _action_copy(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """复制云文档到指定文件夹。"""
    from miniagent.feishu.drive_extra import copy_file
    from miniagent.feishu.folder_token_resolve import resolve_parent_folder_token

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    name = str(args.get("name") or "副本").strip()
    folder, err = resolve_parent_folder_token(str(args.get("folder_token") or ""), cfg=cfg)
    if err or not folder:
        return ToolResult(success=False, content=err or f"{WARNING_PREFIX} 需要 folder_token。")
    new_tok = copy_file(cfg, doc_id, name=name, folder_token=folder)
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已复制，新 token: {new_tok}")


def _action_move(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """移动云文档到指定文件夹。"""
    from miniagent.feishu.drive_extra import move_file
    from miniagent.feishu.folder_token_resolve import resolve_parent_folder_token

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    folder, err = resolve_parent_folder_token(str(args.get("folder_token") or ""), cfg=cfg)
    if err or not folder:
        return ToolResult(success=False, content=err or f"{WARNING_PREFIX} 需要 folder_token。")
    move_file(cfg, doc_id, folder_token=folder)
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已移动文档。")


def _action_list_permissions(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """列出云文档协作者权限。"""
    from miniagent.feishu.drive_extra import list_permissions

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    if not doc_id:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 doc_token。")
    items = list_permissions(cfg, doc_id)
    return ToolResult(success=True, content=fmt_json({"permissions": items}))


def _action_add_permission(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """添加协作者权限（查看/编辑/管理）。"""
    from miniagent.feishu.drive_extra import add_permission

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    member_type = str(args.get("member_type") or "").strip()
    member_id = str(args.get("member_id") or args.get("email") or args.get("open_id") or "").strip()
    perm = str(args.get("perm") or "view").strip()
    if not doc_id or not member_type or not member_id:
        return ToolResult(
            success=False,
            content=f"{WARNING_PREFIX} 需要 doc_token、member_type、member_id（或 email/open_id）。",
        )
    out = add_permission(cfg, doc_id, member_type=member_type, member_id=member_id, perm=perm)
    return ToolResult(success=True, content=fmt_json(out))


def _action_remove_permission(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """移除协作者权限。"""
    from miniagent.feishu.drive_extra import remove_permission

    doc_id = extract_doc_token(str(args.get("doc_token") or args.get("document_id") or ""))
    member_type = str(args.get("member_type") or "").strip()
    member_id = str(args.get("member_id") or args.get("email") or args.get("open_id") or "").strip()
    if not doc_id or not member_type or not member_id:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 需要 doc_token、member_type、member_id。"
        )
    remove_permission(cfg, doc_id, member_type=member_type, member_id=member_id)
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已移除协作者权限。")


def _action_search(args: dict[str, Any], cfg: FeishuConfig) -> ToolResult:
    """搜索云文档（按关键词或类型筛选）。"""
    from miniagent.feishu.drive_extra import (
        SearchApiError,
        SearchRequiresUserTokenError,
        search_docs,
    )

    q = str(args.get("query") or args.get("q") or "").strip()
    if not q:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 query。")
    try:
        items = search_docs(cfg, q)
        return ToolResult(success=True, content=fmt_json({"ok": True, "results": items}))
    except SearchRequiresUserTokenError as e:
        return ToolResult(success=False, content=fmt_json(e.to_payload()))
    except SearchApiError as e:
        return ToolResult(success=False, content=fmt_json(e.to_payload()))


_feishu_doc_schema = build_feishu_doc_schema(_SUPPORTED_ACTIONS)

# Tool Definition（聚合工具保留原有 schema）
feishu_doc_tools: dict[str, ToolDefinition] = {
    "feishu_doc": ToolDefinition(
        schema=_feishu_doc_schema,
        handler=_feishu_doc,
        permission="allowlist",
        help_text="飞书云文档（docx）读写与块操作",
        toolbox="feishu",
    ),
}

__all__ = ["FEISHU_DOC_TOOL_NAMES", "feishu_doc_tools"]
