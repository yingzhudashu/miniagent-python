"""校验 Wheel 的运行时资源及 Python 模块清单与源码树一致。"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REQUIRED_RESOURCES = frozenset(
    {
        "miniagent/assistant/resources/config.defaults.json",
        "miniagent/assistant/skills/templates/builtin-web/SKILL.md",
        "miniagent/assistant/skills/templates/builtin-web/_meta.json",
        "miniagent/assistant/skills/templates/builtin-web/skills/web-tools/SKILL.md",
        "miniagent/assistant/skills/templates/builtin-stackexchange/SKILL.md",
        "miniagent/assistant/skills/templates/builtin-stackexchange/_meta.json",
        "miniagent/assistant/skills/templates/builtin-stackexchange/skills/stackexchange-tools/SKILL.md",
        "miniagent/assistant/skills/templates/skill-creator/SKILL.md",
        "miniagent/assistant/skills/templates/skill-creator/assets/eval_review.html",
        "miniagent/assistant/skills/templates/skill-creator/eval-viewer/viewer.html",
        "miniagent/assistant/skills/templates/skill-creator/references/schemas.md",
        "miniagent/assistant/skills/templates/skill-vetter/SKILL.md",
        "miniagent/assistant/skills/templates/skill-vetter/references/vetting-checklist.md",
    }
)


def _source_modules(source_root: Path) -> frozenset[str]:
    """返回源码树中应进入 Wheel 的 Python 文件路径。"""
    package_root = source_root / "miniagent"
    return frozenset(
        path.relative_to(source_root).as_posix()
        for path in package_root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def check_wheel(path: str | Path, *, source_root: str | Path | None = None) -> list[str]:
    """返回 Wheel 缺失资源及与源码不一致的 Python 模块问题。"""
    wheel = Path(path)
    if not wheel.is_file():
        raise FileNotFoundError(f"wheel not found: {wheel}")
    with zipfile.ZipFile(wheel) as archive:
        names = frozenset(archive.namelist())
    issues = [f"missing resource: {name}" for name in sorted(REQUIRED_RESOURCES - names)]

    root = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[1]
    source_modules = _source_modules(root.resolve())
    wheel_modules = frozenset(
        name for name in names if name.startswith("miniagent/") and name.endswith(".py")
    )
    issues.extend(f"missing Python module: {name}" for name in sorted(source_modules - wheel_modules))
    issues.extend(f"stale Python module: {name}" for name in sorted(wheel_modules - source_modules))
    return issues


def main(argv: list[str] | None = None) -> int:
    """CLI entry point used by CI after building a wheel."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python scripts/check_wheel_resources.py <wheel>", file=sys.stderr)
        return 2
    issues = check_wheel(args[0])
    if issues:
        print("wheel content validation failed:", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print(f"wheel runtime resources and Python modules OK: {args[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
