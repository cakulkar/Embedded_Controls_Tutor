"""Learning engine: attempts, hint ladder, solution gate, independence rule.

Policy (see docs/ARCHITECTURE.md §9.1):
* Hints are revealed strictly in order; hint k (k >= 2) needs >= k-1 attempts.
* The solution needs >= 1 attempt unless the learner explicitly forces it.
* An attempt is *independent* iff the solution was not seen before it, hints used
  <= exercise.max_independent_hints, and it was not AI-assisted.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from ecsim.app import events
from ecsim.app.events import EventBus
from ecsim.learning.content import Catalog, Exercise
from ecsim.learning.graders import GradeResult, Grader


class PolicyError(RuntimeError):
    """Raised when the learner asks for something the pedagogy policy forbids yet."""


@dataclass
class AttemptRecord:
    id: int
    exercise_id: str
    passed: bool
    score: float
    feedback: str
    hints_used: int
    solution_seen: bool
    ai_assisted: bool
    independent: bool
    submitted_at: str


@dataclass
class ExerciseState:
    attempts: int
    passed: bool
    passed_independently: bool
    hints_revealed: int
    hints_total: int
    next_hint_allowed: bool
    next_hint_blocker: str | None
    solution_seen: bool
    solution_allowed: bool


class LearningEngine:
    def __init__(self, conn: sqlite3.Connection, catalog: Catalog, graders: dict[str, Grader],
                 bus: EventBus | None = None) -> None:
        self.conn = conn
        self.catalog = catalog
        self.graders = graders
        self.bus = bus or EventBus()

    # ---- learners ---------------------------------------------------------
    def create_learner(self, name: str, track_code: str | None = None) -> int:
        name = name.strip()
        if not name:
            raise ValueError("learner name required")
        cur = self.conn.execute("INSERT INTO learner(name, track_code) VALUES (?,?)", (name, track_code))
        lid = int(cur.lastrowid)
        self.bus.publish(events.LEARNER_CREATED, learner_id=lid)
        return lid

    def set_track(self, learner_id: int, track_code: str) -> None:
        self.conn.execute("UPDATE learner SET track_code=? WHERE id=?", (track_code, learner_id))

    def learners(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM learner ORDER BY id").fetchall()

    # ---- lessons ----------------------------------------------------------
    def complete_lesson(self, learner_id: int, lesson_id: str) -> None:
        self.catalog.lesson(lesson_id)
        self.conn.execute(
            "INSERT OR IGNORE INTO lesson_progress(learner_id, lesson_id) VALUES (?,?)",
            (learner_id, lesson_id),
        )
        self.bus.publish(events.LESSON_COMPLETED, learner_id=learner_id, lesson_id=lesson_id)

    def completed_lessons(self, learner_id: int) -> set[str]:
        rows = self.conn.execute("SELECT lesson_id FROM lesson_progress WHERE learner_id=?", (learner_id,))
        return {r[0] for r in rows}

    # ---- state ------------------------------------------------------------
    def _count_attempts(self, learner_id: int, ex_id: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM attempt WHERE learner_id=? AND exercise_id=?", (learner_id, ex_id)
        ).fetchone()[0]

    def _hints_revealed(self, learner_id: int, ex_id: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM hint_reveal WHERE learner_id=? AND exercise_id=?", (learner_id, ex_id)
        ).fetchone()[0]

    def _solution_seen(self, learner_id: int, ex_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM solution_reveal WHERE learner_id=? AND exercise_id=?", (learner_id, ex_id)
        ).fetchone() is not None

    def state(self, learner_id: int, ex_id: str) -> ExerciseState:
        ex = self.catalog.exercise(ex_id)
        n_att = self._count_attempts(learner_id, ex_id)
        n_hint = self._hints_revealed(learner_id, ex_id)
        row = self.conn.execute(
            "SELECT MAX(passed), MAX(passed AND independent) FROM attempt "
            "WHERE learner_id=? AND exercise_id=?", (learner_id, ex_id)
        ).fetchone()
        blocker = self._hint_blocker(ex, n_hint, n_att)
        return ExerciseState(
            attempts=n_att, passed=bool(row[0]), passed_independently=bool(row[1]),
            hints_revealed=n_hint, hints_total=len(ex.hints),
            next_hint_allowed=blocker is None, next_hint_blocker=blocker,
            solution_seen=self._solution_seen(learner_id, ex_id),
            solution_allowed=n_att >= 1,
        )

    @staticmethod
    def _hint_blocker(ex: Exercise, revealed: int, attempts: int) -> str | None:
        nxt = revealed + 1
        if nxt > len(ex.hints):
            return "All hints already revealed."
        if nxt >= 2 and attempts < nxt - 1:
            return f"Hint {nxt} unlocks after {nxt - 1} attempt(s). Try applying hint {revealed} first."
        return None

    # ---- hints / solution ------------------------------------------------
    def reveal_hint(self, learner_id: int, ex_id: str) -> tuple[int, str]:
        ex = self.catalog.exercise(ex_id)
        revealed = self._hints_revealed(learner_id, ex_id)
        blocker = self._hint_blocker(ex, revealed, self._count_attempts(learner_id, ex_id))
        if blocker:
            raise PolicyError(blocker)
        level = revealed + 1
        self.conn.execute(
            "INSERT INTO hint_reveal(learner_id, exercise_id, level) VALUES (?,?,?)", (learner_id, ex_id, level)
        )
        self.bus.publish(events.HINT_REVEALED, learner_id=learner_id, exercise_id=ex_id, level=level)
        return level, ex.hints[level - 1]

    def revealed_hints(self, learner_id: int, ex_id: str) -> list[str]:
        ex = self.catalog.exercise(ex_id)
        return ex.hints[: self._hints_revealed(learner_id, ex_id)]

    def reveal_solution(self, learner_id: int, ex_id: str, force: bool = False) -> str:
        ex = self.catalog.exercise(ex_id)
        attempts = self._count_attempts(learner_id, ex_id)
        if attempts < 1 and not force:
            raise PolicyError(
                "Submit at least one attempt before viewing the solution "
                "(or explicitly request it - that exercise will then not count as independent work)."
            )
        explicit = int(force and attempts < 1)
        self.conn.execute(
            "INSERT OR IGNORE INTO solution_reveal(learner_id, exercise_id, explicit_request) VALUES (?,?,?)",
            (learner_id, ex_id, explicit),
        )
        self.bus.publish(events.SOLUTION_REVEALED, learner_id=learner_id, exercise_id=ex_id, explicit=bool(explicit))
        return ex.solution

    # ---- submission ------------------------------------------------------
    def submit(self, learner_id: int, ex_id: str, submission: str, ai_assisted: bool = False) -> AttemptRecord:
        ex = self.catalog.exercise(ex_id)
        grader = self.graders[ex.kind]
        result: GradeResult = grader.grade(ex.spec, submission)
        if result.details.get("infrastructure_error"):
            # Missing toolchain is not the learner's failure - don't record evidence.
            return AttemptRecord(-1, ex_id, False, 0.0, result.feedback, 0, False, ai_assisted, False, "")
        hints = self._hints_revealed(learner_id, ex_id)
        sol = self._solution_seen(learner_id, ex_id)
        independent = (not sol) and (hints <= ex.max_independent_hints) and (not ai_assisted)
        cur = self.conn.execute(
            "INSERT INTO attempt(learner_id, exercise_id, content_hash, submission, passed, score, feedback,"
            " hints_used, solution_seen, ai_assisted, independent) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (learner_id, ex_id, ex.content_hash, submission, int(result.passed), result.score,
             result.feedback, hints, int(sol), int(ai_assisted), int(independent)),
        )
        row = self.conn.execute("SELECT submitted_at FROM attempt WHERE id=?", (cur.lastrowid,)).fetchone()
        rec = AttemptRecord(int(cur.lastrowid), ex_id, result.passed, result.score, result.feedback,
                            hints, sol, ai_assisted, independent, row[0])
        self.bus.publish(events.ATTEMPT_GRADED, learner_id=learner_id, attempt=rec)
        return rec

    def attempts(self, learner_id: int, ex_id: str | None = None, limit: int = 50) -> list[AttemptRecord]:
        q = "SELECT * FROM attempt WHERE learner_id=?"
        args: list = [learner_id]
        if ex_id:
            q += " AND exercise_id=?"
            args.append(ex_id)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [
            AttemptRecord(r["id"], r["exercise_id"], bool(r["passed"]), r["score"], r["feedback"],
                          r["hints_used"], bool(r["solution_seen"]), bool(r["ai_assisted"]),
                          bool(r["independent"]), r["submitted_at"])
            for r in self.conn.execute(q, args)
        ]

    def last_submission(self, learner_id: int, ex_id: str) -> str | None:
        r = self.conn.execute(
            "SELECT submission FROM attempt WHERE learner_id=? AND exercise_id=? ORDER BY id DESC LIMIT 1",
            (learner_id, ex_id),
        ).fetchone()
        return r[0] if r else None

    def export_evidence(self, learner_id: int) -> str:
        """JSON export of a learner's evidence (portfolio/backup interchange)."""
        rows = self.conn.execute("SELECT * FROM attempt WHERE learner_id=? ORDER BY id", (learner_id,))
        return json.dumps([dict(r) for r in rows], indent=2)
