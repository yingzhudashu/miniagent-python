"""测试确认类型 — confirmation.py 的数据类和枚举。

覆盖 ConfirmationStage、ConfirmationRequest、ConfirmationResult 的基本功能。
"""

from miniagent.types.confirmation import (
    ConfirmationStage,
    ConfirmationRequest,
    ConfirmationResult,
)


def test_confirmation_stage_enum_values():
    """测试确认阶段枚举值正确性。"""
    assert ConfirmationStage.CLARIFICATION.value == "clarification"
    assert ConfirmationStage.PLAN.value == "plan"
    assert len(ConfirmationStage) == 2


def test_confirmation_request_creation():
    """测试确认请求创建。"""
    # 最小参数
    req = ConfirmationRequest(
        stage=ConfirmationStage.CLARIFICATION,
        content="请确认需求澄清结果",
    )
    assert req.stage == ConfirmationStage.CLARIFICATION
    assert req.content == "请确认需求澄清结果"
    assert req.full_content == ""
    assert req.context == {}

    # 完整参数
    req2 = ConfirmationRequest(
        stage=ConfirmationStage.PLAN,
        content="请确认执行计划",
        full_content="详细计划内容...",
        context={"plan_id": "test-123", "steps": 5},
    )
    assert req2.stage == ConfirmationStage.PLAN
    assert req2.full_content == "详细计划内容..."
    assert req2.context["plan_id"] == "test-123"
    assert req2.context["steps"] == 5


def test_confirmation_result_approved():
    """测试批准结果。"""
    # 仅批准
    result = ConfirmationResult(approved=True)
    assert result.approved is True
    assert result.adjustment is None
    assert result.rejected is False

    # 批准+调整
    result2 = ConfirmationResult(approved=True, adjustment="修改步骤2")
    assert result2.approved is True
    assert result2.adjustment == "修改步骤2"
    assert result2.rejected is False


def test_confirmation_result_rejected():
    """测试拒绝结果。"""
    # 直接拒绝
    result = ConfirmationResult(approved=False, rejected=True)
    assert result.approved is False
    assert result.rejected is True
    assert result.adjustment is None

    # 拒绝+调整（视为调整而非取消）
    result2 = ConfirmationResult(approved=False, adjustment="重新规划", rejected=False)
    assert result2.approved is False
    assert result2.adjustment == "重新规划"
    assert result2.rejected is False


def test_confirmation_request_context_mutation():
    """测试确认请求上下文可变性。"""
    req = ConfirmationRequest(
        stage=ConfirmationStage.CLARIFICATION,
        content="测试内容",
    )
    req.context["key"] = "value"
    assert req.context["key"] == "value"

    # 多次添加
    req.context["count"] = 10
    assert len(req.context) == 2


def test_confirmation_result_dataclass_equality():
    """测试确认结果数据类相等性。"""
    result1 = ConfirmationResult(approved=True, adjustment="test")
    result2 = ConfirmationResult(approved=True, adjustment="test")
    assert result1 == result2

    result3 = ConfirmationResult(approved=False)
    assert result1 != result3