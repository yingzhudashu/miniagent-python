"""Unit tests for miniagent.core._openai_compat."""

from __future__ import annotations

import pytest

from miniagent.core._openai_compat import (
    ensure_json_object_user_message,
    json_object_requires_json_keyword,
    json_object_unsupported,
)

_JSON_HINT = "Please return a valid JSON object."


def _last_user_content(messages: list[dict[str, object]]) -> object:
    user_messages = [m for m in messages if m.get("role") == "user"]
    assert user_messages, "expected at least one user message"
    return user_messages[-1]["content"]


class TestEnsureJsonObjectUserMessage:
    def test_appends_hint_to_plain_user_string(self) -> None:
        out = ensure_json_object_user_message(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
        )
        assert _last_user_content(out) == f"hello\n\n{_JSON_HINT}"
        assert out[0]["content"] == "sys"

    def test_leaves_messages_unchanged_when_last_user_mentions_json(self) -> None:
        original = [{"role": "user", "content": "return JSON please"}]
        out = ensure_json_object_user_message(original)
        assert out[0]["content"] == "return JSON please"
        assert original[0]["content"] == "return JSON please"

    def test_hints_last_user_even_when_earlier_user_mentions_json(self) -> None:
        out = ensure_json_object_user_message(
            [
                {"role": "user", "content": "old json task"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "new task without keyword"},
            ]
        )
        last = _last_user_content(out)
        assert isinstance(last, str)
        assert last.startswith("new task without keyword")
        assert "json" in last.lower()

    def test_appends_hint_to_multimodal_text_part(self) -> None:
        out = ensure_json_object_user_message(
            [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
        )
        parts = _last_user_content(out)
        assert isinstance(parts, list)
        assert parts[0]["text"] == f"hello\n\n{_JSON_HINT}"

    def test_appends_text_part_when_multimodal_has_only_image(self) -> None:
        out = ensure_json_object_user_message(
            [
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": "x"}}],
                }
            ]
        )
        parts = _last_user_content(out)
        assert isinstance(parts, list)
        assert parts[-1] == {"type": "text", "text": _JSON_HINT}

    def test_detects_input_text_field(self) -> None:
        out = ensure_json_object_user_message(
            [{"role": "user", "content": [{"type": "input_text", "input_text": "json"}]}]
        )
        assert _last_user_content(out) == [{"type": "input_text", "input_text": "json"}]

    def test_replaces_none_content_instead_of_appending_duplicate_user(self) -> None:
        out = ensure_json_object_user_message([{"role": "user", "content": None}])
        assert len(out) == 1
        assert out[0]["content"] == _JSON_HINT

    def test_replaces_non_text_content(self) -> None:
        out = ensure_json_object_user_message([{"role": "user", "content": 123}])
        assert len(out) == 1
        assert out[0]["content"] == _JSON_HINT

    def test_appends_user_when_missing(self) -> None:
        out = ensure_json_object_user_message([{"role": "system", "content": "sys"}])
        assert len(out) == 2
        assert out[-1] == {"role": "user", "content": _JSON_HINT}

    def test_appends_user_for_empty_input(self) -> None:
        out = ensure_json_object_user_message([])
        assert out == [{"role": "user", "content": _JSON_HINT}]

    def test_drops_non_dict_entries(self) -> None:
        out = ensure_json_object_user_message(
            [{"role": "user", "content": "hi"}, "bad", None]  # type: ignore[list-item]
        )
        assert len(out) == 1
        assert "json" in str(_last_user_content(out)).lower()


class TestJsonObjectRequiresJsonKeyword:
    def test_matches_openai_style_message(self) -> None:
        err = Exception(
            "Response input messages must contain the word 'json' in some form "
            "to use 'response.format' of type 'json_object'."
        )
        assert json_object_requires_json_keyword(err)

    def test_rejects_unsupported_errors(self) -> None:
        err = Exception("response_format json_object not supported")
        assert not json_object_requires_json_keyword(err)


class TestJsonObjectUnsupported:
    def test_ignores_missing_json_keyword(self) -> None:
        err = Exception(
            "Response input messages must contain the word 'json' in some form "
            "to use 'response.format' of type 'json_object'."
        )
        assert not json_object_unsupported(err)

    @pytest.mark.parametrize(
        "message",
        [
            "response_format json_object not supported",
            "Model does not support response_format.type=json_object",
            "unknown parameter: response_format",
            "json_object is unavailable for this model",
        ],
    )
    def test_detects_unsupported_endpoints(self, message: str) -> None:
        assert json_object_unsupported(Exception(message))

    @pytest.mark.parametrize(
        "message",
        [
            "rate limit exceeded",
            "response_format validation failed",
            "invalid message content",
        ],
    )
    def test_ignores_unrelated_or_ambiguous_errors(self, message: str) -> None:
        assert not json_object_unsupported(Exception(message))
