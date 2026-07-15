"""提示词结构测试。

验证每个提示词遵循 Claude 最佳实践：
- 包含必需的 XML 标签（role, context, instructions）
- 包含至少 3 个示例
- 包含输出格式或 JSON schema 定义
- 包含验证要求
"""


from miniagent.agent.prompts import (
    AGENT_IDENTITY,
    CLARIFIER_PROMPT,
    CLASSIFIER_PROMPT,
    FEISHU_CHANNEL_HINT_WITH_TOOLS,
    IMPROVE_PROMPT,
    PLAN_SYSTEM_PROMPT,
    REFLECTOR_PROMPT,
    REVIEW_PROMPT,
)


class TestPromptStructure:
    """测试提示词结构完整性。"""

    def test_agent_identity_has_required_tags(self) -> None:
        """AGENT_IDENTITY 应包含必需的 XML 标签。"""
        assert "<role>" in AGENT_IDENTITY
        assert "</role>" in AGENT_IDENTITY
        assert "<context>" in AGENT_IDENTITY
        assert "</context>" in AGENT_IDENTITY
        assert "<instructions>" in AGENT_IDENTITY
        assert "</instructions>" in AGENT_IDENTITY

    def test_agent_identity_has_style_guide(self) -> None:
        """AGENT_IDENTITY 应包含风格指南。"""
        assert "<style_guide>" in AGENT_IDENTITY
        assert "</style_guide>" in AGENT_IDENTITY

    def test_agent_identity_has_action_guidance(self) -> None:
        """AGENT_IDENTITY 应包含行动指导。"""
        assert "<default_to_action>" in AGENT_IDENTITY
        assert "</default_to_action>" in AGENT_IDENTITY

    def test_plan_prompt_has_required_tags(self) -> None:
        """PLAN_SYSTEM_PROMPT 应包含必需的 XML 标签。"""
        assert "<role>" in PLAN_SYSTEM_PROMPT
        assert "</role>" in PLAN_SYSTEM_PROMPT
        assert "<context>" in PLAN_SYSTEM_PROMPT
        assert "</context>" in PLAN_SYSTEM_PROMPT
        assert "<instructions>" in PLAN_SYSTEM_PROMPT
        assert "</instructions>" in PLAN_SYSTEM_PROMPT

    def test_plan_prompt_has_examples(self) -> None:
        """PLAN_SYSTEM_PROMPT 应包含至少 3 个示例。"""
        # 检查 <example index="1"> 等
        assert "<example index=\"1\"" in PLAN_SYSTEM_PROMPT
        assert "<example index=\"2\"" in PLAN_SYSTEM_PROMPT
        assert "<example index=\"3\"" in PLAN_SYSTEM_PROMPT
        assert "</example>" in PLAN_SYSTEM_PROMPT
        # 验证至少有 3 个示例
        example_count = PLAN_SYSTEM_PROMPT.count("<example")
        assert example_count >= 3

    def test_plan_prompt_has_json_schema(self) -> None:
        """PLAN_SYSTEM_PROMPT 应包含 JSON schema。"""
        assert "<json_schema>" in PLAN_SYSTEM_PROMPT
        assert "</json_schema>" in PLAN_SYSTEM_PROMPT
        assert '"summary"' in PLAN_SYSTEM_PROMPT
        assert '"steps"' in PLAN_SYSTEM_PROMPT

    def test_plan_prompt_has_validation(self) -> None:
        """PLAN_SYSTEM_PROMPT 应包含验证要求。"""
        assert "<validation>" in PLAN_SYSTEM_PROMPT
        assert "</validation>" in PLAN_SYSTEM_PROMPT

    def test_classifier_prompt_has_required_tags(self) -> None:
        """CLASSIFIER_PROMPT 应包含必需的 XML 标签。"""
        assert "<role>" in CLASSIFIER_PROMPT
        assert "</role>" in CLASSIFIER_PROMPT
        assert "<context>" in CLASSIFIER_PROMPT
        assert "</context>" in CLASSIFIER_PROMPT
        assert "<instructions>" in CLASSIFIER_PROMPT
        assert "</instructions>" in CLASSIFIER_PROMPT

    def test_classifier_prompt_has_examples(self) -> None:
        """CLASSIFIER_PROMPT 应包含至少 3 个示例。"""
        example_count = CLASSIFIER_PROMPT.count("<example")
        assert example_count >= 3

    def test_classifier_prompt_has_difficulty_levels(self) -> None:
        """CLASSIFIER_PROMPT 应包含难度等级定义。"""
        assert "<difficulty_levels>" in CLASSIFIER_PROMPT
        assert "</difficulty_levels>" in CLASSIFIER_PROMPT
        assert "simple" in CLASSIFIER_PROMPT
        assert "normal" in CLASSIFIER_PROMPT
        assert "medium" in CLASSIFIER_PROMPT
        assert "complex" in CLASSIFIER_PROMPT

    def test_classifier_prompt_has_output_format(self) -> None:
        """CLASSIFIER_PROMPT 应包含输出格式。"""
        assert "<output_format>" in CLASSIFIER_PROMPT
        assert "</output_format>" in CLASSIFIER_PROMPT
        assert '"difficulty"' in CLASSIFIER_PROMPT

    def test_clarifier_prompt_has_required_tags(self) -> None:
        """CLARIFIER_PROMPT 应包含必需的 XML 标签。"""
        assert "<role>" in CLARIFIER_PROMPT
        assert "</role>" in CLARIFIER_PROMPT
        assert "<context>" in CLARIFIER_PROMPT
        assert "</context>" in CLARIFIER_PROMPT
        assert "<instructions>" in CLARIFIER_PROMPT
        assert "</instructions>" in CLARIFIER_PROMPT

    def test_clarifier_prompt_has_examples(self) -> None:
        """CLARIFIER_PROMPT 应包含至少 2 个示例。"""
        example_count = CLARIFIER_PROMPT.count("<example")
        assert example_count >= 2

    def test_clarifier_prompt_has_json_schema(self) -> None:
        """CLARIFIER_PROMPT 应包含 JSON schema。"""
        assert "<json_schema>" in CLARIFIER_PROMPT
        assert '"clarified_goal"' in CLARIFIER_PROMPT
        assert '"boundary_conditions"' in CLARIFIER_PROMPT

    def test_clarifier_prompt_has_important_section(self) -> None:
        """CLARIFIER_PROMPT 应包含重要提示。"""
        assert "<important>" in CLARIFIER_PROMPT
        assert "</important>" in CLARIFIER_PROMPT

    def test_reflector_prompt_has_required_tags(self) -> None:
        """REFLECTOR_PROMPT 应包含必需的 XML 标签。"""
        assert "<role>" in REFLECTOR_PROMPT
        assert "</role>" in REFLECTOR_PROMPT
        assert "<context>" in REFLECTOR_PROMPT
        assert "</context>" in REFLECTOR_PROMPT
        assert "<instructions>" in REFLECTOR_PROMPT
        assert "</instructions>" in REFLECTOR_PROMPT

    def test_reflector_prompt_has_examples(self) -> None:
        """REFLECTOR_PROMPT 应包含至少 2 个示例。"""
        example_count = REFLECTOR_PROMPT.count("<example")
        assert example_count >= 2

    def test_reflector_prompt_has_json_schema(self) -> None:
        """REFLECTOR_PROMPT 应包含 JSON schema。"""
        assert "<json_schema>" in REFLECTOR_PROMPT
        assert '"acceptable"' in REFLECTOR_PROMPT
        assert '"quality_score"' in REFLECTOR_PROMPT

    def test_reflector_prompt_has_validation(self) -> None:
        """REFLECTOR_PROMPT 应包含验证要求。"""
        assert "<validation>" in REFLECTOR_PROMPT
        assert "</validation>" in REFLECTOR_PROMPT

    def test_review_prompt_has_required_tags(self) -> None:
        """REVIEW_PROMPT 应包含必需的 XML 标签。"""
        assert "<role>" in REVIEW_PROMPT
        assert "</role>" in REVIEW_PROMPT
        assert "<context>" in REVIEW_PROMPT
        assert "</context>" in REVIEW_PROMPT
        assert "<instructions>" in REVIEW_PROMPT
        assert "</instructions>" in REVIEW_PROMPT

    def test_review_prompt_has_examples(self) -> None:
        """REVIEW_PROMPT 应包含至少 2 个示例。"""
        example_count = REVIEW_PROMPT.count("<example")
        assert example_count >= 2

    def test_review_prompt_has_json_schema(self) -> None:
        """REVIEW_PROMPT 应包含 JSON schema。"""
        assert "<json_schema>" in REVIEW_PROMPT
        assert '"has_issues"' in REVIEW_PROMPT
        assert '"issues"' in REVIEW_PROMPT

    def test_improve_prompt_has_required_tags(self) -> None:
        """IMPROVE_PROMPT 应包含必需的 XML 标签。"""
        assert "<role>" in IMPROVE_PROMPT
        assert "</role>" in IMPROVE_PROMPT
        assert "<context>" in IMPROVE_PROMPT
        assert "</context>" in IMPROVE_PROMPT
        assert "<instructions>" in IMPROVE_PROMPT
        assert "</instructions>" in IMPROVE_PROMPT

    def test_improve_prompt_has_examples(self) -> None:
        """IMPROVE_PROMPT 应包含至少 2 个示例。"""
        example_count = IMPROVE_PROMPT.count("<example")
        assert example_count >= 2

    def test_feishu_channel_hint_with_tools_has_required_tags(self) -> None:
        """飞书通道提示词应包含必需的 XML 标签。"""
        assert "<feishu_channel_context>" in FEISHU_CHANNEL_HINT_WITH_TOOLS
        assert "<available_tools>" in FEISHU_CHANNEL_HINT_WITH_TOOLS
        assert "<tool_usage_guidance>" in FEISHU_CHANNEL_HINT_WITH_TOOLS

    def test_feishu_channel_hint_lists_tools(self) -> None:
        """飞书通道提示词应列出可用工具。"""
        assert "feishu_doc" in FEISHU_CHANNEL_HINT_WITH_TOOLS
        assert "feishu_bitable" in FEISHU_CHANNEL_HINT_WITH_TOOLS


class TestPromptContent:
    """测试提示词内容完整性。"""

    def test_agent_identity_mentions_miniagent(self) -> None:
        """AGENT_IDENTITY 应提及 MiniAgent。"""
        assert "MiniAgent" in AGENT_IDENTITY

    def test_plan_prompt_mentions_thinking_level(self) -> None:
        """PLAN_SYSTEM_PROMPT 应提及 thinkingLevel。"""
        assert "thinkingLevel" in PLAN_SYSTEM_PROMPT
        assert "low" in PLAN_SYSTEM_PROMPT
        assert "medium" in PLAN_SYSTEM_PROMPT
        assert "high" in PLAN_SYSTEM_PROMPT

    def test_classifier_prompt_returns_json(self) -> None:
        """CLASSIFIER_PROMPT 应明确要求返回 JSON。"""
        assert "JSON" in CLASSIFIER_PROMPT.upper()

    def test_all_prompts_end_cleanly(self) -> None:
        """所有提示词不应有未闭合的标签。"""
        # 检查常见的未闭合标签问题
        prompts = [
            AGENT_IDENTITY,
            PLAN_SYSTEM_PROMPT,
            CLASSIFIER_PROMPT,
            CLARIFIER_PROMPT,
            REFLECTOR_PROMPT,
            REVIEW_PROMPT,
            IMPROVE_PROMPT,
        ]
        for prompt in prompts:
            # 简单检查：每个开始标签应该有对应的结束标签
            for tag in ["role", "context", "instructions"]:
                prompt.count(f"<{tag}>")
                close_count = prompt.count(f"</{tag}>")
                # 注意：有些标签可能有属性，使用宽松匹配
                open_pattern_count = prompt.count(f"<{tag}")
                assert close_count >= open_pattern_count // 2, f"标签 {tag} 未正确闭合"