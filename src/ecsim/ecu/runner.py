"""Run scenarios in a child process so a crashing or hanging learner application
cannot take down the GUI or the test runner.

    python -m ecsim.ecu.runner LIB PROFILE SCENARIO [SEED]   -> JSON on stdout
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ecsim.ecu.scenarios import ScenarioResult, run_scenario
from ecsim.ecu.vecu import AppCrash

MARK = "ECSIM_JSON:"
EXIT_APP_CRASH = 70


@dataclass
class RunOutcome:
    ok: bool                      # process completed and produced a result
    result: ScenarioResult | None
    error: str = ""


def run_isolated(lib: Path, profile: str, scenario: str, seed: int = 1, timeout_s: float = 60.0) -> RunOutcome:
    cmd = [sys.executable, "-m", "ecsim.ecu.runner", str(lib), profile, scenario, str(seed)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return RunOutcome(False, None, f"Simulation exceeded {timeout_s:g} s. A task function probably never "
                                       "returns (infinite loop). On real hardware the watchdog would reset the ECU; "
                                       "here the host process cannot pre-empt your code.")
    for line in p.stdout.splitlines():
        if line.startswith(MARK):
            return RunOutcome(True, ScenarioResult.from_dict(json.loads(line[len(MARK):])))
    if p.returncode == EXIT_APP_CRASH or p.returncode < 0 or p.returncode > 128:
        # EXIT_APP_CRASH: fault caught and located (Windows); <0 signal (POSIX); >128 NTSTATUS
        return RunOutcome(False, None, f"The application crashed (exit code {p.returncode}; e.g. segmentation "
                                       "fault from an invalid pointer or out-of-bounds array access).\n"
                                       + p.stderr[-2000:])
    return RunOutcome(False, None, f"Simulation failed (exit code {p.returncode}).\n" + p.stderr[-3000:])


def _harden_child() -> None:
    """Make a crash in learner code end the process promptly with a diagnostic."""
    import faulthandler
    faulthandler.enable()
    if sys.platform.startswith("win"):
        import ctypes
        # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX: no "has stopped working" dialog
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)


def main(argv: list[str] | None = None) -> int:
    _harden_child()
    a = argv if argv is not None else sys.argv[1:]
    lib, profile, scenario = Path(a[0]), a[1], a[2]
    seed = int(a[3]) if len(a) > 3 else 1
    try:
        res = run_scenario(lib, scenario, profile, seed)
    except AppCrash as exc:
        sys.stderr.write(f"Application crash: {exc}\n")
        return EXIT_APP_CRASH
    sys.stdout.write(MARK + json.dumps(res.to_dict()) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
