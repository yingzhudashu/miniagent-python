"""规划 JSON 中每步 thinkingLevel 与回填。"""

from miniagent.core import planner as planner_mod


def test_dict_to_plan_fills_step_thinking_from_default() -> None:
    data = {
        "summary": "s",
        "steps": [
            {
                "stepNumber": 1,
                "description": "d1",
                "requiredToolboxes": [],
                "expectedInput": "",
                "expectedOutput": "",
                "dependsOn": None,
            }
        ],
        "requiredToolboxes": [],
        "suggestedConfig": {},
        "estimatedTokens": {},
        "contextStrategy": {},
        "requiresConfirmation": False,
        "riskLevel": "low",
        "estimatedCost": {},
        "outputSpec": {},
        "fallbackPlan": {},
    }
    plan = planner_mod._dict_to_plan(data, default_step_thinking="high")
    assert plan.steps[0].thinking_level == "high"


def test_dict_to_plan_respects_step_thinking_level() -> None:
    data = {
        "summary": "s",
        "steps": [
            {
                "stepNumber": 1,
                "description": "d1",
                "requiredToolboxes": [],
                "expectedInput": "",
                "expectedOutput": "",
                "dependsOn": None,
                "thinkingLevel": "low",
            }
        ],
        "requiredToolboxes": [],
        "defaultStepThinkingLevel": "high",
        "suggestedConfig": {},
        "estimatedTokens": {},
        "contextStrategy": {},
        "requiresConfirmation": False,
        "riskLevel": "low",
        "estimatedCost": {},
        "outputSpec": {},
        "fallbackPlan": {},
    }
    plan = planner_mod._dict_to_plan(data, default_step_thinking="medium")
    assert plan.steps[0].thinking_level == "low"


def _minimal_plan_data(**overrides: object) -> dict:
    base = {
        "summary": "s",
        "steps": [],
        "requiredToolboxes": [],
        "suggestedConfig": {},
        "estimatedTokens": {},
        "contextStrategy": {},
        "requiresConfirmation": False,
        "riskLevel": "low",
        "estimatedCost": {},
        "outputSpec": {},
        "fallbackPlan": {},
    }
    base.update(overrides)
    return base


def test_dict_to_plan_accepts_string_steps() -> None:
    data = _minimal_plan_data(steps=["读取 config.json", "分析配置内容"])

    plan = planner_mod._dict_to_plan(data)

    assert len(plan.steps) == 2
    assert plan.steps[0].description == "读取 config.json"
    assert plan.steps[1].description == "分析配置内容"
    assert plan.steps[0].step_number == 1
    assert plan.steps[1].step_number == 2


def test_dict_to_plan_falls_back_invalid_enums() -> None:
    data = _minimal_plan_data(
        contextStrategy={"mode": "invalid_mode", "reason": "x"},
        outputSpec={"format": "pdf", "language": "zh-CN"},
    )

    plan = planner_mod._dict_to_plan(data)

    assert plan.context_strategy.mode == "normal"
    assert plan.output_spec.format == "markdown"
