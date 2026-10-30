"""Detect optional external tools. Features degrade with an explicit message."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

# name -> (executable candidates, version args, purpose, licence note)
TOOLS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str, str]] = {
    "gcc": (("gcc", "clang"), ("--version",), "C exercises, SIL builds", "GPL / Apache (free)"),
    "cmake": (("cmake",), ("--version",), "Embedded project builds (P2+)", "BSD (free)"),
    "gdb": (("gdb",), ("--version",), "Debugging labs (P2+)", "GPL (free)"),
    "cppcheck": (("cppcheck",), ("--version",), "Static analysis labs (P2+)", "GPL (free)"),
    "scilab": (("scilab-cli", "scilab"), ("-version",), "Xcos models (P3)", "GPL (free)"),
    "omc": (("omc",), ("--version",), "OpenModelica plants (P3+)", "OSMC-PL (free)"),
    "candump": (("candump",), (), "SocketCAN labs (P6, Linux only)", "GPL (free)"),
}


@dataclass
class ToolStatus:
    name: str
    available: bool
    path: str | None
    version: str | None
    purpose: str
    licence: str


def probe_one(name: str) -> ToolStatus:
    exes, vargs, purpose, licence = TOOLS[name]
    for exe in exes:
        path = shutil.which(exe)
        if not path:
            continue
        version = None
        if vargs:
            try:
                out = subprocess.run([path, *vargs], capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=5)
                text = (out.stdout or out.stderr).strip().splitlines()
                version = text[0] if text else None
            except (OSError, subprocess.TimeoutExpired):
                version = None
        return ToolStatus(name, True, path, version, purpose, licence)
    return ToolStatus(name, False, None, None, purpose, licence)


def probe() -> dict[str, ToolStatus]:
    return {name: probe_one(name) for name in TOOLS}
