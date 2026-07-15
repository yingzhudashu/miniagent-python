"""Mini Agent Python — 自测模块

提供 Agent 运行时自测能力：

- **mock 模式**（默认）：校验 ``tests/evaluation/samples`` 中 JSON 样本字段自洽，
  并用理想化模拟输出验证约束是否可满足；**不调用 LLM**。
- **real 模式**：注入 :class:`ExecuteAgentFn` 后调用真实 Agent，验证工具选择、
  安全拒绝、输出模式、token/调用次数等。

CLI：``/test run``（mock）、``/test run real``（真实 Agent，需 registry/engine）。
"""

from miniagent.assistant.testing.agent_adapter import (
    build_execute_agent,
    build_execute_agent_from_engine,
)
from miniagent.assistant.testing.test_runner import TestRunner, run_self_test
from miniagent.assistant.testing.types import (
    DEFAULT_REPORT_PATH,
    DEFAULT_SAMPLES_DIR,
    VALID_ACTIONS,
    VALID_CATEGORIES,
    AgentExecutionResult,
    ExecuteAgentFn,
    ReportSummary,
    ResultRecord,
    SampleSpec,
)

__all__ = [
    "DEFAULT_REPORT_PATH",
    "DEFAULT_SAMPLES_DIR",
    "VALID_ACTIONS",
    "VALID_CATEGORIES",
    "AgentExecutionResult",
    "ExecuteAgentFn",
    "SampleSpec",
    "ResultRecord",
    "ReportSummary",
    "TestRunner",
    "run_self_test",
    "build_execute_agent",
    "build_execute_agent_from_engine",
]
