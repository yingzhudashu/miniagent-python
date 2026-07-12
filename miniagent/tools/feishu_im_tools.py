"""飞书 IM / 云盘内置工具（可选；由 ``MINIAGENT_FEISHU_TOOLS`` / ``MINIAGENT_FEISHU_TOOLS_AUTO`` 控制注册）。

云文档与多维表格请使用 ``feishu_doc`` / ``feishu_bitable``。

重构说明：
- 配置检查使用 miniagent/tools/feishu_utils.py 的共享函数
- 使用 ToolBuilder 简化工具定义
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from miniagent.feishu._utils import resolve_under_workspace
from miniagent.feishu.folder_token_resolve import resolve_parent_folder_token_async
from miniagent.feishu.receive_id import default_receive_id_for_send, effective_receive_id_type
from miniagent.tools.base import tool
from miniagent.tools.feishu_utils import check_feishu_config_and_lark_oapi
from miniagent.types.error_prefix import SUCCESS_PREFIX, WARNING_PREFIX
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

FEISHU_IM_TOOL_NAMES = frozenset({"feishu_send_workspace_file", "feishu_recall_message", "feishu_list_drive_files"})


# ════════════════════════════════════════════════════════
# Handlers
# ════════════════════════════════════════════════════════


async def _feishu_send_workspace_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """将工作区内文件以 IM 文件/图片发到当前或指定会话。"""
    rel = str(args.get("relative_path") or "").strip()
    as_image = bool(args.get("as_image"))
    reply_to = str(args.get("reply_to_message_id") or "").strip() or None
    reply_in_thread = bool(args.get("reply_in_thread"))
    receive_id, recv_err = default_receive_id_for_send(args, ctx)
    receive_id_type = effective_receive_id_type(args, ctx)

    cfg, cfg_err = check_feishu_config_and_lark_oapi()
    if cfg_err:
        return cfg_err
    assert cfg is not None
    if recv_err:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} {recv_err}")
    if not receive_id:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 缺少 receive_id。")

    ws = (ctx.cwd or "").strip()
    if not ws or not rel:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 缺少工作区路径或 relative_path。")
    try:
        path = resolve_under_workspace(ws, rel)
    except ValueError as e:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} {e}")
    if not await asyncio.to_thread(os.path.isfile, path):
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 文件不存在: {rel}")

    try:
        from miniagent.feishu.upload_io import (
            send_im_file_message,
            send_im_image_message,
            upload_im_file,
            upload_im_image,
        )
    except ImportError:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 请安装 lark-oapi（pip install miniagent-python[feishu]）。")

    try:
        data = await asyncio.to_thread(Path(path).read_bytes)
    except OSError as e:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 读取文件失败: {e}")

    try:
        if as_image:
            def _upload_image() -> tuple[bool, str | None]:
                image_key = upload_im_image(cfg, data)
                return send_im_image_message(
                    cfg,
                    receive_id,
                    image_key,
                    reply_to_message_id=reply_to,
                    reply_in_thread=reply_in_thread,
                    receive_id_type=receive_id_type,
                )

            ok, err = await asyncio.to_thread(_upload_image)
        else:
            name = os.path.basename(path)
            def _upload_file() -> tuple[bool, str | None]:
                file_key = upload_im_file(cfg, data, file_name=name)
                return send_im_file_message(
                    cfg,
                    receive_id,
                    file_key,
                    file_name=name,
                    reply_to_message_id=reply_to,
                    reply_in_thread=reply_in_thread,
                    receive_id_type=receive_id_type,
                )

            ok, err = await asyncio.to_thread(_upload_file)
    except Exception as e:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 上传或发送失败: {e}")
    if not ok:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 飞书发送失败: {err or 'unknown'}")
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已发送到当前飞书会话。")


async def _feishu_recall_message(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """撤回机器人已发消息。"""
    _ = ctx
    mid = str(args.get("message_id") or "").strip()
    if not mid:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 message_id。")

    cfg, cfg_err = check_feishu_config_and_lark_oapi()
    if cfg_err:
        return cfg_err
    assert cfg is not None

    from miniagent.feishu.upload_io import delete_im_message

    try:
        ok, err = await asyncio.to_thread(delete_im_message, cfg, mid)
    except Exception as e:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 撤回失败: {e}")
    if not ok:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 飞书删除消息 API 失败: {err or 'unknown'}")
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已请求撤回该消息。")


async def _feishu_list_drive_files(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """列举云盘文件夹条目。"""
    _ = ctx
    folder_arg = str(args.get("folder_token") or "").strip()
    cfg, cfg_err = check_feishu_config_and_lark_oapi()
    if cfg_err:
        return cfg_err
    assert cfg is not None

    folder, folder_err = await resolve_parent_folder_token_async(folder_arg, cfg=cfg)
    if folder_err or not folder:
        return ToolResult(success=False, content=folder_err or f"{WARNING_PREFIX} 缺少 folder_token。")

    folders_only = bool(args.get("folders_only"))
    name_sub = str(args.get("name_contains") or "").strip().lower()
    page_token = str(args.get("page_token") or "").strip() or None

    try:
        from miniagent.feishu.drive_client import list_folder_files_page
    except ImportError:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 请安装 lark-oapi。")

    try:
        entries, next_tok, has_more = await asyncio.to_thread(
            list_folder_files_page,
            cfg,
            folder_token=folder,
            page_token=page_token,
        )
    except Exception as exc:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 列举失败: {exc}")

    lines = ["| name | token | type |", "| --- | --- | --- |"]
    for entry in entries:
        if folders_only and str(entry.get("type") or "").lower() != "folder":
            continue
        if name_sub and name_sub not in str(entry.get("name") or "").lower():
            continue
        nm = str(entry.get("name") or "").replace("|", "\\|")
        tk = str(entry.get("token") or "").replace("|", "\\|")
        tp = str(entry.get("type") or "").replace("|", "\\|")
        lines.append(f"| {nm} | {tk} | {tp} |")

    tail = f"\n\nhas_more={has_more}"
    if next_tok:
        tail += f"\nnext_page_token: {next_tok}"
    return ToolResult(success=True, content="\n".join(lines) + tail)


# ════════════════════════════════════════════════════════
# Tool Definitions (使用 ToolBuilder)
# ════════════════════════════════════════════════════════

feishu_im_tools: dict[str, ToolDefinition] = {
    "feishu_send_workspace_file": tool("feishu_send_workspace_file", "将当前 Agent 会话工作区根目录下的文件上传到飞书并以 file 或 image 消息发送。")
        .param("relative_path", "string", "相对会话工作区路径")
        .optional("as_image", "boolean", "是否作为图片发送")
        .optional("reply_to_message_id", "string", "回复的消息 ID")
        .optional("reply_in_thread", "boolean", "是否回复在话题中")
        .optional("receive_id", "string", "接收者 ID")
        .optional("receive_id_type", "string", "接收者 ID 类型")
        .allowlist()
        .toolbox("feishu")
        .handler(_feishu_send_workspace_file)
        .build(),
    "feishu_recall_message": tool("feishu_recall_message", "撤回机器人在飞书已发送的一条消息。")
        .param("message_id", "string", "要撤回的消息 ID")
        .allowlist()
        .toolbox("feishu")
        .handler(_feishu_recall_message)
        .build(),
    "feishu_list_drive_files": tool("feishu_list_drive_files", "列举飞书云盘某文件夹下一页文件/子文件夹（只读）。")
        .optional("folder_token", "string", "文件夹 token 或分享链接")
        .optional("folders_only", "boolean", "仅列出文件夹")
        .optional("name_contains", "string", "名称过滤")
        .optional("page_token", "string", "分页 token")
        .allowlist()
        .toolbox("feishu")
        .handler(_feishu_list_drive_files)
        .build(),
}

__all__ = ["feishu_im_tools", "FEISHU_IM_TOOL_NAMES"]
