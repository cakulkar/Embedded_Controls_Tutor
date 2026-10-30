"""Load, validate and sync content packs (YAML) into the database."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

LEVELS = ("beginner", "intermediate", "advanced", "expert")
LEVEL_CODE = {"b": "beginner", "i": "intermediate", "a": "advanced", "e": "expert"}
KINDS = ("numeric", "choice", "c_function")
COMPETENCY_ORDER = ("EMB", "CTL", "MBD", "VEH", "COM", "ARC", "FUS", "TST", "DOC")


class ContentError(ValueError):
    pass


@dataclass
class Exercise:
    id: str
    module_id: str
    kind: str
    title: str
    prompt: str
    competency: str
    level: str
    points: int
    practical: bool
    max_independent_hints: int
    ordinal: int
    spec: dict[str, Any]
    hints: list[str]
    solution: str

    @property
    def content_hash(self) -> str:
        blob = json.dumps(
            {"kind": self.kind, "prompt": self.prompt, "spec": self.spec}, sort_keys=True
        ).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    def public_spec(self) -> dict[str, Any]:
        """Spec fields safe to show before the solution gate opens."""
        hidden = {"answer", "correct", "reference", "mutants", "harness"}
        return {k: v for k, v in self.spec.items() if k not in hidden}


@dataclass
class Lesson:
    id: str
    module_id: str
    title: str
    body: str
    ordinal: int


@dataclass
class Module:
    id: str
    department: str
    title: str
    ordinal: int
    version: str
    lessons: list[Lesson] = field(default_factory=list)
    exercises: list[Exercise] = field(default_factory=list)


@dataclass
class Catalog:
    competencies: list[dict[str, Any]]
    departments: list[dict[str, Any]]
    tracks: list[dict[str, Any]]
    modules: list[Module]

    def exercise(self, ex_id: str) -> Exercise:
        for m in self.modules:
            for e in m.exercises:
                if e.id == ex_id:
                    return e
        raise KeyError(ex_id)

    def all_exercises(self) -> list[Exercise]:
        return [e for m in self.modules for e in m.exercises]

    def lesson(self, lesson_id: str) -> Lesson:
        for m in self.modules:
            for l in m.lessons:
                if l.id == lesson_id:
                    return l
        raise KeyError(lesson_id)


def _req(d: dict, key: str, where: str) -> Any:
    if key not in d:
        raise ContentError(f"{where}: missing '{key}'")
    return d[key]


def parse_track_requirements(req: str) -> dict[str, str]:
    parts = req.split()
    if len(parts) != len(COMPETENCY_ORDER):
        raise ContentError(f"track requirement needs {len(COMPETENCY_ORDER)} entries: {req!r}")
    out = {}
    for code, p in zip(COMPETENCY_ORDER, parts):
        if p == "-":
            continue
        if p not in LEVEL_CODE:
            raise ContentError(f"bad level code {p!r}")
        out[code] = LEVEL_CODE[p]
    return out


def load_catalog(content_dir: Path) -> Catalog:
    cur = yaml.safe_load((content_dir / "curriculum.yaml").read_text(encoding="utf-8"))
    comps = _req(cur, "competencies", "curriculum")
    total = sum(float(c["weight"]) for c in comps)
    if abs(total - 1.0) > 1e-9:
        raise ContentError(f"competency weights sum to {total}, expected 1.0")
    comp_codes = {c["code"] for c in comps}
    depts = _req(cur, "departments", "curriculum")
    dept_codes = {d["code"] for d in depts}
    tracks = []
    for t in _req(cur, "tracks", "curriculum"):
        reqs = parse_track_requirements(t["req"])
        tracks.append({"code": t["code"], "title": t["title"], "requirements": reqs})

    modules: list[Module] = []
    seen: set[str] = set()

    def unique(i: str) -> str:
        if i in seen:
            raise ContentError(f"duplicate id {i}")
        seen.add(i)
        return i

    for path in sorted((content_dir / "modules").glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        where = path.name
        mid = unique(_req(raw, "id", where))
        dept = _req(raw, "department", where)
        if dept not in dept_codes:
            raise ContentError(f"{where}: unknown department {dept}")
        mod = Module(mid, dept, raw["title"], int(raw.get("ordinal", 0)), str(raw.get("version", "1")))
        for i, l in enumerate(raw.get("lessons", [])):
            mod.lessons.append(
                Lesson(unique(_req(l, "id", where)), mid, l["title"], _req(l, "body", where), i)
            )
        for i, e in enumerate(raw.get("exercises", [])):
            w = f"{where}:{e.get('id')}"
            kind = _req(e, "kind", w)
            if kind not in KINDS:
                raise ContentError(f"{w}: unknown kind {kind}")
            comp = _req(e, "competency", w)
            if comp not in comp_codes:
                raise ContentError(f"{w}: unknown competency {comp}")
            level = _req(e, "level", w)
            if level not in LEVELS:
                raise ContentError(f"{w}: unknown level {level}")
            hints = _req(e, "hints", w)
            if not hints:
                raise ContentError(f"{w}: at least one hint required")
            spec = _req(e, "spec", w)
            _validate_spec(kind, spec, w)
            mod.exercises.append(
                Exercise(
                    id=unique(e["id"]), module_id=mid, kind=kind, title=e["title"],
                    prompt=_req(e, "prompt", w), competency=comp, level=level,
                    points=int(_req(e, "points", w)), practical=bool(e.get("practical", False)),
                    max_independent_hints=int(e.get("max_independent_hints", 1)),
                    ordinal=i, spec=spec, hints=list(hints), solution=_req(e, "solution", w),
                )
            )
        modules.append(mod)
    modules.sort(key=lambda m: (m.department, m.ordinal))
    return Catalog(comps, depts, tracks, modules)


def _validate_spec(kind: str, spec: dict, where: str) -> None:
    if kind == "numeric":
        _req(spec, "answer", where)
    elif kind == "choice":
        opts = _req(spec, "options", where)
        correct = _req(spec, "correct", where)
        if not correct or any(not (0 <= c < len(opts)) for c in correct):
            raise ContentError(f"{where}: bad correct indices")
    elif kind == "c_function":
        for k in ("prototypes", "starter", "harness", "reference"):
            _req(spec, k, where)


def sync_catalog(conn: sqlite3.Connection, cat: Catalog) -> None:
    """Replace content tables from the catalog. Learner evidence is untouched."""
    conn.execute("BEGIN")
    try:
        for t in ("track_requirement", "exercise", "lesson", "module"):
            conn.execute(f"DELETE FROM {t}")
        for c in cat.competencies:
            conn.execute(
                "INSERT INTO competency(code,title,weight) VALUES (?,?,?) "
                "ON CONFLICT(code) DO UPDATE SET title=excluded.title, weight=excluded.weight",
                (c["code"], c["title"], c["weight"]),
            )
        for d in cat.departments:
            conn.execute(
                "INSERT INTO department(code,title,roadmap_phase,description) VALUES (?,?,?,?) "
                "ON CONFLICT(code) DO UPDATE SET title=excluded.title, "
                "roadmap_phase=excluded.roadmap_phase, description=excluded.description",
                (d["code"], d["title"], d["phase"], d.get("description", "")),
            )
        for t in cat.tracks:
            conn.execute(
                "INSERT INTO career_track(code,title) VALUES (?,?) "
                "ON CONFLICT(code) DO UPDATE SET title=excluded.title",
                (t["code"], t["title"]),
            )
            for comp, lvl in t["requirements"].items():
                conn.execute(
                    "INSERT INTO track_requirement VALUES (?,?,?)", (t["code"], comp, lvl)
                )
        for m in cat.modules:
            conn.execute(
                "INSERT INTO module VALUES (?,?,?,?,?)",
                (m.id, m.department, m.title, m.ordinal, m.version),
            )
            for l in m.lessons:
                conn.execute(
                    "INSERT INTO lesson VALUES (?,?,?,?,?)", (l.id, m.id, l.title, l.body, l.ordinal)
                )
            for e in m.exercises:
                conn.execute(
                    "INSERT INTO exercise(id,module_id,kind,title,prompt_md,competency_code,level,"
                    "points,practical,max_independent_hints,ordinal,spec_json,hints_json,solution_md,"
                    "content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        e.id, m.id, e.kind, e.title, e.prompt, e.competency, e.level, e.points,
                        int(e.practical), e.max_independent_hints, e.ordinal,
                        json.dumps(e.spec), json.dumps(e.hints), e.solution, e.content_hash,
                    ),
                )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
