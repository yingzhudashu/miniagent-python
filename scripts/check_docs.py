#!/usr/bin/env python3
"""校验 Markdown 链接、文档索引和容易漂移的项目事实。"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*$")
_HTML_ANCHOR_RE = re.compile(r"<a\s+(?:name|id)=[\"']([^\"']+)[\"']", re.IGNORECASE)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_RETIRED_DOCS = {"TEST_COVERAGE_MATRIX.md"}


def _slug(value: str) -> str:
    value = re.sub(r"<[^>]*>", "", value).strip().lower()
    value = re.sub(r"[`*_~]", "", value)
    value = re.sub(r"[^\w\-\u4e00-\u9fff ]", "", value)
    return value.replace(" ", "-")


def _markdown_files(root: Path) -> list[Path]:
    candidates = [root / "README.md", root / "CHANGELOG.md"]
    for directory in ("docs", "scripts", "tests"):
        candidates.extend((root / directory).rglob("*.md"))
    return sorted(path for path in candidates if path.is_file())


def _collect_headings(files: list[Path]) -> dict[Path, set[str]]:
    """收集每份 Markdown 的 GitHub 风格标题和显式锚点。"""
    headings: dict[Path, set[str]] = {}
    for path in files:
        text = path.read_text(encoding="utf-8")
        headings[path.resolve()] = {
            _slug(match.group(1))
            for line in text.splitlines()
            if (match := _HEADING_RE.match(line))
        }
        headings[path.resolve()].update(_HTML_ANCHOR_RE.findall(text))
    return headings


def _check_file_links(path: Path, headings: dict[Path, set[str]]) -> list[str]:
    """检查单份文档代码块之外的本地链接与锚点。"""
    issues = []
    in_fence = False
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for raw_target in _LINK_RE.findall(_INLINE_CODE_RE.sub("", line)):
            target = raw_target.strip().split()[0].strip("<>")
            if target in {"路径", "url"} or target.startswith(("http://", "https://", "mailto:")):
                continue
            relative, _, anchor = target.partition("#")
            resolved = (path.parent / relative).resolve() if relative else path.resolve()
            if relative and not resolved.exists():
                issues.append(f"{path}:{line_number}: 本地链接不存在: {target}")
            elif anchor and resolved in headings and _slug(anchor) not in headings[resolved]:
                issues.append(f"{path}:{line_number}: Markdown 锚点不存在: {target}")
    return issues


def check_docs(root: Path) -> list[str]:
    """返回文档中的本地链接、索引和事实一致性问题。"""
    files = _markdown_files(root)
    headings = _collect_headings(files)

    issues: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        issues.extend(_check_file_links(path, headings))

        if path.name not in _RETIRED_DOCS:
            for retired in _RETIRED_DOCS:
                if retired in text:
                    issues.append(f"{path}: 仍引用已退役文档 {retired}")
        if re.search(r"\b\d{3,}\s+(?:tests?|passed|项测试)", text, re.IGNORECASE):
            issues.append(f"{path}: 不应硬编码测试数量")

    index = root / "docs" / "INDEX.md"
    index_text = index.read_text(encoding="utf-8")
    for document in sorted((root / "docs").glob("*.md")):
        if document.name in _RETIRED_DOCS or document == index:
            continue
        if document.name not in index_text:
            issues.append(f"{index}: 未索引 {document.name}")
    return issues


def main() -> int:
    """运行文档审计并输出适合 CI 的错误列表。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    issues = check_docs(args.root.resolve())
    for issue in issues:
        print(issue)
    if issues:
        print(f"documentation check failed: {len(issues)} issue(s)")
        return 1
    print("documentation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
