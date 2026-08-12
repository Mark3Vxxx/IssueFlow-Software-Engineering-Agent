"""SQLite-backed immutable traces for IssueFlow runs."""

import json
import re
import sqlite3
from pathlib import Path

from issueflow.models import RunRecord, RunStatus, TraceStep, Usage

_SECRET_PATTERNS = (
    re.compile(r"Authorization:\s*Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"DEEPSEEK_API_KEY=\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]+\b"),
)


def redact(text: str) -> str:
    """Remove credentials before trace data reaches persistent storage."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


class TraceStore:
    """Persist runs and ordered trace steps in a local SQLite database."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    architecture TEXT NOT NULL DEFAULT 'single',
                    status TEXT NOT NULL,
                    stop_reason TEXT,
                    functional_success INTEGER,
                    review_status TEXT,
                    review_reasons TEXT NOT NULL DEFAULT '[]',
                    usage TEXT NOT NULL DEFAULT '{}',
                    role_usage TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS trace_steps (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    step_type TEXT NOT NULL,
                    input_summary TEXT NOT NULL,
                    output_summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (run_id, sequence),
                    FOREIGN KEY (run_id) REFERENCES runs(id)
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)").fetchall()}
            if "stop_reason" not in columns:
                connection.execute("ALTER TABLE runs ADD COLUMN stop_reason TEXT")
            for name, definition in {
                "architecture": "TEXT NOT NULL DEFAULT 'single'",
                "functional_success": "INTEGER",
                "review_status": "TEXT",
                "review_reasons": "TEXT NOT NULL DEFAULT '[]'",
                "usage": "TEXT NOT NULL DEFAULT '{}'",
                "role_usage": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE runs ADD COLUMN {name} {definition}")
            step_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(trace_steps)").fetchall()
            }
            for name, definition in {
                "duration_ms": "INTEGER NOT NULL DEFAULT 0",
                "input_tokens": "INTEGER NOT NULL DEFAULT 0",
                "output_tokens": "INTEGER NOT NULL DEFAULT 0",
                "cost_usd": "REAL NOT NULL DEFAULT 0",
            }.items():
                if name not in step_columns:
                    connection.execute(f"ALTER TABLE trace_steps ADD COLUMN {name} {definition}")

    def create_run(self, record: RunRecord) -> None:
        """Create the top-level record before any trace step is written."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (id, case_id, architecture, status, usage, role_usage)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.case_id,
                    record.architecture,
                    record.status.value,
                    _usage_json(record.usage),
                    _role_usage_json(record.role_usage),
                ),
            )

    def append_step(self, run_id: str, step: TraceStep) -> None:
        """Append one immutable, redacted step to a run."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO trace_steps
                    (run_id, sequence, role, step_type, input_summary, output_summary, status,
                     duration_ms, input_tokens, output_tokens, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    step.sequence,
                    step.role,
                    step.step_type,
                    redact(step.input_summary),
                    redact(step.output_summary),
                    step.status,
                    step.duration_ms,
                    step.input_tokens,
                    step.output_tokens,
                    step.cost_usd,
                ),
            )

    def start_run(self, run_id: str) -> None:
        """Transition one queued run to running exactly once."""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE runs SET status = ? WHERE id = ? AND status = ?",
                (RunStatus.RUNNING.value, run_id, RunStatus.QUEUED.value),
            )
            if cursor.rowcount != 1:
                raise ValueError("start_run requires a queued run")

    def finish_run(
        self,
        run_id: str,
        status: RunStatus,
        stop_reason: str,
        *,
        functional_success: bool | None = None,
        review_status: str | None = None,
        review_reasons: list[str] | None = None,
        usage: Usage | None = None,
        role_usage: dict[str, Usage] | None = None,
    ) -> None:
        """Record the terminal outcome and its human-readable stopping reason."""
        if not status.is_terminal:
            raise ValueError("finish_run requires a terminal status")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET status = ?, stop_reason = ?, functional_success = ?,
                    review_status = ?, review_reasons = ?, usage = ?, role_usage = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status.value,
                    redact(stop_reason),
                    None if functional_success is None else int(functional_success),
                    review_status,
                    json.dumps(
                        [redact(reason) for reason in review_reasons or []],
                        ensure_ascii=False,
                    ),
                    _usage_json(usage or Usage()),
                    _role_usage_json(role_usage or {}),
                    run_id,
                    RunStatus.RUNNING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("finish_run requires a running run")

    def get_run(self, run_id: str) -> RunRecord:
        """Load one run summary for services and UI consumers."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, case_id, architecture, status, stop_reason, functional_success,
                       review_status, review_reasons, usage, role_usage
                FROM runs WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RunRecord(
            id=row[0],
            case_id=row[1],
            architecture=row[2],
            status=RunStatus(row[3]),
            stop_reason=row[4],
            functional_success=None if row[5] is None else bool(row[5]),
            review_status=row[6],
            review_reasons=json.loads(row[7]),
            usage=_load_usage(row[8]),
            role_usage=_load_role_usage(row[9]),
        )

    def export_json(self, run_id: str) -> dict[str, object]:
        """Export one run in a stable JSON-compatible structure."""
        with self._connect() as connection:
            run = connection.execute(
                """
                SELECT id, case_id, architecture, status, stop_reason, functional_success,
                       review_status, review_reasons, usage, role_usage
                FROM runs WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
            steps = connection.execute(
                """
                SELECT sequence, role, step_type, input_summary, output_summary, status,
                       duration_ms, input_tokens, output_tokens, cost_usd
                FROM trace_steps WHERE run_id = ? ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        if run is None:
            raise KeyError(run_id)
        return {
            "run": {
                "id": run[0],
                "case_id": run[1],
                "architecture": run[2],
                "status": run[3],
                "stop_reason": run[4],
                "functional_success": None if run[5] is None else bool(run[5]),
                "review_status": run[6],
                "review_reasons": json.loads(run[7]),
                "usage": _load_usage(run[8]).model_dump(),
                "role_usage": {
                    role: usage.model_dump() for role, usage in _load_role_usage(run[9]).items()
                },
            },
            "steps": [
                {
                    "sequence": step[0],
                    "role": step[1],
                    "step_type": step[2],
                    "input_summary": step[3],
                    "output_summary": step[4],
                    "status": step[5],
                    "duration_ms": step[6],
                    "input_tokens": step[7],
                    "output_tokens": step[8],
                    "cost_usd": step[9],
                }
                for step in steps
            ],
            "artifacts": [],
        }

    def export_json_text(self, run_id: str) -> str:
        """Serialize an exported trace for file download or archival."""
        return json.dumps(self.export_json(run_id), ensure_ascii=False, sort_keys=True)


def _usage_json(usage: Usage) -> str:
    return json.dumps(usage.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def _role_usage_json(role_usage: dict[str, Usage]) -> str:
    return json.dumps(
        {str(role): usage.model_dump(mode="json") for role, usage in role_usage.items()},
        ensure_ascii=False,
        sort_keys=True,
    )


def _load_usage(raw: str) -> Usage:
    return Usage.model_validate(json.loads(raw or "{}"))


def _load_role_usage(raw: str) -> dict[str, Usage]:
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise TypeError("role_usage must be a JSON object")
    return {str(role): Usage.model_validate(usage) for role, usage in parsed.items()}
