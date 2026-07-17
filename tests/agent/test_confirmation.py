"""Confirmation Types 确认机制测试

测试 miniagent/types/confirmation.py 的确认类型定义。
"""


from miniagent.agent.types.confirmation import (
    ConfirmationRequest,
    ConfirmationResult,
    ConfirmationStage,
)


class TestConfirmationStage:
    """ConfirmationStage 枚举测试"""

    def test_confirmation_stage_values(self):
        """测试枚举值"""
        assert ConfirmationStage.CLARIFICATION.value == "clarification"
        assert ConfirmationStage.PLAN.value == "plan"
        assert ConfirmationStage.TOOL.value == "tool"

    def test_confirmation_stage_membership(self):
        """测试枚举成员"""
        assert ConfirmationStage.CLARIFICATION in ConfirmationStage
        assert ConfirmationStage.PLAN in ConfirmationStage
        assert ConfirmationStage.TOOL in ConfirmationStage

    def test_confirmation_stage_count(self):
        """测试枚举成员数量"""
        assert len(ConfirmationStage) == 3

    def test_confirmation_stage_comparison(self):
        """测试枚举比较"""
        assert ConfirmationStage.CLARIFICATION != ConfirmationStage.PLAN
        assert ConfirmationStage.CLARIFICATION == ConfirmationStage.CLARIFICATION


class TestConfirmationRequest:
    """ConfirmationRequest 数据类测试"""

    def test_confirmation_request_creation_basic(self):
        """测试基本创建"""
        request = ConfirmationRequest(
            stage=ConfirmationStage.CLARIFICATION,
            content="需求已澄清，请确认",
        )
        assert request.stage == ConfirmationStage.CLARIFICATION
        assert request.content == "需求已澄清，请确认"
        assert request.full_content == ""
        assert request.context == {}

    def test_confirmation_request_creation_full(self):
        """测试完整创建"""
        request = ConfirmationRequest(
            stage=ConfirmationStage.PLAN,
            content="执行计划确认",
            full_content="详细计划内容...",
            context={"plan": {"steps": ["步骤1", "步骤2"]}},
        )
        assert request.stage == ConfirmationStage.PLAN
        assert request.content == "执行计划确认"
        assert request.full_content == "详细计划内容..."
        assert "plan" in request.context

    def test_confirmation_request_field_defaults(self):
        """测试字段默认值"""
        request = ConfirmationRequest(
            stage=ConfirmationStage.CLARIFICATION,
            content="测试",
        )
        # 验证默认值
        assert request.full_content == ""
        assert request.context == {}

    def test_confirmation_request_context_is_dict(self):
        """测试context字段是字典"""
        request = ConfirmationRequest(
            stage=ConfirmationStage.PLAN,
            content="计划确认",
            context={"key": "value"},
        )
        assert isinstance(request.context, dict)
        assert request.context["key"] == "value"

    def test_confirmation_request_empty_context(self):
        """测试空context"""
        request1 = ConfirmationRequest(
            stage=ConfirmationStage.CLARIFICATION,
            content="测试",
            context={},
        )
        request2 = ConfirmationRequest(
            stage=ConfirmationStage.CLARIFICATION,
            content="测试",
        )
        assert request1.context == request2.context

    def test_confirmation_request_context_is_mutable(self):
        request = ConfirmationRequest(
            stage=ConfirmationStage.CLARIFICATION,
            content="context mutation",
        )

        request.context["key"] = "value"
        request.context["count"] = 10

        assert request.context == {"key": "value", "count": 10}


class TestConfirmationResult:
    """ConfirmationResult 数据类测试"""

    def test_confirmation_result_approved(self):
        """测试批准结果"""
        result = ConfirmationResult(approved=True)
        assert result.approved is True
        assert result.adjustment is None
        assert result.rejected is False

    def test_confirmation_result_rejected(self):
        """测试拒绝结果"""
        result = ConfirmationResult(approved=False, rejected=True)
        assert result.approved is False
        assert result.rejected is True

    def test_confirmation_result_adjustment(self):
        """测试调整结果"""
        result = ConfirmationResult(
            approved=True,
            adjustment="修改步骤2",
        )
        assert result.approved is True
        assert result.adjustment == "修改步骤2"
        assert result.rejected is False

    def test_confirmation_result_adjustment_with_rejection(self):
        """测试调整但拒绝的结果"""
        result = ConfirmationResult(
            approved=False,
            adjustment="重新规划",
            rejected=False,
        )
        assert result.approved is False
        assert result.adjustment == "重新规划"
        assert result.rejected is False

    def test_confirmation_result_field_defaults(self):
        """测试字段默认值"""
        result = ConfirmationResult(approved=True)
        assert result.adjustment is None
        assert result.rejected is False


class TestConfirmationRequestDataClass:
    """ConfirmationRequest 数据类特性测试"""

    def test_confirmation_request_equality(self):
        """测试数据类相等性"""
        request1 = ConfirmationRequest(
            stage=ConfirmationStage.CLARIFICATION,
            content="确认",
        )
        request2 = ConfirmationRequest(
            stage=ConfirmationStage.CLARIFICATION,
            content="确认",
        )
        assert request1 == request2

    def test_confirmation_request_inequality(self):
        """测试数据类不等性"""
        request1 = ConfirmationRequest(
            stage=ConfirmationStage.CLARIFICATION,
            content="确认1",
        )
        request2 = ConfirmationRequest(
            stage=ConfirmationStage.PLAN,
            content="确认2",
        )
        assert request1 != request2


class TestConfirmationResultDataClass:
    """ConfirmationResult 数据类特性测试"""

    def test_confirmation_result_equality(self):
        """测试数据类相等性"""
        result1 = ConfirmationResult(approved=True)
        result2 = ConfirmationResult(approved=True)
        assert result1 == result2

    def test_confirmation_result_inequality(self):
        """测试数据类不等性"""
        result1 = ConfirmationResult(approved=True)
        result2 = ConfirmationResult(approved=False)
        assert result1 != result2

    def test_confirmation_result_with_adjustment_equality(self):
        """测试带调整的相等性"""
        result1 = ConfirmationResult(approved=True, adjustment="调整")
        result2 = ConfirmationResult(approved=True, adjustment="调整")
        assert result1 == result2


class TestConfirmationIntegration:
    """确认机制集成测试"""

    def test_confirmation_request_to_result_flow(self):
        """测试请求到结果的流程"""
        # 创建确认请求
        request = ConfirmationRequest(
            stage=ConfirmationStage.PLAN,
            content="执行计划确认",
            full_content="完整计划详情",
            context={"plan_id": "123"},
        )

        # 模拟用户批准
        result = ConfirmationResult(approved=True)

        # 验证流程完整性
        assert request.stage == ConfirmationStage.PLAN
        assert result.approved is True

    def test_confirmation_rejection_flow(self):
        """测试拒绝流程"""
        request = ConfirmationRequest(
            stage=ConfirmationStage.CLARIFICATION,
            content="需求澄清确认",
        )

        # 模拟用户拒绝
        result = ConfirmationResult(approved=False, rejected=True)

        # 验证拒绝流程
        assert request.stage == ConfirmationStage.CLARIFICATION
        assert result.approved is False
        assert result.rejected is True

    def test_confirmation_adjustment_flow(self):
        """测试调整流程"""
        request = ConfirmationRequest(
            stage=ConfirmationStage.PLAN,
            content="执行计划确认",
            full_content="原始计划详情",
        )

        # 模拟用户调整
        result = ConfirmationResult(
            approved=True,
            adjustment="修改步骤顺序",
        )

        # 验证调整流程
        assert request.stage == ConfirmationStage.PLAN
        assert result.approved is True
        assert result.adjustment == "修改步骤顺序"

    def test_confirmation_context_preservation(self):
        """测试context上下文保持"""
        context = {
            "plan": {
                "steps": ["步骤1", "步骤2"],
                "toolboxes": ["toolbox1"],
            },
            "session_key": "default",
        }

        request = ConfirmationRequest(
            stage=ConfirmationStage.PLAN,
            content="计划确认",
            context=context,
        )

        # 验证context完整性
        assert request.context == context
        assert request.context["plan"]["steps"] == ["步骤1", "步骤2"]
        assert request.context["session_key"] == "default"


class TestConfirmationResultSemantics:
    """ConfirmationResult 语义与工厂方法。"""

    def test_confirm_factory(self):
        result = ConfirmationResult.confirm()
        assert result.plan_action() == ("proceed", None)

    def test_reject_factory(self):
        result = ConfirmationResult.reject()
        assert result.approved is False
        assert result.rejected is True
        assert result.plan_action() == ("cancel", None)

    def test_adjust_factory(self):
        result = ConfirmationResult.adjust("修改步骤 2")
        assert result.plan_action() == ("replan", "修改步骤 2")

    def test_clarification_reply_factory(self):
        result = ConfirmationResult.clarification_reply("  补充需求  ")
        assert result.approved is True
        assert result.adjustment == "补充需求"
        assert result.rejected is False

    def test_plan_action_replan_without_approval(self):
        result = ConfirmationResult(approved=False, adjustment="重新规划", rejected=False)
        assert result.plan_action() == ("replan", "重新规划")

    def test_rejected_normalizes_approved(self):
        result = ConfirmationResult(approved=True, rejected=True)
        assert result.approved is False
        assert result.rejected is True


class TestConfirmationStageUsage:
    """ConfirmationStage 使用场景测试"""

    def test_stage_clarification_scenario(self):
        """测试澄清阶段场景"""
        request = ConfirmationRequest(
            stage=ConfirmationStage.CLARIFICATION,
            content="需求已澄清：编写排序算法",
        )
        assert request.stage == ConfirmationStage.CLARIFICATION
        assert "排序算法" in request.content

    def test_stage_plan_scenario(self):
        """测试规划阶段场景"""
        request = ConfirmationRequest(
            stage=ConfirmationStage.PLAN,
            content="执行计划确认：读取文件 → 处理数据 → 写入结果",
            context={"estimated_tokens": 500},
        )
        assert request.stage == ConfirmationStage.PLAN
        assert "读取文件" in request.content
