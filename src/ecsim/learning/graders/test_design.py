"""Grade learner-written *tests* for a given model: correctness, coverage, fault detection.

Submission: JSON {"tests": [...]} in the MIL test format (open-loop tests with inputs and checks).
1. Every check must pass on the correct model (a test that fails on correct behaviour is a wrong test).
2. Merged decision coverage of the model must reach the required level.
3. Each hidden seeded fault (mutant model) must make at least one check fail.
Mutant identities are not revealed, only how many survive, as with a real fault-injection campaign.
"""
from __future__ import annotations

import json
from typing import Any

from ecsim.learning.graders.base import GradeResult
from ecsim.model.backends import EcsimBackend
from ecsim.model.blocks import ModelError
from ecsim.model.core import Simulator, compile_model
from ecsim.model.coverage import CoverageCollector, CoverageReport
from ecsim.model.mil import n_samples, run_tests, signal_value

MAX_DURATION = 60.0
CHECK_KINDS = {"within", "near", "below_by", "at"}


def _validate(tests: Any, inports: list[str], signals: set[str]) -> list[dict]:
    if not isinstance(tests, list) or not tests:
        raise ModelError('submit {"tests": [ ... ]} with at least one test')
    names = set()
    for i, t in enumerate(tests):
        where = f"test {i + 1}"
        if not isinstance(t, dict):
            raise ModelError(f"{where}: must be an object")
        t.setdefault("type", "open_loop")
        if t["type"] != "open_loop":
            raise ModelError(f"{where}: only open_loop tests are used in this exercise")
        name = t.get("name") or f"T{i + 1}"
        if name in names:
            raise ModelError(f"{where}: duplicate test name '{name}'")
        names.add(name)
        t["name"] = name
        try:
            dur = float(t.get("duration", 0))
        except (TypeError, ValueError):
            raise ModelError(f"{where}: duration must be a number") from None
        if not 0 < dur <= MAX_DURATION:
            raise ModelError(f"{where}: duration must be in (0, {MAX_DURATION:g}] s")
        missing = [n for n in inports if n not in t.get("inputs", {})]
        if missing:
            raise ModelError(f"{where}: no signal given for inputs {missing}")
        for n, sig in t["inputs"].items():
            try:
                signal_value(sig, 0.0)
            except (ModelError, KeyError, TypeError, ValueError):
                raise ModelError(f"{where}: invalid signal for '{n}': {sig}") from None
        checks = t.get("checks")
        if not checks:
            raise ModelError(f"{where}: add at least one check (a test without checks verifies nothing)")
        for k, c in enumerate(checks):
            if c.get("kind") not in CHECK_KINDS:
                raise ModelError(f"{where} check {k + 1}: kind must be one of {sorted(CHECK_KINDS)}")
            if c.get("signal") not in signals:
                raise ModelError(f"{where} check {k + 1}: unknown signal {c.get('signal')!r}; use one of {sorted(signals)}")
    return tests


def coverage_of(model: str, tests: list[dict]) -> CoverageReport:
    c = compile_model(model)
    total: CoverageReport | None = None
    for t in tests:
        col = CoverageCollector(c)
        sim = col.attach(Simulator(c))
        for k in range(n_samples(float(t["duration"]), c.step)):
            sim.step([signal_value(t["inputs"][n], k * c.step) for n in c.inport_names])
        if total is None:
            total = col.report
        else:
            total.merge(col.report)
    return total


class TestDesignGrader:
    __test__ = False

    def grade(self, spec: dict[str, Any], submission: str) -> GradeResult:
        model = spec["model"]
        iface = spec["interface"]
        try:
            data = json.loads(submission)
            tests = _validate(data.get("tests") if isinstance(data, dict) else None, iface["inports"],
                              set(iface["inports"]) | set(iface["outports"]))
        except json.JSONDecodeError as exc:
            return GradeResult(False, 0.0, f"Not valid JSON: {exc}")
        except ModelError as exc:
            return GradeResult(False, 0.0, f"Test definition error: {exc}")
        run_spec = {"interface": iface, "tests": tests}
        ref = run_tests(EcsimBackend(), model, run_spec)
        lines, results = [], []
        wrong = [o for o in ref.outcomes if not o.passed]
        results.append(not wrong and not ref.errors)
        lines.append(f"{'✓' if results[-1] else '✗'} Your {len(tests)} test(s) pass on the correct model")
        for o in wrong:
            lines.append(f"    wrong expectation: [{o.test}] {o.name}: {o.detail}")
        cov = coverage_of(model, tests)
        need = float(spec.get("min_coverage", 1.0))
        results.append(cov.ratio >= need - 1e-12)
        lines.append(f"{'✓' if results[-1] else '✗'} Decision coverage {cov.covered}/{cov.total} ({cov.ratio:.0%}), required {need:.0%}")
        for b, o in cov.missing():
            lines.append(f"    not covered: {b}: {o}")
        killed = 0
        for m in spec.get("mutants", []):
            r = run_tests(EcsimBackend(), m, run_spec)
            if r.errors or any(not o.passed for o in r.outcomes):
                killed += 1
        n = len(spec.get("mutants", []))
        results += [True] * killed + [False] * (n - killed)
        lines.append(f"{'✓' if killed == n else '✗'} Seeded faults detected: {killed}/{n}"
                     + ("" if killed == n else " (a surviving fault means some requirement is not tested precisely enough)"))
        ok = all(results)
        return GradeResult(ok, sum(results) / len(results), f"{sum(results)}/{len(results)} criteria met.\n" + "\n".join(lines),
                           {"coverage": cov.ratio, "killed": killed, "mutants": n})
