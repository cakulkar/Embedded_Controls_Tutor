"""Compile a learner ECU application + the HAL into a host shared library.

Resource figures come from the *host* compiler (x86-64/ARM64 desktop), so they
are indicative of relative size only, not of target flash/RAM usage. The UI and
reports label them that way.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

C_DIR = Path(__file__).resolve().parent / "c"
CFLAGS = ["-std=c99", "-Wall", "-Wextra", "-Werror", "-pedantic", "-O1"]
IS_WINDOWS = sys.platform.startswith("win")
LIB_SUFFIX = ".dll" if IS_WINDOWS else (".dylib" if sys.platform == "darwin" else ".so")
# A *definition*: start of line, a return type, the name, a parameter list without ')', then '{'.
_RESERVED = re.compile(r"^[ \t]*(?:static[ \t]+)?[A-Za-z_][\w \t\*]*?\b"
                       r"(main|ecu_io_size|Adc_ReadChannel|Adc_GetResolutionBits|Adc_GetReferenceVoltage|"
                       r"Pwm_SetDutyCycle|Wdg_Trigger|Dem_SetEventStatus|Dio_ReadChannel|Dio_WriteChannel|"
                       r"Os_GetTimeMs|Mcu_GetResetReason|Mcu_PerformReset)\s*\([^;{)]*\)\s*\{", re.MULTILINE)


@dataclass
class Resources:
    text: int = 0
    data: int = 0
    bss: int = 0
    rodata: int = 0
    max_stack_bytes: int | None = None
    max_stack_function: str | None = None
    available: bool = False

    @property
    def static_ram(self) -> int:
        return self.data + self.bss


@dataclass
class BuildResult:
    ok: bool
    log: str
    lib_path: Path | None = None
    resources: Resources = field(default_factory=Resources)


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)


def build_app(source: str, workdir: Path, compiler: str = "gcc") -> BuildResult:
    if shutil.which(compiler) is None:
        return BuildResult(False, f"C compiler '{compiler}' not found.")
    if _RESERVED.search(source):
        return BuildResult(False, "Do not define main() or HAL functions; the virtual ECU provides them.")
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "ecu_hal.h").write_text((C_DIR / "ecu_hal.h").read_text(encoding="utf-8"), encoding="utf-8")
    (workdir / "hal.c").write_text((C_DIR / "hal.c").read_text(encoding="utf-8"), encoding="utf-8")
    (workdir / "app.c").write_text('#include "ecu_hal.h"\n#line 1 "your_app.c"\n' + source + "\n",
                                   encoding="utf-8")
    pic = [] if IS_WINDOWS else ["-fPIC"]
    steps = [
        [compiler, *CFLAGS, *pic, "-fstack-usage", "-c", "app.c", "-o", "app.o"],
        [compiler, *CFLAGS, *pic, "-c", "hal.c", "-o", "hal.o"],
        [compiler, "-shared", "app.o", "hal.o", "-lm", "-o", "app" + LIB_SUFFIX],
    ]
    log = []
    for cmd in steps:
        try:
            r = _run(cmd, workdir)
        except subprocess.TimeoutExpired:
            return BuildResult(False, "Build timed out.")
        if r.returncode != 0:
            msg = (r.stderr or r.stdout).replace(str(workdir) + "/", "").replace(str(workdir) + "\\", "")
            return BuildResult(False, "Build failed (flags: " + " ".join(CFLAGS) + ")\n\n" + msg[:4000])
        log.append(r.stderr)
    res = _resources(workdir)
    return BuildResult(True, "".join(log), workdir / ("app" + LIB_SUFFIX), res)


def _resources(workdir: Path) -> Resources:
    res = Resources()
    size = shutil.which("size")
    if size:
        try:
            r = _run([size, "-A", "app.o"], workdir)
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) < 2 or not parts[1].isdigit():
                    continue
                name, n = parts[0], int(parts[1])
                if name.startswith(".text"):
                    res.text += n
                elif name.startswith((".rodata", ".rdata")):
                    res.rodata += n
                elif name.startswith(".data"):
                    res.data += n
                elif name.startswith(".bss"):
                    res.bss += n
            res.available = r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    su = workdir / "app.su"
    if su.exists():
        best = None
        for line in su.read_text(encoding="utf-8", errors="replace").splitlines():
            cols = line.split("\t")
            if len(cols) >= 2 and cols[1].isdigit():
                n = int(cols[1])
                if best is None or n > best[0]:
                    best = (n, cols[0].rsplit(":", 1)[-1])
        if best:
            res.max_stack_bytes, res.max_stack_function = best
    return res
