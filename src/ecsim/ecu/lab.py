"""Virtual ECU Lab service: build + run one scenario, record it for reproducibility.

Lab runs are for development and debugging; they are *not* graded evidence.
Graded evidence comes only from exercise submissions (learning engine).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ecsim.ecu.build import BuildResult, build_app
from ecsim.ecu.runner import RunOutcome, run_isolated


@dataclass
class LabRun:
    build: BuildResult
    outcome: RunOutcome | None
    run_id: int | None = None


class EcuLab:
    def __init__(self, conn: sqlite3.Connection, compiler: str = "gcc") -> None:
        self.conn = conn
        self.compiler = compiler

    def run(self, source: str, profile: str, scenario: str, seed: int = 1,
            learner_id: int | None = None) -> LabRun:
        with tempfile.TemporaryDirectory(prefix="ecsim_lab_", ignore_cleanup_errors=True) as tmp:
            b = build_app(source, Path(tmp), self.compiler)
            if not b.ok:
                return LabRun(b, None)
            out = run_isolated(b.lib_path, profile, scenario, seed)
        run_id = None
        if learner_id is not None and out.ok:
            r = out.result
            summary = {"checks": [c.__dict__ for c in r.checks], "resets": r.resets,
                       "dem_log": r.dem_log, "deadline_misses": r.deadline_misses}
            cur = self.conn.execute(
                "INSERT INTO ecu_run(learner_id, profile, scenario, source_sha, seed, passed, cpu_load, resets,"
                " summary_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (learner_id, profile, scenario, hashlib.sha256(source.encode()).hexdigest()[:16], seed,
                 int(r.passed), r.cpu_load, len(r.resets), json.dumps(summary)),
            )
            run_id = int(cur.lastrowid)
        return LabRun(b, out, run_id)

    def history(self, learner_id: int, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM ecu_run WHERE learner_id=? ORDER BY id DESC LIMIT ?", (learner_id, limit)
        ).fetchall()
