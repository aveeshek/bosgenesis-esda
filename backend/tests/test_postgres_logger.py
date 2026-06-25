from contextlib import contextmanager

from backend.app.config import Settings
from backend.app.db.models import AgentEventLog, LlmReviewLog, ToolExecutionLog
from backend.app.logging.postgres_logger import PostgresLogger


class FakeDatabase:
    def __init__(self) -> None:
        self.added = []

    @contextmanager
    def session(self):
        yield self

    def add(self, value) -> None:
        self.added.append(value)


def test_postgres_logger_writes_event_log() -> None:
    database = FakeDatabase()
    logger = PostgresLogger(database, Settings())

    logger._write_event(
        "run_1",
        "usr_1",
        "start",
        "run_started",
        "started",
        {"safe": "value"},
        "INFO",
        7,
        "release_note_creation",
    )

    event = database.added[0]
    assert isinstance(event, AgentEventLog)
    assert event.run_id == "run_1"
    assert event.workflow_type == "release_note_creation"
    assert event.payload == {"safe": "value"}
    assert event.duration_ms == 7


def test_postgres_logger_writes_llm_review_log() -> None:
    database = FakeDatabase()
    logger = PostgresLogger(database, Settings(azure_openai_gpt5_deployment="gpt-5-prod"))

    logger._write_llm_review(
        "run_1",
        "usr_1",
        "plan",
        "diagnose health",
        {"steps": ["rest.get"], "prompt_version": "test_v1", "prompt_hash": "abc"},
        "Use read-only tools",
        "done",
        "release_note_creation",
    )

    review = database.added[0]
    assert isinstance(review, LlmReviewLog)
    assert review.workflow_type == "release_note_creation"
    assert review.model_deployment == "gpt-5-prod"
    assert review.plan_json["steps"] == ["rest.get"]
    assert review.prompt_version == "test_v1"
    assert review.prompt_hash == "abc"
    assert review.human_review_status == "pending"


def test_postgres_logger_writes_tool_execution_log() -> None:
    database = FakeDatabase()
    logger = PostgresLogger(database, Settings())

    logger._write_tool(
        "run_1",
        "rest.get",
        "rest",
        "success",
        {"url": "http://example.test"},
        {"status_code": 200},
        "",
        12,
    )

    tool_log = database.added[0]
    assert isinstance(tool_log, ToolExecutionLog)
    assert tool_log.tool_name == "rest.get"
    assert tool_log.status == "success"
    assert tool_log.response_summary == {"status_code": 200}