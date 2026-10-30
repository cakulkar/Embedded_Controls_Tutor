# Industry Tools vs Training Substitutes

These are **educational substitutes**. None is a drop-in replacement; the "not
reproduced" column is what the learner must learn on the job or in the Industry
Readiness Bridge (Dept N).

| Industry tool | Training substitute | What transfers | Not reproduced |
|---|---|---|---|
| MATLAB | Python (NumPy, SciPy, python-control), Scilab | numerics, LTI design, scripting habits | MATLAB language/toolboxes, licence-bound toolboxes |
| Simulink | ECSim Model Studio (P4) + Scilab/Xcos + OpenModelica | block-diagram semantics, sample times, subsystems, solvers | Simulink file formats, Model Advisor, Simulink Test/Coverage, vast block library |
| Stateflow | ECSim state-machine editor / SCXML (P4) | hierarchical states, guards, entry/exit | Stateflow semantics details (super-step, temporal logic operators) |
| Embedded Coder / TargetLink | ECSim per-block C codegen + back-to-back tests | code-from-model workflow, fixed-point thinking, B2B testing | AUTOSAR codegen, storage classes, certified-tool qualification kits |
| CANalyzer / CANoe | SocketCAN + can-utils + cantools + SavvyCAN + ECSim CAN Analyzer (P6) | traces, DBC decode, logging/replay, simulated nodes | CAPL, CANoe test modules, licensed interface hardware |
| CANape / ETAS INCA | ECSim Calibration Studio (P12): A2L parsing, XCP-on-sim concepts | measurement/calibration concepts, A2L structure, datasets | real XCP on hardware, INCA experiment environment, MDF tooling breadth |
| IBM DOORS / Polarion | ECSim Requirements Manager (P5) + ReqIF import/export | atomic requirements, attributes, baselines, trace links | enterprise workflows, permissions, DXL |
| Tessy | pytest + Unity/Ceedling (P2/P5) | unit-test design, boundary/equivalence, coverage | tool qualification, GUI test-case editor |
| dSPACE / NI VeriStand | ECSim software HIL (P12) | closed-loop test thinking, fault injection, test automation | real-time I/O hardware, electrical fault insertion, real timing |
| Polyspace | cppcheck, clang-tidy, GCC `-fanalyzer` | static-analysis findings triage | sound abstract interpretation (proof of absence of RTE) |
| Git + review | Git + local review workflow | same | — |

## Optional dependencies requiring separate licences
None are required. If a learner connects a licensed tool (e.g. MATLAB), it is
their responsibility; ECSim never bundles or requires one.
