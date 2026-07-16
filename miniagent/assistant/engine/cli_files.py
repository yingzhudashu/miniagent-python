"""CLI file-marker ingestion into the session memory boundary."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from miniagent.agent.types.error_prefix import WARNING_PREFIX
from miniagent.assistant.engine.utils import detect_mime_from_magic
from miniagent.assistant.infrastructure.json_config import get_config

_logger = logging.getLogger(__name__)
_FILE_MARKER_PATTERN = re.compile(r"@file:([^\s]+)|file:([^\s]+)")
FileNotify = Callable[[str, str], None]


def _resolve_marker_path(
    file_path: str,
    session_key: str,
    session_manager: Any,
) -> str:
    if os.path.isabs(file_path):
        return file_path
    if os.path.exists(file_path):
        return file_path
    if session_manager is not None:
        session = session_manager.get(session_key)
        workspace_path = getattr(session, "workspace_path", "") if session else ""
        candidate = os.path.join(workspace_path, file_path) if workspace_path else ""
        if candidate and os.path.exists(candidate):
            return candidate
    return file_path


def _read_file_description(path: str, file_type: str) -> str:
    if file_type != "text":
        return ""
    try:
        with open(path, encoding="utf-8", errors="ignore") as stream:
            return stream.read(500)[:200]
    except Exception as error:
        _logger.debug("文本文件预览失败 (%s): %s", path, error)
        return ""


async def _inspect_cli_file(path: str) -> tuple[str, str, int]:
    """读取文件名、MIME 类型和大小；MIME 失败时安全降级为二进制。"""
    file_name = os.path.basename(path)
    file_size = os.path.getsize(path)

    def read_header() -> bytes:
        with open(path, "rb") as stream:
            return stream.read(32)

    try:
        header = await asyncio.to_thread(read_header)
        mime_type = detect_mime_from_magic(header) or "application/octet-stream"
    except (OSError, ValueError) as error:
        _logger.debug("读取文件 MIME 失败 (%s): %s", path, error, exc_info=True)
        mime_type = "application/octet-stream"
    if mime_type.startswith("image/"):
        return file_name, mime_type, file_size
    if mime_type.startswith("text/"):
        return file_name, mime_type, file_size
    return file_name, mime_type, file_size


def _file_type_from_mime(mime_type: str) -> str:
    """把 MIME 类型映射为提示与记忆使用的三类文件类型。"""
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("text/"):
        return "text"
    return "binary"


async def _describe_cli_file(path: str, file_type: str, runtime_ctx: Any) -> str:
    """生成文本预览或可选图片描述；外部模型失败时返回已有预览。"""
    description = await asyncio.to_thread(_read_file_description, path, file_type)
    if file_type != "image" or runtime_ctx is None or not get_config("cli.file_vision_desc", True):
        return description
    try:
        from miniagent.assistant.feishu.vision_desc import describe_image

        client = getattr(
            runtime_ctx, "llm_client", getattr(runtime_ctx, "llm_gateway", None)
        )
        if client is not None:
            return await describe_image(
                path,
                client,
            )
    except Exception as error:
        _logger.debug("图片描述生成失败 (%s): %s", path, error, exc_info=True)
    return description


async def _remember_cli_file(
    *,
    session_key: str,
    file_path: str,
    resolved: str,
    file_name: str,
    file_size: int,
    mime_type: str,
    file_type: str,
    description: str,
    runtime_ctx: Any,
) -> None:
    """构建文件元数据并写入显式注入的记忆存储。"""
    from miniagent.agent.types.memory import FileMetadata
    from miniagent.assistant.memory.store import add_file_to_memory

    relative_path = file_path if not os.path.isabs(file_path) else file_name
    metadata = FileMetadata(
        name=file_name,
        path=relative_path,
        size=file_size,
        mime_type=mime_type,
        type=file_type,
        description=description,
        timestamp=datetime.now(timezone.utc).isoformat(),
        source="cli",
    )
    await add_file_to_memory(session_key, metadata, runtime_ctx.memory.store)


def _file_marker_replacement(file_name: str, file_type: str, description: str) -> str:
    """生成注入用户输入的紧凑文件引用文本。"""
    type_label = {"image": "图片", "text": "文本文件", "binary": "文件"}.get(file_type, "文件")
    if not description:
        return f"[{type_label}: {file_name}]"
    max_description = 150 if file_type == "image" else 100
    content_label = "图片内容" if file_type == "image" else "内容预览"
    return f"[{type_label}: {file_name}]\n{content_label}：{description[:max_description]}"


def _notify_processed_file(
    notify: FileNotify | None,
    *,
    file_name: str,
    file_size: int,
    description: str,
) -> None:
    """向终端报告文件处理结果，不参与核心数据写入。"""
    if notify is None:
        return
    size_value = file_size // 1024 if file_size >= 1024 else file_size
    size_label = f"{size_value}KB" if file_size >= 1024 else f"{size_value}B"
    notify(f"📎 已处理文件: {file_name} ({size_label})\n", "ansicyan")
    if description:
        suffix = "..." if len(description) > 100 else ""
        notify(f"   内容摘要: {description[:100]}{suffix}\n", "ansicyan")


async def _process_cli_file_marker(
    *,
    file_path: str,
    session_key: str,
    session_manager: Any,
    runtime_ctx: Any,
    notify: FileNotify | None,
) -> tuple[str, dict[str, Any]] | None:
    """处理一个文件标记，成功时返回提示替换文本和公开摘要。"""
    resolved = _resolve_marker_path(file_path, session_key, session_manager)
    if not os.path.isfile(resolved):
        if notify:
            notify(f"{WARNING_PREFIX} 文件不存在: {file_path}\n", "ansiyellow")
        return None
    file_name, mime_type, file_size = await _inspect_cli_file(resolved)
    file_type = _file_type_from_mime(mime_type)
    description = await _describe_cli_file(resolved, file_type, runtime_ctx)
    try:
        await _remember_cli_file(
            session_key=session_key,
            file_path=file_path,
            resolved=resolved,
            file_name=file_name,
            file_size=file_size,
            mime_type=mime_type,
            file_type=file_type,
            description=description,
            runtime_ctx=runtime_ctx,
        )
    except Exception as error:
        _logger.warning("文件标记写入记忆失败 (%s): %s", file_path, error)
        if notify:
            notify(f"{WARNING_PREFIX} 无法保存文件到记忆: {file_name}\n", "ansiyellow")
        return None
    _notify_processed_file(
        notify,
        file_name=file_name,
        file_size=file_size,
        description=description,
    )
    return (
        _file_marker_replacement(file_name, file_type, description),
        {
            "name": file_name,
            "type": file_type,
            "size": file_size,
            "description": description[:100] if description else "",
        },
    )


async def process_cli_file_markers(
    user_input: str,
    session_key: str,
    session_manager: Any,
    runtime_ctx: Any,
    *,
    notify: FileNotify | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Resolve ``@file:``/``file:`` markers, persist metadata and rewrite input."""
    files_info: list[dict[str, Any]] = []
    matches = _FILE_MARKER_PATTERN.findall(user_input)
    if not matches:
        return user_input, files_info

    for at_path, plain_path in matches:
        file_path = at_path or plain_path
        if not file_path:
            continue
        try:
            processed = await _process_cli_file_marker(
                file_path=file_path,
                session_key=session_key,
                session_manager=session_manager,
                runtime_ctx=runtime_ctx,
                notify=notify,
            )
            if processed is None:
                continue
            replacement, summary = processed
            files_info.append(summary)
            marker = f"@file:{file_path}" if at_path else f"file:{file_path}"
            user_input = user_input.replace(marker, replacement)
        except Exception as error:
            _logger.warning("处理文件标记失败 (%s): %s", file_path, error)
            if notify:
                notify(f"{WARNING_PREFIX} 处理文件失败: {error}\n", "ansiyellow")

    return user_input, files_info


__all__ = ["FileNotify", "process_cli_file_markers"]
