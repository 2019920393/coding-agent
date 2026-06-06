"""Module-level evaluation runner."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

from dotenv import load_dotenv

from codo.evals.schema import ModuleCaseResult

EVALS_DIR = Path(__file__).resolve().parent
MODULE_CASES_DIR = EVALS_DIR / "module_cases"
RESULTS_DIR = EVALS_DIR / "results"

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _load_case_files(cases_dir: Path = MODULE_CASES_DIR) -> list[dict[str, Any]]:
    case_files = sorted(cases_dir.glob("*.json"))
    return [json.loads(path.read_text(encoding="utf-8")) for path in case_files]


def _build_eval_messages(raw_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给每条消息生成 uuid，extract_memories 用 uuid 做游标。"""
    enriched: list[dict[str, Any]] = []
    parent: str | None = None
    for raw in raw_messages:
        msg_uuid = str(uuid.uuid4())
        enriched.append({
            "role": raw["role"],
            "content": raw["content"],
            "uuid": msg_uuid,
            "parent_uuid": parent,
            "type": raw["role"],
        })
        parent = msg_uuid
    return enriched


async def _run_memory_extract_case(case: dict[str, Any]) -> tuple[bool, str | None]:
    """运行一条 memory.extract case。返回 (passed, error_message)。"""
    from anthropic import AsyncAnthropic

    from codo.services.memory.extract import MemoryExtractionState, extract_memories

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return False, "ANTHROPIC_API_KEY missing"

    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "default_headers": {"Authorization": f"Bearer {api_key}"},
    }
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if base_url:
        client_kwargs["base_url"] = base_url
    client = AsyncAnthropic(**client_kwargs)

    model = os.environ.get("CODO_EVAL_MODEL", "").strip() or "claude-haiku-4-5-20251001"

    messages = _build_eval_messages(case["messages"])
    state = MemoryExtractionState()
    expect_write: bool = bool(case.get("expect_write"))

    with tempfile.TemporaryDirectory(prefix="codo_eval_mem_") as tmp:
        tmp_path = Path(tmp)
        with patch(
            "codo.services.memory.paths.get_memory_dir",
            return_value=tmp_path,
        ):
            try:
                written = await extract_memories(
                    client=client,
                    model=model,
                    messages=messages,
                    cwd=str(tmp_path),
                    state=state,
                )
            except Exception as exc:
                return False, f"extract_memories raised: {exc}"

        # 过滤掉 MEMORY.md 索引本身，只看是否真的写出了具体记忆文件。
        written_real = [p for p in (written or []) if not str(p).endswith("MEMORY.md")]
        actual_write = bool(written_real)
        passed = actual_write == expect_write
        if passed:
            return True, None
        return False, f"expected_write={expect_write}, actual_write={actual_write}, written={written_real}"


async def _run_case(module: str, case: dict[str, Any]) -> ModuleCaseResult:
    start = time.perf_counter()
    case_id = str(case["id"])
    error_message: str | None = None
    passed = False

    if module == "memory.extract":
        passed, error_message = await _run_memory_extract_case(case)
    else:
        error_message = f"No module evaluator registered for module={module}"

    latency_ms = (time.perf_counter() - start) * 1000
    return ModuleCaseResult(
        module=module,
        case_id=case_id,
        passed=passed,
        latency_ms=latency_ms,
        error_message=error_message,
    )


async def run_module_evals(cases_dir: Path = MODULE_CASES_DIR) -> list[ModuleCaseResult]:
    case_files = _load_case_files(cases_dir)
    flat_cases: list[tuple[str, dict[str, Any]]] = [
        (str(case_file["module"]), case)
        for case_file in case_files
        for case in case_file.get("cases", [])
    ]
    if not flat_cases:
        return []
    # 串行避免上游 429（cc-vibe 网关有并发上限）。
    results: list[ModuleCaseResult] = []
    for module, case in flat_cases:
        results.append(await _run_case(module, case))
    return results


async def main() -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results = await run_module_evals()
    output_path = RESULTS_DIR / f"module_{_timestamp()}.json"
    output_path.write_text(
        json.dumps([asdict(result) for result in results], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    rate = passed / total if total else 0.0
    print(f"saved: {output_path}")
    print(f"passed: {passed}/{total}  ({rate:.1%})")
    return output_path


if __name__ == "__main__":
    print(asyncio.run(main()))
