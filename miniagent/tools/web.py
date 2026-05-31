"""Mini Agent Python — 时间与可用性检查工具

- get_time: 返回指定时区的当前时间和日期信息
- check_app_availability: 检查二进制 / COM ProgID / 环境变量 / Python 包的可用性
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone
from typing import Any

from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

# ════════════════════════════════════════════════════════
# get_time
# ════════════════════════════════════════════════════════

_time_schema = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "获取当前时间和日期信息",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "时区名称（如 Asia/Shanghai），默认使用系统时区",
                },
            },
        },
    },
}


async def _time_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """``get_time``：返回指定时区当前本地时间与 UTC 偏移说明。"""
    from miniagent.infrastructure.timezone_config import process_timezone

    tz_name = str(args.get("timezone", "")).strip() or process_timezone()

    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
    except (ImportError, KeyError):
        if tz_name == "Asia/Shanghai":
            tz = timezone(timedelta(hours=8))
            now = datetime.now(tz)
        else:
            now = datetime.now(timezone.utc)
            tz_name = "UTC"

    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekdays[now.weekday()]
    formatted = f"{now.year}年{now.month}月{now.day}日{weekday} {now.strftime('%H:%M:%S')}"
    iso = now.isoformat()

    return ToolResult(success=True, content=f"当前时间 ({tz_name}): {formatted}\nISO: {iso}")


# ════════════════════════════════════════════════════════
# check_app_availability
# ════════════════════════════════════════════════════════

_app_avail_schema = {
    "type": "function",
    "function": {
        "name": "check_app_availability",
        "description": "检查指定类型的软件/依赖是否可用。支持四种检查类型：binary（命令行工具）、com（Windows COM ProgID）、env（环境变量）、python（Python 包）。供技能在执行前验证前置条件。",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["binary", "com", "env", "python"],
                    "description": "检查类型：binary=命令行工具（如 node, ffmpeg），com=Windows COM ProgID（如 Mathcad.Application），env=环境变量（如 OPENAI_API_KEY），python=Python 包名（如 numpy）",
                },
                "name": {
                    "type": "string",
                    "description": "检查目标的名称：binary 为可执行文件名，com 为 ProgID，env 为环境变量名，python 为包名",
                },
            },
            "required": ["type", "name"],
        },
    },
}


def _check_binary(name: str) -> dict[str, Any]:
    """检查命令行工具是否可用。"""
    path = shutil.which(name)
    if path:
        return {"available": True, "path": path}
    return {"available": False, "error": f"未找到可执行文件: {name}"}


def _check_com(name: str) -> dict[str, Any]:
    """检查 Windows COM ProgID 是否可用。"""
    if os.name != "nt":
        return {"available": False, "error": "COM 检查仅支持 Windows 平台"}
    try:
        import win32com.client

        app = win32com.client.Dispatch(name)
        info: dict[str, Any] = {"available": True, "progid": name}
        for attr in ("Version", "Path", "FullName"):
            try:
                val = getattr(app, attr, None)
                if val is not None:
                    info[attr.lower()] = str(val)
            except Exception:
                pass
        try:
            getattr(app, "Quit", lambda: None)()
        except Exception:
            pass
        return info
    except Exception as e:
        return {"available": False, "error": str(e)}


def _check_env(name: str) -> dict[str, Any]:
    """检查环境变量是否已设置。"""
    value = os.environ.get(name)
    if value:
        # 不泄露完整值，仅返回是否已设置
        return {"available": True, "set": True, "masked": value[:4] + "..." + value[-2:] if len(value) > 6 else "***"}
    return {"available": False, "error": f"环境变量未设置: {name}"}


def _check_python(name: str) -> dict[str, Any]:
    """检查 Python 包是否已安装。"""
    try:
        import importlib.metadata

        version = importlib.metadata.version(name)
        return {"available": True, "version": version}
    except importlib.metadata.PackageNotFoundError:
        try:
            # 尝试导入（有些包名与发行名不同，如 PIL -> pillow）
            importlib.import_module(name)
            return {"available": True, "version": "unknown"}
        except ImportError:
            return {"available": False, "error": f"Python 包未安装: {name}"}


_CHECKERS = {
    "binary": _check_binary,
    "com": _check_com,
    "env": _check_env,
    "python": _check_python,
}


async def _app_avail_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """``check_app_availability``：检查指定类型的软件/依赖是否可用。"""
    check_type = str(args.get("type", ""))
    name = str(args.get("name", "")).strip()

    if not name:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} name 参数不能为空")

    checker = _CHECKERS.get(check_type)
    if not checker:
        types_str = ", ".join(_CHECKERS.keys())
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 不支持的检查类型: {check_type}（支持: {types_str}）")

    result = checker(name)

    if result.get("available"):
        lines = [f"{SUCCESS_PREFIX} {check_type}: {name} 可用"]
        for key in ("path", "progid", "version", "set", "masked"):
            if key in result:
                label = {"path": "路径", "progid": "ProgID", "version": "版本", "set": "已设置", "masked": "值"}[key]
                lines.append(f"   {label}: {result[key]}")
        return ToolResult(success=True, content="\n".join(lines))
    else:
        error = result.get("error", "未知原因不可用")
        return ToolResult(success=False, content=f"{ERROR_PREFIX} {check_type}: {name} 不可用 — {error}")


web_tools: dict[str, ToolDefinition] = {
    "get_time": ToolDefinition(
        schema=_time_schema,
        handler=_time_handler,
        permission="sandbox",
        help_text="获取当前时间和日期",
        toolbox="core",
    ),
    "check_app_availability": ToolDefinition(
        schema=_app_avail_schema,
        handler=_app_avail_handler,
        permission="sandbox",
        help_text="检查 binary / com / env / python 依赖的可用性",
        toolbox="core",
    ),
}

__all__ = ["web_tools"]
