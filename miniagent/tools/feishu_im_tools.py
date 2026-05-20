"""飞书 IM / 云文档相关内置工具（可选；由 ``MINIAGENT_FEISHU_TOOLS`` / ``MINIAGENT_FEISHU_TOOLS_AUTO`` 控制注册）。

需在环境中配置 ``FEISHU_APP_ID`` / ``FEISHU_APP_SECRET``；发送类工具在飞书会话中依赖执行器注入的
``message_queue_abort_chat_id``（与 ``feishu_receive_chat_id`` 同源）作为默认 ``receive_id``。
``receive_id_type`` 可由 ``ToolContext.feishu_im_receive_id_type``、环境变量 ``MINIAGENT_FEISHU_RECEIVE_ID_TYPE``
或工具参数覆盖。
"""

from __future__ import annotations

import os
from typing import Any

from miniagent.feishu.folder_token_resolve import resolve_parent_folder_token
from miniagent.feishu.types import FeishuConfig
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

FEISHU_IM_TOOL_NAMES = frozenset(
    {
        "feishu_send_workspace_file",
        "feishu_recall_message",
        "feishu_create_document",
        "feishu_get_document_markdown",
        "feishu_list_drive_files",
        "feishu_append_document_text",
    }
)


def _feishu_config_from_env() -> FeishuConfig | None:
    """从环境变量构造 ``FeishuConfig``；缺 App ID/Secret 时返回 ``None``。"""
    aid = (os.environ.get("FEISHU_APP_ID") or "").strip()
    sec = (os.environ.get("FEISHU_APP_SECRET") or "").strip()
    if not aid or not sec:
        return None
    return FeishuConfig(
        app_id=aid,
        app_secret=sec,
        encrypt_key=(os.environ.get("FEISHU_ENCRYPT_KEY") or "").strip() or None,
        verification_token=(os.environ.get("FEISHU_VERIFICATION_TOKEN") or "").strip() or None,
    )


def _docx_open_url(document_id: str) -> str | None:
    """若配置了 URL 前缀，则返回浏览器可打开的 docx 链接（租户域名须自行配置）。"""
    from miniagent.infrastructure.env_parse import env_str_legacy

    prefix = env_str_legacy(
        "MINIAGENT_FEISHU_DOCX_URL_PREFIX",
        "FEISHU_DOCX_URL_PREFIX",
        deprecate_msg="FEISHU_DOCX_URL_PREFIX 已弃用，请改用 MINIAGENT_FEISHU_DOCX_URL_PREFIX。",
    )
    if not prefix:
        return None
    did = (document_id or "").strip()
    if not did:
        return None
    return f"{prefix.rstrip('/')}/{did}"


def _resolve_under_workspace(workspace: str, rel: str) -> str:
    """将会话相对路径解析为实路径；越出 ``workspace`` 则抛 ``ValueError``。"""
    base = os.path.realpath(workspace)
    tail = (rel or "").strip().replace("\\", "/").lstrip("/")
    cand = os.path.realpath(os.path.join(base, tail))
    if cand != base and not cand.startswith(base + os.sep):
        raise ValueError("路径越出会话工作区")
    return cand


def _effective_receive_id_type(args: dict[str, Any], ctx: ToolContext) -> str:
    """工具参数 > ToolContext > 环境变量 ``MINIAGENT_FEISHU_RECEIVE_ID_TYPE``，默认 ``chat_id``。"""
    from miniagent.feishu.im_send import resolve_im_receive_id_type

    a = str(args.get("receive_id_type") or "").strip().lower()
    if a in ("chat_id", "open_id", "union_id"):
        return a
    c = (ctx.feishu_im_receive_id_type or "").strip().lower()
    if c in ("chat_id", "open_id", "union_id"):
        return c
    return resolve_im_receive_id_type(None)


def _default_receive_id_for_send(args: dict[str, Any], ctx: ToolContext) -> tuple[str | None, str | None]:
    """解析默认 ``receive_id``。显式参数优先；``open_id``/``union_id`` 时用 ``ctx.feishu_im_receive_id``（入站 sender）。"""
    explicit = str(args.get("receive_id") or "").strip()
    if explicit:
        return explicit, None
    rid_t = _effective_receive_id_type(args, ctx)
    if rid_t == "chat_id":
        mid = (ctx.message_queue_abort_chat_id or "").strip()
        if mid:
            return mid, None
        return None, "当前非飞书会话（无 chat_id），且未传入 receive_id。"
    alt = (getattr(ctx, "feishu_im_receive_id", None) or "").strip()
    if alt:
        return alt, None
    return None, (
        "缺少与 receive_id_type 匹配的 receive_id：请传入 receive_id，"
        "或确保飞书入站已注入发送者 ID（feishu_im_receive_id，通常为 open_id）。"
    )


async def _feishu_send_workspace_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """将工作区内文件以 IM 文件/图片发到当前或指定会话。"""
    rel = str(args.get("relative_path") or "").strip()
    as_image = bool(args.get("as_image"))
    reply_to = str(args.get("reply_to_message_id") or "").strip() or None
    reply_in_thread = bool(args.get("reply_in_thread"))
    receive_id, recv_err = _default_receive_id_for_send(args, ctx)
    receive_id_type = _effective_receive_id_type(args, ctx)

    cfg = _feishu_config_from_env()
    if cfg is None:
        return ToolResult(success=False, content="⚠️ 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET。")
    if recv_err:
        return ToolResult(success=False, content=f"⚠️ {recv_err}")
    if not receive_id:
        return ToolResult(
            success=False,
            content="⚠️ 缺少 receive_id。",
        )
    ws = (ctx.cwd or "").strip()
    if not ws or not rel:
        return ToolResult(success=False, content="⚠️ 缺少工作区路径或 relative_path。")
    try:
        path = _resolve_under_workspace(ws, rel)
    except ValueError as e:
        return ToolResult(success=False, content=f"⚠️ {e}")
    if not os.path.isfile(path):
        return ToolResult(success=False, content=f"⚠️ 文件不存在: {rel}")

    try:
        from miniagent.feishu.upload_io import (
            send_im_file_message,
            send_im_image_message,
            upload_im_file,
            upload_im_image,
        )
    except ImportError:
        return ToolResult(success=False, content="⚠️ 请安装 lark-oapi（pip install miniagent-python[feishu]）。")

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return ToolResult(success=False, content=f"⚠️ 读取文件失败: {e}")

    try:
        if as_image:
            ik = upload_im_image(cfg, data)
            ok, err = send_im_image_message(
                cfg,
                receive_id,
                ik,
                reply_to_message_id=reply_to,
                reply_in_thread=reply_in_thread,
                receive_id_type=receive_id_type,
            )
        else:
            name = os.path.basename(path)
            fk = upload_im_file(cfg, data, file_name=name)
            ok, err = send_im_file_message(
                cfg,
                receive_id,
                fk,
                file_name=name,
                reply_to_message_id=reply_to,
                reply_in_thread=reply_in_thread,
                receive_id_type=receive_id_type,
            )
    except Exception as e:
        return ToolResult(success=False, content=f"⚠️ 上传或发送失败: {e}")
    if not ok:
        return ToolResult(success=False, content=f"⚠️ 飞书发送失败: {err or 'unknown'}")
    return ToolResult(success=True, content="✅ 已发送到当前飞书会话。")


async def _feishu_recall_message(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """调用飞书删除消息 API 撤回机器人已发消息。"""
    _ = ctx
    mid = str(args.get("message_id") or "").strip()
    if not mid:
        return ToolResult(success=False, content="⚠️ 需要 message_id。")
    cfg = _feishu_config_from_env()
    if cfg is None:
        return ToolResult(success=False, content="⚠️ 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET。")
    try:
        from miniagent.feishu.upload_io import delete_im_message
    except ImportError:
        return ToolResult(success=False, content="⚠️ 请安装 lark-oapi。")
    try:
        ok, err = delete_im_message(cfg, mid)
    except Exception as e:
        return ToolResult(success=False, content=f"⚠️ 撤回失败: {e}")
    if not ok:
        return ToolResult(success=False, content=f"⚠️ 飞书删除消息 API 失败: {err or 'unknown'}")
    return ToolResult(success=True, content="✅ 已请求撤回该消息。")


async def _feishu_create_document(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """在指定云盘文件夹下创建云文档并返回 document_id / revision。"""
    _ = ctx
    title = str(args.get("title") or "未命名文档").strip() or "未命名文档"
    folder_arg = str(args.get("folder_token") or "").strip()
    cfg = _feishu_config_from_env()
    if cfg is None:
        return ToolResult(success=False, content="⚠️ 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET。")
    folder, folder_err = resolve_parent_folder_token(folder_arg, cfg=cfg)
    if folder_err or not folder:
        return ToolResult(success=False, content=folder_err or "⚠️ 缺少 folder_token。")
    try:
        from miniagent.feishu.docx_client import create_document
    except ImportError:
        return ToolResult(success=False, content="⚠️ 请安装 lark-oapi。")
    try:
        doc_id, rev = create_document(cfg, folder_token=folder, title=title)
    except Exception as e:
        return ToolResult(success=False, content=f"⚠️ 创建失败: {e}")
    url = _docx_open_url(doc_id)
    url_line = f"\n- url: {url}" if url else ""
    hint = "" if url else "\n（配置 MINIAGENT_FEISHU_DOCX_URL_PREFIX 可在输出中带可分享链接）"
    return ToolResult(
        success=True,
        content=f"✅ 已创建云文档。\n- document_id: {doc_id}\n- revision_id: {rev}{url_line}{hint}",
    )


async def _feishu_get_document_markdown(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """读取云文档原始 Markdown/纯文本内容（大文档截断返回）。"""
    _ = ctx
    doc_id = str(args.get("document_id") or "").strip()
    if not doc_id:
        return ToolResult(success=False, content="⚠️ 需要 document_id。")
    cfg = _feishu_config_from_env()
    if cfg is None:
        return ToolResult(success=False, content="⚠️ 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET。")
    try:
        from miniagent.feishu.docx_client import get_document_raw_content
    except ImportError:
        return ToolResult(success=False, content="⚠️ 请安装 lark-oapi。")
    try:
        text = get_document_raw_content(cfg, doc_id)
    except Exception as e:
        return ToolResult(success=False, content=f"⚠️ 读取失败: {e}")
    cap = 120_000
    if len(text) > cap:
        text = text[:cap] + "\n\n…（已截断）"
    return ToolResult(success=True, content=text or "（空文档）")


async def _feishu_list_drive_files(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """分页列举云盘文件夹下条目，格式化为 Markdown 表格字符串。"""
    _ = ctx
    folder_arg = str(args.get("folder_token") or "").strip()
    cfg = _feishu_config_from_env()
    if cfg is None:
        return ToolResult(success=False, content="⚠️ 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET。")
    folder, folder_err = resolve_parent_folder_token(folder_arg, cfg=cfg)
    if folder_err or not folder:
        return ToolResult(success=False, content=folder_err or "⚠️ 缺少 folder_token。")
    folders_only = bool(args.get("folders_only"))
    name_sub = str(args.get("name_contains") or "").strip().lower()
    page_token = str(args.get("page_token") or "").strip() or None
    try:
        from miniagent.feishu.drive_client import list_folder_files_page
    except ImportError:
        return ToolResult(success=False, content="⚠️ 请安装 lark-oapi。")
    try:
        entries, next_tok, has_more = list_folder_files_page(cfg, folder_token=folder, page_token=page_token)
    except Exception as e:
        return ToolResult(success=False, content=f"⚠️ 列举失败: {e}")
    lines = ["| name | token | type |", "| --- | --- | --- |"]
    for e in entries:
        if folders_only and str(e.get("type") or "").lower() != "folder":
            continue
        if name_sub and name_sub not in str(e.get("name") or "").lower():
            continue
        nm = str(e.get("name") or "").replace("|", "\\|")
        tk = str(e.get("token") or "").replace("|", "\\|")
        tp = str(e.get("type") or "").replace("|", "\\|")
        lines.append(f"| {nm} | {tk} | {tp} |")
    tail = f"\n\nhas_more={has_more}"
    if next_tok:
        tail += f"\nnext_page_token: {next_tok}"
    return ToolResult(success=True, content="\n".join(lines) + tail)


async def _feishu_append_document_text(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """在文档页面下追加纯文本段落（docx block_children.create）。"""
    _ = ctx
    doc_id = str(args.get("document_id") or "").strip()
    text = str(args.get("text") or "")
    if not doc_id:
        return ToolResult(success=False, content="⚠️ 需要 document_id。")
    cfg = _feishu_config_from_env()
    if cfg is None:
        return ToolResult(success=False, content="⚠️ 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET。")
    try:
        from miniagent.feishu.docx_blocks import (
            DOCX_APPEND_MAX_CHARS,
            append_plain_text_to_document,
        )
    except ImportError:
        return ToolResult(success=False, content="⚠️ 请安装 lark-oapi。")
    if not text.strip():
        return ToolResult(success=False, content="⚠️ text 为空。")
    try:
        n = append_plain_text_to_document(cfg, doc_id, text)
    except Exception as e:
        return ToolResult(success=False, content=f"⚠️ 追加失败: {e}")
    return ToolResult(
        success=True,
        content=f"✅ 已在云文档末尾追加 {n} 个文本块（单次最多约 {DOCX_APPEND_MAX_CHARS} 字符，按换行分段）。",
    )


_feishu_send_workspace_file_schema = {
    "type": "function",
    "function": {
        "name": "feishu_send_workspace_file",
        "description": (
            "将当前 Agent 会话工作区根目录（会话 files 沙箱）下的文件上传到飞书并以 file 或 image 消息发送；"
            "不是用户操作系统上任意绝对路径。"
            "飞书用户发来的附件保存在 `files/feishu_incoming/` 下，可用该相对路径发送。"
            "若需发送尚未在工作区内的内容，应先用 read_file/write_file 等写入工作区后再调用本工具。"
            "默认 receive_id 为当前飞书会话；可用 receive_id / receive_id_type 覆盖（须与开放平台 ID 类型一致）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "相对会话工作区根的路径，如 files/feishu_incoming/图_oc_xxx.png 或 notes/a.png",
                },
                "as_image": {
                    "type": "boolean",
                    "description": "为 true 时按图片上传发送（适合 png/jpg）；否则按文件消息发送",
                },
                "reply_to_message_id": {
                    "type": "string",
                    "description": "可选。若填写则使用「回复消息」API 挂到该 message_id 下",
                },
                "reply_in_thread": {
                    "type": "boolean",
                    "description": "与 reply_to_message_id 联用：是否话题内回复",
                },
                "receive_id": {
                    "type": "string",
                    "description": "可选。覆盖默认会话 ID（与 receive_id_type 一致时使用 open_id/union_id 等）",
                },
                "receive_id_type": {
                    "type": "string",
                    "description": "可选。chat_id | open_id | union_id；默认来自环境 MINIAGENT_FEISHU_RECEIVE_ID_TYPE 或会话注入",
                },
            },
            "required": ["relative_path"],
        },
    },
}

_feishu_recall_schema = {
    "type": "function",
    "function": {
        "name": "feishu_recall_message",
        "description": "撤回（删除）机器人在飞书已发送的一条消息；需要该消息的 message_id。",
        "parameters": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
        },
    },
}

_feishu_create_doc_schema = {
    "type": "function",
    "function": {
        "name": "feishu_create_document",
        "description": (
            "在指定云盘文件夹下创建空白飞书云文档，返回 document_id（及 revision_id）；"
            "若环境配置了 MINIAGENT_FEISHU_DOCX_URL_PREFIX（如 https://example.feishu.cn/docx），"
            "则同时返回可分享的浏览器 url。"
            "folder_token 可为云盘文件夹 token 或飞书云盘文件夹分享链接；省略时依次使用环境变量 "
            "MINIAGENT_FEISHU_DOC_FOLDER_TOKEN，"
            "或在设置 FEISHU_DOC_FOLDER_FALLBACK_ROOT_META=1 时调用根目录元数据 API（须具备 drive 权限，默认关闭）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "folder_token": {
                    "type": "string",
                    "description": "云盘父文件夹 token 或完整文件夹分享 URL；可省略若已配置默认环境变量或启用根目录回退",
                },
            },
            "required": [],
        },
    },
}

_feishu_get_doc_schema = {
    "type": "function",
    "function": {
        "name": "feishu_get_document_markdown",
        "description": "读取飞书云文档 Markdown 原文（docx raw_content）；大文档可能截断。",
        "parameters": {
            "type": "object",
            "properties": {"document_id": {"type": "string"}},
            "required": ["document_id"],
        },
    },
}

_feishu_list_drive_schema = {
    "type": "function",
    "function": {
        "name": "feishu_list_drive_files",
        "description": (
            "列举飞书云盘某文件夹下一页文件/子文件夹（只读），返回 Markdown 表；需 drive 相关权限。"
            "folder_token 解析规则与 feishu_create_document 相同（支持 URL、环境默认、可选根目录 API）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "folder_token": {
                    "type": "string",
                    "description": "云盘文件夹 token 或分享 URL；可省略若已配置默认父目录或启用 FEISHU_DOC_FOLDER_FALLBACK_ROOT_META",
                },
                "folders_only": {"type": "boolean", "description": "为 true 时仅保留 type 为 folder 的条目"},
                "name_contains": {"type": "string", "description": "按名称子串过滤（大小写不敏感）"},
                "page_token": {"type": "string", "description": "分页游标；上一页返回的 next_page_token"},
            },
            "required": [],
        },
    },
}

_feishu_append_doc_schema = {
    "type": "function",
    "function": {
        "name": "feishu_append_document_text",
        "description": (
            "在飞书 docx 云文档页面块末尾追加纯文本：按换行拆成多个段落块。"
            "非完整 Markdown 渲染；大文本会截断至单次上限。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "text": {"type": "string", "description": "要追加的正文（可含换行）"},
            },
            "required": ["document_id", "text"],
        },
    },
}

feishu_im_tools: dict[str, ToolDefinition] = {
    "feishu_send_workspace_file": ToolDefinition(
        schema=_feishu_send_workspace_file_schema,
        handler=_feishu_send_workspace_file,
        permission="allowlist",
        help_text="上传会话工作区文件并发送到当前飞书会话",
        toolbox=None,
    ),
    "feishu_recall_message": ToolDefinition(
        schema=_feishu_recall_schema,
        handler=_feishu_recall_message,
        permission="allowlist",
        help_text="撤回机器人已发送的飞书消息",
        toolbox=None,
    ),
    "feishu_create_document": ToolDefinition(
        schema=_feishu_create_doc_schema,
        handler=_feishu_create_document,
        permission="allowlist",
        help_text="创建飞书云文档",
        toolbox=None,
    ),
    "feishu_get_document_markdown": ToolDefinition(
        schema=_feishu_get_doc_schema,
        handler=_feishu_get_document_markdown,
        permission="allowlist",
        help_text="读取飞书云文档 Markdown 正文",
        toolbox=None,
    ),
    "feishu_list_drive_files": ToolDefinition(
        schema=_feishu_list_drive_schema,
        handler=_feishu_list_drive_files,
        permission="allowlist",
        help_text="列举飞书云盘文件夹内容",
        toolbox=None,
    ),
    "feishu_append_document_text": ToolDefinition(
        schema=_feishu_append_doc_schema,
        handler=_feishu_append_document_text,
        permission="allowlist",
        help_text="向飞书云文档追加纯文本段落",
        toolbox=None,
    ),
}

__all__ = ["feishu_im_tools", "FEISHU_IM_TOOL_NAMES"]
