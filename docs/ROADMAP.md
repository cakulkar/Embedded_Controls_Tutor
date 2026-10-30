# Development Roadmap

Each phase ships a working, tested feature before the next begins.

**Priority change (2026-10-29):** model-based development (MATLAB/Simulink-style) is
the core skill. C is supporting. Phases 3–7 are model-based development (MBD). Every MBD exercise works
with **real Simulink when installed** (graded through the MATLAB Engine for Python) and
with **ECSim's own engine** otherwise. The exercises are the same either way.

| Phase | Deliverable | Acceptance criteria (summary) | Status |
|---|---|---|---|
| 1 | Application framework, database, learning engine | Offline boot, migrations, content packs, graders, hint ladder, solution gate, competency scoring, GUI shell, CLI | done |
| 2 | Embedded C academy + virtual ECU | Virtual ECU (timing model, watchdog, resets, DEM, ADC faults), DC motor plant, firmware practical on Cortex-M and TC3xx-style targets | done |
| 3 | **MBD core: model engine + MIL + Simulink backend** | Model format with Simulink semantics (sample times, fixed-step discrete solver, subsystems, LUTs, algebraic-loop detection); requirements-based test-vector MIL; closed-loop MIL with plants; `model_mil` grader for ECSim models **and** `.slx` via MATLAB Engine (fake-engine tested, verified on a MATLAB machine) | in progress |
| 4 | **Control Model Studio (graphical editor)** | Drag-and-drop canvas on the Phase 3 engine, subsystems, masks/parameters, signal plots, undo/redo, simple `.slx` import | planned |
| 5 | **State machines (Stateflow-style)** | Hierarchical charts, entry/during/exit, guards/actions, temporal logic, mode and fault logic exercises, SCXML import/export | planned |
| 6 | **Code generation + SIL / back-to-back** | C codegen from supported blocks/charts, fixed-point types, MIL vs SIL B2B with tolerances, generated code on the virtual ECU, Embedded Coder/TargetLink concept bridge | planned |
| 7 | **Modeling guidelines + verification** | MAB/JMAAB-style rule checks, model review workflow, requirements manager with traceability, model/decision coverage | planned |
| 8 | CAN & diagnostics lab | DBC, J1939, UDS, bus faults; CAN on the virtual ECU | planned |
| 9 | BMS & electric powertrain | Battery/inverter plants, SOC/SOH, balancing, precharge, all model-based | planned |
| 10 | Engine, transmission & driveline | Engine/AT/clutch plants, shift logic, driveline oscillation | planned |
| 11 | Body controls & ADAS | BCM state machines, ACC/AEB/LKA | planned |
| 12 | Agricultural & construction | Hydraulic plants, PTO, hitch, load sensing, anti-stall, ISOBUS concepts | planned |
| 13 | AUTOSAR, safety, cybersecurity | SWC/RTE modeling (incl. model-to-AUTOSAR concepts), HARA/FMEA/FTA, ISO 25119/19014 | planned |
| 14 | Software HIL, calibration, validation | Lock-step vECU + plant, A2L/cal datasets, test automation | planned |
| 15 | AI mentor, job simulation, interviews, capstones | Local-LLM mentor, virtual company, interview engine, 5 capstones, certification | planned |

Embedded C items not yet covered (C++, RTOS sync primitives, DMA, bootloader) move to Phases 8 and 13, where CAN/UDS and AUTOSAR give them context.
