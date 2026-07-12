"""Mini Agent Python — 自测类型定义

定义测试样本、测试结果和测试报告的数据结构，以及 ``execute_agent`` 回调契约。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, TypedDict, runtime_checkable

DEFAULT_SAMPLES_DIR = "tests/evaluation/samples"
DEFAULT_REPORT_PATH = "workspaces/test_report.json"

VALID_CATEGORIES = frozenset({
    "tool_selection",
    "security",
    "prompt_injection",
    "schema",
    "regression",
    "cost",
})
VALID_ACTIONS = frozenset({"execute", "ask_human", "reject"})


class AgentExecutionResult(TypedDict, total=False):
    """``execute_agent`` 标准返回结构。

    Attributes:
        tool_calls: 工具调用列表，每项至少含 ``name`` 键
        output: Agent 最终回复文本
        tokens: token 用量（缺省时由适配器估算）
        action: 行为类型 ``execute`` | ``ask_human`` | ``reject``
    """

    tool_calls: list[dict[str, Any]]
    output: str
    tokens: int
    action: str
    actual_tools: list[str]
    tool_call_count: int


@runtime_checkable
class ExecuteAgentFn(Protocol):
    """自测框架注入的真实 Agent 执行函数。"""

    async def __call__(
        self,
        user_input: str,
        *,
        capture_tools: bool = True,
    ) -> AgentExecutionResult:
        """执行单条用户输入并返回可评估的结构化结果。"""
        ...


@dataclass
class SampleSpec:
    """测试样本：描述一个测试场景

    Attributes:
        name: 唯一标识
        description: 描述
        input: 用户请求
        category: 类别（tool_selection | security | prompt_injection | schema | regression | cost）
        expected_action: 预期行为（execute | ask_human | reject）
        expected_tools: 预期调用的工具（建议）
        must_call_tools: 必须调用的工具
        must_not_call_tools: 禁止调用的工具
        expected_output_pattern: 输出模式（正则）
        max_tokens: Token 预算
        max_tool_calls: 工具调用上限
        tags: 标签
        priority: 优先级（1=高, 2=中, 3=低）
    """

    name: str
    description: str = ""
    input: str = ""
    category: str = "tool_selection"

    # 预期行为
    expected_action: str = "execute"  # execute | ask_human | reject
    expected_tools: list[str] = field(default_factory=list)
    must_call_tools: list[str] = field(default_factory=list)
    must_not_call_tools: list[str] = field(default_factory=list)

    # 验证规则
    expected_output_pattern: str | None = None
    max_tokens: int | None = None
    max_tool_calls: int | None = None

    # 元数据
    tags: list[str] = field(default_factory=list)
    priority: int = 1  # 1=高, 2=中, 3=低

    def validate_schema(self) -> list[str]:
        """校验样本字段（委托 :mod:`miniagent.testing.validation`）。"""
        from miniagent.testing.validation import validate_sample_schema

        return validate_sample_schema(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SampleSpec:
        """从字典创建测试样本（不做校验；加载后由 TestRunner 统一校验）。"""
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            input=data.get("input", ""),
            category=data.get("category", "tool_selection"),
            expected_action=data.get("expected_action", "execute"),
            expected_tools=data.get("expected_tools", []),
            must_call_tools=data.get("must_call_tools", []),
            must_not_call_tools=data.get("must_not_call_tools", []),
            expected_output_pattern=data.get("expected_output_pattern"),
            max_tokens=data.get("max_tokens"),
            max_tool_calls=data.get("max_tool_calls"),
            tags=data.get("tags", []),
            priority=data.get("priority", 1),
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "description": self.description,
            "input": self.input,
            "category": self.category,
            "expected_action": self.expected_action,
            "expected_tools": self.expected_tools,
            "must_call_tools": self.must_call_tools,
            "must_not_call_tools": self.must_not_call_tools,
            "expected_output_pattern": self.expected_output_pattern,
            "max_tokens": self.max_tokens,
            "max_tool_calls": self.max_tool_calls,
            "tags": self.tags,
            "priority": self.priority,
        }


@dataclass
class ResultRecord:
    """单条测试结果

    Attributes:
        sample_name: 测试样本名称
        passed: 是否通过
        actual_action: 实际行为
        actual_tools: 实际调用的工具
        actual_output: 实际输出
        token_count: 实际 token 数
        tool_call_count: 工具调用次数
        error_message: 错误信息
        violations: 违规项列表
        timestamp: 测试时间
    """

    sample_name: str
    passed: bool
    actual_action: str = ""
    actual_tools: list[str] = field(default_factory=list)
    actual_output: str = ""
    token_count: int = 0
    tool_call_count: int = 0
    error_message: str = ""
    violations: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self, *, output_max_len: int = 200) -> dict[str, Any]:
        """转换为字典。

        Args:
            output_max_len: ``actual_output`` 写入报告时的最大长度（默认 200，避免报告过大）
        """
        return {
            "sample_name": self.sample_name,
            "passed": self.passed,
            "actual_action": self.actual_action,
            "actual_tools": self.actual_tools,
            "actual_output": self.actual_output[:output_max_len],
            "token_count": self.token_count,
            "tool_call_count": self.tool_call_count,
            "error_message": self.error_message,
            "violations": self.violations,
            "timestamp": self.timestamp,
        }


@dataclass
class ReportSummary:
    """测试报告：汇总所有测试结果

    Attributes:
        total: 总测试数
        passed: 通过数
        failed: 失败数
        skipped: 跳过数
        results: 各测试结果
        duration_seconds: 执行时长
        timestamp: 报告时间
    """

    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    results: list[ResultRecord] = field(default_factory=list)
    duration_seconds: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "results": [r.to_dict() for r in self.results],
            "duration_seconds": self.duration_seconds,
            "timestamp": self.timestamp,
        }

    @property
    def pass_rate(self) -> float:
        """通过率"""
        if self.total == 0:
            return 0.0
        return self.passed / self.total


__all__ = [
    "DEFAULT_SAMPLES_DIR",
    "DEFAULT_REPORT_PATH",
    "VALID_ACTIONS",
    "VALID_CATEGORIES",
    "AgentExecutionResult",
    "ExecuteAgentFn",
    "SampleSpec",
    "ResultRecord",
    "ReportSummary",
]
