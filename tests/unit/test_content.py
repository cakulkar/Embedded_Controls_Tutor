import shutil

import pytest

from ecsim.app.config import CONTENT_DIR
from ecsim.learning.content import LEVELS, ContentError, load_catalog, parse_track_requirements


def test_catalog_valid():
    cat = load_catalog(CONTENT_DIR)
    assert abs(sum(c["weight"] for c in cat.competencies) - 1.0) < 1e-9
    assert len(cat.competencies) == 9
    assert len(cat.tracks) == 11
    assert {d["code"] for d in cat.departments} >= set("ABCDEFGHIJKLM")
    ids = [e.id for e in cat.all_exercises()]
    assert len(ids) == len(set(ids)) and len(ids) >= 10
    for e in cat.all_exercises():
        assert e.level in LEVELS and e.hints and e.solution.strip()


def test_public_spec_hides_answers():
    cat = load_catalog(CONTENT_DIR)
    for e in cat.all_exercises():
        pub = e.public_spec()
        assert not {"answer", "correct", "reference", "mutants", "harness"} & set(pub)


def test_content_hash_changes_with_spec():
    cat = load_catalog(CONTENT_DIR)
    e = cat.exercise("A02-E1")
    h = e.content_hash
    e.spec["answer"] = 1
    assert e.content_hash != h


def test_track_parse():
    r = parse_track_requirements("i b - b b b b b e")
    assert r["EMB"] == "intermediate" and "MBD" not in r and r["DOC"] == "expert"
    with pytest.raises(ContentError):
        parse_track_requirements("i b")


def _copy(tmp_path):
    d = tmp_path / "content"
    shutil.copytree(CONTENT_DIR, d)
    return d


def test_bad_weights_rejected(tmp_path):
    d = _copy(tmp_path)
    p = d / "curriculum.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("weight: 0.05", "weight: 0.06"), encoding="utf-8")
    with pytest.raises(ContentError, match="weights"):
        load_catalog(d)


def test_duplicate_id_rejected(tmp_path):
    d = _copy(tmp_path)
    src = d / "modules" / "A02_fixed_point_adc.yaml"
    (d / "modules" / "Z99_dup.yaml").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ContentError, match="duplicate"):
        load_catalog(d)


def test_unknown_competency_rejected(tmp_path):
    d = _copy(tmp_path)
    p = d / "modules" / "A02_fixed_point_adc.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("competency: VEH", "competency: XYZ", 1), encoding="utf-8")
    with pytest.raises(ContentError, match="unknown competency"):
        load_catalog(d)
