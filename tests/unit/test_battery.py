"""Phase 9: battery cell / precharge plants and the harness features they need."""
import math

import pytest

from ecsim.model.backends import simulink_backend as sb
from ecsim.model.blocks import ModelError
from ecsim.model.mil import plant_driven_inputs, signal_value
from ecsim.plants.battery import OCV_SOC, OCV_V, Cell, PrechargeCircuit, ocv

from fakes import RecordingEngine, fake_double


def test_ocv_table_interpolates_and_clips():
    for s, v in zip(OCV_SOC, OCV_V):
        assert ocv(s) == pytest.approx(v)
    assert ocv(0.45) == pytest.approx((3.67 + 3.73) / 2)
    assert ocv(-1) == OCV_V[0] and ocv(2) == OCV_V[-1]
    assert all(b > a for a, b in zip(OCV_V, OCV_V[1:]))     # monotonic: invertible by a lookup table


def test_coulomb_counting_matches_hand_calculation():
    c = Cell(soc=0.8)
    for _ in range(1800):
        c.step(-25.0, 1.0)                                    # 25 A discharge for 30 min
    assert c.soc == pytest.approx(0.55, abs=1e-9)


def test_ohmic_and_polarisation_drop():
    c = Cell(soc=0.5)
    assert c.terminal_voltage(0.0) == pytest.approx(ocv(0.5))
    assert c.terminal_voltage(-50.0) == pytest.approx(ocv(0.5) - 0.1)    # I*R0 immediately
    for _ in range(600):
        c.step(-50.0, 1.0)
    assert c.v1 == pytest.approx(-50 * 0.0015, rel=1e-6)                  # RC branch settled at I*R1
    for _ in range(300):
        c.step(0.0, 1.0)
    assert abs(c.v1) < 1e-4 * 0.075                                          # < 0.01 % left after 5 min rest


def test_thermal_model_heats_under_load_and_cools_at_rest():
    c = Cell(soc=0.9)
    for _ in range(600):
        c.step(-100.0, 1.0)
    hot = c.temp
    assert hot > 25.5
    for _ in range(3600):
        c.step(0.0, 1.0)
    assert 25.0 <= c.temp < hot


def test_soc_saturates():
    c = Cell(soc=0.999)
    for _ in range(100):
        c.step(100.0, 10.0)
    assert c.soc == 1.0


def test_precharge_reaches_95_percent_after_3_tau():
    p = PrechargeCircuit()
    t, dt = 0.0, 0.001
    while p.v_link < 0.95 * p.v_pack:
        p.step(True, False, dt)
        t += dt
    assert t == pytest.approx(0.05 * math.log(20), abs=dt)
    p.step(True, True, dt)
    assert p.v_link == p.v_pack


def test_open_resistor_never_charges_and_link_bleeds():
    p = PrechargeCircuit(resistor_open=True)
    for _ in range(1000):
        p.step(True, False, 0.001)
    assert p.v_link == 0.0
    p = PrechargeCircuit(v_link=400.0)
    p.step(False, False, 5.0)
    assert p.v_link == pytest.approx(400 / math.e)


def test_samples_signal_holds_last_value():
    s = {"samples": [1.0, 2.0, 3.0], "dt": 0.5}
    assert [signal_value(s, t) for t in (0, 0.49, 0.5, 1.0, 9.0)] == [1.0, 1.0, 2.0, 3.0, 3.0]


def test_plant_driven_inputs_include_offset_and_truth():
    test = {"plant": "battery_cell", "duration": 10, "plant_params": {"soc0": 0.6, "current_offset_A": 2.0},
            "current_profile": {"const": -10.0}}
    ins, truth = plant_driven_inputs(test, 1.0)
    assert set(ins) == {"current_A", "voltage_V", "temp_degC"}
    assert ins["current_A"]["samples"] == [-8.0] * 11
    assert truth["soc_true_pct"][0] == pytest.approx(60.0)
    assert truth["soc_true_pct"][-1] == pytest.approx(60.0 - 100 * 10 * 10 / (3600 * 50))
    assert ins["voltage_V"]["samples"][0] == pytest.approx(ocv(0.6) - 10 * 0.002)
    with pytest.raises(ModelError, match="unknown plant"):
        plant_driven_inputs({"plant": "flux_capacitor", "duration": 1}, 1.0)


def test_simulink_precharge_harness_script(tmp_path):
    eng = RecordingEngine()
    s = sb.EngineSession(eng, fake_double)
    co = sb.precharge_coefficients(50.0, 0.001, 400.0, 0.01)
    assert co["a"] == pytest.approx(math.exp(-0.2)) and co["c"] == pytest.approx(400 * (1 - math.exp(-0.2)))
    s.sim_precharge("seq", co, [[0.0, 1.0, 0.0]], 0.0, 0.01)
    script = eng.calls[-1][1]
    for frag in ("'ModelName', 'seq'", "add_line(ecsim_h, 'v_link/1', 'ctrl/3')", "add_line(ecsim_h, 'ctrl/2', 'sel_main/2')",
                 "add_line(ecsim_h, 'sel_main/1', 'v_link/1')", f"'Gain', '{co['a']:.17g}'", "close_system(ecsim_h, 0)"):
        assert frag in script, frag
    assert script.index("/v_link_V']") < script.index("/k_precharge']") < script.index("/k_main']") < script.index("/fault']")


def test_simulink_backend_rejects_other_closed_loop_plants():
    b = sb.SimulinkBackend(session=object())
    with pytest.raises(ModelError, match="ECSim engine only"):
        b.run_closed_loop({"name": "m"}, {"plant": "hydraulic_boom", "duration": 1}, 0.01)


# ---- plant explorer (Vehicle & Machine Simulator) ----------------------------------------
from ecsim.plants.explorer import SCENARIOS, Param, Scenario, register  # noqa: E402


@pytest.mark.parametrize("key", list(SCENARIOS))
def test_every_scenario_runs_with_defaults_and_is_well_formed(key):
    r = SCENARIOS[key].run()
    assert r.t and r.panels and r.summary
    for panel in r.panels:
        for name, vals in panel.series.items():
            assert len(vals) == len(r.t), (panel.title, name)
            assert all(math.isfinite(v) for v in vals)


def test_scenario_parameter_validation():
    s = SCENARIOS["precharge"]
    with pytest.raises(ModelError, match="must be in"):
        s.run({"r_pre": 0})
    with pytest.raises(ModelError, match="unknown parameter"):
        s.run({"bogus": 1})
    with pytest.raises(ValueError, match="duplicate"):
        register(Scenario("precharge", "x", "x", (Param("a", "a", 0, 0, 1),), lambda p: None))


def test_explorer_numbers_match_the_lessons():
    pre = SCENARIOS["precharge"].run()
    assert "95 % reached at 150 ms" in pre.summary[1]
    cell = SCENARIOS["battery_cell"].run({"soc0_pct": 80, "discharge_A": 25, "load_s": 1800, "rest_s": 0})
    assert cell.panels[1].series["soc"][-1] == pytest.approx(55.0, abs=0.05)
    motor = SCENARIOS["dc_motor"].run({"load_Nm": 0})
    from ecsim.plants.dc_motor import DcMotorParams
    assert motor.panels[0].series["speed"][-1] == pytest.approx(12 * DcMotorParams().gain_rpm_per_volt, rel=1e-3)


def test_plot_tick_labels_distinguish_neighbours():
    from ecsim.gui.plot import tick_label
    labels = [tick_label(3.8 + 0.1 * k, 0.4) for k in range(5)]
    assert len(set(labels)) == 5
    assert tick_label(150, 600) == "150" and tick_label(-1e-17, 0.4) in ("0.00", "0.000")
