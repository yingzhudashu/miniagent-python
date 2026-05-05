"""Phase 4-6 验证脚本

验证核心引擎、工具系统和技能系统的完整性。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import json


def check(label: str, condition: bool) -> None:
    """简单断言"""
    status = "✅" if condition else "❌"
    print(f"  {status} {label}")
    if not condition:
        raise AssertionError(f"FAILED: {label}")


async def main() -> None:
    errors = 0

    # ================================================================
    # Phase 4: 核心引擎
    # ================================================================
    print("\n🔧 Phase 4: 核心引擎")

    # 4.1 Planner
    print("\n  --- Planner ---")
    try:
        from src.core.planner import generate_plan, _fallback_plan, _dict_to_plan
        check("planner 模块可导入", True)

        plan = _fallback_plan("测试输入")
        check("fallback_plan 生成正确", plan.summary == "直接执行模式：跳过详细规划")
        check("fallback_plan 有步骤", len(plan.steps) == 1)
        check("fallback_plan risk=low", plan.risk_level == "low")

        test_data = {
            "summary": "测试计划",
            "steps": [{"stepNumber": 1, "description": "步骤1", "requiredToolboxes": ["web"]}],
            "requiredToolboxes": ["web"],
            "suggestedConfig": {"maxTurns": 10},
            "estimatedTokens": {"total": 2000},
            "riskLevel": "medium",
        }
        plan2 = _dict_to_plan(test_data)
        check("dict_to_plan 摘要正确", plan2.summary == "测试计划")
        check("dict_to_plan 步骤数正确", len(plan2.steps) == 1)
        check("dict_to_plan 风险等级正确", plan2.risk_level == "medium")
    except Exception as e:
        print(f"  ❌ Planner 错误: {e}")
        errors += 1

    # 4.2 Executor
    print("\n  --- Executor ---")
    try:
        from src.core.executor import execute_plan, get_client, MODEL
        check("executor 模块可导入", True)
        check("MODEL 有默认值", len(MODEL) > 0)
        client = get_client()
        check("get_client() 返回客户端", client is not None)
    except Exception as e:
        print(f"  ❌ Executor 错误: {e}")
        errors += 1

    # 4.3 Agent
    print("\n  --- Agent ---")
    try:
        from src.core.agent import run_agent, run_pipeline, _create_default_plan
        check("agent 模块可导入", True)
        default_plan = _create_default_plan()
        check("默认计划生成正确", default_plan.summary == "直接执行模式")
    except Exception as e:
        print(f"  ❌ Agent 错误: {e}")
        errors += 1

    # ================================================================
    # Phase 5: 工具实现
    # ================================================================
    print("\n\n🔧 Phase 5: 工具实现")

    # 5.1 Filesystem
    print("\n  --- Filesystem ---")
    try:
        from src.tools.filesystem import filesystem_tools
        check("filesystem 模块可导入", True)
        check("包含 8 个工具", len(filesystem_tools) == 8)

        expected = ["read_file", "write_file", "edit_file", "list_dir",
                     "create_dir", "move_file", "copy_file", "delete_file"]
        for name in expected:
            check(f"  {name} 已注册", name in filesystem_tools)

        check("delete_file 权限=require-confirm",
              filesystem_tools["delete_file"].permission == "require-confirm")
        check("read_file 权限=sandbox",
              filesystem_tools["read_file"].permission == "sandbox")

        # 功能测试：write + read + edit
        from src.types.tool import ToolContext
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = ToolContext(cwd=tmpdir, allowed_paths=[tmpdir], permission="sandbox")

            # write
            result = await filesystem_tools["write_file"].handler(
                {"path": os.path.join(tmpdir, "test.txt"), "content": "Hello World"},
                ctx,
            )
            check("write_file 成功", result.success)

            # read
            result = await filesystem_tools["read_file"].handler(
                {"path": os.path.join(tmpdir, "test.txt")},
                ctx,
            )
            check("read_file 成功", result.success)
            check("read_file 内容正确", "Hello World" in result.content)

            # edit
            result = await filesystem_tools["edit_file"].handler(
                {"path": os.path.join(tmpdir, "test.txt"), "oldText": "Hello", "newText": "Hi"},
                ctx,
            )
            check("edit_file 成功", result.success)

            # list_dir
            result = await filesystem_tools["list_dir"].handler(
                {"path": tmpdir},
                ctx,
            )
            check("list_dir 成功", result.success)
            check("list_dir 包含 test.txt", "test.txt" in result.content)

    except Exception as e:
        print(f"  ❌ Filesystem 错误: {e}")
        errors += 1

    # 5.2 Exec
    print("\n  --- Exec ---")
    try:
        from src.tools.exec import exec_tools
        check("exec 模块可导入", True)
        check("包含 exec_command", "exec_command" in exec_tools)
        check("exec_command 权限=allowlist", exec_tools["exec_command"].permission == "allowlist")

        # 功能测试
        from src.types.tool import ToolContext
        ctx = ToolContext(cwd=".", allowed_paths=["."], permission="sandbox")
        result = await exec_tools["exec_command"].handler(
            {"command": "echo hello_miniagent", "timeout": 5},
            ctx,
        )
        check("exec_command 执行成功", result.success)
        check("exec_command 输出正确", "hello_miniagent" in result.content)
    except Exception as e:
        print(f"  ❌ Exec 错误: {e}")
        errors += 1

    # 5.3 Web
    print("\n  --- Web ---")
    try:
        from src.tools.web import web_tools
        check("web 模块可导入", True)
        check("包含 fetch_url", "fetch_url" in web_tools)
        check("包含 get_time", "get_time" in web_tools)

        # 功能测试：get_time
        from src.types.tool import ToolContext
        ctx = ToolContext(cwd=".", allowed_paths=["."])
        result = await web_tools["get_time"].handler(
            {"timezone": "Asia/Shanghai"},
            ctx,
        )
        check("get_time 成功", result.success)
        check("get_time 包含年份", "2026" in result.content or "202" in result.content)
    except Exception as e:
        print(f"  ❌ Web 错误: {e}")
        errors += 1

    # 5.4 Skills tools
    print("\n  --- Skills Tools ---")
    try:
        from src.tools.skills import skills_tools
        check("skills 模块可导入", True)
        check("包含 3 个工具", len(skills_tools) == 3)
        check("search_skills 已注册", "search_skills" in skills_tools)
        check("install_skill 已注册", "install_skill" in skills_tools)
        check("list_skills 已注册", "list_skills" in skills_tools)
        check("install_skill 权限=require-confirm",
              skills_tools["install_skill"].permission == "require-confirm")
    except Exception as e:
        print(f"  ❌ Skills Tools 错误: {e}")
        errors += 1

    # 5.5 Self-opt
    print("\n  --- Self-opt ---")
    try:
        from src.tools.self_opt import self_opt_tools
        check("self_opt 模块可导入", True)
        check("包含 4 个工具", len(self_opt_tools) == 4)
        check("self_inspect 已注册", "self_inspect" in self_opt_tools)
        check("generate_proposal 已注册", "generate_proposal" in self_opt_tools)
        check("run_tests 已注册", "run_tests" in self_opt_tools)
        check("git_snapshot 已注册", "git_snapshot" in self_opt_tools)
    except Exception as e:
        print(f"  ❌ Self-opt 错误: {e}")
        errors += 1

    # 5.6 ALL_TOOLS
    print("\n  --- ALL_TOOLS ---")
    try:
        from src.tools import ALL_TOOLS
        check("ALL_TOOLS 可导入", True)
        check("ALL_TOOLS 总数=18", len(ALL_TOOLS) == 18)
    except Exception as e:
        print(f"  ❌ ALL_TOOLS 错误: {e}")
        errors += 1

    # ================================================================
    # Phase 6: 技能系统
    # ================================================================
    print("\n\n🔧 Phase 6: 技能系统")

    # 6.1 SkillRegistry
    print("\n  --- SkillRegistry ---")
    try:
        from src.skills.registry import DefaultSkillRegistry
        from src.types.skill import Skill, SkillPackage, SkillEntry, SkillMetadata
        from src.types.tool import Toolbox

        registry = DefaultSkillRegistry()
        check("SkillRegistry 创建成功", True)

        # 注册技能
        skill = Skill(
            id="test-skill",
            name="测试技能",
            description="用于测试",
            keywords=["test"],
            toolboxes=[Toolbox(id="test-tb", name="测试工具箱", description="测试")],
            system_prompt="你是一个测试助手",
        )
        registry.register(skill)
        check("技能注册成功", registry.get("test-skill") is not None)
        check("get_all 返回 1 个", len(registry.get_all()) == 1)
        check("get_all_toolboxes 返回 1 个", len(registry.get_all_toolboxes()) == 1)
        check("get_system_prompts 返回 1 个", len(registry.get_system_prompts()) == 1)

        # 注销
        check("注销成功", registry.unregister("test-skill"))
        check("注销后为空", len(registry.get_all()) == 0)

        # 技能包
        pkg = SkillPackage(
            id="test-pkg",
            name="测试包",
            description="测试技能包",
            skills=[skill],
        )
        registry.register_package(pkg)
        check("技能包注册成功", len(registry.get_all()) == 1)
        check("get_packages 返回 1 个", len(registry.get_packages()) == 1)

        # Gating
        meta_skill = Skill(
            id="gated-skill",
            name="Gated",
            description="有条件的技能",
            metadata=SkillMetadata(env=["NONEXISTENT_VAR_12345"]),
        )
        registry.register(meta_skill)
        eligible = registry.get_eligible_skills()
        check("gated skill 被过滤", "gated-skill" not in [s.id for s in eligible])

        always_skill = Skill(
            id="always-skill",
            name="Always",
            description="始终可用",
            metadata=SkillMetadata(always=True),
        )
        registry.register(always_skill)
        eligible = registry.get_eligible_skills()
        check("always skill 通过", "always-skill" in [s.id for s in eligible])

        # SkillEntry disabled
        registry.set_skill_entries({"always-skill": SkillEntry(enabled=False)})
        eligible = registry.get_eligible_skills()
        check("disabled skill 被过滤", "always-skill" not in [s.id for s in eligible])

    except Exception as e:
        print(f"  ❌ SkillRegistry 错误: {e}")
        errors += 1

    # 6.2 Loader
    print("\n  --- Loader ---")
    try:
        from src.skills.loader import parse_skill_md, load_skill_package, discover_skill_packages

        # parse_skill_md
        content = "---\nname: TestSkill\ndescription: A test skill\n---\n# Hello\nBody content"
        meta, body = parse_skill_md(content)
        check("parse_skill_md name 正确", meta.get("name") == "TestSkill")
        check("parse_skill_md description 正确", meta.get("description") == "A test skill")
        check("parse_skill_md body 包含内容", "Body content" in body)

        # 无 front matter
        meta2, body2 = parse_skill_md("# Simple\nNo front matter")
        check("无 front matter 返回空 meta", len(meta2) == 0)
        check("无 front matter body 完整", "Simple" in body2)

        # discover 空目录
        with tempfile.TemporaryDirectory() as tmpdir:
            packages = await discover_skill_packages(tmpdir)
            check("空目录返回空列表", len(packages) == 0)

        # discover 带 SKILL.md 的目录
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_dir = os.path.join(tmpdir, "my-skill")
            os.makedirs(pkg_dir)
            with open(os.path.join(pkg_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("---\nname: MySkill\ndescription: My test skill\n---\n# MySkill\nContent here")

            packages = await discover_skill_packages(tmpdir)
            check("发现 1 个技能包", len(packages) == 1)
            check("技能包名称正确", packages[0].name == "MySkill")

    except Exception as e:
        print(f"  ❌ Loader 错误: {e}")
        errors += 1

    # 6.3 ClawHub Client
    print("\n  --- ClawHub Client ---")
    try:
        from src.skills.clawhub_client import create_clawhub_client, search_local_skills

        client = create_clawhub_client()
        check("ClawHub 客户端创建成功", client is not None)

        # 本地搜索（空目录）
        with tempfile.TemporaryDirectory() as tmpdir:
            results = search_local_skills(tmpdir, "test")
            check("空目录搜索返回空", len(results) == 0)

        # 本地搜索（有技能）
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = os.path.join(tmpdir, "web-tool")
            os.makedirs(skill_dir)
            with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("---\nname: Web Tool\ndescription: A web scraping tool\n---\nContent")

            results = search_local_skills(tmpdir, "web")
            check("搜索到 1 个结果", len(results) == 1)
            check("slug 正确", results[0]["slug"] == "web-tool")
            check("name 正确", results[0]["name"] == "Web Tool")

            # 空查询返回所有
            all_results = search_local_skills(tmpdir, "")
            check("空查询返回所有技能", len(all_results) == 1)

    except Exception as e:
        print(f"  ❌ ClawHub Client 错误: {e}")
        errors += 1

    # ================================================================
    # 总结
    # ================================================================
    print("\n" + "=" * 50)
    if errors == 0:
        print("🎉 Phase 4-6 全部验证通过！")
        print(f"   Phase 4: planner + executor + agent ✅")
        print(f"   Phase 5: 18 个工具 (8 文件 + 1 命令 + 2 网络 + 3 技能 + 4 自优化) ✅")
        print(f"   Phase 6: SkillRegistry + Loader + ClawHub ✅")
    else:
        print(f"❌ {errors} 个模块验证失败")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
