"""Tests for miniagent.memory.activity_log."""

import os

import pytest

from miniagent.memory.activity_log import ActivityLogger, _short_json


class TestActivityLogger:
    """ActivityLogger 每日 Markdown 文件写入。"""

    def test_get_today_path(self, tmp_path: pytest.TempPathFactory):
        logger = ActivityLogger(base_dir=str(tmp_path))
        path = logger._get_today_path()
        assert path.endswith(".md")
        assert str(tmp_path) in path
        # 目录应自动创建
        assert os.path.isdir(os.path.dirname(path))

    def test_log_session_start(self, tmp_path: pytest.TempPathFactory):
        logger = ActivityLogger(base_dir=str(tmp_path))
        logger.log_session_start("cli-1", "帮我查天气")
        path = logger._get_today_path()
        assert os.path.exists(path)
        content = open(path, encoding="utf-8").read()
        assert "## cli-1" in content
        assert "帮我查天气" in content

    def test_log_session_start_no_duplicate(self, tmp_path: pytest.TempPathFactory):
        """同一会话在同一天不应重复添加 header。"""
        logger = ActivityLogger(base_dir=str(tmp_path))
        logger.log_session_start("cli-1", "输入1")
        logger.log_session_start("cli-1", "输入2")
        content = open(logger._get_today_path(), encoding="utf-8").read()
        # header 只出现一次
        assert content.count("## cli-1") == 1
        # 但用户输入会追加
        assert "输入1" in content
        assert "输入2" in content

    def test_log_llm_call(self, tmp_path: pytest.TempPathFactory):
        logger = ActivityLogger(base_dir=str(tmp_path))
        logger.log_llm_call("cli-1", 1, "gpt-4o-mini", 5, 3, "正在查询...")
        content = open(logger._get_today_path(), encoding="utf-8").read()
        assert "LLM 调用" in content
        assert "gpt-4o-mini" in content
        assert "正在查询" in content

    def test_log_llm_call_with_token_usage(self, tmp_path: pytest.TempPathFactory):
        logger = ActivityLogger(base_dir=str(tmp_path))
        logger.log_llm_call(
            "cli-1", 1, "gpt-4o-mini", 5, 3, "thinking",
            token_usage={"prompt_tokens": 100, "completion_tokens": 200},
        )
        content = open(logger._get_today_path(), encoding="utf-8").read()
        assert "prompt=100" in content
        assert "completion=200" in content

    def test_log_tool_call(self, tmp_path: pytest.MonkeyPatch):
        logger = ActivityLogger(base_dir=str(tmp_path))
        logger.log_tool_call(
            "cli-1", "web_search", "搜索天气",
            {"query": "天气"}, "晴天", 150, True,
        )
        content = open(logger._get_today_path(), encoding="utf-8").read()
        assert "web_search" in content
        assert "搜索天气" in content
        assert "150ms" in content
        assert "[ok]" in content

    def test_log_tool_call_failure(self, tmp_path: pytest.TempPathFactory):
        logger = ActivityLogger(base_dir=str(tmp_path))
        logger.log_tool_call(
            "cli-1", "exec", "运行命令",
            {"cmd": "rm -rf /"}, "error", 5000, False,
        )
        content = open(logger._get_today_path(), encoding="utf-8").read()
        assert "[fail]" in content

    def test_log_final_reply(self, tmp_path: pytest.TempPathFactory):
        logger = ActivityLogger(base_dir=str(tmp_path))
        logger.log_final_reply("cli-1", "今天晴天，25°C")
        content = open(logger._get_today_path(), encoding="utf-8").read()
        assert "最终回复" in content
        assert "今天晴天" in content

    def test_log_final_reply_truncated(self, tmp_path: pytest.TempPathFactory):
        logger = ActivityLogger(base_dir=str(tmp_path))
        long_text = "x" * 2000
        logger.log_final_reply("cli-1", long_text)
        content = open(logger._get_today_path(), encoding="utf-8").read()
        # 截断到 1000 字
        assert len(content) < 1500

    def test_log_incomplete(self, tmp_path: pytest.TempPathFactory):
        logger = ActivityLogger(base_dir=str(tmp_path))
        logger.log_incomplete("cli-1", "达到最大轮数")
        content = open(logger._get_today_path(), encoding="utf-8").read()
        assert "未完成" in content
        assert "达到最大轮数" in content

    def test_full_session_flow(self, tmp_path: pytest.TempPathFactory):
        """完整会话流程：开始 → LLM → 工具 → 回复。"""
        logger = ActivityLogger(base_dir=str(tmp_path))
        logger.log_session_start("cli-1", "查询天气")
        logger.log_llm_call("cli-1", 1, "gpt-4o-mini", 3, 2, "调用搜索")
        logger.log_tool_call("cli-1", "web_search", "搜索", {"q": "天气"}, "晴天", 100, True)
        logger.log_final_reply("cli-1", "今天晴天")

        content = open(logger._get_today_path(), encoding="utf-8").read()
        assert "用户输入" in content
        assert "LLM 调用" in content
        assert "工具调用" in content
        assert "最终回复" in content


class TestShortJson:
    """_short_json 辅助函数。"""

    def test_short_dict(self):
        result = _short_json({"key": "value"})
        assert result == '{"key": "value"}'

    def test_truncated(self):
        data = {"long": "a" * 500}
        result = _short_json(data, max_len=100)
        assert len(result) <= 103  # 100 + "..."
        assert result.endswith("...")

    def test_list(self):
        result = _short_json([1, 2, 3])
        assert "[1, 2, 3]" in result or "[1,2,3]" in result

    def test_empty_dict(self):
        assert _short_json({}) == "{}"
