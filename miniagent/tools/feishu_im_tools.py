"""飞书 IM / 云盘内置工具（可选；由 ``MINIAGENT_FEISHU_TOOLS`` / ``MINIAGENT_FEISHU_TOOLS_AUTO`` 控制注册）。

云文档与多维表格请使用 ``feishu_doc`` / ``feishu_bitable``。
"""

from __future__ import annotations

import os
from typing import Any

from miniagent.feishu.folder_token_resolve import resolve_parent_folder_token
from miniagent.feishu.lark_client import config_from_env
from miniagent.feishu.receive_id import (
    default_receive_id_for_send,
    effective_receive_id_type,
)
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

FEISHU_IM_TOOL_NAMES = frozenset(
    {
        "feishu_send_workspace_file",
        "feishu_recall_message",
        "feishu_list_drive_files",
    }
)


def _resolve_under_workspace(workspace: str, rel: str) -> str:
    """将会话相对路径解析为实路径；越出 ``workspace`` 则抛 ``ValueError``。"""
    base = os.path.realpath(workspace)
    tail = (rel or "").strip().replace("\\", "/").lstrip("/")
    cand = os.path.realpath(os.path.join(base, tail))
    if cand != base and not cand.startswith(base + os.sep):
        raise ValueError("路径越出会话工作区")
    return cand


async def _feishu_send_workspace_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """将工作区内文件以 IM 文件/图片发到当前或指定会话。"""
    rel = str(args.get("relative_path") or "").strip()
    as_image = bool(args.get("as_image"))
    reply_to = str(args.get("reply_to_message_id") or "").strip() or None
    reply_in_thread = bool(args.get("reply_in_thread"))
    receive_id, recv_err = default_receive_id_for_send(args, ctx)
    receive_id_type = effective_receive_id_type(args, ctx)

    cfg = config_from_env()
    if cfg is None:
        return ToolResult(success=False, content="⚠️ 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET。")
    if recv_err:
        return ToolResult(success=False, content=f"⚠️ {recv_err}")
    if not receive_id:
        return ToolResult(success=False, content="⚠️ 缺少 receive_id。")
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
        return ToolResult(
            success=False, content="⚠️ 请安装 lark-oapi（pip install miniagent-python[feishu]）。"
        )

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
    """撤回机器人已发消息。"""
    _ = ctx
    mid = str(args.get("message_id") or "").strip()
    if not mid:
        return ToolResult(success=False, content="⚠️ 需要 message_id。")
    cfg = config_from_env()
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


async def _feishu_list_drive_files(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """列举云盘文件夹条目。"""
    _ = ctx
    folder_arg = str(args.get("folder_token") or "").strip()
    cfg = config_from_env()
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
        entries, next_tok, has_more = list_folder_files_page(
            cfg, folder_token=folder, page_token=page_token
        )
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


_feishu_send_workspace_file_schema = {
    "type": "function",
    "function": {
        "name": "feishu_send_workspace_file",
        "description": (
            "将当前 Agent 会话工作区根目录下的文件上传到飞书并以 file 或 image 消息发送；"
            "relative_path 为相对会话工作区路径（如 files/feishu_incoming/xxx.png）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "relative_path": {"type": "string"},
                "as_image": {"type": "boolean"},
                "reply_to_message_id": {"type": "string"},
                "reply_in_thread": {"type": "boolean"},
                "receive_id": {"type": "string"},
                "receive_id_type": {"type": "string"},
            },
            "required": ["relative_path"],
        },
    },
}

_feishu_recall_schema = {
    "type": "function",
    "function": {
        "name": "feishu_recall_message",
        "description": "撤回机器人在飞书已发送的一条消息。",
        "parameters": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
        },
    },
}

_feishu_list_drive_schema = {
    "type": "function",
    "function": {
        "name": "feishu_list_drive_files",
        "description": (
            "列举飞书云盘某文件夹下一页文件/子文件夹（只读）。"
            "folder_token 可为 token 或文件夹分享链接；可省略若已配置默认父目录。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "folder_token": {"type": "string"},
                "folders_only": {"type": "boolean"},
                "name_contains": {"type": "string"},
                "page_token": {"type": "string"},
            },
            "required": [],
        },
    },
}

feishu_im_tools: dict[str, ToolDefinition] = {
    "feishu_send_workspace_file": ToolDefinition(
        schema=_feishu_send_workspace_file_schema,
        handler=_feishu_send_workspace_file,
        permission="allowlist",
        help_text="上传会话工作区文件并发送到当前飞书会话",
        toolbox="feishu",
    ),
    "feishu_recall_message": ToolDefinition(
        schema=_feishu_recall_schema,
        handler=_feishu_recall_message,
        permission="allowlist",
        help_text="撤回机器人已发送的飞书消息",
        toolbox="feishu",
    ),
    "feishu_list_drive_files": ToolDefinition(
        schema=_feishu_list_drive_schema,
        handler=_feishu_list_drive_files,
        permission="allowlist",
        help_text="列举飞书云盘文件夹内容",
        toolbox="feishu",
    ),
}

__all__ = ["feishu_im_tools", "FEISHU_IM_TOOL_NAMES"]
