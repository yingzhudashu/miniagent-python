"""Mini Agent Python — 自测模块

提供 Agent 运行时自测能力，包括：
- 工具调用测试：验证工具选择是否正确
- Schema 测试：验证模型输出是否符合结构
- 安全测试：验证是否拒绝越权操作
- Prompt injection 测试：验证防御恶意输入
- 回归测试：验证历史失败样本是否修复
- 成本测试：验证 token 和调用次数是否在预算内
"""

from miniagent.testing.test_runner import TestRunner, run_self_test
from miniagent.testing.types import (
    ReportSummary,
    ResultRecord,
    SampleSpec,
    TestReport,
    TestResult,
    TestSample,
)

__all__ = [
    "TestSample",
    "TestResult",
    "TestReport",
    "SampleSpec",
    "ResultRecord",
    "ReportSummary",
    "TestRunner",
    "run_self_test",
]