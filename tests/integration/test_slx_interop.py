"""Simulink interop: .slx import (synthetic files in Simulink's layout) and build-script export."""
import zipfile

import pytest

from ecsim.app.config import CONTENT_DIR
from ecsim.learning.content import load_catalog
from ecsim.learning.graders import ModelMilGrader
from ecsim.model import ModelError, Simulator, compile_model, load_model
from ecsim.model.slx import export_build_script, import_slx, matlab_value, parse_m_variables

from fakes import write_slx

SPEC = load_catalog(CONTENT_DIR).exercise("B02-E4").spec
REF = load_model(SPEC["reference"])


def outputs(model, inputs):
    sim = Simulator(compile_model(model))
    return [v for u in inputs for v in sim.step(u)]


@pytest.mark.parametrize("split", [True, False])
def test_import_reference_model_is_equivalent_and_passes_grading(tmp_path, split):
    p = tmp_path / "speed_pi.slx"
    write_slx(REF, p, split_systems=split)
    model, warnings = import_slx(p)
    assert model["name"] == "speed_pi" and model["solver"]["step"] == 0.01 and not warnings
    stim = [[float(v)] for v in range(0, 5000, 250)]
    assert outputs(model, stim) == pytest.approx(outputs(REF, stim))
    import json
    assert ModelMilGrader().grade(SPEC, json.dumps(model)).passed


def test_import_applies_simulink_defaults(tmp_path):
    m = {"format": "ecsim-model", "version": 1, "name": "d", "solver": {"type": "FixedStepDiscrete", "step": 0.01},
         "blocks": [{"id": "c", "type": "Constant", "params": {"value": 1.0}},
                    {"id": "g", "type": "Gain", "params": {"gain": 1.0}},
                    {"id": "s", "type": "Saturation", "params": {"upper": 0.5, "lower": -0.5}},
                    {"id": "o", "type": "Outport", "params": {"port": 1}}],
         "lines": [{"src": "c:1", "dst": "g:1"}, {"src": "g:1", "dst": "s:1"}, {"src": "s:1", "dst": "o:1"}]}
    p = tmp_path / "d.slx"
    write_slx(m, p, omit_defaults=True)
    raw = zipfile.ZipFile(p).read("simulink/systems/system_1.xml").decode()
    assert 'Name="Value"' not in raw and 'Name="UpperLimit"' not in raw     # Simulink-style omission
    model, _ = import_slx(p)
    assert outputs(model, [[]]) == [0.5]


def test_nested_subsystems_and_fanout(tmp_path):
    inner = {"format": "ecsim-model", "version": 1, "name": "inner", "solver": {"type": "FixedStepDiscrete", "step": 0.01},
             "blocks": [{"id": "x", "type": "Inport", "params": {"port": 1}}, {"id": "g", "type": "Gain", "params": {"gain": 3}},
                        {"id": "y", "type": "Outport", "params": {"port": 1}}],
             "lines": [{"src": "x:1", "dst": "g:1"}, {"src": "g:1", "dst": "y:1"}]}
    top = {"format": "ecsim-model", "version": 1, "name": "top", "solver": {"type": "FixedStepDiscrete", "step": 0.01},
           "blocks": [{"id": "u", "type": "Inport", "params": {"port": 1}}, {"id": "S", "type": "Subsystem", "params": {"model": inner}},
                      {"id": "add", "type": "Sum", "params": {"signs": "++"}}, {"id": "o", "type": "Outport", "params": {"port": 1}}],
           "lines": [{"src": "u:1", "dst": "S:1"}, {"src": "u:1", "dst": "add:2"}, {"src": "S:1", "dst": "add:1"},
                     {"src": "add:1", "dst": "o:1"}]}
    p = tmp_path / "top.slx"
    write_slx(top, p)
    model, _ = import_slx(p)
    assert outputs(model, [[2.0]]) == [8.0]


def test_variables_from_m_file(tmp_path):
    v = parse_m_variables("% gains\nKp = 0.00174;   % V/rpm\nKi = Kp/0.055; tbl = [0 1 2];\n")
    assert v["Ki"] == pytest.approx(0.00174 / 0.055) and v["tbl"] == [0, 1, 2]
    m = load_model(SPEC["reference"])
    p = tmp_path / "speed_pi.slx"
    write_slx(m, p)
    z = zipfile.ZipFile(p)
    files = {n: z.read(n) for n in z.namelist()}
    files["simulink/systems/system_1.xml"] = files["simulink/systems/system_1.xml"].replace(
        b'<P Name="Gain">0.00174</P>', b'<P Name="Gain">Kp</P>')
    z.close()
    with zipfile.ZipFile(p, "w") as z2:
        for n, b in files.items():
            z2.writestr(n, b)
    with pytest.raises(ModelError, match="variable 'Kp'"):
        import_slx(p)
    model, _ = import_slx(p, {"Kp": 0.00174})
    assert any(b["params"].get("gain") == 0.00174 for b in model["blocks"])


def test_unsupported_blocks_are_all_listed(tmp_path):
    p = tmp_path / "bad.slx"
    write_slx(REF, p, extra_blocks=["Integrator", "S-Function"])
    with pytest.raises(ModelError) as e:
        import_slx(p)
    assert "Integrator" in str(e.value) and "S-Function" in str(e.value)


def test_bad_files(tmp_path):
    (tmp_path / "x.slx").write_text("not a zip", encoding="utf-8")
    with pytest.raises(ModelError, match="not a readable .slx"):
        import_slx(tmp_path / "x.slx")
    with zipfile.ZipFile(tmp_path / "y.slx", "w") as z:
        z.writestr("other.xml", "<a/>")
    with pytest.raises(ModelError, match="blockdiagram.xml"):
        import_slx(tmp_path / "y.slx")


@pytest.mark.parametrize("text,val", [("3000", 3000), ("-0.5", -0.5), ("1e-3", 0.001), ("[0 1, 2]", [0, 1, 2]),
                                      ("2^3", 8), ("inf", float("inf")), ("[1;2]", [1, 2])])
def test_matlab_value(text, val):
    assert matlab_value(text) == val


def test_matlab_value_rejects_code():
    with pytest.raises(ModelError):
        matlab_value("__import__('os')")
    with pytest.raises(ModelError):
        matlab_value("system('rm')")


def test_export_build_script():
    s = export_build_script(REF)
    for frag in ("new_system(mdl);", "'Solver', 'FixedStepDiscrete', 'FixedStep', '0.01'",
                 "add_block('simulink/Discrete/Discrete-Time Integrator', [mdl '/integrator'], 'gainval', '0.0316'",
                 "'LimitOutput', 'on', 'UpperSaturationLimit', '24'", "add_block('simulink/Math Operations/Sum', [mdl '/error'], 'Inputs', '|+-'",
                 "add_line(mdl, 'error/1', 'Kp/1', 'autorouting', 'on');", "save_system(mdl);"):
        assert frag in s, frag
    # Ports are created first and in port order, as Simulink numbers them on creation.
    assert s.index("'/speed_meas_rpm'") < s.index("'/voltage_cmd_V'") < s.index("'/setpoint'")


def test_export_subsystem_script_replaces_default_contents():
    from ecsim.model.document import ModelDocument
    d = ModelDocument()
    d.add_block("Inport", bid="u"); s = d.add_block("Subsystem"); d.add_block("Outport", bid="y")
    d.connect("u:1", f"{s}:1"); d.connect(f"{s}:1", "y:1")
    script = export_build_script(d.model)
    assert "delete_block([[mdl '/Subsystem'] '/In1'])" in script
    assert "add_block('simulink/Sources/In1', [[mdl '/Subsystem'] '/In1']" in script
