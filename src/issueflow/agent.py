"""Budgeted single-agent loop with an explicit tool boundary."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field, NonNegativeFloat, NonNegativeInt

from issueflow.models import BenchmarkCase, Budget, RunStatus, TraceStep

ALLOWED_TOOLS = frozenset({"search", "read_file", "apply_patch", "run_tests"})
TOOL_ARGUMENTS = {
    "search": frozenset({"query"}),
    "read_file": frozenset({"path", "start_line", "end_line"}),
    "apply_patch": frozenset({"path", "old_text", "new_text", "patch"}),
    "run_tests": frozenset({"command"}),
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search for an exact text fragment inside the task workspace.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a line range from a relative workspace file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": (
                "Replace exactly one known text fragment in a workspace file. Copy old_text "
                "verbatim from read_file output, excluding its displayed line-number prefix."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path of the existing workspace file.",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact non-empty text currently present exactly once.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text, including intended indentation.",
                    },
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run one test command registered by the benchmark case.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
]

MODEL_PRICES_PER_MILLION = {
    "deepseek-v4-flash": {"cache_hit": 0.0028, "cache_miss": 0.14, "output": 0.28},
    "deepseek-v4-pro": {"cache_hit": 0.003625, "cache_miss": 0.435, "output": 0.87},
}


class ModelAction(BaseModel):
    """One structured action proposed by the model."""

    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cost_usd: NonNegativeFloat = 0.0


class AgentResult(BaseModel):
    """Terminal outcome and trace from one repair attempt."""

    status: RunStatus
    stop_reason: str
    steps: list[TraceStep] = Field(default_factory=list)
    tool_calls: NonNegativeInt = 0
    patch_attempts: NonNegativeInt = 0
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cost_usd: NonNegativeFloat = 0.0
    duration_ms: NonNegativeInt = 0
    final_message: str = ""


class ModelClient(Protocol):
    """Small interface that keeps the agent independent of one model vendor."""

    def next_action(self, issue: str, history: list[dict[str, object]]) -> ModelAction: ...


class DeepSeekModelClient:
    """HTTPX adapter for DeepSeek's OpenAI-compatible tool-calling endpoint."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.http_client = http_client or httpx.Client(timeout=60)

    def next_action(self, issue: str, history: list[dict[str, object]]) -> ModelAction:
        """Request exactly one structured tool action, or a final response."""
        context = json.dumps(history, ensure_ascii=False, separators=(",", ":"))
        response = self.http_client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a single software-repair agent. Use one provided tool at a "
                            "time. Inspect evidence before editing, use unified diffs, and run the "
                            "registered tests. Never invent tools or shell commands. When the repair "
                            "is complete, answer briefly without a tool call."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Issue:\n{issue}\n\nTool history (JSON):\n{context}",
                    },
                ],
                "tools": TOOL_DEFINITIONS,
                "tool_choice": "auto",
                "thinking": {"type": "disabled"},
                "stream": False,
                "max_tokens": 2_048,
            },
        )
        response.raise_for_status()
        payload = response.json()
        message = payload["choices"][0]["message"]
        usage = payload.get("usage", {})
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))
        cost_usd = self._estimate_cost(usage, input_tokens, output_tokens)

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return ModelAction(
                message=message.get("content") or "",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
            )
        function = tool_calls[0]["function"]
        arguments = json.loads(function["arguments"])
        if not isinstance(arguments, dict):
            raise TypeError("tool arguments must be a JSON object")
        return ModelAction(
            tool=function["name"],
            arguments=arguments,
            message=message.get("content") or "",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )

    def _estimate_cost(
        self, usage: dict[str, object], input_tokens: int, output_tokens: int
    ) -> float:
        """Estimate request cost from the current official per-token rates."""
        prices = MODEL_PRICES_PER_MILLION.get(self.model)
        if prices is None:
            return 0.0
        cache_hit = int(usage.get("prompt_cache_hit_tokens", 0))
        cache_miss = int(usage.get("prompt_cache_miss_tokens", input_tokens - cache_hit))
        return (
            cache_hit * prices["cache_hit"]
            + cache_miss * prices["cache_miss"]
            + output_tokens * prices["output"]
        ) / 1_000_000


class SandboxExecution(Protocol):
    """Result shape returned by a sandbox runner."""

    returncode: int
    output: str
    timed_out: bool


class SandboxRunner(Protocol):
    """Minimal sandbox interface used by the test tool."""

    def run(self, workspace: Path, command: str, timeout_seconds: int) -> SandboxExecution: ...


class ToolExecutor:
    """Execute only the narrow tools exposed to the model."""

    def __init__(self, workspace: Path, case: BenchmarkCase, sandbox: SandboxRunner | None) -> None:
        self.workspace = workspace.resolve()
        self.case = case
        self.sandbox = sandbox

    def execute(self, action: ModelAction, timeout_seconds: int = 60) -> str:
        """Execute a validated model action and return a compact observation."""
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        expected_arguments = TOOL_ARGUMENTS.get(action.tool or "")
        if expected_arguments is not None:
            unexpected = set(action.arguments) - expected_arguments
            if unexpected:
                names = ", ".join(sorted(unexpected))
                raise ValueError(f"unexpected arguments for {action.tool}: {names}")
        if action.tool == "search":
            query = action.arguments.get("query")
            if not isinstance(query, str) or not query:
                raise ValueError("search requires a non-empty query")
            completed = subprocess.run(
                [
                    "rg",
                    "--line-number",
                    "--fixed-strings",
                    "--glob",
                    "!.git",
                    "--",
                    query,
                    ".",
                ],
                cwd=self.workspace,
                capture_output=True,
                check=False,
                text=True,
                timeout=min(10, timeout_seconds),
            )
            return completed.stdout or completed.stderr or "no matches"
        if action.tool == "read_file":
            path = self._resolve_workspace_path(action.arguments.get("path"))
            start_line = action.arguments.get("start_line", 1)
            end_line = action.arguments.get("end_line", start_line + 199)
            if (
                not isinstance(start_line, int)
                or not isinstance(end_line, int)
                or start_line < 1
                or end_line < start_line
            ):
                raise ValueError("line range must contain positive ascending integers")
            lines = path.read_text(encoding="utf-8").splitlines()
            selected = lines[start_line - 1 : end_line]
            return "\n".join(
                f"{line_number}: {line}"
                for line_number, line in enumerate(selected, start=start_line)
            )
        if action.tool == "apply_patch":
            if "patch" not in action.arguments:
                self._apply_structured_patch(
                    action.arguments.get("path"),
                    action.arguments.get("old_text"),
                    action.arguments.get("new_text"),
                )
                return "patch applied"
            if set(action.arguments) != {"patch"}:
                raise ValueError("apply_patch accepts either structured fields or patch")
            patch = action.arguments.get("patch")
            if not isinstance(patch, str) or not patch.strip():
                raise ValueError("apply_patch requires a non-empty patch")
            if patch.startswith("*** Begin Patch\n"):
                self._apply_update_file_envelope(patch)
                return "patch applied"
            self._validate_patch_paths(patch)
            completed = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", "-"],
                cwd=self.workspace,
                capture_output=True,
                check=False,
                input=patch,
                text=True,
                timeout=min(10, timeout_seconds),
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip() or "git apply failed"
                raise ValueError(detail)
            return "patch applied"
        if action.tool == "run_tests":
            command = action.arguments.get("command")
            allowed_commands = {self.case.reproduce_command, self.case.verify_command}
            if not isinstance(command, str) or command not in allowed_commands:
                raise ValueError("test command is not registered for this case")
            if self.sandbox is None:
                raise RuntimeError("run_tests requires a sandbox")
            result = self.sandbox.run(self.workspace, command, timeout_seconds=timeout_seconds)
            if result.timed_out:
                raise TimeoutError(result.output)
            return f"exit_code={result.returncode}\n{result.output}".rstrip()
        raise NotImplementedError(f"tool is not implemented: {action.tool}")

    def _apply_structured_patch(
        self, path_value: object, old_value: object, new_value: object
    ) -> None:
        """Replace one exact text occurrence inside a workspace file."""
        path = self._resolve_workspace_path(path_value)
        if not isinstance(old_value, str) or not old_value or not isinstance(new_value, str):
            raise ValueError("structured patch requires path, old_text, and new_text")
        original = path.read_text(encoding="utf-8")
        matches = original.count(old_value)
        if matches != 1:
            raise ValueError(f"old_text must match exactly once: found {matches}")
        path.write_text(original.replace(old_value, new_value, 1), encoding="utf-8")

    def _resolve_workspace_path(self, value: object) -> Path:
        """Resolve a relative path without allowing traversal or symlink escape."""
        if not isinstance(value, str) or not value or Path(value).is_absolute():
            raise ValueError("path must stay inside workspace")
        resolved = (self.workspace / value).resolve()
        try:
            resolved.relative_to(self.workspace)
        except ValueError as error:
            raise ValueError("path must stay inside workspace") from error
        return resolved

    def _validate_patch_paths(self, patch: str) -> None:
        """Reject patch headers that could address files outside the workspace."""
        paths_found = 0
        for line in patch.splitlines():
            if not line.startswith(("--- ", "+++ ")):
                continue
            raw_path = line[4:].split("\t", maxsplit=1)[0]
            if raw_path == "/dev/null":
                continue
            if raw_path.startswith(("a/", "b/")):
                raw_path = raw_path[2:]
            self._resolve_workspace_path(raw_path)
            paths_found += 1
        if paths_found == 0:
            raise ValueError("patch must include workspace file headers")

    def _apply_update_file_envelope(self, patch: str) -> None:
        """Apply the model's context patch format without executing arbitrary commands."""
        lines = patch.splitlines()
        if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
            raise ValueError("invalid model patch envelope")

        pending_writes: dict[Path, str] = {}
        index = 1
        while index < len(lines) - 1:
            header = lines[index]
            prefix = "*** Update File: "
            if not header.startswith(prefix):
                raise ValueError("model patch supports only Update File sections")
            path = self._resolve_workspace_path(header.removeprefix(prefix))
            index += 1
            hunks: list[list[str]] = []
            current_hunk: list[str] = []
            while index < len(lines) - 1 and not lines[index].startswith("*** Update File: "):
                line = lines[index]
                if line.startswith("*** "):
                    raise ValueError("unsupported model patch section")
                if line.startswith("@@"):
                    if current_hunk:
                        hunks.append(current_hunk)
                        current_hunk = []
                elif line and line[0] in {" ", "+", "-"}:
                    current_hunk.append(line)
                else:
                    raise ValueError("invalid model patch hunk")
                index += 1
            if current_hunk:
                hunks.append(current_hunk)
            if not hunks:
                raise ValueError("model patch requires at least one hunk")

            original = pending_writes.get(path, path.read_text(encoding="utf-8"))
            keep_trailing_newline = original.endswith("\n")
            file_lines = original.splitlines()
            for hunk in hunks:
                old_lines = [line[1:] for line in hunk if line[0] in {" ", "-"}]
                new_lines = [line[1:] for line in hunk if line[0] in {" ", "+"}]
                if not old_lines:
                    raise ValueError("model patch hunk requires existing context")
                matches = [
                    start
                    for start in range(len(file_lines) - len(old_lines) + 1)
                    if file_lines[start : start + len(old_lines)] == old_lines
                ]
                if len(matches) != 1:
                    raise ValueError("model patch context must match exactly once")
                start = matches[0]
                file_lines[start : start + len(old_lines)] = new_lines
            updated = "\n".join(file_lines)
            if keep_trailing_newline:
                updated += "\n"
            pending_writes[path] = updated

        if not pending_writes:
            raise ValueError("model patch contains no file updates")
        for path, updated in pending_writes.items():
            path.write_text(updated, encoding="utf-8")


class SingleAgent:
    """Ask one model for actions until a hard budget or terminal condition is reached."""

    def __init__(
        self,
        model: ModelClient,
        tools: ToolExecutor,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.model = model
        self.tools = tools
        self.clock = clock

    def run(self, case: BenchmarkCase, workspace: Path, budget: Budget) -> AgentResult:
        """Run the bounded repair loop for one benchmark case."""
        steps: list[TraceStep] = []
        history: list[dict[str, object]] = []
        tool_calls = 0
        patch_attempts = 0
        input_tokens = 0
        output_tokens = 0
        cost_usd = 0.0
        started_at = self.clock()

        def elapsed_seconds() -> float:
            return self.clock() - started_at

        def result(status: RunStatus, stop_reason: str, final_message: str = "") -> AgentResult:
            return AgentResult(
                status=status,
                stop_reason=stop_reason,
                steps=steps,
                tool_calls=tool_calls,
                patch_attempts=patch_attempts,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                duration_ms=max(0, int(elapsed_seconds() * 1_000)),
                final_message=final_message,
            )

        def append_step(
            action: ModelAction,
            output: str,
            status: str,
            duration_ms: int = 0,
        ) -> None:
            if action.tool == "apply_patch":
                input_summary = f"apply_patch: {len(str(action.arguments.get('patch', '')))} chars"
            else:
                input_summary = f"{action.tool or 'final'}: {action.arguments}"
            steps.append(
                TraceStep(
                    sequence=len(steps) + 1,
                    role="single_agent",
                    step_type="tool" if action.tool else "model",
                    input_summary=input_summary[:2_000],
                    output_summary=output[:2_000],
                    status=status,
                    duration_ms=duration_ms,
                    input_tokens=action.input_tokens,
                    output_tokens=action.output_tokens,
                    cost_usd=action.cost_usd,
                )
            )

        if workspace.resolve() != self.tools.workspace:
            return result(RunStatus.FAILED, "workspace_mismatch")

        while True:
            if elapsed_seconds() >= budget.max_seconds:
                return result(RunStatus.TIMED_OUT, "time_budget_exhausted")
            if tool_calls >= budget.max_tool_calls:
                return result(RunStatus.BUDGET_EXHAUSTED, "tool_budget_exhausted")

            try:
                action = self.model.next_action(case.issue, history)
            except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as error:
                return result(RunStatus.FAILED, f"model_error:{type(error).__name__}")

            input_tokens += action.input_tokens
            output_tokens += action.output_tokens
            cost_usd += action.cost_usd

            budget_reason = None
            if input_tokens > budget.max_input_tokens:
                budget_reason = "input_token_budget_exhausted"
            elif output_tokens > budget.max_output_tokens:
                budget_reason = "output_token_budget_exhausted"
            elif cost_usd > budget.max_cost_usd:
                budget_reason = "cost_budget_exhausted"
            if budget_reason is not None:
                append_step(action, budget_reason, "budget_exhausted")
                return result(RunStatus.BUDGET_EXHAUSTED, budget_reason)
            if elapsed_seconds() >= budget.max_seconds:
                append_step(action, "time budget exhausted", "timed_out")
                return result(RunStatus.TIMED_OUT, "time_budget_exhausted")

            if action.tool is None:
                append_step(action, action.message, "failed")
                return result(
                    RunStatus.FAILED,
                    "model_finished_without_verification",
                    action.message,
                )
            if action.tool not in ALLOWED_TOOLS:
                append_step(action, "tool rejected by allowlist", "failed")
                return result(RunStatus.FAILED, f"disallowed_tool:{action.tool}")

            if action.tool == "apply_patch":
                if patch_attempts >= budget.max_patch_attempts:
                    append_step(action, "patch budget exhausted", "budget_exhausted")
                    return result(RunStatus.BUDGET_EXHAUSTED, "patch_budget_exhausted")
                patch_attempts += 1

            tool_calls += 1
            tool_started_at = self.clock()
            remaining_seconds = max(1, int(budget.max_seconds - elapsed_seconds()))
            try:
                observation = self.tools.execute(action, timeout_seconds=remaining_seconds)
            except TimeoutError as error:
                append_step(
                    action,
                    str(error) or "tool timed out",
                    "timed_out",
                    max(0, int((self.clock() - tool_started_at) * 1_000)),
                )
                return result(RunStatus.TIMED_OUT, f"tool_timeout:{action.tool}")
            except (ValueError, TypeError, FileNotFoundError, UnicodeDecodeError) as error:
                append_step(
                    action,
                    str(error),
                    "failed",
                    max(0, int((self.clock() - tool_started_at) * 1_000)),
                )
                return result(RunStatus.FAILED, f"invalid_arguments:{action.tool}")
            history.append(
                {
                    "tool": action.tool,
                    "arguments": action.arguments,
                    "observation": observation,
                }
            )
            append_step(
                action,
                observation,
                "completed",
                max(0, int((self.clock() - tool_started_at) * 1_000)),
            )
            if (
                action.tool == "run_tests"
                and action.arguments.get("command") == case.verify_command
                and observation.startswith("exit_code=0")
            ):
                return result(RunStatus.SUCCEEDED, "verification_passed")
            if elapsed_seconds() >= budget.max_seconds:
                return result(RunStatus.TIMED_OUT, "time_budget_exhausted")
