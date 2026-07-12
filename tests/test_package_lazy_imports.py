"""Fresh-process checks for aggregate package import boundaries."""

from __future__ import annotations

import json
import subprocess
import sys


def _fresh_import(*statements: str) -> dict[str, object]:
    script = "; ".join(
        [
            "import json, sys",
            *statements,
            "print(json.dumps({'modules': len(sys.modules)}))",
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


def test_core_agent_import_succeeds_in_fresh_process() -> None:
    result = _fresh_import("import miniagent.core.agent")

    assert result["modules"] > 0


def test_engine_package_does_not_eagerly_import_cli_or_runtime() -> None:
    result = _fresh_import(
        "import miniagent.engine",
        "assert 'miniagent.engine.main' not in sys.modules",
        "assert 'miniagent.engine.cli_tui' not in sys.modules",
    )

    assert result["modules"] < 150


def test_memory_package_does_not_eagerly_import_embedding_stack() -> None:
    result = _fresh_import(
        "import miniagent.memory",
        "assert 'miniagent.memory.embedding_search' not in sys.modules",
    )

    assert result["modules"] < 150


def test_knowledge_context_import_does_not_eagerly_import_yaml_stack() -> None:
    result = _fresh_import(
        "from miniagent.knowledge import retrieve_knowledge_context",
        "assert callable(retrieve_knowledge_context)",
        "assert 'miniagent.knowledge.registry' not in sys.modules",
        "assert 'miniagent.knowledge.base' not in sys.modules",
        "assert 'yaml' not in sys.modules",
    )

    assert result["modules"] < 150


def test_knowledge_registry_lazy_export_remains_compatible() -> None:
    result = _fresh_import(
        "from miniagent.knowledge import KnowledgeRegistry",
        "assert KnowledgeRegistry.__name__ == 'KnowledgeRegistry'",
    )

    assert result["modules"] > 0


def test_memory_lazy_export_remains_compatible() -> None:
    result = _fresh_import(
        "from miniagent.memory import KeywordIndex",
        "assert KeywordIndex.__name__ == 'KeywordIndex'",
    )

    assert result["modules"] > 0


def test_types_config_does_not_import_openai_sdk() -> None:
    result = _fresh_import(
        "import miniagent.types.config",
        "assert 'openai' not in sys.modules",
    )

    assert result["modules"] < 150


def test_types_lazy_export_remains_compatible() -> None:
    result = _fresh_import(
        "from miniagent.types import AgentConfig, ToolDefinition",
        "assert AgentConfig.__name__ == 'AgentConfig'",
        "assert ToolDefinition.__name__ == 'ToolDefinition'",
    )

    assert result["modules"] < 200


def test_feishu_runtime_constructor_does_not_import_lark_sdk() -> None:
    result = _fresh_import(
        "from miniagent.engine.feishu_state import FeishuRuntime",
        "runtime = FeishuRuntime(None)",
        "assert runtime._poll_state is None",
        "assert 'lark_oapi' not in sys.modules",
    )

    assert result["modules"] < 200


def test_memory_runtime_constructor_does_not_import_numpy() -> None:
    result = _fresh_import(
        "import tempfile",
        "from miniagent.memory.runtime import create_memory_runtime",
        "tmp = tempfile.TemporaryDirectory()",
        "runtime = create_memory_runtime(tmp.name)",
        "assert 'numpy' not in sys.modules",
        "tmp.cleanup()",
    )

    assert result["modules"] < 500


def test_single_tool_module_does_not_import_all_tool_collections() -> None:
    result = _fresh_import(
        "import miniagent.tools.data_tools",
        "assert 'miniagent.tools.feishu_doc_tools' not in sys.modules",
        "assert 'miniagent.tools.filesystem' not in sys.modules",
    )

    assert result["modules"] < 250


def test_all_tools_lazy_export_builds_complete_mapping() -> None:
    result = _fresh_import(
        "from miniagent.tools import ALL_TOOLS",
        "assert 'read_file' in ALL_TOOLS",
        "assert 'json_read' in ALL_TOOLS",
    )

    assert result["modules"] > 0
