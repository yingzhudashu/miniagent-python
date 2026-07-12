"""全屏 CLI 补全器的行为测试。"""

from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from miniagent.engine.cli_completion import (
    CommandCompleter,
    FileMarkerCompleter,
    create_cli_completer,
)


def test_command_completer_uses_injected_registry_names() -> None:
    completer = CommandCompleter(("/stats", "/status", "/session"))
    results = list(completer.get_completions(Document("/sta"), CompleteEvent()))
    assert [item.text for item in results] == ["/stats", "/status"]
    assert all(item.start_position == -4 for item in results)


def test_command_completer_ignores_non_command_text() -> None:
    completer = CommandCompleter(("/help",))
    assert list(completer.get_completions(Document("help"), CompleteEvent())) == []
    assert list(completer.get_completions(Document(""), CompleteEvent())) == []


class _FakePathCompleter(Completer):
    """记录收到的独立路径 Document，并返回一个确定结果。"""

    def __init__(self) -> None:
        self.seen = ""

    def get_completions(self, document, complete_event):
        del complete_event
        self.seen = document.text
        yield Completion("report.md", start_position=-len(document.text))


def test_file_marker_completer_only_passes_path_suffix() -> None:
    paths = _FakePathCompleter()
    completer = FileMarkerCompleter(paths)
    results = list(completer.get_completions(Document("请看 @file:docs/re"), CompleteEvent()))
    assert paths.seen == "docs/re"
    assert [item.text for item in results] == ["report.md"]
    assert results[0].start_position == -len("docs/re")


def test_file_marker_completer_degrades_on_path_errors() -> None:
    class BrokenCompleter(Completer):
        def get_completions(self, document, complete_event):
            del document, complete_event
            raise OSError("drive unavailable")
            yield

    completer = FileMarkerCompleter(BrokenCompleter())
    assert list(completer.get_completions(Document("@file:x"), CompleteEvent())) == []
    assert list(completer.get_completions(Document("plain"), CompleteEvent())) == []
    assert isinstance(create_cli_completer(["/help"]), Completer)
