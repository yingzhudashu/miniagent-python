"""不可变配置快照的递归冻结与点路径读取测试。"""

from __future__ import annotations

import pytest

from miniagent.assistant.contracts.configuration import ConfigSnapshot


def test_config_snapshot_recursively_freezes_input() -> None:
    source = {"model": {"names": ["a", {"nested": True}]}}
    snapshot = ConfigSnapshot(source)
    source["model"]["names"].append("later")

    assert list(snapshot) == ["model"]
    assert len(snapshot) == 1
    assert snapshot.get_path("model.names")[0] == "a"
    assert snapshot.get_path("missing", "fallback") == "fallback"
    with pytest.raises(TypeError):
        snapshot["model"]["new"] = 1
