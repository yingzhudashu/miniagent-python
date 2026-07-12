"""批量技能验证测试 — 检查 workspaces/skills/ 下所有技能是否能被 miniagent 正确加载。"""

import asyncio
import os
import sys
from pathlib import Path

# 确保项目路径在 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from miniagent.skills.loader import discover_skill_packages, parse_skill_md
from miniagent.skills.registry import DefaultSkillRegistry
from miniagent.types.config import AgentConfig


def _check_skill_parsing(pkg_dir, name):
    """测试 SKILL.md 的 YAML front matter 解析。"""
    skill_md = os.path.join(pkg_dir, "SKILL.md")
    if not os.path.exists(skill_md):
        # 尝试 skill.yaml
        skill_yaml = os.path.join(pkg_dir, "skill.yaml")
        if os.path.exists(skill_yaml):
            return "SKIP", "使用 skill.yaml 而非 SKILL.md"
        # 尝试 instructions.md
        instr = os.path.join(pkg_dir, "instructions.md")
        if os.path.exists(instr):
            return "SKIP", "使用 instructions.md 而非 SKILL.md"
        return "FAIL", "缺少 SKILL.md（也无 skill.yaml / instructions.md）"

    try:
        content = Path(skill_md).read_text(encoding="utf-8")
        meta, body = parse_skill_md(content)
    except Exception as e:
        return "FAIL", f"SKILL.md 解析异常: {e}"

    if not meta:
        return "FAIL", "SKILL.md front matter 为空"
    if "name" not in meta:
        return "FAIL", "SKILL.md 缺少 name 字段"
    if "description" not in meta:
        return "FAIL", "SKILL.md 缺少 description 字段"
    if not body or len(body.strip()) < 10:
        return "WARN", f"SKILL.md body 过短 ({len(body.strip())} chars)"

    return (
        "PASS",
        f"name={meta.get('name')}, desc_len={len(meta.get('description', ''))}, body_len={len(body.strip())}",
    )


def _check_skill_registration(packages):
    """测试技能注册到 registry。"""
    reg = DefaultSkillRegistry()
    config = AgentConfig()

    results = []
    for pkg in packages:
        try:
            reg.register_package(pkg)
            eligible = reg.get_eligible_skills(config)
            eligible_names = {s.name for s in eligible}

            if pkg.skills:
                pkg_skill_names = [s.name for s in pkg.skills]
                gated_out = [n for n in pkg_skill_names if n not in eligible_names]
                if gated_out:
                    results.append((pkg.id, "WARN", f"sub-skill 被 gating 过滤: {gated_out}"))
                else:
                    results.append(
                        (pkg.id, "PASS", f"{len(pkg.skills)} 个 sub-skill 全部 eligible")
                    )
            else:
                results.append((pkg.id, "PASS", "注册成功（纯指令型技能）"))
        except Exception as e:
            results.append((pkg.id, "FAIL", f"注册异常: {e}"))

    return results


def _check_tools_import(pkg_dir, name):
    """检查 tools.py 是否可以正常 import。"""
    tools_py = os.path.join(pkg_dir, "tools.py")
    if not os.path.exists(tools_py):
        return "SKIP", "无 tools.py（纯指令型技能）"

    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(f"{name}_tools", tools_py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return "PASS", "tools.py 导入成功"
    except Exception as e:
        return "FAIL", f"tools.py 导入失败: {e}"


def _check_scripts_exist(pkg_dir, name):
    """检查引用的脚本文件是否存在。"""
    scripts_dir = os.path.join(pkg_dir, "scripts")
    if not os.path.exists(scripts_dir):
        return "SKIP", "无 scripts 目录"

    scripts = os.listdir(scripts_dir)
    if not scripts:
        return "WARN", "scripts 目录为空"

    non_files = []
    for s in scripts:
        full = os.path.join(scripts_dir, s)
        if not os.path.isfile(full):
            non_files.append(s)

    if non_files:
        return "WARN", f"以下不是常规文件: {non_files}"
    return "PASS", f"包含 {len(scripts)} 个脚本: {', '.join(scripts)}"


async def main():
    skills_root = os.path.join(PROJECT_ROOT, "workspaces", "skills")
    print(f"技能根目录: {skills_root}")
    print(f"存在: {os.path.exists(skills_root)}")
    print("=" * 80)

    # 1) 技能发现
    print("\n【阶段 1: 技能发现】")
    try:
        packages = await discover_skill_packages(skills_root)
    except Exception as e:
        print(f"  [FAIL] discover_skill_packages 抛出异常: {e}")
        return

    print(f"  [OK] 共发现 {len(packages)} 个技能包")

    if not packages:
        print("\n  未发现任何技能包！")
        return

    pkg_names = [p.id for p in packages]
    print(f"  发现的技能包 ID: {', '.join(pkg_names)}")

    # 列出所有目录但未被发现的
    all_dirs = set()
    for d in os.listdir(skills_root):
        full = os.path.join(skills_root, d)
        if os.path.isdir(full) and not d.startswith("."):
            all_dirs.add(d)
    missed = all_dirs - set(pkg_names)
    if missed:
        print(f"  [WARN] 存在但未被发现的目录: {', '.join(sorted(missed))}")

    # 2) SKILL.md 解析测试
    print("\n【阶段 2: SKILL.md 解析】")
    parse_results = []
    for pkg in sorted(packages, key=lambda p: p.id):
        status, msg = _check_skill_parsing(pkg.source_path, pkg.id)
        parse_results.append((pkg.id, status, msg))
        icon = {"PASS": "[OK]", "FAIL": "[FAIL]", "WARN": "[WARN]", "SKIP": "[SKIP]"}.get(
            status, "?"
        )
        print(f"  {icon} {pkg.id}: {msg}")

    # 3) 注册测试
    print("\n【阶段 3: 注册 & Gating】")
    reg_results = _check_skill_registration(packages)
    for pkg_id, status, msg in reg_results:
        icon = {"PASS": "[OK]", "FAIL": "[FAIL]", "WARN": "[WARN]", "SKIP": "[SKIP]"}.get(
            status, "?"
        )
        print(f"  {icon} {pkg_id}: {msg}")

    # 4) tools.py 导入测试
    print("\n【阶段 4: tools.py 导入（如有）】")
    for pkg in sorted(packages, key=lambda p: p.id):
        status, msg = _check_tools_import(pkg.source_path, pkg.id)
        icon = {"PASS": "[OK]", "FAIL": "[FAIL]", "WARN": "[WARN]", "SKIP": "[SKIP]"}.get(
            status, "?"
        )
        print(f"  {icon} {pkg.id}: {msg}")

    # 5) 脚本文件检查
    print("\n【阶段 5: 脚本文件完整性】")
    for pkg in sorted(packages, key=lambda p: p.id):
        status, msg = _check_scripts_exist(pkg.source_path, pkg.id)
        icon = {"PASS": "[OK]", "FAIL": "[FAIL]", "WARN": "[WARN]", "SKIP": "[SKIP]"}.get(
            status, "?"
        )
        print(f"  {icon} {pkg.id}: {msg}")

    # 汇总
    print("\n" + "=" * 80)
    print("【汇总】")
    all_statuses = [s for _, s, _ in parse_results + reg_results]
    fail_count = sum(1 for s in all_statuses if s == "FAIL")
    warn_count = sum(1 for s in all_statuses if s == "WARN")
    pass_count = sum(1 for s in all_statuses if s == "PASS")
    skip_count = sum(1 for s in all_statuses if s == "SKIP")
    print(f"  总计: {pass_count} PASS, {warn_count} WARN, {fail_count} FAIL, {skip_count} SKIP")

    # 列出所有失败的项
    fails = [(n, s, m) for n, s, m in parse_results + reg_results if s == "FAIL"]
    if fails:
        print("\n  失败项详情:")
        for name, _, msg in fails:
            print(f"    [FAIL] {name}: {msg}")

    warns = [(n, s, m) for n, s, m in parse_results + reg_results if s == "WARN"]
    if warns:
        print("\n  警告项详情:")
        for name, _, msg in warns:
            print(f"    [WARN] {name}: {msg}")


if __name__ == "__main__":
    asyncio.run(main())
