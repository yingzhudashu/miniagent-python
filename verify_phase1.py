"""Phase 1 import verification script."""

from src.types import *
from src.core.config import (
    MODEL_PROFILES,
    get_default_model_config,
    get_default_agent_config,
    apply_model_profile,
    merge_agent_config,
)

print("[OK] All Phase 1 imports successful")
print(f"  Config profiles: {list(MODEL_PROFILES.keys())}")
print(f"  Default model: {get_default_model_config().model}")
print(f"  Default max_turns: {get_default_agent_config().max_turns}")

# Verify dataclass instantiation
from src.types.planning import StructuredPlan, PlanStep, SuggestedConfig
plan = StructuredPlan(
    summary="Test plan",
    steps=[PlanStep(step_number=1, description="Step 1")],
    required_toolboxes=["filesystem"],
)
print(f"  StructuredPlan: {plan.summary}, {len(plan.steps)} steps")

# Verify config merge
base = get_default_agent_config()
merged = merge_agent_config(base, {"max_turns": 50, "debug": True})
print(f"  Merged config: max_turns={merged.max_turns}, debug={merged.debug}")

# Verify model profile
model_cfg = get_default_model_config()
precise = apply_model_profile(model_cfg, "precise")
print(f"  Precise profile: temp={precise.temperature}, thinking={precise.thinking_level}")

print("\n[DONE] Phase 1 verification complete - all types and configs working!")
