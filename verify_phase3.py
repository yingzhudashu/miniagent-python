"""Phase 3 import and functionality verification."""

from src.core.memory_store import (
    DefaultMemoryStore,
    format_memory_for_prompt,
    extract_facts,
    generate_turn_summary,
)
from src.core.context_manager import DefaultContextManager, estimate_tokens
from src.session.manager import DefaultSessionManager
from src.session.workspace import WorkspaceManager
from src.security.sandbox import resolve_sandbox_path, is_path_allowed

print("All Phase 3 imports OK")

# Test sandbox
assert is_path_allowed("src/file.txt", ["D:/test"]) is False
print("Sandbox: path blocking OK")

# Test memory store helpers
facts = extract_facts("记住我喜欢用中文回复")
print(f"Facts extracted: {len(facts)} items")

summary = generate_turn_summary("查询天气", [{"name": "get_weather"}], "今天晴天")
print(f"Turn summary: {summary}")

# Test memory format
from src.types.memory import SessionMemory, MemoryEntry

mem = SessionMemory(
    session_id="test",
    cumulative_summary="Test summary",
    key_facts=["Fact 1", "Fact 2"],
    entries=[
        MemoryEntry(
            timestamp="2026-05-05T10:00:00Z",
            user_snippet="Hello",
            summary="Greeting",
        )
    ],
)
text = format_memory_for_prompt(mem)
assert "关键记忆" in text
print("Memory format: OK")

# Test context manager
cm = DefaultContextManager(context_window=128000, compress_threshold=0.6)
cm.init("You are a helpful assistant.", "Hello")
assert len(cm.get_messages()) == 2
print(f"Context manager: {len(cm.get_messages())} messages initialized")

# Test workspace manager
import tempfile, os, shutil

test_dir = os.path.join(tempfile.gettempdir(), "test_ws")
wm = WorkspaceManager(base_dir=test_dir)
paths = wm.create_workspace("session-1")
assert os.path.exists(paths["files_path"])
fp = paths["files_path"]
print(f"Workspace created: {fp}")
wm.destroy_workspace("session-1")
shutil.rmtree(test_dir, ignore_errors=True)
print("Workspace destroyed")

print()
print("Phase 3 verification complete")
