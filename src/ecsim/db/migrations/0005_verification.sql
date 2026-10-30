-- 0005_verification: 'test_design' exercise kind; requirements and trace links (content-only table, rebuilt from packs).

DROP TABLE exercise;

CREATE TABLE exercise (
    id               TEXT PRIMARY KEY,
    module_id        TEXT NOT NULL REFERENCES module(id),
    kind             TEXT NOT NULL CHECK (kind IN ('numeric','choice','c_function','ecu_app','model_mil','model_sil','test_design')),
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

CREATE TABLE requirement (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id    INTEGER NOT NULL REFERENCES learner(id),
    req_id        TEXT NOT NULL,
    level         TEXT NOT NULL CHECK (level IN ('customer','system','software','test')),
    title         TEXT NOT NULL,
    text          TEXT NOT NULL DEFAULT '',
    acceptance    TEXT NOT NULL DEFAULT '',
    parent_req_id TEXT,
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','reviewed','approved','obsolete')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (learner_id, req_id)
);

CREATE TABLE trace_link (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id  INTEGER NOT NULL REFERENCES learner(id),
    req_id      TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('derives','implements','verifies')),
    target      TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (learner_id, req_id, kind, target)
);
