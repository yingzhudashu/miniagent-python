"""Mini Agent Python — Self-Optimization 工具 (Phase 5)

提供自我优化能力：
- self_inspect: 分析当前架构和代码质量
- external_research: 搜索外部先进架构和论文
- generate_proposal: 生成优化提案
- implement_change: 实施代码变更
- run_tests: 运行测试验证
- git_snapshot: Git 快照管理

生产环境可通过 ``MINIAGENT_SELF_OPT_TOOLS=0`` 关闭注册；说明见 ``docs/SELF_OPT.md``。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from miniagent.types.tool import ToolDefinition, ToolContext, ToolResult


# ─── 风险评估 ────────────────────────────────────────────

def _assess_risk(type_: str, target: str) -> str:
    """评估优化操作的风险等级。

    根据操作类型和目标的关键词匹配，返回 risk level：
    - destructive: 删除核心文件、覆盖配置等破坏性操作
    - high: 修改核心模块（agent/planner/registry）
    - medium: 修改工具逻辑、添加依赖
    - low: 其他低风险操作
    """
    lower = f"{type_} {target}".lower()
    destructive = ["delete", "remove core", "overwrite config", ".env"]
    high = ["modify core", "refactor agent", "modify planner", "change registry"]
    medium = ["modify tool", "add dependency", "refactor"]

    for k in destructive:
        if k in lower:
            return "destructive"
    for k in high:
        if k in lower:
            return "high"
    for k in medium:
        if k in lower:
            return "medium"
    return "low"


# ─── Git 辅助函数 ────────────────────────────────────────

async def _run_git(args: list[str], cwd: str) -> tuple[int, str]:
    """执行 git 命令并返回 (exit_code, output)。

    使用 asyncio.create_subprocess_exec 异步执行，避免阻塞事件循环。
    stdout 和 stderr 合并为单一输出字符串。
    """
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    out = (stdout or b"").decode("utf-8", errors="replace").strip()
    err = (stderr or b"").decode("utf-8", errors="replace").strip()
    return proc.returncode or 0, out or err


# ════════════════════════════════════════════════════════
# self_inspect
# ════════════════════════════════════════════════════════


def _resolve_self_inspect_root(args: dict[str, Any], cwd: str) -> str:
    """扫描根目录：优先显式参数，否则存在 ``cwd/miniagent`` 则用其，否则 ``cwd``。"""
    raw = (
        str(args.get("packageRoot") or "").strip()
        or str(args.get("codeDir") or "").strip()
        or str(args.get("srcDir") or "").strip()
    )
    if raw:
        return raw
    mini = os.path.join(cwd, "miniagent")
    if os.path.isdir(mini):
        return mini
    return cwd


_self_inspect_schema = {
    "type": "function",
    "function": {
        "name": "self_inspect",
        "description": "分析当前 Agent 的架构完整性、代码质量和痛点",
        "parameters": {
            "type": "object",
            "properties": {
                "packageRoot": {
                    "type": "string",
                    "description": "要扫描的 Python 包根目录（推荐）",
                },
                "codeDir": {
                    "type": "string",
                    "description": "与 packageRoot 相同含义的别名",
                },
                "srcDir": {
                    "type": "string",
                    "description": "历史别名，等同于 packageRoot / codeDir",
                },
                "detailed": {"type": "boolean", "description": "是否输出详细报告"},
            },
        },
    },
}


async def _self_inspect_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """分析当前 Agent 的架构完整性和代码质量。

    通过静态扫描 Python 文件，统计行数、导入数和导出情况，
    生成项目概览报告，帮助识别大型模块和潜在架构问题。

    Args:
        args: 可选 ``packageRoot`` / ``codeDir`` / ``srcDir``（三者等价，指定扫描根目录；
            未指定时：若存在 ``ctx.cwd/miniagent`` 则扫描该目录，否则扫描 ``ctx.cwd``）。
            ``detailed``：是否输出详细报告。
        ctx: 工具执行上下文

    Returns:
        ToolResult: 自我检视报告，包含文件统计和模块概览
    """
    code_root = _resolve_self_inspect_root(args, ctx.cwd)
    detailed = bool(args.get("detailed", False))

    if not os.path.isdir(code_root):
        return ToolResult(success=False, content=f"错误: 代码目录不存在: {code_root}")

    # 简单的代码分析
    py_files: list[dict[str, Any]] = []
    total_lines = 0

    for root, _, files in os.walk(code_root):
        for f in files:
            if not f.endswith(".py"):
                continue
            fp = os.path.join(root, f)
            try:
                content = Path(fp).read_text(encoding="utf-8")
                lines = content.count("\n") + 1
                total_lines += lines
                imports = len([ln for ln in content.split("\n") if ln.strip().startswith(("import ", "from "))])
                exports = content.count("__all__")
                py_files.append({
                    "path": os.path.relpath(fp, code_root),
                    "lines": lines,
                    "imports": imports,
                    "has_exports": exports > 0,
                })
            except Exception:
                pass

    lines_out = [
        "══════════════════════════════════════════",
        "🔍 Self-Inspection Report",
        "══════════════════════════════════════════",
        f"📦 Python 文件数: {len(py_files)}",
        f"📊 总代码行数: {total_lines}",
        "",
        "📁 模块概览:",
    ]

    for m in sorted(py_files, key=lambda x: -x["lines"]):
        lines_out.append(f"  📄 {m['path']} ({m['lines']} 行, {m['imports']} imports)")

    if detailed:
        lines_out.append("\n📋 详细分析:")
        for m in py_files:
            lines_out.append(f"\n  {m['path']}:")
            lines_out.append(f"    行数: {m['lines']}")
            lines_out.append(f"    导入数: {m['imports']}")
            lines_out.append(f"    有 __all__: {'✅' if m['has_exports'] else '❌'}")

    return ToolResult(success=True, content="\n".join(lines_out))


# ════════════════════════════════════════════════════════
# generate_proposal
# ════════════════════════════════════════════════════════

_generate_proposal_schema = {
    "type": "function",
    "function": {
        "name": "generate_proposal",
        "description": "生成优化提案（含测试用例），不会自动执行",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "优化目标"},
                "type": {"type": "string", "enum": ["add", "remove", "modify", "refactor"]},
                "description": {"type": "string", "description": "改动说明"},
                "rationale": {"type": "string", "description": "优化依据"},
            },
            "required": ["target", "description"],
        },
    },
}


async def _generate_proposal_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """生成优化提案（含测试用例），不会自动执行。

    根据优化目标、类型和描述生成结构化提案，自动评估风险等级。
    提案生成后需通过 implement_change 工具执行。

    Args:
        args: 包含 target（优化目标）、type（操作类型：add/remove/modify/refactor）、
              description（改动说明）、rationale（可选，优化依据）
        _ctx: 工具执行上下文（此工具不使用）

    Returns:
        ToolResult: 格式化的提案报告，包含 ID、类型、风险等级等信息
    """
    target = str(args.get("target", ""))
    type_ = str(args.get("type", "add"))
    description = str(args.get("description", ""))
    rationale = str(args.get("rationale", "基于架构分析"))
    risk = _assess_risk(type_, target)
    proposal_id = f"prop-{int(__import__('time').time())}"

    lines = [
        "══════════════════════════════════════════",
        "📋 Optimization Proposal",
        "══════════════════════════════════════════",
        f"🆔 ID: {proposal_id}",
        f"📌 类型: {type_}",
        f"⚠️ 风险等级: {risk}",
        f"🎯 目标: {target}",
        f"📝 说明: {description}",
        f"📖 依据: {rationale}",
        "",
        "⚠️ 此提案尚未执行。使用 implement_change 工具实施。",
    ]

    return ToolResult(success=True, content="\n".join(lines))


# ════════════════════════════════════════════════════════
# run_tests
# ════════════════════════════════════════════════════════

_run_tests_schema = {
    "type": "function",
    "function": {
        "name": "run_tests",
        "description": "运行测试验证变更",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "测试命令（默认 pytest）"},
                "cwd": {"type": "string", "description": "工作目录"},
            },
        },
    },
}


async def _run_tests_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """运行测试验证变更。

    默认使用 pytest，支持自定义测试命令。120 秒超时保护，
    防止测试陷入死循环或长时间挂起。

    Args:
        args: 包含 command（可选，测试命令，默认 'python -m pytest'）、cwd（可选，工作目录）
        ctx: 工具执行上下文

    Returns:
        ToolResult: 测试输出和 exit code；超时或异常时返回错误信息
    """
    command = str(args.get("command", "python -m pytest"))
    cwd = str(args.get("cwd", "")) or ctx.cwd

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        out = (stdout or b"").decode("utf-8", errors="replace").strip()
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        code = proc.returncode or 0

        content = out or err or "(无输出)"
        content += f"\n\n[exit code: {code}]"

        return ToolResult(success=(code == 0), content=content)
    except asyncio.TimeoutError:
        return ToolResult(success=False, content="❌ 测试执行超时 (120s)")
    except Exception as e:
        return ToolResult(success=False, content=f"❌ 测试执行失败: {e}")


# ════════════════════════════════════════════════════════
# git_snapshot
# ════════════════════════════════════════════════════════

_git_snapshot_schema = {
    "type": "function",
    "function": {
        "name": "git_snapshot",
        "description": "Git 快照管理（创建/列出/回滚）",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "list", "revert"]},
                "message": {"type": "string", "description": "commit message"},
                "commitHash": {"type": "string", "description": "要回滚到的 commit hash"},
            },
            "required": ["action"],
        },
    },
}


async def _git_snapshot_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Git 快照管理（创建/列出/回滚）。

    此工具标记为 require-confirm 权限，因为回滚操作会覆盖工作区。
    create 操作先 add -A 再 commit，确保所有变更被记录。

    Args:
        args: 包含 action（必需：create/list/revert）、message（create 时的 commit message）、
              commitHash（revert 时的目标 commit hash）
        ctx: 工具执行上下文，cwd 作为项目根目录

    Returns:
        ToolResult: 操作结果 — create 返回 commit hash，list 返回最近 10 条历史，revert 返回成功/失败
    """
    action = str(args.get("action", "create"))
    project_root = ctx.cwd

    try:
        if action == "create":
            message = str(args.get("message", "self-opt: snapshot"))
            await _run_git(["add", "-A"], project_root)
            code, out = await _run_git(["commit", "-m", message], project_root)
            if code != 0:
                return ToolResult(success=True, content="无变更需要提交")
            _, hash_out = await _run_git(["log", "-1", "--format=%H"], project_root)
            return ToolResult(success=True, content=f"✅ Git 快照已创建: {hash_out}")

        if action == "list":
            _, out = await _run_git(["log", "-10", "--format=%h %s (%cr)"], project_root)
            return ToolResult(success=True, content=f"Git 历史:\n{out}")

        if action == "revert":
            commit_hash = str(args.get("commitHash", "HEAD"))
            code, out = await _run_git(["reset", "--hard", commit_hash], project_root)
            if code == 0:
                return ToolResult(success=True, content=f"✅ 已回滚到 {commit_hash}")
            return ToolResult(success=False, content=f"回滚失败: {out}")

        return ToolResult(success=False, content=f"未知操作: {action}")

    except Exception as e:
        return ToolResult(success=False, content=f"Git 操作失败: {e}")


# ─── 导出 ────────────────────────────────────────────────

self_opt_tools: dict[str, ToolDefinition] = {
    "self_inspect": ToolDefinition(
        schema=_self_inspect_schema,
        handler=_self_inspect_handler,
        permission="sandbox",
        help_text="分析当前 Agent 架构和代码质量",
        toolbox="self_optimization",
    ),
    "generate_proposal": ToolDefinition(
        schema=_generate_proposal_schema,
        handler=_generate_proposal_handler,
        permission="sandbox",
        help_text="生成优化提案",
        toolbox="self_optimization",
    ),
    "run_tests": ToolDefinition(
        schema=_run_tests_schema,
        handler=_run_tests_handler,
        permission="allowlist",
        help_text="运行测试验证",
        toolbox="self_optimization",
    ),
    "git_snapshot": ToolDefinition(
        schema=_git_snapshot_schema,
        handler=_git_snapshot_handler,
        permission="require-confirm",
        help_text="Git 快照管理",
        toolbox="self_optimization",
    ),
}

__all__ = ["self_opt_tools"]
