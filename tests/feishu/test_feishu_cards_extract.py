import json

from miniagent.assistant.feishu.cards.extract import extract_text_from_interactive_content


def test_extract_v1_card() -> None:
    payload = {
        "header": {"title": {"tag": "plain_text", "content": "H"}},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "Hello **world**"}}],
    }
    t = extract_text_from_interactive_content(json.dumps(payload))
    assert "Hello" in t


def test_extract_sanitizes_control_chars() -> None:
    t = extract_text_from_interactive_content(json.dumps({"elements": []}))
    assert "\x00" not in t
