from miniagent.assistant.feishu.cards.builder import build_button, build_interactive_card


def test_build_interactive_card_with_button() -> None:
    btn = build_button("OK", miniagent_text="confirm", chat_id="oc_x", action_id="a1")
    card = build_interactive_card("Title", "body **x**", "blue", buttons=[btn])
    assert card["header"]["template"] == "blue"
    assert card["elements"][-1]["tag"] == "action"
    assert card["elements"][-1]["actions"][0]["value"]["action_id"] == "a1"
