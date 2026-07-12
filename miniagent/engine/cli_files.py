"""CLI file-marker ingestion into the session memory boundary."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from miniagent.engine.utils import detect_mime_from_magic
from miniagent.infrastructure.json_config import get_config
from miniagent.types.error_prefix import WARNING_PREFIX

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
            resolved = _resolve_marker_path(file_path, session_key, session_manager)
            if not os.path.isfile(resolved):
                if notify:
                    notify(f"{WARNING_PREFIX} 文件不存在: {file_path}\n", "ansiyellow")
                continue

            file_name = os.path.basename(resolved)
            file_size = os.path.getsize(resolved)
            try:
                def _read_header() -> bytes:
                    with open(resolved, "rb") as stream:
                        return stream.read(32)

                header = await asyncio.to_thread(_read_header)
                mime_type = detect_mime_from_magic(header) or "application/octet-stream"
            except Exception as error:
                _logger.debug("读取文件 MIME 失败 (%s): %s", resolved, error)
                mime_type = "application/octet-stream"

            if mime_type.startswith("image/"):
                file_type = "image"
            elif mime_type.startswith("text/"):
                file_type = "text"
            else:
                file_type = "binary"

            description = await asyncio.to_thread(
                _read_file_description,
                resolved,
                file_type,
            )
            if (
                file_type == "image"
                and runtime_ctx is not None
                and get_config("cli.file_vision_desc", True)
            ):
                try:
                    from miniagent.feishu.vision_desc import describe_image

                    client = getattr(runtime_ctx, "openai_client", None)
                    if client is not None:
                        description = await describe_image(
                            resolved,
                            client,
                            get_config("model.model", "gpt-4o-mini"),
                        )
                except Exception as error:
                    _logger.debug("图片描述生成失败 (%s): %s", resolved, error)

            from miniagent.memory.store import add_file_to_memory
            from miniagent.types.memory import FileMetadata

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
            try:
                await add_file_to_memory(session_key, metadata, runtime_ctx.memory.store)
            except Exception as error:
                _logger.warning("文件标记写入记忆失败 (%s): %s", file_path, error)
                if notify:
                    notify(
                        f"{WARNING_PREFIX} 无法保存文件到记忆: {file_name}\n",
                        "ansiyellow",
                    )
                continue

            files_info.append(
                {
                    "name": file_name,
                    "type": file_type,
                    "size": file_size,
                    "description": description[:100] if description else "",
                }
            )
            marker = f"@file:{file_path}" if at_path else f"file:{file_path}"
            type_label = {"image": "图片", "text": "文本文件", "binary": "文件"}.get(
                file_type, "文件"
            )
            max_description = 150 if file_type == "image" else 100
            if description:
                content_label = "图片内容" if file_type == "image" else "内容预览"
                replacement = (
                    f"[{type_label}: {file_name}]\n"
                    f"{content_label}：{description[:max_description]}"
                )
            else:
                replacement = f"[{type_label}: {file_name}]"
            user_input = user_input.replace(marker, replacement)

            if notify:
                size_value = file_size // 1024 if file_size >= 1024 else file_size
                size_label = f"{size_value}KB" if file_size >= 1024 else f"{size_value}B"
                notify(f"📎 已处理文件: {file_name} ({size_label})\n", "ansicyan")
                if description:
                    suffix = "..." if len(description) > 100 else ""
                    notify(
                        f"   内容摘要: {description[:100]}{suffix}\n",
                        "ansicyan",
                    )
        except Exception as error:
            _logger.warning("处理文件标记失败 (%s): %s", file_path, error)
            if notify:
                notify(f"{WARNING_PREFIX} 处理文件失败: {error}\n", "ansiyellow")

    return user_input, files_info


__all__ = ["FileNotify", "process_cli_file_markers"]
