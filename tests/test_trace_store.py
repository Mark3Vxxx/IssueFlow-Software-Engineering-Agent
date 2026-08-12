import json
import sqlite3

import pytest

from issueflow.models import RunRecord, RunStatus, TraceStep, Usage
from issueflow.trace_store import TraceStore


@pytest.fixture
def store(tmp_path):
    return TraceStore(tmp_path / "issueflow.sqlite3")


@pytest.fixture
def run_record():
    return RunRecord(id="run-123", case_id="historical-01")


def test_trace_step_sequence_is_immutable(store, run_record):
    store.create_run(run_record)
    step = TraceStep(
        sequence=1,
        role="single_agent",
        step_type="tool",
        input_summary="search Value",
        output_summary="micrograd/engine.py:1",
        status="ok",
    )
    store.append_step(run_record.id, step)

    with pytest.raises(sqlite3.IntegrityError):
        store.append_step(run_record.id, step)


def test_json_export_redacts_api_key(store, run_record):
    store.create_run(run_record)
    store.append_step(
        run_record.id,
        TraceStep(
            sequence=1,
            role="single_agent",
            step_type="model",
            input_summary="Authorization: Bearer secret-value",
            output_summary="done",
            status="ok",
        ),
    )

    exported = store.export_json(run_record.id)

    assert exported["run"]["id"] == run_record.id
    assert "secret-value" not in json.dumps(exported)


def test_finish_run_records_terminal_status_and_reason(store, run_record):
    store.create_run(run_record)
    store.start_run(run_record.id)

    usage = Usage(model_calls=2, tool_calls=3, input_tokens=40, cost_usd=0.001)
    store.finish_run(
        run_record.id,
        RunStatus.SUCCEEDED,
        "public_tests_passed",
        usage=usage,
        role_usage={"single_agent": usage},
    )

    exported = store.export_json(run_record.id)
    assert exported["run"]["status"] == "succeeded"
    assert exported["run"]["stop_reason"] == "public_tests_passed"
    assert exported["run"]["usage"] == usage.model_dump()
    assert exported["run"]["role_usage"] == {"single_agent": usage.model_dump()}


def test_run_state_must_transition_from_queued_to_running_to_terminal(store, run_record):
    store.create_run(run_record)

    with pytest.raises(ValueError, match="running run"):
        store.finish_run(run_record.id, RunStatus.FAILED, "invalid_transition")

    store.start_run(run_record.id)
    with pytest.raises(ValueError, match="queued run"):
        store.start_run(run_record.id)


def test_json_export_preserves_step_efficiency_metrics(store, run_record):
    store.create_run(run_record)
    store.append_step(
        run_record.id,
        TraceStep(
            sequence=1,
            role="single_agent",
            step_type="model",
            input_summary="plan repair",
            output_summary="call search",
            status="ok",
            duration_ms=125,
            input_tokens=30,
            output_tokens=12,
            cost_usd=0.0004,
        ),
    )

    exported = store.export_json(run_record.id)

    assert exported["steps"][0]["duration_ms"] == 125
    assert exported["steps"][0]["input_tokens"] == 30
    assert exported["steps"][0]["output_tokens"] == 12
    assert exported["steps"][0]["cost_usd"] == 0.0004


def test_finish_run_redacts_reviewer_reasons(store, run_record):
    store.create_run(run_record)
    store.start_run(run_record.id)

    store.finish_run(
        run_record.id,
        RunStatus.SUCCEEDED,
        "functional_success",
        functional_success=True,
        review_status="approved",
        review_reasons=["DEEPSEEK_API_KEY=secret-value"],
    )

    exported = store.export_json(run_record.id)
    assert exported["run"]["review_reasons"] == ["[REDACTED]"]


def test_existing_database_migrates_architecture_with_safe_single_default(tmp_path):
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                status TEXT NOT NULL
            );
            INSERT INTO runs (id, case_id, status)
            VALUES ('legacy-run', 'historical-01', 'queued');
            """
        )

    migrated = TraceStore(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = connection.execute("PRAGMA table_info(runs)").fetchall()
    architecture_column = next(column for column in columns if column[1] == "architecture")
    assert architecture_column[2:5] == ("TEXT", 1, "'single'")
    assert migrated.get_run("legacy-run").architecture == "single"
    assert migrated.export_json("legacy-run")["run"]["architecture"] == "single"
    assert migrated.export_json("legacy-run")["run"]["usage"] == Usage().model_dump()
    assert migrated.export_json("legacy-run")["run"]["role_usage"] == {}


def test_new_run_json_includes_selected_architecture(store):
    record = RunRecord(
        id="run-fixed",
        case_id="historical-01",
        architecture="fixed",
    )

    store.create_run(record)

    assert store.get_run(record.id).architecture == "fixed"
    assert store.export_json(record.id)["run"]["architecture"] == "fixed"
