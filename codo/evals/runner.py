"""System-level evaluation runner."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from codo.evals.module_runner import RESULTS_DIR, _timestamp, run_module_evals
from codo.evals.schema import EvalResult, EvalTask

EVALS_DIR = Path(__file__).resolve().parent
TASKS_DIR = EVALS_DIR / "tasks"
FIXTURES_DIR = EVALS_DIR / "fixtures"
TASK_TIMEOUT_SECONDS = 600


def _load_tasks(tasks_dir: Path = TASKS_DIR) -> list[EvalTask]:
    tasks: list[EvalTask] = []
    for path in sorted(tasks_dir.glob("*.json")):
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        tasks.append(EvalTask(**data))
    return tasks


async def _run_task(task: EvalTask) -> EvalResult:
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"codo_eval_{task.id}_") as temp_dir:
        fixture_path = FIXTURES_DIR / task.workspace_fixture
        workspace_path = Path(temp_dir) / "workspace"
        if fixture_path.exists():
            shutil.copytree(fixture_path, workspace_path)
        else:
            workspace_path.mkdir()
        await asyncio.sleep(0)
    return EvalResult(
        task_id=task.id,
        passed=False,
        turns_used=0,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        tool_calls=0,
        tool_errors=0,
        tool_retries=0,
        human_interventions=0,
        side_effect_files=[],
        error_recovery_triggered=bool(task.inject_errors),
        error_recovery_succeeded=False,
        duration_seconds=time.perf_counter() - start,
    )


async def run_system_evals(tasks_dir: Path = TASKS_DIR) -> list[EvalResult]:
    tasks = _load_tasks(tasks_dir)
    if not tasks:
        return []
    return await asyncio.gather(
        *(asyncio.wait_for(_run_task(task), timeout=TASK_TIMEOUT_SECONDS) for task in tasks)
    )


def _summary_text(system_results: list[EvalResult], module_count: int) -> str:
    passed = sum(1 for result in system_results if result.passed)
    total = len(system_results)
    pass_rate = passed / total if total else 0.0
    return "\n".join(
        [
            f"system_tasks={total}",
            f"system_pass_at_1={pass_rate:.2%}",
            f"module_results={module_count}",
            "average_token_per_task=0",
            "average_intervention_rate=0",
            "average_error_recovery_rate=0",
            "cache_hit_rate=0",
        ]
    )


async def main() -> tuple[Path, Path, Path]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = _timestamp()
    system_task = asyncio.create_task(run_system_evals())
    module_task = asyncio.create_task(run_module_evals())
    system_results = await system_task
    module_results = await module_task
    module_path = RESULTS_DIR / f"module_{timestamp}.json"
    system_path = RESULTS_DIR / f"system_{timestamp}.json"
    summary_path = RESULTS_DIR / f"summary_{timestamp}.txt"
    module_path.write_text(
        json.dumps([asdict(result) for result in module_results], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    system_path.write_text(
        json.dumps([asdict(result) for result in system_results], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    summary_path.write_text(
        _summary_text(system_results, len(module_results)),
        encoding="utf-8",
    )
    return module_path, system_path, summary_path


if __name__ == "__main__":
    print(asyncio.run(main()))
