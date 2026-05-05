"""Self-Inspection Engine 自检查引擎 (Phase 5.2 升级)

获取项目源代码，执行代码质量分析、架构合规检查、使用痛点分析，生成自检报告。

Phase 5.1 升级：
- 动态模块发现：扫描 src/ 下所有 .py 文件，不再硬编码模块列表
- 自动分组：按目录自动分组
- 核心文件特殊检查：保留对关键架构文件的专项检查

Phase 5.2 升级：
- 接入运行时错误数据：可选传入 error_log_path，运行时错误优先级 > 静态分析

分析维度：
1. 代码质量指标：文件数、总代码行数、测试覆盖率等
2. 模块分析：每个模块的复杂度、依赖关系、是否有对应测试
3. 架构合规检查：验证关键模块是否存在
4. 使用痛点：高频失败、循环检测触发、日志告警等 + 运行时错误

设计原则：
- 静态分析为主，动态数据为辅（Phase 5.2 接入运行时数据）
- 所有检查可追溯、可验证
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .types import (
    InspectionReport,
    CodeQualityMetric,
    ModuleAnalysis,
    ArchitectureCheck,
    PainPoint,
)


# 核心架构文件（保留特殊检查）
CORE_FILES = {
    "core/agent.py",
    "core/planner.py",
    "core/monitor.py",
    "core/config.py",
    "core/loop_detector.py",
    "core/registry.py",
}


def scan_py_files(src_dir: str) -> list[str]:
    """递归扫描 src/ 目录下所有 .py 文件。

    Args:
        src_dir: src 目录路径。

    Returns:
        相对路径列表（相对于 src_dir，使用正斜杠）。
    """
    results: list[str] = []
    skip_dirs = {"node_modules", "dist", ".git", "__pycache__", ".venv", "venv"}

    src_path = Path(src_dir)
    if not src_path.exists():
        return results

    for py_file in src_path.rglob("*.py"):
        # 检查路径中是否包含要跳过的目录
        rel = py_file.relative_to(src_path)
        parts = set(rel.parts)
        if parts & skip_dirs:
            continue
        results.append(str(rel).replace(os.sep, "/"))

    return sorted(results)


def _count_total_lines(src_dir: str) -> int:
    """递归统计 src/ 下所有 .py 文件的总行数。"""
    total = 0
    skip_dirs = {"node_modules", "dist", ".git", "__pycache__", ".venv", "venv"}
    src_path = Path(src_dir)
    if not src_path.exists():
        return 0

    for py_file in src_path.rglob("*.py"):
        rel = py_file.relative_to(src_path)
        if set(rel.parts) & skip_dirs:
            continue
        try:
            total += len(py_file.read_text(encoding="utf-8").splitlines())
        except Exception:
            pass
    return total


def _count_exports(content: str) -> int:
    """统计 Python 文件中的导出数量（def/class/变量）。"""
    # 匹配 def, class, __all__ 中的名称
    defs = len(re.findall(r"^(?:def|class|async def)\s+\w+", content, re.MULTILINE))
    return defs


def _count_imports(content: str) -> int:
    """统计 import 语句数量。"""
    return len(re.findall(r"^(?:import|from)\s+", content, re.MULTILINE))


def _estimate_complexity(content: str) -> int:
    """估算代码复杂度（1-10）。"""
    lines = [l for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
    if not lines:
        return 1

    control_flow = len(re.findall(r"\b(if|elif|else|for|while|try|except|return|raise|with)\b", content))
    nesting = len(re.findall(r":", content))
    density = (control_flow + nesting) / len(lines)

    if density > 0.5:
        return 9
    if density > 0.35:
        return 7
    if density > 0.25:
        return 5
    if density > 0.15:
        return 3
    return 1


def _has_corresponding_test(src_path: str, test_dir: str) -> bool:
    """检查源文件是否有对应的测试文件。"""
    base_name = Path(src_path).stem
    test_file = Path(test_dir) / f"test_{base_name}.py"
    if test_file.exists():
        return True
    # 也检查 tests/{base_name}_test.py
    test_file2 = Path(test_dir) / f"{base_name}_test.py"
    if test_file2.exists():
        return True
    return False


def _detect_issues(content: str) -> list[str]:
    """检测代码问题。"""
    issues: list[str] = []

    # 空 except 块
    if re.search(r"except\s*(?:\w+)?\s*:\s*\n\s*(?:\n|$|pass\s*$)", content, re.MULTILINE):
        issues.append("存在空 except 块（吞异常）")

    # 过多 print
    print_count = len(re.findall(r"\bprint\s*\(", content))
    if print_count > 10:
        issues.append(f"print 调用过多 ({print_count} 处)")

    # 超长函数
    lines = content.splitlines()
    in_fn = False
    fn_start = 0
    indent_level = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^(?:async\s+)?def\s+\w+", line):
            in_fn = True
            fn_start = i
            indent_level = len(line) - len(line.lstrip())
        if in_fn and stripped and not stripped.startswith("#"):
            current_indent = len(line) - len(line.lstrip()) if line.strip() else indent_level + 1
            if current_indent <= indent_level and i > fn_start and stripped:
                if i - fn_start > 50:
                    issues.append(f"超长函数 ({i - fn_start + 1} 行)")
                in_fn = False

    # 过长文件
    if len(lines) > 500:
        issues.append(f"文件过长 ({len(lines)} 行)")

    return issues


def _get_version(project_root: str) -> str:
    """获取项目版本（从 pyproject.toml 或 package.json）。"""
    # 尝试 pyproject.toml
    pyproject = Path(project_root) / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
            match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                return match.group(1)
        except Exception:
            pass

    # 尝试 package.json
    pkg_json = Path(project_root) / "package.json"
    if pkg_json.exists():
        try:
            import json
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            return pkg.get("version", "unknown")
        except Exception:
            pass

    return "unknown"


def _check_architecture(all_files: list[str], file_contents: dict[str, str]) -> list[ArchitectureCheck]:
    """架构完整性检查。"""
    checks: list[ArchitectureCheck] = []

    # 检查核心文件
    for core_file in sorted(CORE_FILES):
        exists = core_file in all_files
        checks.append(ArchitectureCheck(
            name=f"核心文件 {core_file}",
            passed=exists,
            details=f"期望存在核心文件 {core_file}",
            recommendation=None if exists else f"需要实现: {core_file}",
        ))

    # 检查两阶段架构 (Plan-then-Execute)
    planner_exists = "core/planner.py" in all_files
    agent_exists = "core/agent.py" in all_files

    if planner_exists:
        planner_content = file_contents.get("core/planner.py", "")
        planner_has_plan = "generate_plan" in planner_content or "generatePlan" in planner_content or "plan" in planner_content
        checks.append(ArchitectureCheck(
            name="Planner 模块实现",
            passed=planner_has_plan,
            details="Planner 模块应包含 plan 生成逻辑",
            recommendation=None if planner_has_plan else "缺少 generate_plan 相关逻辑",
        ))

    if agent_exists:
        agent_content = file_contents.get("core/agent.py", "")
        agent_has_run = "run" in agent_content or "execute" in agent_content
        checks.append(ArchitectureCheck(
            name="Agent 支持两阶段模式",
            passed=agent_has_run,
            details="Agent 应支持 run/execute 方法",
            recommendation=None if agent_has_run else "缺少 run 或 execute 方法",
        ))

    # 检查测试覆盖
    test_dir = Path(all_files[0].split("/")[0] if all_files else "src") / ".." / "tests"
    has_tests = any(f.startswith("tests/") or f.startswith("test_") for f in all_files)
    checks.append(ArchitectureCheck(
        name="测试覆盖",
        passed=has_tests,
        details="项目应包含测试文件",
        recommendation=None if has_tests else "缺少 tests/ 目录或测试文件",
    ))

    return checks


async def inspect_project(
    src_dir: str,
    error_log_path: str | None = None,
) -> InspectionReport:
    """执行项目自检。

    Phase 5.2 新增参数：
    - error_log_path: 可选，运行时错误日志目录路径。如果提供，会注入运行时错误数据。

    Args:
        src_dir: 项目 src/ 目录路径。
        error_log_path: 可选，运行时错误日志目录。

    Returns:
        自检报告。
    """
    project_root = str(Path(src_dir).resolve().parent)
    test_dir = os.path.join(project_root, "tests")

    # Step 1: 动态扫描所有 .py 文件
    all_files = scan_py_files(src_dir)
    file_contents: dict[str, str] = {}
    for file in all_files:
        fp = os.path.join(src_dir, file)
        try:
            file_contents[file] = Path(fp).read_text(encoding="utf-8")
        except Exception:
            file_contents[file] = ""

    # Step 2: 代码质量指标
    py_file_count = len(all_files)
    total_lines = _count_total_lines(src_dir)
    total_exports = 0
    total_imports = 0
    for content in file_contents.values():
        total_exports += _count_exports(content)
        total_imports += _count_imports(content)

    modules_with_tests = 0
    for file in all_files:
        if _has_corresponding_test(file, test_dir):
            modules_with_tests += 1

    test_coverage = round((modules_with_tests / py_file_count * 100)) if py_file_count > 0 else 0

    # 类型定义统计
    types_content = file_contents.get("core/types.py", "") or file_contents.get("types/__init__.py", "")
    class_count = len(re.findall(r"\bclass\s+\w+", types_content))

    quality_metrics = [
        CodeQualityMetric(name="Python 文件数", value=float(py_file_count), passed=py_file_count > 5),
        CodeQualityMetric(
            name="总代码行数", value=float(total_lines), passed=total_lines > 100,
            note=f"{py_file_count} 个 .py 文件",
        ),
        CodeQualityMetric(
            name="测试覆盖率", value=f"{test_coverage}%", target="100%",
            passed=test_coverage >= 80,
            note=f"{modules_with_tests}/{py_file_count} 个模块有测试",
        ),
        CodeQualityMetric(
            name="类定义数", value=float(class_count), passed=class_count > 5,
            note="类定义越多，结构越清晰",
        ),
        CodeQualityMetric(
            name="导出函数数", value=float(total_exports), passed=total_exports > 20,
            note="导出越多，模块化程度越高",
        ),
        CodeQualityMetric(
            name="导入语句数", value=float(total_imports), passed=total_imports > 20,
            note="导入越多，模块间耦合越高",
        ),
    ]

    # Step 3: 模块分析
    module_analysis: list[ModuleAnalysis] = []
    for file in all_files:
        content = file_contents.get(file, "")
        loc = len(content.splitlines())
        module_analysis.append(ModuleAnalysis(
            path=file,
            lines_of_code=loc,
            has_tests=_has_corresponding_test(file, test_dir),
            exports_count=_count_exports(content),
            imports_count=_count_imports(content),
            complexity_score=_estimate_complexity(content),
            issues=_detect_issues(content),
        ))

    # Step 4: 架构合规检查
    architecture_checks = _check_architecture(all_files, file_contents)

    # Step 5: 使用痛点（静态分析）
    pain_points: list[PainPoint] = []
    for m in module_analysis:
        for issue in m.issues:
            severity = "medium" if ("空 except" in issue or "过长" in issue) else "low"
            pain_points.append(PainPoint(
                description=f"{m.path}: {issue}",
                severity=severity,
                evidence="静态分析得出",
            ))

    untested = [m for m in module_analysis if not m.has_tests and m.lines_of_code > 50]
    for m in untested:
        pain_points.append(PainPoint(
            description=f"{m.path} 没有对应测试 ({m.lines_of_code} 行)",
            severity="high",
            evidence="文件存在但无测试覆盖",
        ))

    complex_mods = [m for m in module_analysis if m.complexity_score >= 7]
    for m in complex_mods:
        pain_points.append(PainPoint(
            description=f"{m.path} 复杂度过高 (评分: {m.complexity_score}/10)",
            severity="medium",
            evidence="控制流密度分析",
        ))

    # Step 6: 优化建议
    suggestions: list[str] = []
    if test_coverage < 80:
        suggestions.append(f"提高测试覆盖率到 80%+（当前 {test_coverage}%）")
    if complex_mods:
        suggestions.append(f"重构 {len(complex_mods)} 个高复杂度模块")
    if untested:
        suggestions.append(f"为 {len(untested)} 个未测试模块添加测试")
    suggestions.append("实现循环错误自动修复机制")
    suggestions.append("增加端到端测试验证完整流程")
    suggestions.append("增加工具调用的路径追踪")

    # Step 7: 总结
    passed_checks = sum(1 for c in architecture_checks if c.passed)
    total_checks = len(architecture_checks)
    health_score = round((passed_checks / max(total_checks, 1)) * 100)

    if health_score >= 90:
        summary = f"架构通过率 {health_score}%，系统健康。建议：{'；'.join(suggestions[:2])}。"
    elif health_score >= 70:
        summary = f"架构通过率 {health_score}%，系统基本正常。优先处理：{'；'.join(suggestions[:3])}。"
    else:
        summary = f"架构通过率 {health_score}%，系统存在缺陷。重点关注：{'；'.join(suggestions[:3])}。"

    import datetime
    report = InspectionReport(
        timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
        version=_get_version(project_root),
        quality_metrics=quality_metrics,
        module_analysis=module_analysis,
        architecture_checks=architecture_checks,
        pain_points=pain_points,
        suggestions=suggestions,
        summary=summary,
    )

    # Phase 5.2: 注入运行时错误数据
    if error_log_path is not None:
        try:
            from .error_analyzer import analyze_errors, inject_errors_into_inspection
            error_analysis = await analyze_errors(error_log_path)
            if error_analysis.total_errors > 0:
                inject_errors_into_inspection(report, error_analysis)
        except Exception as e:
            print(f"[inspector] 运行时错误分析失败: {e}")

    return report
