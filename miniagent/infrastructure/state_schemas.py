"""MiniAgent 内置结构化状态文件的版本与迁移步骤。"""

from __future__ import annotations

from typing import Any

from miniagent.infrastructure.persistence import StateSchema, register_state_schema


def _identity(payload: dict[str, Any]) -> dict[str, Any]:
    """把无版本的既有对象提升为第一版，字段保持不变。"""
    return payload


def _scheduled_v0_to_v1(payload: dict[str, Any]) -> dict[str, Any]:
    """兼容早期无版本的 ``tasks`` 对象。"""
    payload.setdefault("tasks", [])
    return payload


def _scheduled_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """第二版沿用任务条目格式，仅统一 schema 元数据。"""
    payload.pop("version", None)
    return payload


def _session_history_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """Mark the stable cross-provider conversation shape without rewriting content."""
    payload["message_format"] = "miniagent-conversation-v1"
    return payload


def install_builtin_state_schemas() -> None:
    """安装内置 schema；模块重复导入时保持幂等。"""
    schemas = (
        StateSchema("session_config", 1, {0: _identity}),
        StateSchema(
            "session_history",
            2,
            {0: _identity, 1: _session_history_v1_to_v2},
            legacy_list_key="messages",
        ),
        StateSchema("channel_router", 1, {0: _identity}),
        StateSchema("knowledge_registry", 1, {0: _identity}),
        StateSchema("dream_state", 1, {0: _identity}),
        StateSchema("session_longterm", 1, {0: _identity}),
        StateSchema("agent_longterm", 1, {0: _identity}),
        StateSchema(
            "self_opt_proposal_index",
            1,
            {0: _identity},
            legacy_list_key="entries",
        ),
        StateSchema("self_opt_runtime_report", 1, {0: _identity}),
        StateSchema("testing_report", 1, {0: _identity}),
        StateSchema("instance_metadata", 1, {0: _identity}),
        StateSchema(
            "scheduled_tasks",
            2,
            {0: _scheduled_v0_to_v1, 1: _scheduled_v1_to_v2},
        ),
    )
    for schema in schemas:
        try:
            register_state_schema(schema)
        except ValueError as error:
            if "已注册" not in str(error):
                raise


install_builtin_state_schemas()

__all__ = ["install_builtin_state_schemas"]
