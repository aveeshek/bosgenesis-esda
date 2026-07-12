from __future__ import annotations

from importlib.util import find_spec
from typing import Any

from backend.app.config import Settings
from backend.app.db.database import RunRepository


class MemoryService:
    """Session-scoped memory facade for prompt-first workflows.

    LangMem is treated as the optional memory-management layer. The durable
    memory source of truth remains PostgreSQL through RunRepository so local and
    deployed ESDA runs behave the same way when LangMem is unavailable.
    """

    def __init__(self, *, repository: RunRepository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings
        self.langmem_available = find_spec("langmem") is not None
        self._ephemeral_turns: dict[str, list[dict[str, Any]]] = {}

    def env_agent_context(self, *, user_id: str, session_id: str | None) -> dict[str, Any]:
        if not session_id:
            return self._empty_context(session_id=None)
        messages = self.repository.list_chat_messages(session_id=session_id, user_id=user_id) or []
        short_limit = max(1, int(self.settings.env_agent_short_term_memory_messages))
        recent_messages = messages[-short_limit:]
        long_term = self.repository.list_memories(
            user_id=user_id,
            workflow_type="env_agent",
            memory_type="long_term",
            memory_scope="chat_session",
            scope_id=session_id,
            limit=max(1, int(self.settings.env_agent_long_term_memory_limit)),
        )
        namespace = self._latest_namespace(messages, long_term)
        return {
            "session_id": session_id,
            "short_term": {
                "provider": "langmem" if self.settings.langmem_enabled and self.langmem_available else "session_window",
                "langmem_enabled": self.settings.langmem_enabled,
                "langmem_available": self.langmem_available,
                "message_count": len(messages),
                "recent_messages": recent_messages,
                "ephemeral_turns": list(self._ephemeral_turns.get(session_id, []))[-short_limit:],
            },
            "long_term": {
                "provider": "postgres",
                "memory_count": len(long_term),
                "memories": long_term,
            },
            "latest_namespace": namespace,
        }

    def remember_env_agent_turn(
        self,
        *,
        user_id: str,
        session_id: str,
        run_id: str,
        namespace: str | None,
        user_text: str,
        assistant_text: str,
        status: str,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        turns = self._ephemeral_turns.setdefault(session_id, [])
        turns.append(
            {
                "run_id": run_id,
                "namespace": namespace,
                "user": user_text[:1000],
                "assistant_preview": assistant_text[:1000],
                "status": status,
            }
        )
        max_turns = max(1, int(self.settings.env_agent_short_term_memory_messages))
        del turns[:-max_turns]

        safe_summaries = state.get("safe_summaries") or []
        evidence_count = len(state.get("evidence") or [])
        content = self._memory_summary(
            namespace=namespace,
            status=status,
            user_text=user_text,
            assistant_text=assistant_text,
            evidence_count=evidence_count,
        )
        long_term = self.repository.upsert_memory(
            user_id=user_id,
            workflow_type="env_agent",
            memory_type="long_term",
            memory_scope="chat_session",
            scope_id=session_id,
            key="latest_environment_context",
            content=content,
            value_json={
                "session_id": session_id,
                "run_id": run_id,
                "namespace": namespace,
                "status": status,
                "evidence_count": evidence_count,
                "safe_reasoning_summaries": safe_summaries[-6:],
                "last_prompt": user_text[:1200],
                "last_answer_preview": assistant_text[:1800],
            },
            importance=3,
        )
        short_term = self.repository.upsert_memory(
            user_id=user_id,
            workflow_type="env_agent",
            memory_type="short_term",
            memory_scope="chat_session",
            scope_id=session_id,
            key="recent_turns",
            content=f"Recent Environment Chat turns for {namespace or 'runtime-selected namespace'}.",
            value_json={"turns": turns[-max_turns:]},
            importance=1,
        )
        return {"long_term": long_term, "short_term": short_term}

    def _empty_context(self, *, session_id: str | None) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "short_term": {
                "provider": "langmem" if self.settings.langmem_enabled and self.langmem_available else "session_window",
                "langmem_enabled": self.settings.langmem_enabled,
                "langmem_available": self.langmem_available,
                "message_count": 0,
                "recent_messages": [],
                "ephemeral_turns": [],
            },
            "long_term": {"provider": "postgres", "memory_count": 0, "memories": []},
            "latest_namespace": None,
        }

    @staticmethod
    def _latest_namespace(messages: list[dict[str, Any]], memories: list[dict[str, Any]]) -> str | None:
        for message in reversed(messages):
            namespace = (message.get("payload") or {}).get("namespace")
            if namespace:
                return str(namespace)
        for memory in memories:
            namespace = (memory.get("value_json") or {}).get("namespace")
            if namespace:
                return str(namespace)
        return None

    @staticmethod
    def _memory_summary(
        *,
        namespace: str | None,
        status: str,
        user_text: str,
        assistant_text: str,
        evidence_count: int,
    ) -> str:
        return (
            f"Environment Chat session last discussed namespace {namespace or 'runtime-selected namespace'} "
            f"with status {status} and {evidence_count} evidence record(s). "
            f"Last user prompt: {user_text[:220]} "
            f"Last answer preview: {assistant_text[:320]}"
        )
