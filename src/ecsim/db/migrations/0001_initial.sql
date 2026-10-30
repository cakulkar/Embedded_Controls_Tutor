-- 0001_initial: Phase 1 learning engine schema.
-- Content tables are rebuilt from content packs; learner tables hold evidence.

CREATE TABLE competency (
    code        TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    weight      REAL NOT NULL CHECK (weight > 0 AND weight <= 1)
);

CREATE TABLE department (
    code          TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    roadmap_phase INTEGER NOT NULL,
    description   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE module (
    id              TEXT PRIMARY KEY,
    department_code TEXT NOT NULL REFERENCES department(code),
    title           TEXT NOT NULL,
    ordinal         INTEGER NOT NULL,
    version         TEXT NOT NULL DEFAULT '1'
);

CREATE TABLE lesson (
    id        TEXT PRIMARY KEY,
    module_id TEXT NOT NULL REFERENCES module(id),
    title     TEXT NOT NULL,
    body_md   TEXT NOT NULL,
    ordinal   INTEGER NOT NULL
);

CREATE TABLE exercise (
    id               TEXT PRIMARY KEY,
    module_id        TEXT NOT NULL REFERENCES module(id),
    kind             TEXT NOT NULL CHECK (kind IN ('numeric','choice','c_function')),
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

CREATE TABLE career_track (
    code        TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE track_requirement (
    track_code      TEXT NOT NULL REFERENCES career_track(code),
    competency_code TEXT NOT NULL REFERENCES competency(code),
    min_level       TEXT NOT NULL CHECK (min_level IN ('beginner','intermediate','advanced','expert')),
    PRIMARY KEY (track_code, competency_code)
);

-- Learner evidence ------------------------------------------------------------
CREATE TABLE learner (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    track_code  TEXT REFERENCES career_track(code),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE lesson_progress (
    learner_id   INTEGER NOT NULL REFERENCES learner(id),
    lesson_id    TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (learner_id, lesson_id)
);

-- exercise_id intentionally has no FK: evidence survives content-pack changes.
CREATE TABLE attempt (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id    INTEGER NOT NULL REFERENCES learner(id),
    exercise_id   TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    submitted_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    submission    TEXT NOT NULL,
    passed        INTEGER NOT NULL,
    score         REAL NOT NULL,
    feedback      TEXT NOT NULL,
    hints_used    INTEGER NOT NULL,
    solution_seen INTEGER NOT NULL,
    ai_assisted   INTEGER NOT NULL DEFAULT 0,
    independent   INTEGER NOT NULL
);
CREATE INDEX idx_attempt_learner_ex ON attempt(learner_id, exercise_id);

CREATE TABLE hint_reveal (
    learner_id  INTEGER NOT NULL REFERENCES learner(id),
    exercise_id TEXT NOT NULL,
    level       INTEGER NOT NULL,
    revealed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (learner_id, exercise_id, level)
);

CREATE TABLE solution_reveal (
    learner_id       INTEGER NOT NULL REFERENCES learner(id),
    exercise_id      TEXT NOT NULL,
    explicit_request INTEGER NOT NULL,
    revealed_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (learner_id, exercise_id)
);
