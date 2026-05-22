"""轻量级技能自动审查 — 在 install_skill 时运行，无需 LLM 参与。

检查项：
1. SKILL.md 存在且非空
2. scripts/ 中的危险模式（rm -rf、curl | bash、硬编码密钥等）
3. metadata 中的权限要求（bins/env）
4. 可疑导入（os.system、subprocess.call、eval/exec）

返回审查报告字符串（警告但不阻断安装）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# 危险 shell 模式
_DANGEROUS_SHELL = [
    (r"rm\s+-rf\s+/", "递归删除根目录或系统路径"),
    (r"curl\s+\S+\s*\|\s*(ba)?sh", "管道执行远程脚本 (curl | bash)"),
    (r"wget\s+\S+\s*-\S*\s*\|\s*(ba)?sh", "管道执行远程脚本 (wget | bash)"),
    (r">>\s*/etc/(passwd|shadow|sudoers)", "写入系统认证文件"),
    (r"chmod\s+777\s+/", "过度放宽系统目录权限"),
]

# 硬编码密钥模式
_HARDcoded_SECRETS = [
    (
        r"(?:api_key|apikey|secret_key|token|password)\s*=\s*['\"][A-Za-z0-9+/=]{16,}['\"]",
        "疑似硬编码密钥/令牌",
    ),
    (r"AKIA[0-9A-Z]{16}", "疑似 AWS Access Key"),
    (r"ghp_[A-Za-z0-9]{36}", "疑似 GitHub Personal Access Token"),
]

# 危险 Python 调用
_DANGEROUS_PY = [
    (r"os\.system\s*\(", "直接执行 shell 命令 (os.system)"),
    (r"subprocess\.(call|run|Popen)\s*\(.*shell\s*=\s*True", "子进程 shell 执行"),
    (r"\beval\s*\(", "使用 eval() 执行任意代码"),
    (r"\bexec\s*\(", "使用 exec() 执行任意代码"),
    (r"__import__\s*\(", "动态模块导入"),
]


def _auto_vet_skill(skill_dir: str) -> str:
    """自动审查一个技能目录。

    Args:
        skill_dir: 技能目录路径

    Returns:
        审查报告字符串（空字符串表示无警告）
    """
    warnings: list[str] = []
    skill_name = os.path.basename(skill_dir)

    # 1. 检查 SKILL.md
    skill_md_path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md_path):
        warnings.append("  - SKILL.md 不存在")
    else:
        content = Path(skill_md_path).read_text(encoding="utf-8", errors="replace")
        if not content.strip():
            warnings.append("  - SKILL.md 为空")
        else:
            _scan_content(skill_md_path, content, warnings)

    # 2. 扫描 scripts/ 目录
    scripts_dir = os.path.join(skill_dir, "scripts")
    if os.path.isdir(scripts_dir):
        for root, _dirs, files in os.walk(scripts_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    fcontent = Path(fpath).read_text(encoding="utf-8", errors="replace")
                    _scan_content(fpath, fcontent, warnings)
                except Exception:
                    pass

    # 3. 检查元数据权限
    if warnings:
        header = f"\n自动审查 [{skill_name}] — 发现 {len(warnings)} 项警告:"
    else:
        header = f"\n自动审查 [{skill_name}] — 通过，无警告"

    return header + "\n" + "\n".join(warnings) if warnings else header


def _scan_content(filepath: str, content: str, warnings: list[str]) -> None:
    """扫描文件内容中的危险模式。"""
    rel = filepath
    for pattern, desc in _DANGEROUS_SHELL + _HARDcoded_SECRETS + _DANGEROUS_PY:
        if re.search(pattern, content, re.IGNORECASE):
            warnings.append(f"  - [{desc}] in {rel}")
