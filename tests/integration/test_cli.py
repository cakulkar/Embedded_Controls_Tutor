from ecsim.cli import main

from conftest import needs_cc


def run(tmp_path, *args, capsys):
    code = main(["--home", str(tmp_path / "home"), *args])
    return code, capsys.readouterr().out


def test_full_cli_workflow(tmp_path, capsys):
    assert run(tmp_path, "init", "--name", "Ana", "--track", "BMS_CTL", capsys=capsys)[0] == 0
    code, out = run(tmp_path, "list", capsys=capsys)
    assert code == 0 and "A01-E1" in out and "B01-E4" in out
    code, out = run(tmp_path, "show", "A02-E2", capsys=capsys)
    assert "MAP sensor" in out and "156" not in out          # answer not leaked
    assert run(tmp_path, "solution", "A02-E2", capsys=capsys)[0] == 3   # gated
    assert run(tmp_path, "hint", "A02-E2", capsys=capsys)[0] == 0
    assert run(tmp_path, "hint", "A02-E2", capsys=capsys)[0] == 3       # needs attempt
    code, out = run(tmp_path, "submit", "A02-E2", "--answer", "150", capsys=capsys)
    assert code == 1 and "too low" in out
    code, out = run(tmp_path, "submit", "A02-E2", "--answer", "156.3 kPa", capsys=capsys)
    assert code == 0 and "(independent)" in out
    code, out = run(tmp_path, "status", capsys=capsys)
    assert "Weighted competency score" in out and "BMS_CTL" in out
    code, out = run(tmp_path, "doctor", capsys=capsys)
    assert "gcc" in out


def test_backup_restore_roundtrip(tmp_path, capsys):
    run(tmp_path, "init", "--name", "Ana", capsys=capsys)
    run(tmp_path, "submit", "A02-E1", "--answer", "-12288", capsys=capsys)
    bak = tmp_path / "b.db"
    assert run(tmp_path, "backup", str(bak), capsys=capsys)[0] == 0
    run(tmp_path, "submit", "A01-E2", "--answer", "4056", capsys=capsys)
    assert run(tmp_path, "restore", str(bak), capsys=capsys)[0] == 0
    _, out = run(tmp_path, "list", capsys=capsys)
    lines = {k: l for l in out.splitlines() for k in ("A02-E1", "A01-E2") if f" {k} " in l}
    assert lines["A02-E1"].strip().startswith("★")
    assert not lines["A01-E2"].strip().startswith("★")


def test_content_resync_preserves_evidence(tmp_path, capsys):
    run(tmp_path, "init", "--name", "Ana", capsys=capsys)
    run(tmp_path, "submit", "A02-E1", "--answer", "-12288", capsys=capsys)
    # Second bootstrap re-syncs content; evidence must survive.
    _, out = run(tmp_path, "status", capsys=capsys)
    _, out = run(tmp_path, "list", capsys=capsys)
    assert any(l.strip().startswith("★") and "A02-E1" in l for l in out.splitlines())


@needs_cc
def test_cli_c_submission(tmp_path, capsys):
    from ecsim.app.config import CONTENT_DIR
    from ecsim.learning.content import load_catalog
    run(tmp_path, "init", "--name", "Ana", capsys=capsys)
    src = tmp_path / "pi.c"
    src.write_text(load_catalog(CONTENT_DIR).exercise("B01-E4").spec["reference"])
    code, out = run(tmp_path, "submit", "B01-E4", "--file", str(src), capsys=capsys)
    assert code == 0 and "13/13" in out
