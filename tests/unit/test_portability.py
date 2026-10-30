"""Portability guards. Windows defaults to the cp1252 locale encoding, Linux to UTF-8,
so encoding bugs only show up on Windows. These tests catch them on any OS."""
import ast
from pathlib import Path

from ecsim.app.config import CONTENT_DIR
from ecsim.learning.content import load_catalog
from ecsim.learning.graders import CFunctionGrader

from conftest import needs_cc

ROOT = Path(__file__).resolve().parents[2]
TEXT_IO = {"read_text", "write_text", "open"}


def _missing_encoding(path: Path) -> list[str]:
    bad = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else None
        kws = {k.arg for k in node.keywords}
        if name in TEXT_IO and "encoding" not in kws:
            # open() in binary mode needs no encoding
            mode = node.args[1] if name == "open" and len(node.args) > 1 else None
            if isinstance(mode, ast.Constant) and "b" in str(mode.value):
                continue
            bad.append(f"{path.relative_to(ROOT)}:{node.lineno} {name}()")
        if name == "run" and "text" in kws and "encoding" not in kws:
            bad.append(f"{path.relative_to(ROOT)}:{node.lineno} subprocess.run(text=True) without encoding")
    return bad


def test_all_text_io_declares_encoding():
    offenders = []
    for base in ("src", "tests"):
        for p in (ROOT / base).rglob("*.py"):
            offenders += _missing_encoding(p)
    assert not offenders, "Text I/O without explicit encoding (breaks on Windows):\n" + "\n".join(offenders)


@needs_cc
def test_c_submission_with_non_ascii_comments():
    ex = load_catalog(CONTENT_DIR).exercise("A01-E3")
    code = "/* Feld schreiben: 25 °C, −40…125 °C, ×2, µs — ünïcode */\n" + ex.spec["reference"]
    r = CFunctionGrader().grade(ex.spec, code)
    assert r.passed, r.feedback
