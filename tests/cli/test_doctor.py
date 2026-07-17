"""环境诊断 ``doctor`` 模块测试。"""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import patch

from miniagent.assistant.engine.doctor import (
    REQUIRED_DEPENDENCIES,
    _format_masked_secret,
    _resolve_api_key,
    _resolve_knowledge_root,
    diagnose_environment,
)


def test_required_dependency_inventory_matches_core_runtime() -> None:
    modules = {module_name for module_name, _display_name in REQUIRED_DEPENDENCIES}
    assert {"croniter", "tzdata", "typing_extensions"} <= modules
    assert "websockets" not in modules


def test_format_masked_secret_truncates_long_values() -> None:
    assert _format_masked_secret("sk-abcdefghijklmnop") == "sk-abcde..."
    assert _format_masked_secret("short") == "***"


def test_resolve_api_key_prefers_json(isolated_config_loader, monkeypatch) -> None:
    isolated_config_loader({
        "secrets": {"llm": {"openai": {"api_key": "sk-from-json-key"}}},
    })
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env-key")
    value, source = _resolve_api_key()
    assert value == "sk-from-json-key"
    assert source == "json"


def test_resolve_api_key_falls_back_to_env(isolated_config_loader, monkeypatch) -> None:
    isolated_config_loader({})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env-only")
    value, source = _resolve_api_key()
    assert value == "sk-from-env-only"
    assert source == "env"


def test_resolve_api_key_missing(isolated_config_loader, monkeypatch) -> None:
    isolated_config_loader({})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    value, source = _resolve_api_key()
    assert value is None
    assert source == ""


def test_resolve_knowledge_root_absolute(isolated_config_loader) -> None:
    isolated_config_loader({"knowledge": {"root": "/tmp/custom-kb"}})
    assert _resolve_knowledge_root() == "/tmp/custom-kb"


def test_resolve_knowledge_root_relative(isolated_config_loader, monkeypatch) -> None:
    isolated_config_loader({"knowledge": {"default_root": "workspaces/knowledge"}})
    monkeypatch.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    root = _resolve_knowledge_root()
    assert os.path.isabs(root)
    assert root.endswith(os.path.join("workspaces", "knowledge"))


def test_diagnose_environment_reports_missing_api_key(isolated_config_loader, monkeypatch) -> None:
    isolated_config_loader({})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = diagnose_environment()
    assert "### 必需依赖" in out
    assert "### 可选依赖" in out
    assert "API 密钥: 未设置" in out
    assert "未配置 OpenAI API 密钥" in out


def test_diagnose_environment_reports_env_api_key(isolated_config_loader, monkeypatch) -> None:
    isolated_config_loader(
        {
            "llm": {
                "providers": {
                    "openai": {
                        "driver": "openai",
                        "headers": {"User-Agent": "MiniAgent-Test"},
                    }
                },
                "models": {
                    "primary": {
                        "provider": "openai",
                        "model": "test-model",
                        "api": "openai_responses",
                    }
                },
                "roles": {"default": "primary"},
            }
        }
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-diagnostic-key")
    out = diagnose_environment()
    assert "环境变量 OPENAI_API_KEY" in out
    assert "sk-env-d..." in out
    assert "传输协议: responses" in out
    assert "自定义 User-Agent: 已设置" in out
    assert "关键配置检查通过" in out


def test_diagnose_environment_uses_knowledge_root_not_state_subdir(
    isolated_config_loader,
    state_dir,
) -> None:
    isolated_config_loader({"knowledge": {"default_root": "workspaces/knowledge"}})
    out = diagnose_environment()
    assert "### 知识库" in out
    assert "workspaces" in out and "knowledge" in out
    assert os.path.join(state_dir, "knowledge") not in out


def test_diagnose_environment_flags_missing_required_dependency(
    isolated_config_loader,
    monkeypatch,
) -> None:
    isolated_config_loader({})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")

    def _availability(module_name: str) -> bool:
        return module_name != "croniter"

    with patch("miniagent.assistant.engine.doctor._is_module_available", side_effect=_availability):
        out = diagnose_environment()

    assert "croniter" in out
    assert "缺少" in out and "必需依赖" in out
    assert "关键配置检查通过" not in out


def test_diagnose_environment_treats_websockets_as_feishu_optional(
    isolated_config_loader,
    monkeypatch,
) -> None:
    isolated_config_loader({})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")

    with patch(
        "miniagent.assistant.engine.doctor._is_module_available",
        side_effect=lambda module_name: module_name != "websockets",
    ):
        out = diagnose_environment()

    assert "WebSocket (websockets): 未安装" in out
    assert "缺少 1 个必需依赖" not in out
    assert "关键配置检查通过" in out


def test_main_doctor_flag_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "miniagent", "--doctor"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0
    assert "MiniAgent 环境诊断" in result.stdout
