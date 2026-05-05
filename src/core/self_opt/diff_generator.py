"""Diff Generator — 差异生成器

使用 LLM 生成修复补丁，并验证补丁有效性。

工作流程：
1. 接收错误信息和当前代码
2. 调用 LLM 生成修复补丁
3. 验证补丁语法正确性
4. 返回可应用的差异

设计原则：
- 使用 openai SDK 异步调用
- 补丁验证：确保语法正确
- 安全的补丁应用：先验证再应用
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI


@dataclass
class FixDiff:
    """修复差异。"""
    proposal_id: str
    file_path: str
    original_content: str
    new_content: str
    description: str
    validation_passed: bool = False
    validation_output: str = ""


def _extract_code_block(text: str) -> str | None:
    """从 LLM 响应中提取代码块。"""
    # 尝试提取 ```python ... ``` 或 ``` ... ```
    import re
    match = re.search(r"```(?:python)?\n(.*?)\n```", text, re.DOTALL)
    if match:
        return match.group(1)
    # 如果没有代码块标记，返回整个文本
    return text.strip() if text.strip() else None


def _validate_python_syntax(code: str) -> tuple[bool, str]:
    """验证 Python 代码语法。"""
    try:
        compile(code, "<string>", "exec")
        return True, "Syntax OK"
    except SyntaxError as e:
        return False, f"Syntax error: {e}"


async def generate_fix_diff(
    proposal_id: str,
    file_path: str,
    original_content: str,
    error_message: str,
    model: str = "gpt-4o-mini",
    api_key: str | None = None,
    base_url: str | None = None,
) -> FixDiff | None:
    """使用 LLM 生成修复补丁。

    Args:
        proposal_id: 关联的提案ID。
        file_path: 要修复的文件路径。
        original_content: 原始文件内容。
        error_message: 错误信息。
        model: LLM 模型名称。
        api_key: OpenAI API 密钥（可选，从环境变量读取）。
        base_url: OpenAI API 基础 URL（可选）。

    Returns:
        修复差异对象，如果生成失败则返回 None。
    """
    api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("[diff-generator] OPENAI_API_KEY not set, skipping LLM fix")
        return None

    try:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or os.environ.get("OPENAI_BASE_URL"),
        )

        system_prompt = (
            "You are a Python code fixer. Given a file content and an error message, "
            "fix the code and return ONLY the complete fixed file content. "
            "Do not explain, do not add markdown formatting unless necessary. "
            "Return the full file content, not just the changes."
        )

        user_prompt = (
            f"File: {file_path}\n\n"
            f"Error: {error_message}\n\n"
            f"Current content:\n{original_content}\n\n"
            f"Return the fixed file content."
        )

        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=4000,
        )

        fixed_content = response.choices[0].message.content or ""
        extracted = _extract_code_block(fixed_content)
        new_content = extracted if extracted else fixed_content

        # 语法验证
        valid, validation_msg = _validate_python_syntax(new_content)

        return FixDiff(
            proposal_id=proposal_id,
            file_path=file_path,
            original_content=original_content,
            new_content=new_content,
            description=f"Auto-fix for: {error_message[:100]}",
            validation_passed=valid,
            validation_output=validation_msg,
        )

    except Exception as e:
        print(f"[diff-generator] LLM fix failed: {e}")
        return None


async def apply_diff(
    diff: FixDiff,
    project_root: str,
) -> tuple[bool, str]:
    """应用修复补丁到文件。

    Args:
        diff: 修复差异对象。
        project_root: 项目根目录。

    Returns:
        (是否成功, 错误信息)
    """
    try:
        full_path = os.path.join(project_root, diff.file_path)

        # 确保父目录存在
        Path(os.path.dirname(full_path)).mkdir(parents=True, exist_ok=True)

        # 写入新内容
        Path(full_path).write_text(diff.new_content, encoding="utf-8")

        return True, ""
    except Exception as e:
        return False, str(e)


async def generate_and_apply_fix(
    proposal_id: str,
    file_path: str,
    error_message: str,
    project_root: str,
    model: str = "gpt-4o-mini",
    api_key: str | None = None,
) -> tuple[bool, FixDiff | None]:
    """生成并应用修复补丁（便捷函数）。

    Args:
        proposal_id: 提案ID。
        file_path: 文件路径（相对于 project_root）。
        error_message: 错误信息。
        project_root: 项目根目录。
        model: LLM 模型。
        api_key: API 密钥。

    Returns:
        (是否成功, 修复差异对象)
    """
    full_path = os.path.join(project_root, file_path)

    # 读取原始内容
    if not os.path.exists(full_path):
        return False, None

    original = Path(full_path).read_text(encoding="utf-8")

    # 生成修复
    diff = await generate_fix_diff(
        proposal_id=proposal_id,
        file_path=file_path,
        original_content=original,
        error_message=error_message,
        model=model,
        api_key=api_key,
    )

    if diff is None:
        return False, None

    # 验证通过才应用
    if not diff.validation_passed:
        print(f"[diff-generator] Fix validation failed: {diff.validation_output}")
        return False, diff

    # 应用修复
    success, error = await apply_diff(diff, project_root)
    if not success:
        print(f"[diff-generator] Failed to apply fix: {error}")
        return False, diff

    return True, diff
