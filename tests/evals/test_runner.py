import json

import pytest

from codo.evals import module_runner, runner


@pytest.mark.asyncio
async def test_eval_runners_handle_empty_case_directories(tmp_path):
    assert await module_runner.run_module_evals(tmp_path / "module_cases") == []
    assert await runner.run_system_evals(tmp_path / "tasks") == []


@pytest.mark.asyncio
async def test_eval_runner_writes_empty_outputs(monkeypatch, tmp_path):
    async def empty_system_results():
        return []

    async def empty_module_results():
        return []

    monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(runner, "run_system_evals", empty_system_results)
    monkeypatch.setattr(runner, "run_module_evals", empty_module_results)

    module_path, system_path, summary_path = await runner.main()

    assert module_path.name.startswith("module_")
    assert system_path.name.startswith("system_")
    assert summary_path.name.startswith("summary_")
    assert json.loads(module_path.read_text(encoding="utf-8")) == []
    assert json.loads(system_path.read_text(encoding="utf-8")) == []
    assert "system_tasks=0" in summary_path.read_text(encoding="utf-8")
