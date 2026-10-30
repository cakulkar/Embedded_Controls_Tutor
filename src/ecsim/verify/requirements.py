"""Requirements store, ReqIF interchange and traceability matrix.

Requirements live per learner in SQLite (migration 0005). Trace links connect a requirement to
what derives from, implements or verifies it. Verification evidence is computed from test results:
any check whose name starts with the requirement ID ("REQ-M3 …") verifies that requirement.

ReqIF: a minimal, standards-shaped ReqIF 1.0 document (one SPEC-OBJECT-TYPE with string attributes
ReqIF.ForeignID, ReqIF.Name, ReqIF.Text, plus ECSim attributes for level, acceptance, parent and status).
Import accepts documents with those attribute names (as DOORS/Polarion exports typically map them),
and reports what it could not map.
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

LEVELS = ("customer", "system", "software", "test")
STATUSES = ("draft", "reviewed", "approved", "obsolete")
_RID = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,40}$")
NS = "http://www.omg.org/spec/ReqIF/20110401/reqif.xsd"


class RequirementError(ValueError):
    pass


@dataclass
class Requirement:
    req_id: str
    level: str
    title: str
    text: str = ""
    acceptance: str = ""
    parent_req_id: str | None = None
    status: str = "draft"


class RequirementStore:
    def __init__(self, conn: sqlite3.Connection, learner_id: int) -> None:
        self.conn, self.learner = conn, learner_id

    def _validate(self, r: Requirement) -> None:
        if not _RID.match(r.req_id):
            raise RequirementError(f"requirement ID {r.req_id!r}: letters, digits, . _ - (start with a letter)")
        if r.level not in LEVELS:
            raise RequirementError(f"level must be one of {LEVELS}")
        if r.status not in STATUSES:
            raise RequirementError(f"status must be one of {STATUSES}")
        if not r.title.strip():
            raise RequirementError("title is required")
        if r.parent_req_id:
            if r.parent_req_id == r.req_id:
                raise RequirementError("a requirement cannot be its own parent")
            if self.get(r.parent_req_id) is None:
                raise RequirementError(f"parent {r.parent_req_id} does not exist")
            cur, seen = r.parent_req_id, {r.req_id}
            while cur:
                if cur in seen:
                    raise RequirementError("parent chain would form a cycle")
                seen.add(cur)
                p = self.get(cur)
                cur = p.parent_req_id if p else None

    def upsert(self, r: Requirement) -> None:
        self._validate(r)
        self.conn.execute(
            "INSERT INTO requirement(learner_id, req_id, level, title, text, acceptance, parent_req_id, status) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(learner_id, req_id) DO UPDATE SET level=excluded.level, "
            "title=excluded.title, text=excluded.text, acceptance=excluded.acceptance, "
            "parent_req_id=excluded.parent_req_id, status=excluded.status, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
            (self.learner, r.req_id, r.level, r.title.strip(), r.text, r.acceptance, r.parent_req_id or None, r.status))

    def delete(self, req_id: str) -> None:
        if self.conn.execute("SELECT 1 FROM requirement WHERE learner_id=? AND parent_req_id=?", (self.learner, req_id)).fetchone():
            raise RequirementError(f"{req_id} has child requirements; re-parent or delete them first")
        self.conn.execute("DELETE FROM trace_link WHERE learner_id=? AND req_id=?", (self.learner, req_id))
        self.conn.execute("DELETE FROM requirement WHERE learner_id=? AND req_id=?", (self.learner, req_id))

    def get(self, req_id: str) -> Requirement | None:
        r = self.conn.execute("SELECT * FROM requirement WHERE learner_id=? AND req_id=?", (self.learner, req_id)).fetchone()
        return self._row(r) if r else None

    @staticmethod
    def _row(r) -> Requirement:
        return Requirement(r["req_id"], r["level"], r["title"], r["text"], r["acceptance"], r["parent_req_id"], r["status"])

    def all(self) -> list[Requirement]:
        rows = self.conn.execute("SELECT * FROM requirement WHERE learner_id=? ORDER BY req_id", (self.learner,))
        return [self._row(r) for r in rows]

    def link(self, req_id: str, kind: str, target: str) -> None:
        if self.get(req_id) is None:
            raise RequirementError(f"unknown requirement {req_id}")
        if kind not in ("derives", "implements", "verifies"):
            raise RequirementError("link kind must be derives, implements or verifies")
        self.conn.execute("INSERT OR IGNORE INTO trace_link(learner_id, req_id, kind, target) VALUES (?,?,?,?)",
                          (self.learner, req_id, kind, target))

    def links(self, req_id: str | None = None) -> list[tuple[str, str, str]]:
        q, a = "SELECT req_id, kind, target FROM trace_link WHERE learner_id=?", [self.learner]
        if req_id:
            q += " AND req_id=?"
            a.append(req_id)
        return [(r[0], r[1], r[2]) for r in self.conn.execute(q + " ORDER BY req_id, kind, target", a)]

    def import_exercise(self, ex_id: str, spec: dict, title_prefix: str = "") -> int:
        """Create requirements from an exercise's check names ("REQ-x description")."""
        n = 0
        for t in spec.get("tests", []):
            for c in t.get("checks", []):
                m = re.match(r"^(REQ-[A-Za-z0-9.\-]+)\s+(.*)$", c.get("name", ""))
                if m and self.get(m.group(1)) is None:
                    self.upsert(Requirement(m.group(1), "software", (title_prefix + m.group(2)).strip()[:120]))
                    n += 1
                if m:
                    self.link(m.group(1), "verifies", f"{ex_id}:{t['name']}")
        return n


# ---- ReqIF -----------------------------------------------------------------------------
_ATTRS = ["ReqIF.ForeignID", "ReqIF.Name", "ReqIF.Text", "ECSIM.Level", "ECSIM.Acceptance", "ECSIM.Parent", "ECSIM.Status"]


def to_reqif(reqs: list[Requirement], title: str = "ECSim requirements") -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dt = "DT-STRING"
    attr_defs = "".join(f'<ATTRIBUTE-DEFINITION-STRING IDENTIFIER="AD-{i}" LONG-NAME={quoteattr(a)} LAST-CHANGE="{now}">'
                        f'<TYPE><DATATYPE-DEFINITION-STRING-REF>{dt}</DATATYPE-DEFINITION-STRING-REF></TYPE>'
                        f'</ATTRIBUTE-DEFINITION-STRING>' for i, a in enumerate(_ATTRS))
    objs, children = [], []
    for r in reqs:
        vals = [r.req_id, r.title, r.text, r.level, r.acceptance, r.parent_req_id or "", r.status]
        av = "".join(f'<ATTRIBUTE-VALUE-STRING THE-VALUE={quoteattr(v)}><DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>AD-{i}'
                     f'</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION></ATTRIBUTE-VALUE-STRING>' for i, v in enumerate(vals))
        objs.append(f'<SPEC-OBJECT IDENTIFIER="SO-{escape(r.req_id)}" LAST-CHANGE="{now}"><TYPE><SPEC-OBJECT-TYPE-REF>SOT-REQ'
                    f'</SPEC-OBJECT-TYPE-REF></TYPE><VALUES>{av}</VALUES></SPEC-OBJECT>')
        children.append(f'<SPEC-HIERARCHY IDENTIFIER="SH-{escape(r.req_id)}" LAST-CHANGE="{now}"><OBJECT><SPEC-OBJECT-REF>'
                        f'SO-{escape(r.req_id)}</SPEC-OBJECT-REF></OBJECT></SPEC-HIERARCHY>')
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<REQ-IF xmlns="{NS}"><THE-HEADER><REQ-IF-HEADER IDENTIFIER="H-{uuid.uuid4()}">'
            f'<CREATION-TIME>{now}</CREATION-TIME><REQ-IF-TOOL-ID>ECSim</REQ-IF-TOOL-ID><REQ-IF-VERSION>1.0</REQ-IF-VERSION>'
            f'<SOURCE-TOOL-ID>ECSim</SOURCE-TOOL-ID><TITLE>{escape(title)}</TITLE></REQ-IF-HEADER></THE-HEADER>'
            f'<CORE-CONTENT><REQ-IF-CONTENT><DATATYPES><DATATYPE-DEFINITION-STRING IDENTIFIER="{dt}" LAST-CHANGE="{now}" '
            f'MAX-LENGTH="10000"/></DATATYPES><SPEC-TYPES><SPEC-OBJECT-TYPE IDENTIFIER="SOT-REQ" LONG-NAME="Requirement" '
            f'LAST-CHANGE="{now}"><SPEC-ATTRIBUTES>{attr_defs}</SPEC-ATTRIBUTES></SPEC-OBJECT-TYPE>'
            f'<SPECIFICATION-TYPE IDENTIFIER="ST-1" LAST-CHANGE="{now}"/></SPEC-TYPES>'
            f'<SPEC-OBJECTS>{"".join(objs)}</SPEC-OBJECTS><SPECIFICATIONS><SPECIFICATION IDENTIFIER="S-1" '
            f'LONG-NAME={quoteattr(title)} LAST-CHANGE="{now}"><TYPE><SPECIFICATION-TYPE-REF>ST-1</SPECIFICATION-TYPE-REF></TYPE>'
            f'<CHILDREN>{"".join(children)}</CHILDREN></SPECIFICATION></SPECIFICATIONS></REQ-IF-CONTENT></CORE-CONTENT></REQ-IF>\n')


def from_reqif(text: str) -> tuple[list[Requirement], list[str]]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise RequirementError(f"invalid ReqIF XML: {exc}") from None
    q = lambda tag: f"{{{NS}}}{tag}"                                    # noqa: E731
    if root.tag != q("REQ-IF"):
        raise RequirementError("not a ReqIF document (root must be REQ-IF in the ReqIF 1.0 namespace)")
    names = {}
    for ad in root.iter():
        if ad.tag.startswith(q("ATTRIBUTE-DEFINITION")) and ad.get("IDENTIFIER"):
            names[ad.get("IDENTIFIER")] = ad.get("LONG-NAME", "")
    reqs, warnings = [], []
    for so in root.iter(q("SPEC-OBJECT")):
        vals = {}
        for av in so.iter():
            if av.tag.startswith(q("ATTRIBUTE-VALUE")):
                ref = next((e.text for e in av.iter() if e.tag.endswith("-REF")), None)
                val = av.get("THE-VALUE")
                if val is None:                                     # XHTML values
                    val = "".join(av.itertext()).strip()
                vals[names.get(ref, ref)] = val or ""
        rid = vals.get("ReqIF.ForeignID") or so.get("IDENTIFIER", "")
        rid = re.sub(r"[^A-Za-z0-9_.\-]", "_", rid)
        if not rid or not rid[0].isalpha():
            rid = "R_" + rid
        level = vals.get("ECSIM.Level", "system")
        if level not in LEVELS:
            warnings.append(f"{rid}: level '{level}' unknown, imported as 'system'")
            level = "system"
        status = vals.get("ECSIM.Status", "draft")
        if status not in STATUSES:
            warnings.append(f"{rid}: status '{status}' unknown, imported as 'draft'")
            status = "draft"
        title = vals.get("ReqIF.Name") or (vals.get("ReqIF.Text", "")[:80]) or rid
        reqs.append(Requirement(rid, level, title, vals.get("ReqIF.Text", ""), vals.get("ECSIM.Acceptance", ""),
                                vals.get("ECSIM.Parent") or None, status))
        unknown = [k for k in vals if k not in _ATTRS]
        if unknown:
            warnings.append(f"{rid}: attributes not imported: {', '.join(sorted(unknown))}")
    return reqs, warnings


def import_into(store: RequirementStore, reqs: list[Requirement]) -> list[str]:
    """Insert parents before children; report requirements that could not be imported."""
    problems, pending = [], list(reqs)
    while pending:
        progress = False
        for r in list(pending):
            if r.parent_req_id and store.get(r.parent_req_id) is None and any(p.req_id == r.parent_req_id for p in pending):
                continue
            try:
                store.upsert(r)
            except RequirementError as exc:
                problems.append(f"{r.req_id}: {exc}")
            pending.remove(r)
            progress = True
        if not progress:
            problems += [f"{r.req_id}: parent cycle" for r in pending]
            break
    return problems


# ---- traceability --------------------------------------------------------------------
@dataclass
class TraceRow:
    req_id: str
    title: str
    checks: list[tuple[str, str, bool]]       # (test, check name, passed)

    @property
    def status(self) -> str:
        if not self.checks:
            return "not verified"
        return "passed" if all(p for _t, _c, p in self.checks) else "failed"


def trace_matrix(reqs: list[Requirement], outcomes) -> list[TraceRow]:
    """Map MIL check outcomes (CheckOutcome list) onto requirements by ID prefix in the check name."""
    rows = []
    for r in reqs:
        hits = [(o.test, o.name, o.passed) for o in outcomes if re.match(rf"^{re.escape(r.req_id)}\b", o.name)]
        rows.append(TraceRow(r.req_id, r.title, hits))
    return rows
