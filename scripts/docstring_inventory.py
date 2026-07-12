#!/usr/bin/env python3
"""审计 ``miniagent/`` 中应当具备 docstring 的公开接口与复杂实现。

用法::

    python scripts/docstring_inventory.py
    python scripts/docstring_inventory.py --check

检查刻意忽略局部闭包、Protocol 的省略号方法和简单私有辅助函数，避免为了
通过门禁而产生复述代码的低价值注释。模块、公开顶层符号、公开具体类方法，
以及体量较大的私有顶层实现属于强制范围。

模块级 docstring 按 AST 首条语句判断；本仓库约定其位于 ``from __future__ import annotations`` **之前**
（见 CONTRIBUTING「模块级 docstring」）。

在 Windows 控制台运行本脚本时，``main`` 会尝试将 ``sys.stdout`` 设为 UTF-8（``reconfigure`` 或
``TextIOWrapper``），减轻中文清单在终端乱码；管道到其它程序时若仍异常，可设置环境变量 ``PYTHONUTF8=1``
或仅用 ``--write`` 输出到 Markdown 文件查看。
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


def _has_docstring(node: ast.AST) -> bool:
    body = getattr(node, "body", None)
    if not body:
        return False
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        return isinstance(first.value.value, str) and bool(first.value.value.strip())
    return False


_COMPLEX_PRIVATE_LINES = 40


class MissingDocstring:
    """表示一个需要补充 docstring 的符号。"""

    __slots__ = ("qualified_name", "line")

    def __init__(self, qualified_name: str, line: int) -> None:
        """保存符号限定名和源码行号。"""
        self.qualified_name = qualified_name
        self.line = line

    def __eq__(self, other: object) -> bool:
        """支持测试和调用方按值比较扫描结果。"""
        return isinstance(other, MissingDocstring) and (
            self.qualified_name,
            self.line,
        ) == (other.qualified_name, other.line)


def _is_protocol(node: ast.ClassDef) -> bool:
    return any(
        (isinstance(base, ast.Name) and base.id == "Protocol")
        or (isinstance(base, ast.Attribute) and base.attr == "Protocol")
        for base in node.bases
    )


def _requires_function_docstring(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    in_concrete_class: bool,
    in_private_class: bool = False,
) -> bool:
    if node.name.startswith("__") and node.name.endswith("__"):
        return False
    if not node.name.startswith("_") and not in_private_class:
        return True
    length = (node.end_lineno or node.lineno) - node.lineno
    if in_private_class:
        return length >= _COMPLEX_PRIVATE_LINES
    return not in_concrete_class and length >= _COMPLEX_PRIVATE_LINES


def scan_file(path: Path) -> tuple[bool, list[MissingDocstring]]:
    """返回模块说明状态和需要补充说明的顶层/公开符号。"""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    missing: list[MissingDocstring] = []
    mod_ok = _has_docstring(tree)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if (
                _requires_function_docstring(node, in_concrete_class=False)
                and not _has_docstring(node)
            ):
                missing.append(MissingDocstring(node.name, node.lineno))
        elif isinstance(node, ast.ClassDef):
            private_class = node.name.startswith("_")
            if not private_class and not _has_docstring(node):
                missing.append(MissingDocstring(f"class {node.name}", node.lineno))
            if _is_protocol(node):
                continue
            for member in node.body:
                if not isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if (
                    _requires_function_docstring(member, in_concrete_class=True)
                    if not private_class
                    else _requires_function_docstring(
                        member,
                        in_concrete_class=True,
                        in_private_class=True,
                    )
                ) and not _has_docstring(member):
                    missing.append(MissingDocstring(f"{node.name}.{member.name}", member.lineno))
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
        "--check",
        action="store_true",
        help="发现强制范围内的缺失项时返回非零退出码",
    )
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
        help="Write Markdown report to this path",
    )
    args = parser.parse_args()
    root: Path = args.root
    rows: list[tuple[str, bool, list[MissingDocstring]]] = []
    for py in sorted(root.rglob("*.py")):
        if "templates" in py.relative_to(root).parts:
            continue
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
        for item in miss:
            lines.append(f"- **{rel}:{item.line}**：`{item.qualified_name}`")
    text = "\n".join(lines) + "\n"

    print(text)
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(text, encoding="utf-8")
    if args.check and rows:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
