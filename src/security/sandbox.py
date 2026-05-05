"""Mini Agent Python — 路径沙箱

确保文件操作只在允许的目录范围内执行，防止越权访问。

安全威胁模型：
假设 LLM 生成工具调用时可能被"注入"（prompt injection），
或者用户不小心输入了 `read_file(path="/etc/shadow")`。
沙箱确保即使发生了这些情况，也不会读取或修改工作区外的文件。

工作原理：
1. 将用户输入的路径解析为绝对路径
2. 遍历允许的目录列表，检查解析后的路径是否在某个目录内
3. 如果不在任何允许的范围，抛出错误

边界情况处理：
- 符号链接（symlink）：os.path.realpath() 会解析到真实路径，防止 symlink 逃逸
- 相对路径（如 "../../etc/passwd"）：解析后会被 os.path.abspath() 转为绝对路径，
  然后检查是否在允许范围内
- 空路径：os.path.abspath("") 返回 cwd，需要在 allowed_dirs 中包含 cwd
"""

from __future__ import annotations

import os


def resolve_sandbox_path(input_path: str, allowed_dirs: list[str]) -> str:
    """解析并验证路径是否在允许的目录范围内

    安全检查流程：
    1. 使用 os.path.abspath() 将输入路径转为绝对路径
       （这会自动处理相对路径、. 和 .. 等）
    2. 使用 os.path.realpath() 解析符号链接，防止 symlink 逃逸
    3. 遍历 allowed_dirs，检查 resolved 路径是否与某个目录匹配
    4. 匹配规则：路径等于目录本身，或以 "目录 + 分隔符" 开头

    Args:
        input_path: 用户提供的文件路径（可以是相对路径或绝对路径）
        allowed_dirs: 允许访问的目录列表（绝对路径）

    Returns:
        解析后的绝对路径

    Raises:
        ValueError: 如果路径超出允许的范围

    Example:
        # 通过
        resolve_sandbox_path("src/index.ts", ["/workspace"])
        # → "/workspace/src/index.ts"

        # 拒绝
        resolve_sandbox_path("/etc/passwd", ["/workspace"])
        # → ValueError: 路径 "/etc/passwd" 超出允许的范围: /workspace

        # 拒绝（相对路径逃逸尝试）
        resolve_sandbox_path("../../etc/passwd", ["/workspace"])
        # → 如果解析后不在 /workspace 下 → ValueError
    """
    # 将输入路径解析为绝对路径（处理相对路径、. 和 ..）
    resolved = os.path.realpath(os.path.abspath(input_path))

    # 遍历允许的目录，检查解析后的路径是否在某个目录范围内
    for dir_path in allowed_dirs:
        abs_dir = os.path.realpath(os.path.abspath(dir_path))
        # 匹配条件：
        # 1. 路径等于目录本身（用户要列目录内容）
        # 2. 路径以 "目录 + 分隔符" 开头（用户在目录下的某个子路径）
        #
        # 为什么要加分隔符？
        # 因为如果没有分隔符检查，"/workspace-file" 会以 "/workspace" 开头，
        # 但实际上它是另一个文件，不是子目录。
        if resolved == abs_dir or resolved.startswith(abs_dir + os.sep):
            return resolved

    # 路径不在任何允许的目录范围内，抛出错误
    raise ValueError(
        f'路径 "{input_path}" 超出允许的范围: {", ".join(allowed_dirs)}'
    )


def is_path_allowed(input_path: str, allowed_dirs: list[str]) -> bool:
    """检查路径是否在允许的目录范围内

    这是 resolve_sandbox_path 的"安全版"：不抛出错误，而是返回布尔值。
    适用于需要预判路径合法性但不想捕获异常的场景。

    Args:
        input_path: 用户提供的文件路径
        allowed_dirs: 允许的目录列表

    Returns:
        如果路径在允许范围内返回 True，否则返回 False
    """
    try:
        resolve_sandbox_path(input_path, allowed_dirs)
        return True
    except ValueError:
        return False


__all__ = ["resolve_sandbox_path", "is_path_allowed"]
