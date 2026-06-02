#!/usr/bin/env python3
"""从 ClawHub **额外**下载技能到默认技能目录（与 ``get_skills_root()`` 一致）。

仓库已内置 **skill-creator**（来自 anthropics/skills）与 **skill-vetter**（本仓库配套审查说明），
克隆即可加载；本脚本**不是**基线必需步骤，仅用于在 ClawHub API 可用时安装**更多**技能包。

用法:
  python scripts/bootstrap_clawhub_skills.py
  python scripts/bootstrap_clawhub_skills.py --slug your-org/skill-example --slug other-org/other-skill

slug **必须与 ClawHub 技能详情页上的标识一致**（可能含 ``author/slug`` 形式）。
安装目录为 slug **最后一段**（与 ``miniagent.skills.clawhub_client.skill_install_dir_name`` 一致），
以便引擎只扫描 ``skills_root`` 一级子目录时仍能发现包。

以下示例中的 ``your-org/skill-example`` 仅为占位，**请替换**为站点上的真实 slug；若 HTTP 404，
请打开 https://clawhub.ai 搜索技能并复制页面 slug，或使用 Agent 的 ``search_skills`` 工具核对。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

# 短 slug 与带前缀 slug 均可能存在；安装失败时请改用站点上的完整 slug
DEFAULT_SLUGS = ("skill-creator", "skill-vetter")


async def _install_slugs(slugs: list[str]) -> int:
    from miniagent.skills.clawhub_client import create_clawhub_client, skill_install_dir_name
    from miniagent.skills.paths import get_skills_root

    root = get_skills_root()
    os.makedirs(root, exist_ok=True)
    client = create_clawhub_client()
    ok = 0
    for slug in slugs:
        slug = slug.strip()
        if not slug:
            continue
        dest = os.path.join(root, skill_install_dir_name(slug))
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
    epilog = """示例（slug 请改为 clawhub.ai 上真实值）:
  %(prog)s --slug your-org/skill-example
  %(prog)s --slug skill-creator
若默认列表报错 404，请以站点展示为准替换 slug。
含 author/ 前缀的 slug 会安装到目录名最后一段（与技能发现规则一致）。"""
    ap = argparse.ArgumentParser(
        description=(
            "可选：从 ClawHub 额外安装技能（内置 skill-creator / skill-vetter 已在 workspaces/skills，无需本脚本）。"
            "安装到 MINIAGENT_PATHS_SKILLS_DIR 或默认 workspaces/skills。"
        ),
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
