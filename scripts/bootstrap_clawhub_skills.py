#!/usr/bin/env python3
"""从 ClawHub 批量安装基线技能到默认技能目录（与引擎 ``get_skills_root()`` 一致）。

用法:
  python scripts/bootstrap_clawhub_skills.py
  python scripts/bootstrap_clawhub_skills.py --slug chindden/skill-creator --slug spclaudehome/skill-vetter

slug **必须与 ClawHub 技能详情页上的标识一致**（可能含 ``author/slug`` 形式）。
默认列表仅为占位；若 HTTP 404，请打开 https://clawhub.ai 搜索技能并复制页面 slug，
或使用 Agent 的 ``search_skills`` 工具核对后再传入 ``--slug``。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


# 短 slug 与带前缀 slug 均可能存在；安装失败时请改用站点上的完整 slug
DEFAULT_SLUGS = ("skill-creator", "skill-vetter")


async def _install_slugs(slugs: list[str]) -> int:
    from miniagent.skills.clawhub_client import create_clawhub_client
    from miniagent.skills.paths import get_skills_root

    root = get_skills_root()
    os.makedirs(root, exist_ok=True)
    client = create_clawhub_client()
    ok = 0
    for slug in slugs:
        slug = slug.strip()
        if not slug:
            continue
        dest = os.path.join(root, *slug.replace("\\", "/").split("/"))
        if os.path.isdir(dest):
            print(f"跳过（已存在）: {slug} -> {dest}")
            continue
        try:
            await client.download(slug, skills_root=root)
            print(f"已安装: {slug}")
            ok += 1
        except Exception as e:
            print(f"失败 {slug}: {e}", file=sys.stderr)
            print(
                "  提示: 在 clawhub.ai 打开技能页核对 slug；"
                "可尝试 --slug author/slug 形式。",
                file=sys.stderr,
            )
    return ok


def main() -> None:
    epilog = """示例:
  %(prog)s --slug chindden/skill-creator
  %(prog)s --slug skill-creator
若默认列表报错 404，请以站点展示为准替换 slug。"""
    ap = argparse.ArgumentParser(
        description="从 ClawHub 下载技能到 MINI_AGENT_SKILLS（默认 workspaces/skills）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    ap.add_argument(
        "--slug",
        action="append",
        dest="slugs",
        metavar="SLUG",
        help="技能 slug，与 ClawHub 详情页一致；可多次指定",
    )
    args = ap.parse_args()
    slugs = list(args.slugs) if args.slugs else list(DEFAULT_SLUGS)
    n = asyncio.run(_install_slugs(slugs))
    print(f"完成，新安装 {n} 个技能。若进程已运行，请重启 Agent 以加载。")


if __name__ == "__main__":
    main()
