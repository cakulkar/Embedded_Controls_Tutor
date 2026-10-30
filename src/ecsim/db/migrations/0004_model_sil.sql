-- 0004_model_sil: allow the 'model_sil' exercise kind (content-only table, rebuilt from packs).

DROP TABLE exercise;

CREATE TABLE exercise (
    id               TEXT PRIMARY KEY,
    module_id        TEXT NOT NULL REFERENCES module(id),
    kind             TEXT NOT NULL CHECK (kind IN ('numeric','choice','c_function','ecu_app','model_mil','model_sil')),
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
