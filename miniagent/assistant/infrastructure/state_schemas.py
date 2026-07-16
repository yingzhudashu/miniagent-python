"""Current structured state schema registrations."""

from __future__ import annotations

from miniagent.assistant.infrastructure.persistence import (
    StateSchema,
    StateSchemaError,
    get_state_schema,
    register_state_schema,
)

_BUILTIN_SCHEMAS = (
    StateSchema("session_config", 1),
    StateSchema("session_history", 2),
    StateSchema("channel_router", 1),
    StateSchema("knowledge_registry", 1),
    StateSchema("dream_state", 1),
    StateSchema("session_longterm", 1),
    StateSchema("agent_longterm", 1),
    StateSchema("self_opt_proposal_index", 1),
    StateSchema("self_opt_runtime_report", 1),
    StateSchema("testing_report", 1),
    StateSchema("instance_metadata", 1),
    StateSchema("scheduled_tasks", 2),
)


def install_builtin_state_schemas() -> None:
    """Install each current schema once."""
    for schema in _BUILTIN_SCHEMAS:
        try:
            existing = get_state_schema(schema.name)
        except StateSchemaError:
            register_state_schema(schema)
        else:
            if existing != schema:
                raise ValueError(f"状态 schema 定义冲突: {schema.name}")


install_builtin_state_schemas()

__all__ = ["install_builtin_state_schemas"]
