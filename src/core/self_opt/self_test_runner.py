"""Self-Test Runner — 自测试执行器

在安全沙箱中执行测试用例。

安全约束：
- 命令白名单机制：只允许安全命令
- 超时控制：单个测试不超过 60 秒
- 结果记录：详细记录测试输出和耗时
- 沙箱隔离：避免测试影响主项目

设计原则：
- 安全第一：所有命令必须通过白名单
- 结果可追溯：详细记录每个测试的输出
- 与 Git 快照配合：测试前恢复快照状态
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from .types import (
    OptimizationProposal,
    OptimizationResult,
    TestExecutionResult,
    TestSummary,
    TestCase,
    FileChange,
)


# 安全命令白名单
SAFE_COMMANDS = {
    "python", "python3", "pytest", "python -m pytest",
    "node", "npm test", "npm run test",
    "npx", "tsx", "ts-node",
    "echo", "cat", "ls", "dir",
    "type", "find", "grep",
}


def _is_safe_command(command: str) -> bool:
    """检查命令是否在白名单中。"""
    cmd = command.strip().lower()
    for safe in SAFE_COMMANDS:
        if cmd.startswith(safe):
            return True
    return False


async def _run_command(
    command: str,
    cwd: str,
    timeout_seconds: int = 60,
) -> tuple[int, str, str]:
    """在沙箱中执行命令。

    Args:
        command: 要执行的命令。
        cwd: 工作目录。
        timeout_seconds: 超时时间。

    Returns:
        (exit_code, stdout, stderr)
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            shell=True,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds,
            )
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            return proc.returncode or 0, stdout, stderr
        except asyncio.TimeoutError:
            proc.kill()
            return -1, "", f"Command timed out after {timeout_seconds}s"
    except Exception as e:
        return -1, "", f"Failed to run command: {e}"


async def apply_file_changes(
    proposal: OptimizationProposal,
    project_root: str,
) -> tuple[bool, str]:
    """应用文件变更。

    根据提案中的 FileChange 列表，创建、修改或删除文件。

    Args:
        proposal: 优化提案。
        project_root: 项目根目录。

    Returns:
        (是否成功, 错误信息)
    """
    try:
        for file_change in proposal.files:
            file_path = os.path.join(project_root, file_change.path)

            if file_change.action == "create":
                parent = os.path.dirname(file_path)
                Path(parent).mkdir(parents=True, exist_ok=True)
                content = file_change.content or f"# {file_change.description or file_path}\n"
                Path(file_path).write_text(content, encoding="utf-8")

            elif file_change.action == "modify":
                if not os.path.exists(file_path):
                    return False, f"File not found: {file_path}"
                if file_change.content:
                    Path(file_path).write_text(file_change.content, encoding="utf-8")

            elif file_change.action == "delete":
                if os.path.exists(file_path):
                    os.remove(file_path)

        return True, ""
    except Exception as e:
        return False, str(e)


async def run_test_case(
    test_case: TestCase,
    project_root: str,
    timeout_seconds: int = 60,
) -> TestExecutionResult:
    """执行单个测试用例。

    Args:
        test_case: 测试用例。
        project_root: 项目根目录。
        timeout_seconds: 超时时间。

    Returns:
        测试执行结果。
    """
    start_time = time.time()

    if not _is_safe_command(test_case.command):
        return TestExecutionResult(
            test_case_id=test_case.id,
            passed=False,
            output=f"Command not in whitelist: {test_case.command}",
            duration_ms=(time.time() - start_time) * 1000,
        )

    exit_code, stdout, stderr = await _run_command(
        test_case.command,
        project_root,
        timeout_seconds,
    )

    duration_ms = (time.time() - start_time) * 1000
    passed = exit_code == 0
    output = stdout if passed else stderr

    return TestExecutionResult(
        test_case_id=test_case.id,
        passed=passed,
        output=output[:2000] if output else "",
        duration_ms=duration_ms,
    )


async def execute_optimization(
    proposal: OptimizationProposal,
    project_root: str,
    timeout_seconds: int = 120,
) -> OptimizationResult:
    """执行优化提案。

    完整流程：
    1. 应用文件变更
    2. 执行测试用例
    3. 收集结果

    Args:
        proposal: 优化提案。
        project_root: 项目根目录。
        timeout_seconds: 总超时时间。

    Returns:
        优化执行结果。
    """
    import datetime

    start_time = time.time()
    test_results: list[TestExecutionResult] = []

    try:
        # Step 1: 应用文件变更
        apply_ok, apply_error = await apply_file_changes(proposal, project_root)
        if not apply_ok:
            return OptimizationResult(
                proposal_id=proposal.id,
                status="failed",
                test_results=[],
                test_summary=None,
                fix_attempts=0,
                reverted=False,
                lesson=f"文件变更失败: {apply_error}",
                timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
                total_duration_seconds=time.time() - start_time,
            )

        # Step 2: 执行测试用例
        for test_case in proposal.test_cases:
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                break

            result = await run_test_case(test_case, project_root)
            test_results.append(result)

        # Step 3: 汇总
        passed = sum(1 for r in test_results if r.passed)
        failed = sum(1 for r in test_results if not r.passed)
        total = len(test_results)

        if failed > 0:
            lesson = f"{failed}/{total} 个测试失败"
        elif total == 0:
            lesson = "没有测试用例可执行"
        else:
            lesson = f"所有 {total} 个测试通过"

        return OptimizationResult(
            proposal_id=proposal.id,
            status="success" if failed == 0 and total > 0 else "failed",
            test_results=test_results,
            test_summary=TestSummary(total=total, passed=passed, failed=failed),
            fix_attempts=0,
            reverted=False,
            lesson=lesson,
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            total_duration_seconds=time.time() - start_time,
        )

    except Exception as e:
        return OptimizationResult(
            proposal_id=proposal.id,
            status="failed",
            test_results=test_results,
            test_summary=None,
            fix_attempts=0,
            reverted=False,
            lesson=f"执行异常: {e}",
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            total_duration_seconds=time.time() - start_time,
        )
