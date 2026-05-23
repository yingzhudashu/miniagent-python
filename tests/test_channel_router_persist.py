"""ChannelRouter 磁盘持久化。"""

import json
import os

from miniagent.infrastructure.channel_router import ChannelRouter


def test_save_and_load_roundtrip(tmp_path, monkeypatch) -> None:
    """绑定保存到文件并从磁盘恢复。"""
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))
    router = ChannelRouter()
    router.bind("__cli__", "default")
    router.bind("feishu_p2p:ou_abc", "default")
    router.set_primary("default")
    router.save()

    path = os.path.join(str(tmp_path), "channel-router.json")
    assert os.path.isfile(path)

    # 新实例从磁盘加载
    router2 = ChannelRouter()
    assert router2.load() is True
    assert router2.resolve("__cli__") == "default"
    assert router2.resolve("feishu_p2p:ou_abc") == "default"
    assert router2.primary == "default"


def test_load_returns_false_when_no_file(tmp_path, monkeypatch) -> None:
    """无文件时 load() 返回 False。"""
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))
    router = ChannelRouter()
    assert router.load() is False


def test_bind_auto_saves(tmp_path, monkeypatch) -> None:
    """bind() 调用后自动写入磁盘。"""
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))
    router = ChannelRouter()
    router.bind("__cli__", "session-1")
    path = os.path.join(str(tmp_path), "channel-router.json")
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["bindings"]["__cli__"] == "session-1"


def test_unbind_auto_saves(tmp_path, monkeypatch) -> None:
    """unbind() 调用后自动更新磁盘文件。"""
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))
    router = ChannelRouter()
    router.bind("ch1", "sess1")
    path = os.path.join(str(tmp_path), "channel-router.json")
    assert os.path.isfile(path)
    router.unbind("ch1")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert "ch1" not in data["bindings"]


def test_startup_load_restores_p2p_binding(tmp_path, monkeypatch) -> None:
    """模拟重启后 P2P 绑定恢复——这是修复的核心场景。"""
    # 第 1 次运行：创建绑定
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))
    router1 = ChannelRouter()
    router1.bind("__cli__", "default")
    router1.bind("feishu_p2p:ou_test123", "default")
    router1.set_primary("default")

    # 模拟重启：全新实例
    router2 = ChannelRouter()
    # 加载前无绑定
    assert router2.resolve("feishu_p2p:ou_test123") == "feishu_p2p:ou_test123"
    # 加载后恢复
    router2.load()
    assert router2.resolve("feishu_p2p:ou_test123") == "default"
    assert router2.resolve("__cli__") == "default"


def test_set_primary_auto_saves(tmp_path, monkeypatch) -> None:
    """set_primary() 调用后自动写入磁盘。"""
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))
    router = ChannelRouter()
    router.bind("__cli__", "default")
    path = os.path.join(str(tmp_path), "channel-router.json")
    router.set_primary("primary-session")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["primary"] == "primary-session"
