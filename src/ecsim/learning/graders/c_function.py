"""Compile learner C against a hidden harness with strict warnings and run it.

This runs the learner's *own* code on their own machine with a timeout. It is not
a security sandbox and does not need to be one for a local, single-user tool.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ecsim.learning.graders.base import GradeResult

PRELUDE = """\
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
"""

TEST_HEADER = r"""
#include <stdio.h>
#include <math.h>
static int ecsim_pass = 0, ecsim_total = 0;
#define ECSIM_REPORT(ok, msg, fmt, ...) do { ecsim_total++; if (ok) { ecsim_pass++; } \
    else { printf("FAIL: %s: " fmt "\n", msg, __VA_ARGS__); } } while (0)
#define CHECK(cond, msg) ECSIM_REPORT((cond), msg, "%s", "condition false")
#define CHECK_EQ_U(act, exp, msg) do { unsigned long long a_ = (unsigned long long)(act), \
    e_ = (unsigned long long)(exp); ECSIM_REPORT(a_ == e_, msg, "got 0x%llX expected 0x%llX", a_, e_); } while (0)
#define CHECK_EQ_I(act, exp, msg) do { long long a_ = (long long)(act), e_ = (long long)(exp); \
    ECSIM_REPORT(a_ == e_, msg, "got %lld expected %lld", a_, e_); } while (0)
#define CHECK_NEAR(act, exp, tol, msg) do { double a_ = (double)(act), e_ = (double)(exp); \
    ECSIM_REPORT(fabs(a_ - e_) <= (tol), msg, "got %.7g expected %.7g", a_, e_); } while (0)
#define ECSIM_DONE() (printf("ECSIM_RESULT %d %d\n", ecsim_pass, ecsim_total), \
    (ecsim_pass == ecsim_total) ? 0 : 1)
"""

CFLAGS = ["-std=c99", "-Wall", "-Wextra", "-Werror", "-pedantic", "-O1"]
_RESULT = re.compile(r"ECSIM_RESULT (\d+) (\d+)")
MAX_FEEDBACK = 4000


class CFunctionGrader:
    def __init__(self, compiler: str = "gcc", timeout_s: float = 5.0) -> None:
        self.compiler = compiler
        self.timeout_s = timeout_s

    def available(self) -> bool:
        return shutil.which(self.compiler) is not None

    def grade(self, spec: dict[str, Any], submission: str) -> GradeResult:
        if not self.available():
            return GradeResult(
                False, 0.0,
                f"C compiler '{self.compiler}' not found. Install GCC or Clang "
                "(see README → Troubleshooting). This attempt was not recorded as a failure.",
                {"infrastructure_error": True},
            )
        if re.search(r"\bmain\s*\(", submission):
            return GradeResult(False, 0.0, "Do not define main(); the test harness provides it.")
        prototypes = spec["prototypes"]
        with tempfile.TemporaryDirectory(prefix="ecsim_c_") as tmp:
            d = Path(tmp)
            (d / "learner.c").write_text(PRELUDE + prototypes + "\n#line 1 \"your_code.c\"\n" + submission + "\n", encoding="utf-8")
            (d / "harness.c").write_text(PRELUDE + TEST_HEADER + prototypes + "\n" + spec["harness"], encoding="utf-8")
            exe = d / "test.bin"
            # Learner file gets strict flags; the harness is ours and is built with the same flags.
            cc = [self.compiler, *CFLAGS, "learner.c", "harness.c", "-lm", "-o", str(exe)]
            try:
                build = subprocess.run(cc, cwd=d, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=60)
            except subprocess.TimeoutExpired:
                return GradeResult(False, 0.0, "Compilation timed out.")
            if build.returncode != 0:
                log = _clean(build.stderr, d)
                return GradeResult(
                    False, 0.0,
                    "Compilation failed (flags: " + " ".join(CFLAGS) + ")\n\n" + log[:MAX_FEEDBACK],
                    {"stage": "compile"},
                )
            try:
                run = subprocess.run([str(exe)], cwd=d, capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                return GradeResult(False, 0.0, f"Tests exceeded {self.timeout_s:g} s - infinite loop?", {"stage": "run"})
        out = run.stdout
        m = _RESULT.search(out)
        if not m:
            sig = f" (exit code {run.returncode}; negative = killed by signal, e.g. -11 segmentation fault)"
            return GradeResult(False, 0.0, "Test program crashed before finishing" + sig + "\n" + out[:MAX_FEEDBACK], {"stage": "run"})
        passed, total = int(m.group(1)), int(m.group(2))
        fails = [ln for ln in out.splitlines() if ln.startswith("FAIL:")]
        ok = passed == total and total > 0
        fb = f"{passed}/{total} tests passed."
        if fails:
            fb += "\n" + "\n".join(fails)
        return GradeResult(ok, passed / total if total else 0.0, fb[:MAX_FEEDBACK], {"passed": passed, "total": total})


def _clean(text: str, d: Path) -> str:
    return text.replace(str(d) + "/", "")
