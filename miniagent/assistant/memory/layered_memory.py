"""SQLite-backed session and agent long-term memory profiles.

The current runtime stores bounded long-term summaries in the project
``state.sqlite3`` database.  Diary Markdown remains on the filesystem because
it is user-readable source material; the structured rollup and agent-wide
index are profiles committed through the process-owned :class:`StateStore`.
No legacy JSON path is inspected or imported.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from miniagent.assistant.state import StateStore

SESSION_LONGTERM_NAMESPACE = "session_longterm"
AGENT_LONGTERM_NAMESPACE = "agent_longterm"
AGENT_LONGTERM_SCOPE = "__agent__"


def _updated(document: dict[str, Any]) -> dict[str, Any]:
    """Copy a profile and stamp the durable update time in UTC."""
    result = dict(document)
    result["updated_at"] = datetime.now(timezone.utc).isoformat()
    return result


class LongTermMemoryStore:
    """Own current-version long-term profiles on an open project database.

    The store does not open or close the database.  That ownership remains at
    the application composition root, which guarantees Dream, prompt assembly,
    and cleanup share the same transaction infrastructure.
    """

    def __init__(self, state_store: StateStore) -> None:
        self._state_store = state_store

    async def load_session(self, session_key: str) -> dict[str, Any]:
        """Load a session rollup, returning an empty current profile if absent."""
        profile = await self._state_store.load_memory_profile(
            session_key,
            SESSION_LONGTERM_NAMESPACE,
        )
        if profile is None:
            return {"session_key": session_key, "day_entries": []}
        return {"session_key": session_key, **profile}

    async def save_session(self, session_key: str, document: dict[str, Any]) -> None:
        """Replace one session rollup without retaining a duplicate scope field."""
        profile = _updated(document)
        profile.pop("session_key", None)
        await self._state_store.save_memory_profile(
            session_key,
            SESSION_LONGTERM_NAMESPACE,
            profile,
        )

    async def append_session_day_rollup(
        self,
        session_key: str,
        *,
        day: str,
        diary_relative: str,
        summary: str,
    ) -> None:
        """Append one diary anchor and commit the resulting session profile."""
        document = await self.load_session(session_key)
        entries = list(document.get("day_entries") or [])
        entries.append(
            {
                "day": day,
                "diary_path": diary_relative,
                "summary": summary,
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        document["day_entries"] = entries
        await self.save_session(session_key, document)

    async def load_agent(self) -> dict[str, Any]:
        """Load the process-wide agent profile from its fixed namespace."""
        profile = await self._state_store.load_memory_profile(
            AGENT_LONGTERM_SCOPE,
            AGENT_LONGTERM_NAMESPACE,
        )
        return profile if profile is not None else {"entries": []}

    async def save_agent(self, document: dict[str, Any]) -> None:
        """Replace the agent-wide long-term profile."""
        await self._state_store.save_memory_profile(
            AGENT_LONGTERM_SCOPE,
            AGENT_LONGTERM_NAMESPACE,
            _updated(document),
        )

    async def promote(
        self,
        text: str,
        *,
        source_session: str,
        priority: int = 0,
    ) -> None:
        """Append one explicitly promoted fact to the agent-wide profile."""
        document = await self.load_agent()
        entries = list(document.get("entries") or [])
        entries.append(
            {
                "text": text,
                "source_session": source_session,
                "priority": priority,
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        document["entries"] = entries
        await self.save_agent(document)

    async def remove_agent_entries_for_session(self, source_session: str) -> int:
        """Remove agent-wide entries originating from one background session."""
        if not source_session:
            return 0
        document = await self.load_agent()
        entries = list(document.get("entries") or [])
        kept = [entry for entry in entries if entry.get("source_session") != source_session]
        removed = len(entries) - len(kept)
        if removed:
            document["entries"] = kept
            await self.save_agent(document)
        return removed


__all__ = [
    "AGENT_LONGTERM_NAMESPACE",
    "AGENT_LONGTERM_SCOPE",
    "LongTermMemoryStore",
    "SESSION_LONGTERM_NAMESPACE",
]
