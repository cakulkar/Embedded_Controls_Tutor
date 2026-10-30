"""Virtual ECU engine tests with small purpose-built C applications (trusted, run in-process)."""
import math
import shutil

import pytest

from ecsim.ecu.build import build_app
from ecsim.ecu.profiles import PROFILES, get_profile
from ecsim.ecu.vecu import RESET_SOFTWARE, RESET_WATCHDOG, AdcFault, VirtualEcu
from ecsim.plants.dc_motor import DcMotor, DcMotorParams

from conftest import needs_cc

CM, TC = PROFILES["CM4_GENERIC"], PROFILES["TC3XX_LIKE"]


def lib(tmp_path, src):
    b = build_app(src, tmp_path)
    assert b.ok, b.log
    return b.lib_path


def run(ecu, ms):
    for _ in range(ms):
        ecu.tick()


# Counts task_10ms executions on PWM ch 0; services the watchdog from task_10ms.
COUNTER_APP = """
static uint16_t n10;
void app_init(void) { n10 = 0u; }
void task_10ms(void) { n10++; Pwm_SetDutyCycle(0u, n10); Wdg_Trigger(); }
"""


def test_profiles():
    assert set(PROFILES) == {"CM4_GENERIC", "TC3XX_LIKE"}
    assert TC.watchdog.window_min_ms > 0 and CM.watchdog.window_min_ms == 0
    assert TC.cores > TC.cores_simulated and TC.planned
    with pytest.raises(KeyError, match="available"):
        get_profile("NOPE")


def test_dc_motor_matches_first_order_model():
    p = DcMotorParams()
    m = DcMotor(p)
    m.duty = 0.5
    for _ in range(int(p.tau / 0.001)):
        m.step(0.001)
    final = p.gain_rpm_per_volt * 12.0
    assert m.rpm == pytest.approx(final * (1 - math.exp(-1)), rel=0.02)  # 63 % after one tau
    for _ in range(2000):
        m.step(0.001)
    assert m.rpm == pytest.approx(final, rel=1e-6)
    assert m.speed_sensor_v() == pytest.approx(0.5 + final * 4 / 6000)


@needs_cc
def test_build_rejects_hal_redefinition_but_allows_calls(tmp_path):
    bad = build_app("void Wdg_Trigger(void) { }\nvoid app_init(void) {}", tmp_path / "a")
    assert not bad.ok and "HAL" in bad.log
    ok = build_app("void app_init(void) { if (Mcu_GetResetReason() == MCU_RESET_WATCHDOG) { Wdg_Trigger(); } }",
                   tmp_path / "b")
    assert ok.ok, ok.log


@needs_cc
def test_build_reports_compile_errors_and_resources(tmp_path):
    b = build_app("void app_init(void) { int unused; }", tmp_path / "a")
    assert not b.ok and "your_app.c" in b.log
    b = build_app(COUNTER_APP, tmp_path / "b")
    assert b.ok
    if shutil.which("size"):
        assert b.resources.available and b.resources.text > 0
    else:
        assert not b.resources.available           # degrades, never fails the build


@needs_cc
def test_cpu_load_and_task_rates(tmp_path):
    with VirtualEcu(lib(tmp_path, COUNTER_APP), CM) as ecu:
        run(ecu, 1000)
        assert ecu.pwm(0) == 100
        assert ecu.cpu_load == pytest.approx(900 / 10000)
        assert not ecu.deadline_misses and not ecu.resets


@needs_cc
def test_overrun_drops_every_second_activation(tmp_path):
    with VirtualEcu(lib(tmp_path, COUNTER_APP), CM) as ecu:
        ecu.wcet_override["task_10ms"] = 12000
        run(ecu, 200)
        assert ecu.pwm(0) == 10           # 20 releases, 10 executed
        assert len(ecu.deadline_misses) == 10
        assert not ecu.resets             # still serviced every 20 ms


@needs_cc
def test_watchdog_timeout_and_reset_reason(tmp_path):
    src = """
    void app_init(void) { Pwm_SetDutyCycle(0u, (uint16_t)(1000u * (uint16_t)Mcu_GetResetReason())); }
    """
    with VirtualEcu(lib(tmp_path, src), CM) as ecu:
        assert ecu.pwm(0) == 0            # power-on
        run(ecu, 100)
        assert ecu.resets and ecu.resets[0].reason == RESET_WATCHDOG and ecu.resets[0].time_ms == 100
        assert ecu.in_reset and ecu.pwm(0) == 0
        run(ecu, CM.reset_startup_ms)
        assert not ecu.in_reset and ecu.pwm(0) == 1000   # app saw WATCHDOG


@needs_cc
def test_windowed_watchdog(tmp_path):
    fast = "void app_init(void) {}\nvoid task_1ms(void) { Wdg_Trigger(); }"
    with VirtualEcu(lib(tmp_path / "f", fast), CM) as ecu:
        run(ecu, 500)
        assert not ecu.resets             # timeout-only watchdog accepts it
    with VirtualEcu(lib(tmp_path / "g", fast), TC) as ecu:
        run(ecu, 5)
        assert ecu.resets and "window" in ecu.resets[0].detail and ecu.resets[0].time_ms == 2
    with VirtualEcu(lib(tmp_path / "h", COUNTER_APP), TC) as ecu:
        run(ecu, 1000)
        assert not ecu.resets             # 10 ms spacing is inside the window


@needs_cc
def test_reset_restarts_static_variables_but_keeps_dem(tmp_path):
    src = """
    static uint16_t n = 0u;
    void app_init(void) { Dem_SetEventStatus(7u, DEM_EVENT_STATUS_FAILED); }
    void task_1ms(void) { n++; Pwm_SetDutyCycle(0u, n); if (n == 50u) { Mcu_PerformReset(); } Wdg_Trigger(); }
    """
    with VirtualEcu(lib(tmp_path, src), CM) as ecu:
        run(ecu, 40)
        assert ecu.pwm(0) == 40
        run(ecu, 10)
        assert ecu.resets[0].reason == RESET_SOFTWARE
        run(ecu, CM.reset_startup_ms + 3)
        assert ecu.pwm(0) == 3            # counter restarted from its initial value
        assert ecu.dem_first_failed(7) == 0
        assert len([d for d in ecu.dem_log if d.event_id == 7]) == 1   # unchanged status not re-logged


@needs_cc
def test_adc_conversion_and_faults(tmp_path):
    src = "void app_init(void) {}\nvoid task_1ms(void) { Pwm_SetDutyCycle(1u, Adc_ReadChannel(0u)); Wdg_Trigger(); }"
    with VirtualEcu(lib(tmp_path, src), CM) as ecu:
        ecu.analog_in[0] = 2.5
        run(ecu, 1)
        assert ecu.pwm(1) == 2048
        ecu.analog_in[0] = 7.0
        run(ecu, 1)
        assert ecu.pwm(1) == 4095         # clipped at Vref, max code
        for fault, expect in ((AdcFault("open"), 0), (AdcFault("short_supply"), 4095),
                              (AdcFault("stuck", 1.25), 1024), (AdcFault("offset", 0.5), 2458)):
            ecu.analog_in[0] = 2.5
            ecu.adc_faults[0] = fault
            run(ecu, 1)
            assert ecu.pwm(1) == expect, fault


@needs_cc
def test_noise_is_seeded_and_reproducible(tmp_path):
    src = "void app_init(void) {}\nvoid task_1ms(void) { Pwm_SetDutyCycle(1u, Adc_ReadChannel(0u)); Wdg_Trigger(); }"
    path = lib(tmp_path, src)

    def sample(seed):
        out = []
        with VirtualEcu(path, CM, seed=seed) as ecu:
            ecu.analog_in[0], ecu.adc_noise_v[0] = 2.5, 0.01
            for _ in range(20):
                ecu.tick()
                out.append(ecu.pwm(1))
        return out
    assert sample(3) == sample(3) and sample(3) != sample(4)


@needs_cc
def test_missing_app_init(tmp_path):
    with pytest.raises(RuntimeError, match="app_init"):
        VirtualEcu(lib(tmp_path, "void task_1ms(void) {}"), CM)
