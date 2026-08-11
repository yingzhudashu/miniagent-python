from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from miniagent.assistant.state import (
    STATE_SCHEMA_VERSION,
    StateConflictError,
    StateSchemaError,
    StateStore,
)


@pytest.mark.asyncio
async def test_empty_directory_creates_exact_v5_schema(tmp_path: Path) -> None:
    async with StateStore(tmp_path) as store:
        row = await store.connection.execute_fetchall("PRAGMA user_version")
        assert row[0][0] == STATE_SCHEMA_VERSION
        names = {
            item[0]
            for item in await store.connection.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        assert {
            "sessions",
            "messages",
            "memory_entries",
            "memory_fts",
            "knowledge_documents",
            "knowledge_fts",
            "scheduled_tasks",
            "feishu_message_claims",
            "process_leases",
        } <= names


@pytest.mark.asyncio
async def test_nonempty_unversioned_database_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE old_state(value TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(StateSchemaError, match="unversioned non-empty"):
        await StateStore(tmp_path).open()


@pytest.mark.asyncio
async def test_other_schema_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=4")
    connection.commit()
    connection.close()

    with pytest.raises(StateSchemaError, match="schema v4"):
        await StateStore(tmp_path).open()


@pytest.mark.asyncio
async def test_future_schema_version_is_rejected_without_rewriting_database(
    tmp_path: Path,
) -> None:
    async with StateStore(tmp_path):
        pass
    path = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=6")
    connection.commit()
    connection.close()
    original = path.read_bytes()

    with pytest.raises(StateSchemaError, match="schema v6"):
        await StateStore(tmp_path).open()
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_corrupt_database_is_rejected_without_rewriting_file(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    original = b"this is not sqlite"
    path.write_bytes(original)

    with pytest.raises(StateSchemaError, match="cannot open state database"):
        await StateStore(tmp_path).open()
    assert path.read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing_table", "extra_column", "wrong_fts"])
async def test_incomplete_or_incorrect_v5_schema_is_rejected(
    tmp_path: Path, damage: str
) -> None:
    async with StateStore(tmp_path):
        pass
    connection = sqlite3.connect(tmp_path / "state.sqlite3")
    if damage == "missing_table":
        connection.execute("DROP TABLE maintenance_state")
    elif damage == "extra_column":
        connection.execute("ALTER TABLE cli_state ADD COLUMN legacy_value TEXT")
    else:
        connection.execute("DROP TABLE memory_fts")
        connection.execute("CREATE VIRTUAL TABLE memory_fts USING fts5(content)")
    connection.commit()
    connection.close()

    with pytest.raises(StateSchemaError):
        await StateStore(tmp_path).open()


@pytest.mark.asyncio
async def test_old_json_state_is_never_read_or_imported(tmp_path: Path) -> None:
    old_state = tmp_path / "tasks.json"
    payload = b'{"tasks":[{"id":"v4-task"}]}'
    old_state.write_bytes(payload)

    async with StateStore(tmp_path) as store:
        assert await store.list_scheduled_tasks() == []
    assert old_state.read_bytes() == payload


@pytest.mark.asyncio
async def test_v5_database_with_unknown_user_table_is_rejected(tmp_path: Path) -> None:
    async with StateStore(tmp_path):
        pass
    connection = sqlite3.connect(tmp_path / "state.sqlite3")
    try:
        connection.execute("CREATE TABLE foreign_state(id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(StateSchemaError, match="unexpected foreign_state"):
        await StateStore(tmp_path).open()


@pytest.mark.asyncio
async def test_sessions_messages_and_bindings_survive_restart(tmp_path: Path) -> None:
    async with StateStore(tmp_path) as store:
        await store.create_session("session-1", title="First")
        assert await store.append_message("session-1", "m1", "user", {"text": "hello"}) == 1
        assert await store.append_message("session-1", "m2", "assistant", {"text": "hi"}) == 2
        await store.bind_channel("cli", "terminal", "session-1", metadata={"ui": "plain"})

    async with StateStore(tmp_path) as reopened:
        assert (await reopened.get_session("session-1"))["next_sequence"] == 3
        assert [item["content"] for item in await reopened.list_messages("session-1")] == [
            {"text": "hello"},
            {"text": "hi"},
        ]
        assert await reopened.resolve_channel("cli", "terminal") == "session-1"


@pytest.mark.asyncio
async def test_two_connections_append_unique_contiguous_sequences(tmp_path: Path) -> None:
    async with StateStore(tmp_path) as bootstrap:
        await bootstrap.create_session("session-1")

    async with StateStore(tmp_path) as first, StateStore(tmp_path) as second:
        async def append_batch(store: StateStore, indexes: range) -> list[int]:
            return [
                await store.append_message(
                    "session-1", f"message-{index}", "user", {"index": index}
                )
                for index in indexes
            ]

        batches = await asyncio.gather(
            append_batch(first, range(0, 20, 2)),
            append_batch(second, range(1, 20, 2)),
        )
        sequences = [sequence for batch in batches for sequence in batch]
        assert sorted(sequences) == list(range(1, 21))
        assert [
            item["sequence"] for item in await first.list_messages("session-1")
        ] == list(range(1, 21))


@pytest.mark.asyncio
async def test_cancelled_write_transaction_rolls_back_completely(tmp_path: Path) -> None:
    async with StateStore(tmp_path) as store:
        entered = asyncio.Event()
        block = asyncio.Event()

        async def write_then_wait() -> None:
            async with store.transaction() as connection:
                await connection.execute(
                    "INSERT INTO cli_state VALUES ('current', '\"session-1\"', 1)"
                )
                entered.set()
                await block.wait()

        task = asyncio.create_task(write_then_wait())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        rows = await store.connection.execute_fetchall("SELECT * FROM cli_state")
        assert rows == []


@pytest.mark.asyncio
async def test_fts_and_vector_search_are_explicit(tmp_path: Path) -> None:
    async with StateStore(tmp_path) as store:
        first = await store.add_memory("project", "facts", "the quick fox")
        second = await store.add_memory("project", "facts", "a sleepy turtle")
        await store.put_memory_embedding(first, "embed", [1.0, 0.0])
        await store.put_memory_embedding(second, "embed", [0.0, 1.0])

        assert [item["id"] for item in await store.search_memory_fts(
            "quick", scope="project", namespace="facts"
        )] == [first]
        assert [item["id"] for item in await store.search_memory_vector(
            [0.9, 0.1], model="embed", scope="project", namespace="facts"
        )] == [first, second]
        with pytest.raises(StateSchemaError, match="dimension mismatch"):
            await store.search_memory_vector(
                [1.0, 0.0, 0.0], model="embed", scope="project", namespace="facts"
            )


@pytest.mark.asyncio
async def test_embedding_dimension_is_rejected_before_namespace_is_polluted(
    tmp_path: Path,
) -> None:
    async with StateStore(tmp_path) as store:
        first = await store.add_memory("project", "facts", "first")
        second = await store.add_memory("project", "facts", "second")
        await store.put_memory_embedding(first, "embed", [1.0, 0.0])

        with pytest.raises(StateSchemaError, match="dimension mismatch for embed"):
            await store.put_memory_embedding(second, "embed", [1.0, 0.0, 0.0])

        rows = await store.connection.execute_fetchall(
            "SELECT memory_id, dimension FROM memory_embeddings ORDER BY memory_id"
        )
        assert [(int(row[0]), int(row[1])) for row in rows] == [(first, 2)]


@pytest.mark.asyncio
async def test_trigram_fts_supports_chinese_english_and_mixed_queries(
    tmp_path: Path,
) -> None:
    async with StateStore(tmp_path) as store:
        chinese = await store.add_memory("project", "facts", "SQLite 数据库架构设计")
        english = await store.add_memory("project", "facts", "reliable session checkpoint")
        mixed = await store.add_memory(
            "project", "facts", "MiniAgent 使用 SQLite 保存中文记忆"
        )

        assert [
            item["id"]
            for item in await store.search_memory_fts(
                "数据库", scope="project", namespace="facts"
            )
        ] == [chinese]
        assert [
            item["id"]
            for item in await store.search_memory_fts(
                "checkpoint", scope="project", namespace="facts"
            )
        ] == [english]
        assert [
            item["id"]
            for item in await store.search_memory_fts(
                "SQLite 中文记忆", scope="project", namespace="facts"
            )
        ] == [mixed]


@pytest.mark.asyncio
async def test_independent_connections_have_one_claim_and_lease_winner(
    tmp_path: Path,
) -> None:
    async with StateStore(tmp_path) as first, StateStore(tmp_path) as second:
        claims = await asyncio.gather(
            first.claim_feishu_message("event", "worker-a", now=10.0),
            second.claim_feishu_message("event", "worker-b", now=10.0),
        )
        assert sorted(claims) == [False, True]

        async def acquire(store: StateStore, owner: str) -> str:
            try:
                await store.acquire_lease("scheduler", owner, now=10.0, ttl=20.0)
            except StateConflictError:
                return "conflict"
            return "winner"

        leases = await asyncio.gather(
            acquire(first, "worker-a"), acquire(second, "worker-b")
        )
        assert sorted(leases) == ["conflict", "winner"]


@pytest.mark.asyncio
async def test_process_lease_rejects_a_second_live_owner(tmp_path: Path) -> None:
    async with StateStore(tmp_path) as store:
        await store.acquire_lease("application", "owner-a", now=10.0, ttl=20.0)
        with pytest.raises(StateConflictError, match="owner-a"):
            await store.acquire_lease("application", "owner-b", now=15.0, ttl=20.0)
        await store.acquire_lease("application", "owner-b", now=31.0, ttl=20.0)
        with pytest.raises(StateConflictError, match="owner-b"):
            await store.renew_lease("application", "owner-a", now=32.0)


@pytest.mark.asyncio
async def test_feishu_claim_only_recovers_after_expiry(tmp_path: Path) -> None:
    async with StateStore(tmp_path) as store:
        assert await store.claim_feishu_message("message", "worker-a", now=10.0, lease_seconds=20)
        assert not await store.claim_feishu_message("message", "worker-b", now=20.0)
        assert await store.claim_feishu_message("message", "worker-b", now=31.0)
        await store.complete_feishu_message("message", "worker-b")
        assert not await store.claim_feishu_message("message", "worker-a", now=100.0)


@pytest.mark.asyncio
async def test_knowledge_index_is_replaced_deterministically(tmp_path: Path) -> None:
    async with StateStore(tmp_path) as store:
        mount = await store.upsert_knowledge_mount("docs", str(tmp_path / "docs"))
        document = await store.upsert_knowledge_document(
            mount, "guide.md", "Guide", "alpha content", "hash-1"
        )
        assert [item["id"] for item in await store.search_knowledge("alpha")] == [document]
        same_document = await store.upsert_knowledge_document(
            mount, "guide.md", "Guide", "beta content", "hash-2"
        )
        assert same_document == document
        assert await store.search_knowledge("alpha") == []
        assert [item["id"] for item in await store.search_knowledge("beta")] == [document]
