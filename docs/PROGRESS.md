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

**Test totals:** 71 passed (Python 3.13, PySide6 6.12, GCC 13.3, Linux).
Not yet verified on Windows. Expected to work, since the code has no POSIX-only calls,
but it's untested there.

### Known limitations (Phase 1)
- Lesson math is written in LaTeX (`$$…$$`). Qt's Markdown renderer shows it as raw text.
  MathJax-free rendering is planned for Phase 3.
- C grading runs the learner's own code locally with a timeout. That's appropriate for a
  single-user offline tool, but it isn't a sandbox.
- Content covers only the competencies EMB, CTL, VEH, COM and FUS at beginner/intermediate. Other
  levels show "no content yet" and cannot be achieved, by design.

## Next: Phase 2 — Embedded C academy + virtual ECU
1. `ecsim/ecu/`: deterministic virtual-time scheduler (1 ms base tick), task table with
   priorities, a per-task WCET budget model, ISR injection, watchdog with window, reset causes.
2. Peripheral models: ADC (resolution, Vref, noise, fault states), PWM (resolution, frequency),
   GPIO, and a DTC memory with debounce counters.
3. C application ABI: `app_init()`, `task_1ms()`, `task_10ms()`, `isr_*()`, built as a shared library
   and driven through ctypes.
4. Assignment A-P1 (motor-control firmware): periodic tasks, sensor input, PWM out, fault
   handling, watchdog supervision. Graded on the virtual ECU.
5. Migration 0006_ecu, the Virtual ECU Lab screen, and tests.
