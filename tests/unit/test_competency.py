from conftest import needs_cc


def _ans(ctx, ex_id):
    e = ctx.catalog.exercise(ex_id)
    if e.kind == "numeric":
        return str(e.spec["answer"])
    if e.kind == "choice":
        return ",".join(map(str, e.spec["correct"]))
    return e.spec["reference"]


def _status(prof, code):
    return next(c for c in prof.competencies if c.code == code)


def test_empty_profile(ctx, learner):
    p = ctx.competency.profile(learner)
    assert p.weighted_score == 0 and all(c.achieved_level is None for c in p.competencies)
    assert p.track_code == "JR_EMB_CTL" and not p.track_ready


def test_beginner_level_needs_80_percent(ctx, learner):
    # EMB beginner pool: A01-E1, A01-E2, A02-E1 (10 pts each).
    for ex in ("A01-E1", "A01-E2"):
        ctx.learning.submit(learner, ex, _ans(ctx, ex))
    emb = _status(ctx.competency.profile(learner), "EMB")
    assert emb.levels[0].points_earned == 20 and not emb.levels[0].achieved  # 67 %
    ctx.learning.submit(learner, "A02-E1", _ans(ctx, "A02-E1"))
    p = ctx.competency.profile(learner)
    assert _status(p, "EMB").achieved_level == "beginner"
    assert p.weighted_score == 15 * 1 / 4


def test_assisted_pass_gives_no_credit(ctx, learner):
    for ex in ("A01-E1", "A01-E2", "A02-E1"):
        ctx.learning.submit(learner, ex, _ans(ctx, ex), ai_assisted=True)
    assert _status(ctx.competency.profile(learner), "EMB").achieved_level is None


def test_level_without_content_cannot_be_achieved(ctx, learner):
    ctl = _status(ctx.competency.profile(learner), "MBD")
    assert all(ls.points_available == 0 and not ls.achieved for ls in ctl.levels)


@needs_cc
def test_intermediate_requires_beginner_first(ctx, learner):
    for ex in ("A01-E3", "A02-E4"):
        ctx.learning.submit(learner, ex, _ans(ctx, ex))
    emb = _status(ctx.competency.profile(learner), "EMB")
    assert emb.levels[1].fraction == 1.0 and not emb.levels[1].achieved
    for ex in ("A01-E1", "A01-E2", "A02-E1"):
        ctx.learning.submit(learner, ex, _ans(ctx, ex))
    assert _status(ctx.competency.profile(learner), "EMB").achieved_level == "intermediate"


@needs_cc
def test_track_gaps(ctx, learner):
    for e in ctx.catalog.all_exercises():
        ctx.learning.submit(learner, e.id, _ans(ctx, e.id))
    p = ctx.competency.profile(learner)
    gaps = {g.competency: g for g in p.track_gaps}
    assert gaps["EMB"].met                          # intermediate content complete
    assert not gaps["MBD"].met                      # no MBD content until Phase 3/4
    assert not p.track_ready
