"""MiniAgent 系统提示词模块。

根据 Claude 最佳实践，使用 XML 标签结构化提示词：
- <role>：角色定位和职责边界
- <context>：上下文动机和背景说明
- <instructions>：明确指令和操作步骤
- <examples>：3-5 个高质量示例
- <output_format>：输出格式和 JSON schema
- <validation>：自我检查验证要求

模块结构：
- identity.py：AGENT_IDENTITY（核心身份）
- planner.py：PLAN_SYSTEM_PROMPT（规划阶段）
- classifier.py：CLASSIFIER_PROMPT（任务分类）
- clarifier.py：CLARIFIER_PROMPT（需求澄清）
- reflector.py：REFLECTOR_PROMPT（结果反思）
- reviewer.py：REVIEW_PROMPT（答案审查）
- improver.py：IMPROVE_PROMPT（答案改进）
- feishu_channel.py：FEISHU_CHANNEL_HINT（飞书通道）

所有提示词遵循统一规范：
1. 明确角色定位，包含"为什么"
2. 提供 3-5 个多样化示例
3. 使用 XML 标签分隔不同部分
4. 包含输出格式和验证要求
"""

from miniagent.agent.prompts.clarifier import CLARIFIER_PROMPT
from miniagent.agent.prompts.classifier import CLASSIFIER_PROMPT
from miniagent.agent.prompts.feishu_channel import (
    FEISHU_CHANNEL_HINT_WITH_TOOLS,
    FEISHU_CHANNEL_HINT_WITHOUT_TOOLS,
)
from miniagent.agent.prompts.identity import AGENT_IDENTITY
from miniagent.agent.prompts.improver import IMPROVE_PROMPT
from miniagent.agent.prompts.planner import PLAN_SYSTEM_PROMPT
from miniagent.agent.prompts.reflector import REFLECTOR_PROMPT
from miniagent.agent.prompts.reviewer import REVIEW_ITERATION_PROMPT, REVIEW_PROMPT

__all__ = [
    # 核心身份
    "AGENT_IDENTITY",
    # 规划阶段
    "PLAN_SYSTEM_PROMPT",
    # 任务分类
    "CLASSIFIER_PROMPT",
    # 需求澄清
    "CLARIFIER_PROMPT",
    # 结果反思
    "REFLECTOR_PROMPT",
    # 答案审查
    "REVIEW_PROMPT",
    "REVIEW_ITERATION_PROMPT",
    # 答案改进
    "IMPROVE_PROMPT",
    # 飞书通道
    "FEISHU_CHANNEL_HINT_WITH_TOOLS",
    "FEISHU_CHANNEL_HINT_WITHOUT_TOOLS",
]