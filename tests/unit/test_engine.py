import pytest

from ecsim.app import events
from ecsim.learning.engine import PolicyError
from ecsim.learning.graders import CFunctionGrader

from conftest import needs_cc


def test_create_learner_requires_name(ctx):
    with pytest.raises(ValueError):
        ctx.learning.create_learner("  ")


def test_hint_ladder_requires_attempts(ctx, learner):
    L = ctx.learning
    lvl, _ = L.reveal_hint(learner, "A02-E2")          # hint 1 free
    assert lvl == 1
    with pytest.raises(PolicyError, match="after 1 attempt"):
        L.reveal_hint(learner, "A02-E2")               # hint 2 needs an attempt
    L.submit(learner, "A02-E2", "100")
    assert L.reveal_hint(learner, "A02-E2")[0] == 2
    with pytest.raises(PolicyError, match="All hints"):
        L.reveal_hint(learner, "A02-E2")


def test_solution_gate(ctx, learner):
    L = ctx.learning
    with pytest.raises(PolicyError):
        L.reveal_solution(learner, "A01-E2")
    assert "0x0FD8" in L.reveal_solution(learner, "A01-E2", force=True)
    row = ctx.conn.execute("SELECT explicit_request FROM solution_reveal").fetchone()
    assert row[0] == 1
    # Passing after seeing the solution is recorded but not independent.
    rec = L.submit(learner, "A01-E2", "0x0FD8")
    assert rec.passed and not rec.independent and rec.solution_seen


def test_solution_after_attempt_not_explicit(ctx, learner):
    L = ctx.learning
    L.submit(learner, "A01-E2", "1")
    L.reveal_solution(learner, "A01-E2")
    assert ctx.conn.execute("SELECT explicit_request FROM solution_reveal").fetchone()[0] == 0


def test_independence_rules(ctx, learner):
    L = ctx.learning
    assert L.submit(learner, "A02-E1", "-12288").independent
    L.reveal_hint(learner, "A01-E2")                        # 1 hint allowed (default)
    assert L.submit(learner, "A01-E2", "4056").independent
    assert not L.submit(learner, "B01-E1", "0.951229", ai_assisted=True).independent
    L.reveal_hint(learner, "A02-E2"); L.submit(learner, "A02-E2", "1"); L.reveal_hint(learner, "A02-E2")
    rec = L.submit(learner, "A02-E2", "156.28")
    assert rec.passed and not rec.independent and rec.hints_used == 2
    st = L.state(learner, "A02-E2")
    assert st.passed and not st.passed_independently


def test_events_published(ctx, learner):
    got = []
    ctx.bus.subscribe(events.ATTEMPT_GRADED, lambda **kw: got.append(kw["attempt"].exercise_id))
    ctx.learning.submit(learner, "A02-E1", "0")
    assert got == ["A02-E1"]


def test_lessons(ctx, learner):
    ctx.learning.complete_lesson(learner, "A01-L1")
    ctx.learning.complete_lesson(learner, "A01-L1")
    assert ctx.learning.completed_lessons(learner) == {"A01-L1"}
    with pytest.raises(KeyError):
        ctx.learning.complete_lesson(learner, "NOPE")


def test_missing_compiler_not_recorded(ctx, learner):
    ctx.learning.graders["c_function"] = CFunctionGrader(compiler="no-such-cc")
    rec = ctx.learning.submit(learner, "A01-E3", "x")
    assert rec.id == -1 and ctx.learning.state(learner, "A01-E3").attempts == 0


@needs_cc
def test_c_submission_end_to_end(ctx, learner):
    ex = ctx.catalog.exercise("B01-E4")
    rec = ctx.learning.submit(learner, ex.id, ex.spec["reference"])
    assert rec.passed and rec.independent and "13/13" in rec.feedback
    assert ctx.learning.last_submission(learner, ex.id) == ex.spec["reference"]


def test_evidence_export(ctx, learner):
    ctx.learning.submit(learner, "A02-E1", "-12288")
    assert '"exercise_id": "A02-E1"' in ctx.learning.export_evidence(learner)
