# Development Progress Record

## Phase 1 — Application framework, database, learning engine — **DONE (2026-10-30)**

| Item | Status | Evidence |
|---|---|---|
| Settings / data dir (`ECSIM_HOME`) | built | `test_settings_roundtrip` |
| Event bus (isolated subscribers) | built | `test_event_bus_*` |
| Service registry + `bootstrap()` composition root | built | used by all tests |
| Toolchain probe (gcc, cmake, gdb, cppcheck, scilab, omc, candump) | built | `test_toolchain_probe_reports_all_tools` |
| SQLite schema 0001 + idempotent migrator, FK on, WAL | built | `test_migrations_idempotent`, `test_foreign_keys_enforced` |
| Content packs: YAML loader, validation, hashing, DB sync preserving evidence | built | `test_content.py`, `test_content_resync_preserves_evidence` |
| Graders: numeric (tolerance, hex, units, directional feedback), choice, C function (strict gcc, timeout, crash detection) | built | `test_graders.py` |
| Harness-strength checks (reference passes / starter fails / mutants caught) | built | `test_c_reference_passes`, `test_c_mutants_compile_but_fail` |
| Hint ladder, solution gate, independence rule, AI-assisted flag | built | `test_engine.py` |
| Competency scoring (weights, 4 levels, 80 % rule, practical gate, track gaps) | built | `test_competency.py` |
| Backup / restore with integrity check | built | `test_backup_restore_roundtrip` |
| CLI (init, tracks, list, show, submit, hint, solution, status, doctor, backup, restore) | built | `test_cli.py` |
| GUI shell: 14 screens; Dashboard, Learning Academy, Competency built; 11 labelled with their phase | built | `test_gui_smoke.py` |
| Starter content: A01, A02, B01 (8 lessons, 12 exercises, 4 compiled-C exercises) | built | content tests |

**Test totals:** 73 passed on Linux (Python 3.13, PySide6 6.12, GCC 13.3) and in CI
on `ubuntu-latest` and `windows-latest` (Python 3.11, GitHub Actions).

**Windows fix (2026-10-29):** the first Windows CI run found that text I/O used the
locale encoding (cp1252). That broke two content tests and would have crashed C
grading for learner code with non-ASCII comments. All text I/O is now explicitly
UTF-8, and `test_portability.py` guards it on every OS.

### Known limitations (Phase 1)
- Lesson math is written in LaTeX (`$$…$$`). Qt's Markdown renderer shows it as raw text.
  MathJax-free rendering is planned for Phase 3.
- C grading runs the learner's own code locally with a timeout. That's appropriate for a
  single-user offline tool, but it isn't a sandbox.
- Content covers only the competencies EMB, CTL, VEH, COM and FUS at beginner/intermediate. Other
  levels show "no content yet" and cannot be achieved, by design.

## Phase 2 plan (as written before implementation)
1. `ecsim/ecu/`: deterministic virtual-time scheduler (1 ms base tick), task table with
   priorities, a per-task WCET budget model, ISR injection, watchdog with window, reset causes.
2. Peripheral models: ADC (resolution, Vref, noise, fault states), PWM (resolution, frequency),
   GPIO, and a DTC memory with debounce counters.
3. C application ABI: `app_init()`, `task_1ms()`, `task_10ms()`, `isr_*()`, built as a shared library
   and driven through ctypes.
4. Assignment A-P1 (motor-control firmware): periodic tasks, sensor input, PWM out, fault
   handling, watchdog supervision. Graded on the virtual ECU.
5. Migration 0006_ecu, the Virtual ECU Lab screen, and tests.

## Phase 2 — Embedded C academy + virtual ECU — **DONE on branch (2026-10-29)**

| Item | Status | Evidence |
|---|---|---|
| C HAL ABI (`ecu_hal.h`, `hal.c`) with layout check against ctypes mirror | built | `test_vecu.py` (every test loads it) |
| Shared-library build with `-Werror`, HAL-redefinition guard, indicative size/stack report | built | `test_build_*` |
| Target profiles: Cortex-M4 generic, AURIX TC3xx-style (windowed safety WDT; multi-core declared as planned) | built | `test_profiles`, `test_windowed_watchdog` |
| Preemptive fixed-priority timing model, CPU load, activation limit 1, deadline misses | built | `test_cpu_load_and_task_rates`, `test_overrun_drops_every_second_activation` |
| Watchdog: timeout + window; reset reasons; fresh RAM after reset; DEM survives reset | built | `test_watchdog_*`, `test_reset_restarts_static_variables_but_keeps_dem` |
| ADC model: quantisation, seeded noise, offset/stuck/open/short faults | built | `test_adc_conversion_and_faults`, `test_noise_is_seeded_and_reproducible` |
| DC motor plant (exact discretisation) | built | `test_dc_motor_matches_first_order_model` |
| 4 closed-loop scenarios with fault injection; deterministic | built | `test_scenarios_are_deterministic` |
| Process isolation for crashes and infinite loops | built | `test_crashing_application_is_isolated`, `test_hanging_application_times_out` |
| `ecu_app` grader; reference passes, starter fails, 6 mutants caught | built | `test_ecu.py` |
| Migration 0002 (ecu_app kind, `ecu_run` log), in-place upgrade from Phase 1 | built | `test_phase1_database_upgrades_in_place` |
| Content: A03 scheduling, A04 fault handling (C debounce), A05 practical on both targets | built | content + grader tests |
| Virtual ECU Lab screen (build/run, plots, checks, DEM/reset log, CSV export) | built | `test_ecu_lab_screen` |
| CLI `ecsim ecu profiles`, `ecsim ecu run … --csv` | built | `test_cli_ecu` |

**Test totals:** 111 passed on Linux and in CI on `ubuntu-latest` and `windows-latest`.

**Windows fixes found by CI (2026-10-29):** unique DLL copy per load (Windows locks loaded
DLLs); no error-reporting dialog in the runner child; UTF-8 CLI output; access violations
in learner code (raised as `OSError` by ctypes on Windows) reported as located crashes,
e.g. `task_10ms() crashed at t=20 ms`. CI now publishes failing tests as annotations.

### Known limitations (Phase 2)
- Execution time is *modelled* per task, not measured from the learner's instructions.
  Instruction-accurate timing needs an instruction-set simulator (e.g. QEMU Cortex-M),
  evaluated for Phase 12 (PIL approximation).
- Interrupts are not simulated as asynchronous preemption inside a task; ISR/task data
  races get dedicated exercises later rather than a fake model.
- TC3xx-style profile runs the application on one core. Multi-core execution,
  per-core watchdogs, lockstep and memory protection are declared as planned.
- CAN is not part of the virtual ECU yet (Phase 6).
- Spec items still open in Dept A: C++ for embedded, RTOS mutex/semaphore labs,
  DMA, bootloader/firmware update. They are scheduled with Phases 6 and 11, where
  CAN and UDS make bootloader flashing meaningful.


## Priority change (2026-10-29)
Model-based development (MATLAB/Simulink) is now the core skill, and C is supporting. The roadmap
was reordered (see ROADMAP.md): Phases 3–7 are MBD. Every MBD exercise runs on **real
Simulink when installed** and on the **ECSim engine** otherwise.

## Phase 3 — MBD core: model engine + MIL + Simulink backend — **DONE on branch (2026-10-29)**

| Item | Status | Evidence |
|---|---|---|
| Block library (19 types) with Simulink semantics and Simulink equivalents | built | `test_block_semantics`, `test_integrators_*`, `test_transfer_function_and_rate_limiter` |
| Compiler: feedthrough-aware sorted order, algebraic-loop detection with path | built | `test_sorted_order_*`, `test_algebraic_loop_reports_path` |
| Sample times: inheritance, multiples of base step, multi-rate hold, rate-transition warning | built | `test_multirate_*`, `test_inherited_sample_time_*` |
| Virtual subsystems (nested), actionable compile errors | built | `test_nested_subsystems_flatten`, `test_compile_errors_are_actionable` |
| MIL harness: open-loop test vectors, closed loop with exact-ZOH DC motor, 4 check kinds | built | `test_discrete_motor_is_exact_zoh_of_plant`, `test_check_kinds` |
| `model_mil` grader, backend auto-selection, interface/solver contract | built | `test_model_mil.py` |
| Simulink backend (MATLAB Engine): load, ports, solver check, open-loop `sim`, generated closed-loop harness | **built, CI-tested with fakes; real-MATLAB run pending** | `test_engine_session_commands`, `test_simulink_backend_matches_ecsim_backend` |
| Content B02: Simulink semantics, MIL, PI model practical (8 requirement checks, 4 mutants) | built | grader + parity tests |
| GUI: model exercises in Academy with "Use Simulink model…"; CLI `model check`, `submit --file x.slx` | built | `test_model_exercise_in_academy`, `test_cli_model_check_and_slx_submit` |

**Test totals:** 172 passed on Linux and in CI on `ubuntu-latest` and `windows-latest`.

### Verification still needed on a machine with MATLAB
1. `pip install matlabengine` (version matching the MATLAB release), then `ecsim doctor` shows `matlab_engine` available.
2. Build `speed_pi.slx` per B02-E4 (In1 `speed_meas_rpm`, Out1 `voltage_cmd_V`, Fixed-step discrete 0.01).
3. `ecsim submit B02-E4 --file speed_pi.slx`. Expected: 8/8 checks, with results matching the ECSim reference within rounding.
Until this is done, the Simulink backend is labelled unverified.

### Known limitations (Phase 3)
- Learners without MATLAB author models as JSON until the Phase 4 editor lands.
- Scalar doubles only; no buses, vectors or fixed-point yet.
- Closed-loop plant library contains the DC motor only.

## Phase 4 — Control Model Studio — **DONE (2026-10-29)**

| Item | Evidence |
|---|---|
| Qt-free `ModelDocument`: add/remove/rename/move/connect/disconnect, params, sample times, solver step, full undo/redo, dangling-line pruning in the same undo step, port renumbering | `test_model_document.py` |
| Create subsystem from selection (behaviour-preserving), edit inside subsystems | `test_group_into_subsystem_preserves_behaviour`, `test_subsystem_navigation_and_edit` |
| Canvas: palette drag/double-click, wiring by mouse drag, move by mouse (undoable), delete, zoom, feedback-wire routing | `test_wire_by_mouse_drag`, `test_move_block_by_mouse_is_undoable` |
| Properties panel with validation; Simulink library path shown per block | `test_properties_panel_edits_and_validates` |
| Simulate (open loop / closed loop with DC motor) with plots | `test_simulate_open_and_closed_loop` |
| Grade directly from the Studio; "Open in Model Studio" from the Academy | `test_grade_from_studio`, `test_open_in_studio_from_academy` |
| `.slx` import: R2012b+ inline and R2019+ split-system layouts, Simulink defaults, fan-out branches, nested subsystems, workspace variables from `.m` | `test_slx_interop.py` |
| Export: MATLAB build script (`new_system`/`add_block`/`add_line`) | `test_export_build_script` |

Bugs found and fixed while testing: the exporter's parameter map was evaluated for every block type;
`.m` files with several statements per line failed to parse; and the properties panel dropped
later edits when one Apply changed several fields.

**Verification note:** `.slx` import is tested against files produced in Simulink's documented XML
layout by a test writer, not against files saved by MATLAB. Please import a few of your own models.

## Phase 5 — State machines (Stateflow-style) — **DONE (2026-10-29)**

| Item | Evidence |
|---|---|
| `Chart` block: hierarchy, default children, entry/during/exit, guards/actions, priorities, outer-first checking, one transition per step | `test_chart.py` (action order checked digit by digit) |
| Temporal logic `after(n, tick)` / `after(t, sec)`, counters reset on (re-)entry | `test_temporal_logic_counts_from_entry_and_resets_on_reentry` |
| Restricted expression/action language (parsed, never `eval`'d), actionable validation | `test_expression_language`, `test_validation_messages` |
| SCXML export/import with behaviour-preserving round trip | `test_scxml_round_trip_preserves_behaviour` |
| Chart editor in the Studio: definition with live validation + state diagram | `test_chart_editing_in_studio` |
| Content B03: chart semantics, mode/fault patterns, motor mode manager practical (16 checks, 4 mutants) | `test_content_models.py` (applies to every model exercise, both backends) |
| `.slx` import reports Stateflow charts clearly; script export notes charts | `slx.py` |

Not yet: junctions, events, parallel states, `duration()`, and graphical state drawing (the diagram is
generated from the definition). Real Stateflow charts are graded directly through the Simulink backend.
