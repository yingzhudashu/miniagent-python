"""constants.py 与各模块接线回归测试。"""

from __future__ import annotations

from miniagent.core.agent import _clarifier_max_questions_for_difficulty
from miniagent.core.constants import (
    BITABLE_DEFAULT_PAGE_SIZE,
    CLARIFIER_MAX_QUESTIONS_COMPLEX,
    CLARIFIER_MAX_QUESTIONS_MEDIUM,
    CLARIFIER_MAX_QUESTIONS_NORMAL,
    CLARIFIER_MAX_QUESTIONS_SIMPLE,
    HISTORY_ARCHIVE_MAX_MESSAGES,
    IMPROVE_MAX_ITERATIONS,
    KEYWORD_EXTRACT_MAX,
    KEYWORD_INDEX_MAX_KEYWORDS,
    PLANNER_MAX_RETRIES,
)
from miniagent.core.planner import PLANNER_MAX_RETRIES as PLANNER_RETRIES_IN_PLANNER
from miniagent.core.task_classifier import TaskDifficulty
from miniagent.engine import command_dispatch
from miniagent.feishu.bitable import client as bitable_client
from miniagent.memory import history_archive, keyword_index


def test_planner_retries_use_shared_constant() -> None:
    assert PLANNER_RETRIES_IN_PLANNER is PLANNER_MAX_RETRIES
    assert PLANNER_MAX_RETRIES == 3


def test_clarifier_limits_by_difficulty() -> None:
    assert _clarifier_max_questions_for_difficulty(TaskDifficulty.SIMPLE) == (
        CLARIFIER_MAX_QUESTIONS_SIMPLE
    )
    assert _clarifier_max_questions_for_difficulty(TaskDifficulty.NORMAL) == (
        CLARIFIER_MAX_QUESTIONS_NORMAL
    )
    assert _clarifier_max_questions_for_difficulty(TaskDifficulty.MEDIUM) == (
        CLARIFIER_MAX_QUESTIONS_MEDIUM
    )
    assert _clarifier_max_questions_for_difficulty(TaskDifficulty.COMPLEX) == (
        CLARIFIER_MAX_QUESTIONS_COMPLEX
    )


def test_history_archive_default_from_constant(monkeypatch) -> None:
    monkeypatch.setattr(
        "miniagent.memory.history_archive.get_config",
        lambda key, default: default,
    )
    assert history_archive.history_archive_max_messages() == HISTORY_ARCHIVE_MAX_MESSAGES


def test_keyword_extract_defaults() -> None:
    assert keyword_index._default_max_keywords() == KEYWORD_EXTRACT_MAX
    assert KEYWORD_EXTRACT_MAX >= KEYWORD_INDEX_MAX_KEYWORDS


def test_bitable_default_page_size_wired() -> None:
    import inspect

    sig = inspect.signature(bitable_client.list_records)
    assert sig.parameters["page_size"].default == BITABLE_DEFAULT_PAGE_SIZE


def test_cli_thinking_rich_env_override(monkeypatch) -> None:
    from miniagent.engine.thinking import _cli_thinking_rich_enabled

    monkeypatch.delenv("MINIAGENT_CLI_THINKING_RICH", raising=False)
    monkeypatch.setattr(
        "miniagent.infrastructure.json_config.get_config",
        lambda key, default: default,
    )
    monkeypatch.setenv("MINIAGENT_CLI_THINKING_RICH", "1")
    assert _cli_thinking_rich_enabled() is True


def test_improve_max_iterations_wired() -> None:
    import inspect

    sig = inspect.signature(command_dispatch._run_review)
    assert sig.parameters["max_iterations"].default == IMPROVE_MAX_ITERATIONS
