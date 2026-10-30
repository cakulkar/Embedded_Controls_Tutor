"""Headless interface. Every GUI action has a CLI equivalent (scriptable, testable)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ecsim import __version__
from ecsim.app import toolchain
from ecsim.app.services import AppContext, bootstrap
from ecsim.db import backup_to, restore_from
from ecsim.learning.engine import PolicyError


def _learner(ctx: AppContext, lid: int | None) -> int:
    rows = ctx.learning.learners()
    if not rows:
        raise SystemExit("No learner yet. Run: ecsim init --name 'Your Name'")
    if lid is None:
        return rows[0]["id"]
    if lid not in {r["id"] for r in rows}:
        raise SystemExit(f"Unknown learner id {lid}")
    return lid


def cmd_init(ctx: AppContext, a) -> int:
    if a.track and a.track not in {t["code"] for t in ctx.catalog.tracks}:
        print("Unknown track. Available:")
        for t in ctx.catalog.tracks:
            print(f"  {t['code']:12s} {t['title']}")
        return 2
    lid = ctx.learning.create_learner(a.name, a.track)
    print(f"Created learner #{lid} '{a.name}' in {ctx.settings.home}")
    return 0


def cmd_tracks(ctx: AppContext, a) -> int:
    for t in ctx.catalog.tracks:
        req = " ".join(f"{k}:{v[0]}" for k, v in t["requirements"].items())
        print(f"{t['code']:12s} {t['title']:44s} {req}")
    return 0


def cmd_list(ctx: AppContext, a) -> int:
    lid = _learner(ctx, a.learner)
    done = ctx.learning.completed_lessons(lid)
    for m in ctx.catalog.modules:
        print(f"\n[{m.department}] {m.id}  {m.title}")
        for l in m.lessons:
            print(f"   {'✓' if l.id in done else ' '} {l.id:8s} lesson    {l.title}")
        for e in m.exercises:
            st = ctx.learning.state(lid, e.id)
            mark = "★" if st.passed_independently else ("✓" if st.passed else " ")
            print(f"   {mark} {e.id:8s} {e.kind:10s} {e.competency}/{e.level:12s} {e.points:3d}pt  {e.title}")
    print("\n★ independent pass   ✓ passed (assisted)")
    return 0


def cmd_show(ctx: AppContext, a) -> int:
    lid = _learner(ctx, a.learner)
    try:
        l = ctx.catalog.lesson(a.id)
        print(f"# {l.title}\n\n{l.body}")
        ctx.learning.complete_lesson(lid, l.id)
        return 0
    except KeyError:
        pass
    e = ctx.catalog.exercise(a.id)
    print(f"{e.id} - {e.title}  [{e.competency}/{e.level}, {e.points} pts{', practical' if e.practical else ''}]\n")
    print(e.prompt)
    if e.kind == "choice":
        for i, o in enumerate(e.spec["options"]):
            print(f"  [{i}] {o}")
    if e.kind in ("c_function", "ecu_app"):
        print("--- starter code (save to a file and submit with --file) ---")
        print(e.spec["starter"])
    st = ctx.learning.state(lid, e.id)
    for i, h in enumerate(ctx.learning.revealed_hints(lid, e.id), 1):
        print(f"Hint {i}: {h}")
    print(f"\nattempts={st.attempts} hints={st.hints_revealed}/{st.hints_total} passed={st.passed}")
    return 0


def cmd_submit(ctx: AppContext, a) -> int:
    lid = _learner(ctx, a.learner)
    if a.file:
        text = Path(a.file).read_text(encoding="utf-8")
    elif a.answer is not None:
        text = a.answer
    else:
        text = sys.stdin.read()
    rec = ctx.learning.submit(lid, a.id, text, ai_assisted=a.ai_assisted)
    print("PASS" if rec.passed else "FAIL", f"score={rec.score:.2f}",
          "(independent)" if rec.independent else "(not independent evidence)" if rec.id > 0 else "")
    print(rec.feedback)
    return 0 if rec.passed else 1


def cmd_hint(ctx: AppContext, a) -> int:
    lid = _learner(ctx, a.learner)
    try:
        level, text = ctx.learning.reveal_hint(lid, a.id)
    except PolicyError as exc:
        print(exc)
        return 3
    print(f"Hint {level}: {text}")
    return 0


def cmd_solution(ctx: AppContext, a) -> int:
    lid = _learner(ctx, a.learner)
    try:
        print(ctx.learning.reveal_solution(lid, a.id, force=a.force))
    except PolicyError as exc:
        print(exc)
        return 3
    return 0


def cmd_status(ctx: AppContext, a) -> int:
    lid = _learner(ctx, a.learner)
    p = ctx.competency.profile(lid)
    print(f"Weighted competency score: {p.weighted_score:.1f}/100 (progress indicator, not a certification)\n")
    for cs in p.competencies:
        bars = " ".join(f"{ls.level[0]}:{ls.points_earned}/{ls.points_available}{'✓' if ls.achieved else ''}"
                        for ls in cs.levels)
        print(f"{cs.code} {cs.title:40s} {int(cs.weight*100):3d}%  level={cs.achieved_level or '-':12s} {bars}")
    if p.track_code:
        print(f"\nTrack {p.track_code}: {'requirements met' if p.track_ready else 'gaps remain'}")
        for g in p.track_gaps:
            print(f"   {'✓' if g.met else '✗'} {g.competency} need {g.required:12s} have {g.achieved or '-'}")
    return 0


def cmd_doctor(ctx: AppContext, a) -> int:
    print(f"ECSim {__version__}  home={ctx.settings.home}")
    for t in toolchain.probe().values():
        state = "OK     " if t.available else "missing"
        print(f"  {state} {t.name:9s} {t.purpose:36s} {t.version or ''}")
    return 0


def cmd_backup(ctx: AppContext, a) -> int:
    print("Backup written to", backup_to(ctx.conn, a.dest))
    return 0


def cmd_restore(ctx: AppContext, a) -> int:
    ctx.close()
    restore_from(a.src, ctx.settings.db_path)
    print("Restored from", a.src)
    return 0


def cmd_ecu_profiles(ctx: AppContext, a) -> int:
    from ecsim.ecu.profiles import PROFILES
    from ecsim.ecu.scenarios import SCENARIOS
    for p in PROFILES.values():
        wd = p.watchdog
        win = f", window ≥{wd.window_min_ms} ms" if wd.window_min_ms else ""
        print(f"{p.code:12s} {p.title}\n             {p.cpu_mhz} MHz, {p.cores} core(s) ({p.cores_simulated} simulated), "
              f"{wd.name} timeout {wd.timeout_ms} ms{win}")
        for t in p.tasks:
            print(f"             {t.name:11s} every {t.period_ms:3d} ms, modelled WCET {t.wcet_us} µs")
        for item in p.planned:
            print(f"             planned: {item}")
    print("\nScenarios:")
    for sc in SCENARIOS.values():
        print(f"  {sc.name:26s} {sc.description}")
    return 0


def cmd_ecu_run(ctx: AppContext, a) -> int:
    rows = ctx.learning.learners()
    lid = _learner(ctx, a.learner) if rows else None
    source = Path(a.file).read_text(encoding="utf-8")
    run = ctx.ecu_lab.run(source, a.profile, a.scenario, a.seed, lid)
    if not run.build.ok:
        print(run.build.log)
        return 2
    out = run.outcome
    if not out.ok:
        print(out.error)
        return 2
    r = out.result
    print(f"{'PASS' if r.passed else 'FAIL'}  {r.scenario} on {r.profile}  CPU load {r.cpu_load:.1%}  "
          f"watchdog services {r.wdg_services}  deadline misses {r.deadline_misses}")
    for c in r.checks:
        print(f"  {'ok ' if c.passed else 'FAIL'} {c.name}: {c.detail}")
    for x in r.resets:
        print(f"  reset at {x['time_ms']} ms: {x['reason']} - {x['detail']}")
    for d in r.dem_log:
        print(f"  DEM event {d['event_id']} -> {'FAILED' if d['status'] else 'PASSED'} at {d['time_ms']} ms")
    res = run.build.resources
    if res.available:
        print(f"  host-compiled size (indicative): code {res.text} B, static RAM {res.static_ram} B")
    if a.csv:
        Path(a.csv).write_text(r.trace.to_csv(), encoding="utf-8")
        print(f"  trace written to {a.csv}")
    return 0 if r.passed else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ecsim", description="Embedded Controls Engineer Job Simulator (offline)")
    p.add_argument("--home", help="data directory (default $ECSIM_HOME or ~/.ecsim)")
    p.add_argument("--learner", type=int, help="learner id (default: first)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init", help="create a learner"); s.add_argument("--name", required=True); s.add_argument("--track")
    s.set_defaults(fn=cmd_init)
    sub.add_parser("tracks", help="list career tracks").set_defaults(fn=cmd_tracks)
    sub.add_parser("list", help="list modules, lessons, exercises").set_defaults(fn=cmd_list)
    s = sub.add_parser("show", help="show a lesson or exercise"); s.add_argument("id"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("submit", help="submit an answer")
    s.add_argument("id"); s.add_argument("--answer"); s.add_argument("--file")
    s.add_argument("--ai-assisted", action="store_true", help="declare AI help (attempt is then not independent)")
    s.set_defaults(fn=cmd_submit)
    s = sub.add_parser("hint", help="reveal next hint"); s.add_argument("id"); s.set_defaults(fn=cmd_hint)
    s = sub.add_parser("solution", help="reveal solution (after an attempt)"); s.add_argument("id")
    s.add_argument("--force", action="store_true", help="explicit request before attempting"); s.set_defaults(fn=cmd_solution)
    sub.add_parser("status", help="competency profile").set_defaults(fn=cmd_status)
    sub.add_parser("doctor", help="check optional toolchains").set_defaults(fn=cmd_doctor)
    ecu = sub.add_parser("ecu", help="virtual ECU lab").add_subparsers(dest="ecu_cmd", required=True)
    ecu.add_parser("profiles", help="list target profiles and scenarios").set_defaults(fn=cmd_ecu_profiles)
    s = ecu.add_parser("run", help="build an application and run one scenario")
    s.add_argument("file"); s.add_argument("--profile", default="CM4_GENERIC")
    s.add_argument("--scenario", default="motor_nominal"); s.add_argument("--seed", type=int, default=1)
    s.add_argument("--csv", help="write the signal trace to a CSV file")
    s.set_defaults(fn=cmd_ecu_run)
    s = sub.add_parser("backup", help="back up the database"); s.add_argument("dest"); s.set_defaults(fn=cmd_backup)
    s = sub.add_parser("restore", help="restore a backup"); s.add_argument("src"); s.set_defaults(fn=cmd_restore)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ctx = bootstrap(args.home)
    try:
        return args.fn(ctx, args)
    finally:
        try:
            ctx.close()
        except Exception:  # noqa: BLE001 - already closed by restore
            pass


if __name__ == "__main__":
    raise SystemExit(main())
