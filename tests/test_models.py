import pytest
from pydantic import ValidationError

from issueflow.models import BenchmarkCase, Budget, RunRecord, RunStatus, TraceStep


def test_budget_rejects_zero_tool_limit():
    with pytest.raises(ValidationError):
        Budget(
            max_tool_calls=0,
            max_patch_attempts=1,
            max_seconds=60,
            max_input_tokens=100,
            max_output_tokens=100,
            max_cost_usd=1.0,
        )


def test_run_status_identifies_terminal_and_active_states():
    assert RunStatus.FAILED.is_terminal is True
    assert RunStatus.TIMED_OUT.is_terminal is True
    assert RunStatus.BUDGET_EXHAUSTED.is_terminal is True
    assert RunStatus.RUNNING.is_terminal is False


def test_benchmark_case_requires_a_full_lowercase_git_sha():
    with pytest.raises(ValidationError):
        BenchmarkCase(
            id="bad-revision",
            kind="historical",
            repository_url="https://github.com/karpathy/micrograd",
            revision="main",
            license="MIT",
            issue="Regression in gradient calculation",
            source_url="https://github.com/karpathy/micrograd/issues/1",
            reproduce_command="python -m pytest",
            verify_command="python -m pytest",
            reference_patch="patches/bad-revision.patch",
            construction_notes="",
        )


def test_trace_step_requires_a_positive_sequence_number():
    with pytest.raises(ValidationError):
        TraceStep(
            sequence=0,
            role="single_agent",
            step_type="tool",
            input_summary="search Value",
            output_summary="micrograd/engine.py:1",
            status="ok",
        )


def test_new_run_record_starts_queued():
    run = RunRecord(id="run-123", case_id="historical-01")

    assert run.status is RunStatus.QUEUED


def test_constructed_case_requires_a_fault_patch():
    with pytest.raises(ValidationError):
        BenchmarkCase(
            id="constructed-01",
            kind="constructed",
            repository_url="https://github.com/karpathy/micrograd",
            revision="a" * 40,
            license="MIT",
            issue="Unary negation returns a positive value",
            source_url="https://github.com/karpathy/micrograd",
            reproduce_command="python -m pytest",
            verify_command="python -m pytest",
            reference_patch="patches/constructed-01-fix.patch",
            construction_notes="A controlled regression case.",
        )
