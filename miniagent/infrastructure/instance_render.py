"""多实例注册信息的 Markdown 与终端文本渲染。"""

from __future__ import annotations

import os
from typing import Any


def _meta_project_dir(instance: dict[str, Any]) -> str:
    """从兼容元数据字段读取项目目录。"""
    return str(instance.get("project_dir") or instance.get("cwd") or "?")


def _short_state_dir_label(state_dir: str, *, canonical: str | None = None) -> str:
    """生成状态目录的稳定短标签。"""
    normalized = os.path.normpath(state_dir)
    if canonical and normalized == os.path.normpath(canonical):
        return "canonical"
    base = os.path.basename(normalized) or normalized
    parent = os.path.basename(os.path.dirname(normalized))
    return f"{parent}/{base}" if parent and parent not in (".", "..") else base


def _markdown_cell(text: str) -> str:
    """将单元格压成单行并转义 GFM 分隔符。"""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("|", "\\|").replace("\n", " ").strip()


def _short_project_dir_label(project_dir: str) -> str:
    """生成表格用短项目目录标签。"""
    normalized = os.path.normpath(project_dir)
    base = os.path.basename(normalized) or normalized
    parent = os.path.basename(os.path.dirname(normalized))
    return f"{parent}/{base}" if parent and parent not in (".", "..") else base


def _workspace_label(instance: dict[str, Any]) -> str:
    """生成 workspace 标签，优先使用 project_key。"""
    project_key = instance.get("project_key")
    if project_key:
        return f"projects/{project_key}"
    state_dir = instance.get("project_state_dir")
    if not state_dir:
        return "?"
    normalized = os.path.normpath(str(state_dir))
    base = os.path.basename(normalized)
    if os.path.basename(os.path.dirname(normalized)) == "projects" and base:
        return f"projects/{base}"
    return _short_state_dir_label(normalized)


def format_instances_markdown(instances: list[dict[str, Any]]) -> str:
    """把实例列表渲染为飞书友好的 GFM 表格。"""
    from miniagent.infrastructure.paths import resolve_registry_state_dir

    registry = resolve_registry_state_dir()
    if not instances:
        return f"📭 暂无运行实例\n\n注册表: `{registry}`"
    multi_root = len({str(item.get("state_dir", registry)) for item in instances}) > 1
    lines = [
        "## 运行实例", "", f"注册表: `{registry}`", "", "> cli=仅 CLI，both=CLI+飞书", "",
        "| ID | PID | 模式 | 项目目录 | Workspace | 启动时间 | 会话数 | 主机 |"
        + (" 状态目录 |" if multi_root else "") + " 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |"
        + (" --- |" if multi_root else "") + " --- |",
    ]
    current_pid = os.getpid()
    for item in instances:
        row = (
            f"| {item['instance_id']} | {item['pid']} | {_markdown_cell(str(item.get('mode', '?')))} | "
            f"{_markdown_cell(_short_project_dir_label(_meta_project_dir(item)))} | "
            f"{_markdown_cell(_workspace_label(item))} | "
            f"{_markdown_cell(str(item.get('start_time', '?'))[:19])} | "
            f"{len(item.get('active_sessions', []))} | {_markdown_cell(str(item.get('hostname', '?')))} |"
        )
        if multi_root:
            label = _short_state_dir_label(str(item.get("state_dir", registry)), canonical=registry)
            row += f" {_markdown_cell(label)} |"
        marker = "当前" if item["pid"] == current_pid else ""
        lines.append(row + f" {_markdown_cell(marker)} |")
    return "\n".join(lines) + "\n"


def format_instances_table(instances: list[dict[str, Any]]) -> str:
    """把实例列表渲染为等宽终端文本。"""
    from miniagent.infrastructure.paths import resolve_registry_state_dir

    registry = resolve_registry_state_dir()
    if not instances:
        return f"📭 暂无运行实例\n\n  注册表: {registry}\n"
    multi_root = len({str(item.get("state_dir", registry)) for item in instances}) > 1
    lines = ["📋 运行实例列表:\n", f"  注册表: {registry}\n"]
    if multi_root:
        lines.extend([
            f"  {'ID':<6} {'PID':<8} {'模式':<8} {'项目目录':<18} {'Workspace':<22} "
            f"{'启动时间':<22} {'会话数':<6} {'主机':<12} {'状态目录'}",
            "  " + "-" * 128,
        ])
    else:
        lines.extend([
            f"  {'ID':<6} {'PID':<8} {'模式':<8} {'项目目录':<18} {'Workspace':<22} "
            f"{'启动时间':<22} {'会话数':<6} {'主机'}",
            "  " + "-" * 108,
        ])
    lines.append("  （cli=仅 CLI，both=CLI+飞书）")
    current_pid = os.getpid()
    for item in instances:
        marker = " ← 当前" if item["pid"] == current_pid else ""
        prefix = (
            f"  #{item['instance_id']:<5} {item['pid']:<8} {item.get('mode', '?'):<8} "
            f"{_short_project_dir_label(_meta_project_dir(item)):<18} {_workspace_label(item):<22} "
            f"{str(item.get('start_time', '?'))[:19]:<22} "
            f"{len(item.get('active_sessions', [])):<6}"
        )
        if multi_root:
            label = _short_state_dir_label(str(item.get("state_dir", registry)), canonical=registry)
            lines.append(f"{prefix} {item.get('hostname', '?'):<12} {label}{marker}")
        else:
            lines.append(f"{prefix} {item.get('hostname', '?')}{marker}")
    lines.append("")
    return "\n".join(lines)


__all__ = ["format_instances_markdown", "format_instances_table"]
