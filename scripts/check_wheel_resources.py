"""Validate that a built wheel contains every required MiniAgent runtime resource."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REQUIRED_RESOURCES = frozenset(
    {
        "miniagent/resources/config.defaults.json",
        "miniagent/skills/templates/builtin-web/SKILL.md",
        "miniagent/skills/templates/builtin-web/_meta.json",
        "miniagent/skills/templates/builtin-web/skills/web-tools/SKILL.md",
        "miniagent/skills/templates/skill-creator/SKILL.md",
        "miniagent/skills/templates/skill-creator/assets/eval_review.html",
        "miniagent/skills/templates/skill-creator/eval-viewer/viewer.html",
        "miniagent/skills/templates/skill-creator/references/schemas.md",
        "miniagent/skills/templates/skill-vetter/SKILL.md",
        "miniagent/skills/templates/skill-vetter/references/vetting-checklist.md",
    }
)


def check_wheel(path: str | Path) -> list[str]:
    """Return required resource paths missing from ``path``."""
    wheel = Path(path)
    if not wheel.is_file():
        raise FileNotFoundError(f"wheel not found: {wheel}")
    with zipfile.ZipFile(wheel) as archive:
        names = frozenset(archive.namelist())
    return sorted(REQUIRED_RESOURCES - names)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point used by CI after building a wheel."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python scripts/check_wheel_resources.py <wheel>", file=sys.stderr)
        return 2
    missing = check_wheel(args[0])
    if missing:
        print("wheel is missing required runtime resources:", file=sys.stderr)
        for name in missing:
            print(f"- {name}", file=sys.stderr)
        return 1
    print(f"wheel runtime resources OK: {args[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
