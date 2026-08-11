import json
import sqlite3

import pytest

from issueflow.models import RunRecord, RunStatus, TraceStep
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

    store.finish_run(run_record.id, RunStatus.SUCCEEDED, "public_tests_passed")

    exported = store.export_json(run_record.id)
    assert exported["run"]["status"] == "succeeded"
    assert exported["run"]["stop_reason"] == "public_tests_passed"


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
