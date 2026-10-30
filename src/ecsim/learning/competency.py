"""Competency scoring from independent evidence only.

A level is achieved when >= LEVEL_THRESHOLD of that level's available points are
earned by independent passes AND the previous level is achieved. Advanced and
expert additionally require at least one practical exercise passed at that level.
A level with no available content cannot be achieved (no evidence, no credit).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ecsim.learning.content import LEVELS, Catalog

LEVEL_THRESHOLD = 0.8
PRACTICAL_REQUIRED = {"advanced", "expert"}


@dataclass
class LevelStatus:
    level: str
    points_available: int
    points_earned: int
    practical_available: int
    practical_passed: int
    achieved: bool

    @property
    def fraction(self) -> float:
        return self.points_earned / self.points_available if self.points_available else 0.0


@dataclass
class CompetencyStatus:
    code: str
    title: str
    weight: float
    levels: list[LevelStatus]

    @property
    def achieved_level(self) -> str | None:
        last = None
        for ls in self.levels:
            if not ls.achieved:
                break
            last = ls.level
        return last

    @property
    def achieved_index(self) -> int:
        """0 = none, 1 = beginner ... 4 = expert."""
        lvl = self.achieved_level
        return LEVELS.index(lvl) + 1 if lvl else 0


@dataclass
class TrackGap:
    competency: str
    required: str
    achieved: str | None
    met: bool


@dataclass
class CompetencyProfile:
    competencies: list[CompetencyStatus]
    weighted_score: float  # 0..100
    track_code: str | None = None
    track_gaps: list[TrackGap] = field(default_factory=list)

    @property
    def track_ready(self) -> bool:
        return bool(self.track_gaps) and all(g.met for g in self.track_gaps)


class CompetencyService:
    def __init__(self, conn: sqlite3.Connection, catalog: Catalog) -> None:
        self.conn = conn
        self.catalog = catalog

    def _independent_passes(self, learner_id: int) -> set[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT exercise_id FROM attempt WHERE learner_id=? AND passed=1 AND independent=1",
            (learner_id,),
        )
        return {r[0] for r in rows}

    def profile(self, learner_id: int) -> CompetencyProfile:
        passed = self._independent_passes(learner_id)
        exercises = self.catalog.all_exercises()
        comps: list[CompetencyStatus] = []
        for c in self.catalog.competencies:
            levels: list[LevelStatus] = []
            prev_ok = True
            for lvl in LEVELS:
                pool = [e for e in exercises if e.competency == c["code"] and e.level == lvl]
                avail = sum(e.points for e in pool)
                earned = sum(e.points for e in pool if e.id in passed)
                prac = [e for e in pool if e.practical]
                prac_ok = sum(1 for e in prac if e.id in passed)
                ok = (
                    prev_ok and avail > 0 and earned / avail >= LEVEL_THRESHOLD
                    and (lvl not in PRACTICAL_REQUIRED or prac_ok >= 1)
                )
                levels.append(LevelStatus(lvl, avail, earned, len(prac), prac_ok, ok))
                prev_ok = ok
            comps.append(CompetencyStatus(c["code"], c["title"], float(c["weight"]), levels))
        score = 100.0 * sum(cs.weight * cs.achieved_index / len(LEVELS) for cs in comps)

        row = self.conn.execute("SELECT track_code FROM learner WHERE id=?", (learner_id,)).fetchone()
        track_code = row[0] if row else None
        gaps: list[TrackGap] = []
        if track_code:
            track = next((t for t in self.catalog.tracks if t["code"] == track_code), None)
            if track:
                by_code = {cs.code: cs for cs in comps}
                for code, req in track["requirements"].items():
                    cs = by_code[code]
                    gaps.append(TrackGap(code, req, cs.achieved_level, cs.achieved_index >= LEVELS.index(req) + 1))
        return CompetencyProfile(comps, round(score, 2), track_code, gaps)
