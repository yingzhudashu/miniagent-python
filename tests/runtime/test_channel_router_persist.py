"""ChannelRouter SQLite persistence."""

from miniagent.assistant.infrastructure.channel_router import ChannelRouter


def test_save_and_load_roundtrip(state_dir: str) -> None:
    router = ChannelRouter()
    router.bind("__cli__", "default")
    router.bind("feishu_p2p:ou_abc", "default")
    router.set_primary("default")

    router2 = ChannelRouter()
    assert router2.load() is True
    assert router2.resolve("__cli__") == "default"
    assert router2.resolve("feishu_p2p:ou_abc") == "default"
    assert router2.primary == "default"


def test_load_returns_false_when_database_has_no_router_state(state_dir: str) -> None:
    assert ChannelRouter().load() is False


def test_bind_and_unbind_are_immediately_durable(state_dir: str) -> None:
    router = ChannelRouter()
    router.bind("ch1", "sess1")
    loaded = ChannelRouter()
    assert loaded.load() is True
    assert loaded.resolve("ch1") == "sess1"

    router.unbind("ch1")
    reloaded = ChannelRouter()
    assert reloaded.load() is True
    assert reloaded.resolve("ch1") == "ch1"


def test_primary_and_cli_continue_state_survive_restart(state_dir: str) -> None:
    router = ChannelRouter()
    router.set_primary("primary-session")
    router.save_cli_session_state("work", 2, "Work", "2026-01-01T00:00:00+00:00")

    loaded = ChannelRouter()
    assert loaded.load() is True
    assert loaded.primary == "primary-session"
    assert loaded.load_cli_session_state() == {
        "last_cli_session": "work",
        "last_cli_session_number": 2,
        "last_cli_session_title": "Work",
        "last_cli_exit_time": "2026-01-01T00:00:00+00:00",
    }
