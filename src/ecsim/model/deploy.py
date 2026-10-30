"""Deploy a generated speed-controller model to the virtual ECU (model → C → firmware → vECU).

The wrapper plays the role of the hand-written integration code around generated code in a
production ECU: read and scale the sensor (ADC → rpm), call the model step from the 10 ms task,
scale the output (V → PWM duty), service the watchdog.

Contract: model step 0.01 s, inport 1 = measured speed [rpm], outport 1 = voltage command [V].
"""
from __future__ import annotations

from ecsim.model.blocks import ModelError
from ecsim.model.codegen import GeneratedCode, generate_c


def motor_firmware(model: str | dict, dtype: str = "single") -> tuple[str, GeneratedCode]:
    g = generate_c(model, dtype)
    from ecsim.model.core import compile_model
    c = compile_model(model)
    if abs(c.step - 0.01) > 1e-12:
        raise ModelError(f"the model runs at {c.step:g} s; the motor ECU calls it from task_10ms (0.01 s)")
    if len(g.inports) != 1 or len(g.outports) < 1:
        raise ModelError("controller must have 1 inport (speed rpm) and at least 1 outport (voltage V)")
    m, u, y = g.name, g.inports[0], g.outports[0]
    header = "\n".join(ln for ln in g.header.splitlines() if not ln.startswith(("#ifndef", "#define", "#endif")))
    body = g.source.replace(f'#include "{m}.h"', "")
    wrapper = f"""
/* ---- integration code (hand-written in a real project) -------------------------------- */
void app_init(void)
{{
    Pwm_SetDutyCycle(0u, 0u);
    {m}_initialize();
}}

void task_10ms(void)
{{
    float volts = ((float)Adc_ReadChannel(0u) * Adc_GetReferenceVoltage()) / (float)(1UL << Adc_GetResolutionBits());
    float cmd;
    uint16_t duty;
    {m}_U.{u} = (real_T)((volts - 0.5f) * (6000.0f / 4.0f));
    {m}_step();
    cmd = (float){m}_Y.{y};
    if (cmd < 0.0f) {{ cmd = 0.0f; }}
    if (cmd > 24.0f) {{ cmd = 24.0f; }}
    duty = (uint16_t)((cmd / 24.0f) * (float)PWM_DUTY_MAX);
    Pwm_SetDutyCycle(0u, duty);
    Wdg_Trigger();
}}
"""
    return header + "\n" + body + wrapper, g


def deploy_and_run(model: str | dict, profile: str = "CM4_GENERIC", scenarios=("motor_nominal", "motor_load_step"),
                   dtype: str = "single", compiler: str = "gcc"):
    """Build the firmware and run vECU scenarios (isolated process). Returns (build, {scenario: outcome})."""
    import tempfile
    from pathlib import Path
    from ecsim.ecu.build import build_app
    from ecsim.ecu.runner import run_isolated
    src, _ = motor_firmware(model, dtype)
    results = {}
    with tempfile.TemporaryDirectory(prefix="ecsim_deploy_", ignore_cleanup_errors=True) as tmp:
        b = build_app(src, Path(tmp), compiler)
        if b.ok:
            for sc in scenarios:
                results[sc] = run_isolated(b.lib_path, profile, sc)
    return b, results
