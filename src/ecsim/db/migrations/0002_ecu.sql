-- 0002_ecu: allow the 'ecu_app' exercise kind and record virtual-ECU lab runs.
-- SQLite cannot alter a CHECK constraint, so the content-only exercise table is
-- recreated (it is rebuilt from content packs on every start; no evidence lives here).

DROP TABLE exercise;

CREATE TABLE exercise (
    id               TEXT PRIMARY KEY,
    module_id        TEXT NOT NULL REFERENCES module(id),
    kind             TEXT NOT NULL CHECK (kind IN ('numeric','choice','c_function','ecu_app')),
    title            TEXT NOT NULL,
    prompt_md        TEXT NOT NULL,
    competency_code  TEXT NOT NULL REFERENCES competency(code),
    level            TEXT NOT NULL CHECK (level IN ('beginner','intermediate','advanced','expert')),
    points           INTEGER NOT NULL CHECK (points > 0),
    practical        INTEGER NOT NULL DEFAULT 0,
    max_independent_hints INTEGER NOT NULL DEFAULT 1,
    ordinal          INTEGER NOT NULL,
    spec_json        TEXT NOT NULL,
    hints_json       TEXT NOT NULL,
    solution_md      TEXT NOT NULL,
    content_hash     TEXT NOT NULL
);

CREATE TABLE ecu_run (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id   INTEGER NOT NULL REFERENCES learner(id),
    started_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    profile      TEXT NOT NULL,
    scenario     TEXT NOT NULL,
    source_sha   TEXT NOT NULL,
    seed         INTEGER NOT NULL,
    passed       INTEGER NOT NULL,
    cpu_load     REAL NOT NULL,
    resets       INTEGER NOT NULL,
    summary_json TEXT NOT NULL
);
CREATE INDEX idx_ecu_run_learner ON ecu_run(learner_id, started_at);
