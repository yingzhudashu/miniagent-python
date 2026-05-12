#!/usr/bin/env python3
"""扫描 ``miniagent/`` 下 Python 文件，列出缺少 docstring 的模块、类与函数。

用法::

    python scripts/docstring_inventory.py
    python scripts/docstring_inventory.py --write docs/docstring_inventory.md

**检查规则（与 CONTRIBUTING 中「缺失项扫描」说明一致）**：

- 名称形如 ``__x__`` 的特殊方法 **默认跳过**（不要求 docstring）。
- **唯一例外**：``__init__`` **不跳过**，仍须具备 docstring 才会被本脚本视为合规。

模块级 docstring 按 AST 首条语句判断；本仓库约定其位于 ``from __future__ import annotations`` **之前**
（见 CONTRIBUTING「模块级 docstring」）。

在 Windows 控制台运行本脚本时，``main`` 会尝试将 ``sys.stdout`` 设为 UTF-8（``reconfigure`` 或
``TextIOWrapper``），减轻中文清单在终端乱码；管道到其它程序时若仍异常，可设置环境变量 ``PYTHONUTF8=1``
或仅用 ``--write`` 输出到 Markdown 文件查看。
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable
from pathlib import Path


def _has_docstring(node: ast.AST) -> bool:
    body = getattr(node, "body", None)
    if not body:
        return False
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        return isinstance(first.value.value, str) and bool(first.value.value.strip())
    return False


def _skip_name(name: str) -> bool:
    return name.startswith("__") and name.endswith("__") and name != "__init__"


def _walk_functions(
    nodes: Iterable[ast.stmt],
    prefix: str,
    missing: list[str],
) -> None:
    for node in nodes:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if not _skip_name(node.name):
                qual = f"{prefix}.{node.name}" if prefix else node.name
                if not _has_docstring(node):
                    missing.append(qual)
            _walk_functions(node.body, f"{prefix}.{node.name}" if prefix else node.name, missing)
        elif isinstance(node, ast.ClassDef):
            _walk_class(node, prefix, missing)


def _walk_class(node: ast.ClassDef, outer: str, missing: list[str]) -> None:
    qual = f"{outer}.{node.name}" if outer else node.name
    if not _has_docstring(node):
        missing.append(f"class {qual}")
    _walk_functions(node.body, qual, missing)


def scan_file(path: Path) -> tuple[bool, list[str]]:
    """返回 (模块是否有 docstring, 缺失项列表)。"""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    missing: list[str] = []
    mod_ok = _has_docstring(tree)
    _walk_functions(tree.body, "", missing)
    return mod_ok, missing


def main() -> None:
    import io
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError, AttributeError):
            pass
    enc = getattr(sys.stdout, "encoding", None) or ""
    if enc.lower() != "utf-8" and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer,
            encoding="utf-8",
            errors="replace",
            line_buffering=True,
        )

    parser = argparse.ArgumentParser(description="List miniagent symbols missing docstrings.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "miniagent",
        help="Package root (default: repo miniagent/)",
    )
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
        help="Write Markdown report to this path",
    )
    args = parser.parse_args()
    root: Path = args.root
    rows: list[tuple[str, bool, list[str]]] = []
    for py in sorted(root.rglob("*.py")):
        mod_ok, miss = scan_file(py)
        if not mod_ok or miss:
            rel = py.relative_to(root.parent)
            rows.append((str(rel).replace("\\", "/"), mod_ok, miss))

    lines: list[str] = [
        "# Docstring 缺失清单",
        "",
        "由 `scripts/docstring_inventory.py` 生成；运行前请先执行该脚本更新本文件。",
        "",
    ]
    if not rows:
        lines.append("- （本次扫描无缺失项。）")
    for rel, mod_ok, miss in rows:
        if not mod_ok:
            lines.append(f"- **{rel}**（模块 docstring 缺失）")
        for m in miss:
            lines.append(f"- **{rel}**：`{m}`")
    text = "\n".join(lines) + "\n"

    print(text)
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
