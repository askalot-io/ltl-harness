"""Flight recorder: one JSONL file per run — same format and directory as the
JS harness, so both show up in the same dashboard."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


class Recorder:
    def __init__(self, runs_dir: str | Path, run_id: str, meta: dict):
        self.runs_dir = Path(runs_dir)
        self.run_id = run_id
        self.file = self.runs_dir / f"{run_id}.jsonl"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.write({"type": "run_start", "runId": run_id, **meta})

    def write(self, record: dict) -> None:
        line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **record})
        with open(self.file, "a") as f:
            f.write(line + "\n")


def list_runs(runs_dir: str | Path) -> list[dict]:
    runs_dir = Path(runs_dir)
    if not runs_dir.exists():
        return []
    out = []
    for f in runs_dir.glob("*.jsonl"):
        stat = f.stat()
        out.append({"runId": f.stem, "mtime": stat.st_mtime * 1000, "size": stat.st_size})
    return sorted(out, key=lambda r: -r["mtime"])


def read_run(runs_dir: str | Path, run_id: str) -> list[dict] | None:
    file = Path(runs_dir) / (os.path.basename(run_id) + ".jsonl")
    if not file.exists():
        return None
    records = []
    for line in file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
