"""一次性修复：从 transcript 物理顺序重建 user/assistant 的 parent 链，
覆盖 events.jsonl 里 message_recorded 的 parent_uuid，并删除 snapshot 让它下次重建。

只动 user/assistant 两类消息，其它（last-prompt / ai-title 等）原样保留。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def repair(session_dir: Path, session_id: str) -> None:
    transcript = session_dir / f"{session_id}.jsonl"
    events = session_dir / f"{session_id}.events.jsonl"
    snapshot = session_dir / f"{session_id}.snapshot.json"

    if not transcript.exists():
        sys.exit(f"transcript not found: {transcript}")
    if not events.exists():
        sys.exit(f"events not found: {events}")

    # 1. 从 transcript 按物理顺序构建 uuid -> parent_uuid 映射
    parent_map: dict[str, str | None] = {}
    last_uuid: str | None = None
    fixed = 0
    with transcript.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") not in ("user", "assistant"):
                continue
            uid = rec.get("uuid")
            if not uid:
                continue
            # 物理上紧邻的前一条 user/assistant 就是 parent
            parent_map[uid] = last_uuid
            if rec.get("parent_uuid") != last_uuid:
                fixed += 1
            last_uuid = uid

    print(f"[transcript] {len(parent_map)} user/assistant messages, {fixed} need repair")

    # 2. 重写 events.jsonl，用新 parent_map 覆盖 message_recorded 的 parent
    events_bak = events.with_suffix(events.suffix + ".bak")
    events.rename(events_bak)
    repaired_events = 0
    skipped_events = 0
    with events_bak.open("r", encoding="utf-8") as src, events.open("w", encoding="utf-8") as dst:
        for line in src:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                dst.write(line)
                continue
            if e.get("event_type") != "message_recorded":
                dst.write(line)
                continue
            payload = e.get("payload") or {}
            msg = payload.get("message") or {}
            uid = msg.get("uuid")
            if msg.get("type") in ("user", "assistant") and uid in parent_map:
                new_parent = parent_map[uid]
                if msg.get("parent_uuid") != new_parent or payload.get("parent_uuid") != new_parent:
                    msg["parent_uuid"] = new_parent
                    payload["parent_uuid"] = new_parent
                    payload["message"] = msg
                    e["payload"] = payload
                    repaired_events += 1
                else:
                    skipped_events += 1
            dst.write(json.dumps(e, ensure_ascii=False) + "\n")

    print(f"[events] repaired {repaired_events}, already-correct {skipped_events}, backup at {events_bak.name}")

    # 3. 同步修 transcript jsonl 自身的 parent_uuid（让 load_session_from_file 也能正确 fallback）
    transcript_bak = transcript.with_suffix(transcript.suffix + ".bak")
    transcript.rename(transcript_bak)
    repaired_tr = 0
    with transcript_bak.open("r", encoding="utf-8") as src, transcript.open("w", encoding="utf-8") as dst:
        for line in src:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                dst.write(line)
                continue
            uid = rec.get("uuid")
            if rec.get("type") in ("user", "assistant") and uid in parent_map:
                new_parent = parent_map[uid]
                if rec.get("parent_uuid") != new_parent:
                    rec["parent_uuid"] = new_parent
                    repaired_tr += 1
            dst.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[transcript] repaired {repaired_tr}, backup at {transcript_bak.name}")

    # 4. 删 snapshot 让下次加载时从 events 重建（带正确 parent）
    if snapshot.exists():
        snapshot.unlink()
        print(f"[snapshot] removed {snapshot.name} (will rebuild on next load)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python repair_session_parents.py <session_dir> <session_id>")
    repair(Path(sys.argv[1]), sys.argv[2])
