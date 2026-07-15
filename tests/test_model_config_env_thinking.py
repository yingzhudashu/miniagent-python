"""model thinking 配置从 config.user.json 加载。"""


from miniagent.agent.config import get_default_model_config
from tests.test_config import _install_loader


def test_user_thinking_level_sets_budget(tmp_path) -> None:
    _install_loader(tmp_path, {"model": {"thinking_level": "medium"}})
    mc = get_default_model_config()
    assert mc.thinking_level == "medium"
    assert mc.thinking_budget == 8192


def test_user_thinking_budget_overrides_derived(tmp_path) -> None:
    _install_loader(
        tmp_path,
        {"model": {"thinking_level": "high", "thinking_budget": 12345}},
    )
    mc = get_default_model_config()
    assert mc.thinking_level == "heavy"
    assert mc.thinking_budget == 12345


def test_user_context_and_max_tokens(tmp_path) -> None:
    _install_loader(
        tmp_path,
        {"model": {"context_window": 40000, "max_tokens": 9000}},
    )
    mc = get_default_model_config()
    assert mc.context_window == 40000
    assert mc.max_tokens == 9000
