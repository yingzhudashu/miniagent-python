# 提示词编写规范

> Mini Agent Python | 版本: 2.1.0 | 基于 Claude 官方最佳实践

## 一、设计原则

### 1.1 XML 标签结构化

所有提示词必须使用 XML 标签分隔不同部分，避免指令、示例、格式要求混在一起：

```text
<role>        必需 - 角色定位和职责边界
<context>     必需 - 上下文动机，解释"为什么"
<instructions> 必需 - 明确指令，按顺序列出
<examples>    必需 - 3-5 个多样化示例
<output_format> 或 <json_schema> - 必需 - 输出格式定义
<validation>  推荐 - 自我检查验证要求
```

### 1.2 上下文动机

每个指令应解释背后的动机，帮助模型理解目标：

```text
<!-- 效果较差 -->
永远不要使用省略号

<!-- 更有效 -->
<context>
你的响应将由文本转语音引擎朗读，因此永远不要使用省略号，
因为文本转语音引擎不知道如何发音。
</context>
```

### 1.3 多样化示例

提供 3-5 个示例，覆盖不同场景和难度：

- **简单示例**：展示基础用法
- **中等示例**：展示多步操作
- **复杂示例**：展示工具协作或高风险场景
- **边界示例**：展示特殊情况处理

### 1.4 "要做什么"而非"不要做什么"

使用正面指令而非负面禁止：

```text
<!-- 效果较差 -->
不要在响应中使用 markdown

<!-- 更有效 -->
<style_guide>
使用清晰、流畅的散文，使用完整的段落和句子。
使用标准段落中断进行组织。
</style_guide>
```

### 1.5 自我验证

添加 `<validation>` 段指导模型提交前检查：

```text
<validation>
提交前请验证：
- ✓ 每个步骤都有 thinkingLevel 字段
- ✓ 涉及时效数据时 requiredToolboxes 包含 "web"
- ✓ 步骤间依赖关系正确
</validation>
```

---

## 二、标准模板

### 2.1 基础模板

```text
<role>
你是 [角色名称]，一个专业的 [领域] 专家。
你的核心职责是：
- [职责1]
- [职责2]
- [职责3]
</role>

<context>
[目标用户]需要：
- [需求1]
- [需求2]
因此，你的回答必须：
- [约束1]
- [约束2]
</context>

<instructions>
处理任务时：
1. [步骤1]
2. [步骤2]
3. [步骤3]
</instructions>

<examples>
<example index="1" type="simple">
[输入示例]
[输出示例]
</example>

<example index="2" type="medium">
[输入示例]
[输出示例]
</example>

<example index="3" type="complex">
[输入示例]
[输出示例]
</example>
</examples>

<output_format>
[输出格式说明]
</output_format>

<validation>
提交前验证：
- ✓ [检查项1]
- ✓ [检查项2]
</validation>
```

### 2.2 JSON 输出模板

```text
<json_schema>
```json
{
  "field1": "type - 描述",
  "field2": "type - 描述",
  "field3": "array - 描述"
}
```
</json_schema>

只返回 JSON，不要包含其他文字。
```

---

## 三、提示词模块结构

### 3.1 目录结构

```
miniagent/core/prompts/
├── __init__.py              # 导出所有提示词
├── identity.py              # AGENT_IDENTITY
├── planner.py               # PLAN_SYSTEM_PROMPT
├── classifier.py            # CLASSIFIER_PROMPT
├── clarifier.py             # CLARIFIER_PROMPT
├── reflector.py             # REFLECTOR_PROMPT
├── reviewer.py              # REVIEW_PROMPT
├── improver.py              # IMPROVE_PROMPT
└── feishu_channel.py        # 飞书通道提示词
```

### 3.2 导出方式

`__init__.py` 统一导出：

```python
from miniagent.core.prompts.identity import AGENT_IDENTITY
from miniagent.core.prompts.planner import PLAN_SYSTEM_PROMPT
# ...

__all__ = [
    "AGENT_IDENTITY",
    "PLAN_SYSTEM_PROMPT",
    # ...
]
```

### 3.3 消费者导入

消费者模块从 `prompts` 模块导入：

```python
# executor.py
from miniagent.core.prompts.identity import AGENT_IDENTITY

# planner.py  
from miniagent.core.prompts.planner import PLAN_SYSTEM_PROMPT

# 或从 __init__ 导入
from miniagent.core.prompts import AGENT_IDENTITY, PLAN_SYSTEM_PROMPT
```

---

## 四、现有提示词说明

### 4.1 AGENT_IDENTITY（核心身份）

**用途**：执行阶段系统提示词的基础，定义 Agent 的角色、能力和行为规范。

**关键标签**：
- `<role>`：Agent 身份定位
- `<style_guide>`：回答风格规范
- `<default_to_action>`：默认行动而非建议
- `<parallel_execution>`：并行工具调用指导
- `<avoid_over_engineering>`：避免过度设计
- `<investigate_before_answering>`：调查后再回答

### 4.2 PLAN_SYSTEM_PROMPT（规划阶段）

**用途**：Phase 1 规划器，将用户需求分解为结构化执行计划。

**关键标签**：
- `<instructions>`：5 步规划流程
- `<examples>`：4 个示例（简单/中等/复杂/高风险）
- `<json_schema>`：完整 JSON 输出格式
- `<validation>`：提交前检查项

### 4.3 CLASSIFIER_PROMPT（任务分类）

**用途**：Phase 0 任务难度预分类。

**关键标签**：
- `<difficulty_levels>`：四档难度定义表格
- `<examples>`：5 个示例覆盖各档位
- `<output_format>`：单字段 JSON 输出

### 4.4 CLARIFIER_PROMPT（需求澄清）

**用途**：Phase 0.5 三步需求澄清。

**关键标签**：
- `<role>`：三步方法论说明
- `<important>`：历史记忆利用提示

### 4.5 REFLECTOR_PROMPT（结果反思）

**用途**：Phase 3 结果质量评估。

**关键标签**：
- `<instructions>`：5 步评估流程
- `<examples>`：3 个示例（可接受/需改进/有错误）
- `<validation>`：acceptable=false 时建议数量要求

### 4.6 REVIEW_PROMPT（答案审查）

**用途**：`/review` 命令，多维度审查答案质量。

**关键标签**：
- `<instructions>`：5 维度审查
- `<examples>`：3 个示例

### 4.7 IMPROVE_PROMPT（答案改进）

**用途**：`/improve` 命令，根据审查建议改进答案。

**关键标签**：
- `<instructions>`：4 步优化流程
- `<examples>`：2 个示例

---

## 五、测试验证

### 5.1 结构测试

每个提示词应通过 `tests/test_prompts_structure.py` 的验证：

- 包含必需的 XML 标签
- 示例数量 ≥ 3
- JSON schema 定义完整
- 标签正确闭合

### 5.2 功能测试

运行现有测试确保向后兼容：

```bash
pytest tests/ -v
```

---

## 六、更新指南

### 6.1 修改现有提示词

1. 在对应文件中修改内容
2. 运行结构测试验证格式
3. 运行功能测试确保兼容

### 6.2 新增提示词

1. 在 `miniagent/core/prompts/` 下创建新文件
2. 使用标准模板编写
3. 在 `__init__.py` 中导出
4. 编写结构测试
5. 更新 ARCHITECTURE.md

### 6.3 删除提示词

1. 从消费者模块移除导入
2. 删除提示词文件
3. 从 `__init__.py` 移除导出
4. 更新文档

---

## 七、参考资源

- [Claude 提示词最佳实践](https://platform.claude.com/docs/zh-CN/build-with-claude/prompt-engineering/claude-prompting-best-practices)
- [ARCHITECTURE.md](ARCHITECTURE.md) §提示词模块
- [tests/test_prompts_structure.py](../tests/test_prompts_structure.py)