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
