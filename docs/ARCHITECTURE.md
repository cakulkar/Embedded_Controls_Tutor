# ECSim — Embedded Controls Engineer Job Simulator
## System Architecture (v0.1, Phase 1 baseline)

> Status legend used throughout this repo: **[BUILT]** implemented and tested,
> **[PLANNED Pn]** designed but not implemented, scheduled for roadmap phase *n*.
> Nothing is marked BUILT unless it has passing automated tests.

---

## 1. Goals and non-goals

**Goal.** A desktop-first, fully offline engineering simulator in which a learner
performs the work of an embedded controls engineer: requirements → math design →
executable model → embedded C → MIL/SIL/software-HIL → calibration → release,
across automotive, agricultural and construction domains.

**Non-goals / honesty constraints (enforced in UI copy and docs).**
- Completion does not imply employability or expertise. Competency is evidenced
  only by graded, independent work (see §8).
- Python / Xcos / OpenModelica / the built-in editor are *educational substitutes*
  for MATLAB/Simulink/Stateflow/Embedded Coder — not equivalents (see `TOOL_SUBSTITUTES.md`).
- Software HIL is not physical HIL (dSPACE, NI). It is labelled "simulated HIL" everywhere.
- Exercises that *cover* ISO 26262, ISO 25119, ISO 19014 or ISO/SAE 21434 topics never
  claim compliance. The platform does not reproduce standard text; it references clause
  topics and directs learners to the licensed standard.

## 2. Layered architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Presentation (PySide6)                                                   │
│  Dashboard · Academy · Job Simulator · Control Model Studio · Virtual ECU│
│  Plant Sim · CAN Analyzer · Requirements · Test Center · Calibration     │
│  Mentor · Interview · Competency · Portfolio        + CLI (headless)     │
├──────────────────────────────────────────────────────────────────────────┤
│ Application services  (ecsim.app.services.ServiceRegistry)               │
│  LearningService · CompetencyService · JobSimService* · ProjectService*  │
│  ModelService* · SimulationService* · EcuLabService* · CanService*       │
│  RequirementsService* · TestService* · CalibrationService* · MentorSvc*  │
├──────────────────────────────────────────────────────────────────────────┤
│ Engines (pure Python / C, no Qt dependency, unit-testable)               │
│  learning.engine · learning.graders · competency scoring      [BUILT]    │
│  model graph + solver + C codegen                             [P4/P5]    │
│  virtual ECU: scheduler, CPU-time model, periph, watchdog     [P2]       │
│  plant library (ODE / FMU via OpenModelica)                   [P3/P7-10] │
│  CAN bus sim, DBC/A2L/ARXML I/O, UDS server                   [P6]       │
├──────────────────────────────────────────────────────────────────────────┤
│ Persistence: SQLite (WAL) + versioned SQL migrations          [BUILT]    │
│ Content packs: YAML + Markdown + C harnesses (read-only, hashed) [BUILT] │
│ Project workspace: Git repo per engineering project           [P5]       │
├──────────────────────────────────────────────────────────────────────────┤
│ External toolchains (detected, optional): gcc/clang, cmake, gdb,         │
│ cppcheck, Scilab/Xcos, OpenModelica (omc), SocketCAN, local LLM runtime  │
└──────────────────────────────────────────────────────────────────────────┘
  * = PLANNED
```

**Rules.**
1. Engines never import Qt. GUI is a thin client over services → every feature is
   testable headless and reachable from the CLI.
2. Services communicate through the `EventBus` (publish/subscribe, synchronous,
   typed topics) so departments evolve independently (`attempt.graded`,
   `lesson.completed`, later `model.simulated`, `ecu.fault`, `defect.raised`).
3. Each engineering department is a **content pack + optional plug-in** implementing
   `DepartmentPlugin` (plants, scenario generators, graders). Core never hard-codes
   department logic.
4. Every external tool is wrapped by a `Toolchain` probe that reports *available /
   missing / licence-required*; features degrade with an explicit message.

## 3. Project folder structure

```
ecsim/
├── pyproject.toml              packaging, entry points (ecsim, ecsim-gui)
├── README.md                   install / run / troubleshoot / extend
├── docs/
│   ├── ARCHITECTURE.md         this file
│   ├── CURRICULUM.md           departments ↔ competencies ↔ tracks ↔ capstones
│   ├── TOOL_SUBSTITUTES.md     industry tool vs training substitute + gaps
│   ├── ROADMAP.md              15 phases with acceptance criteria
│   └── PROGRESS.md             development progress record (truthful)
├── src/ecsim/
│   ├── app/                    config, paths, event bus, service registry, toolchain probe
│   ├── db/                     connection, migrator, migrations/*.sql, repositories
│   ├── learning/               content loader, engine, competency, graders/
│   ├── gui/                    main window + screens/
│   ├── content/                curriculum.yaml + modules/*.yaml (+ C harnesses inline)
│   ├── cli.py                  headless interface
│   ├── model/        [P4]      block library, graph, solver, codegen
│   ├── ecu/          [P2]      virtual ECU
│   ├── plants/       [P3+]     plant models
│   ├── comms/        [P6]      CAN/J1939/ISOBUS/UDS/DBC/A2L
│   ├── reqs/         [P5]      requirements + ReqIF + traceability
│   ├── testing/      [P5/12]   MIL/SIL/HIL orchestration, coverage
│   ├── jobsim/       [P14]     company, roles, tasks, injected incidents
│   └── mentor/       [P13]     local LLM adapter, RAG over local docs, rubric reviewers
└── tests/  unit/ integration/ gui/
```

## 4. Software modules and interfaces (Phase 1 contracts)

| Module | Public interface | Notes |
|---|---|---|
| `app.config` | `Settings.load(home) -> Settings` | `ECSIM_HOME` env overrides `~/.ecsim` |
| `app.events` | `EventBus.subscribe(topic, fn)`, `.publish(topic, **payload)` | exceptions in a handler are logged, never swallow others |
| `app.services` | `ServiceRegistry.register(name, obj)`, `.get(name)` | composition root in `bootstrap()` |
| `app.toolchain` | `probe() -> dict[str, ToolStatus]` | gcc, cmake, cppcheck, scilab, omc, candump |
| `db.connection` | `open_db(path) -> sqlite3.Connection` | WAL, FK on, row factory |
| `db.migrate` | `migrate(conn) -> list[int]` | ordered `NNNN_name.sql`, idempotent |
| `learning.content` | `load_catalog(dir) -> Catalog` ; `sync_catalog(conn, cat)` | content validated on load, SHA-256 hash per exercise |
| `learning.graders` | `Grader.grade(spec, submission) -> GradeResult` | `numeric`, `choice`, `c_function` |
| `learning.engine` | `LearningEngine.submit / reveal_hint / reveal_solution / complete_lesson` | enforces hint ladder & solution gate |
| `learning.competency` | `CompetencyService.profile(learner) -> CompetencyProfile` | weighted score, per-level status, track gaps |

Planned interfaces (signatures fixed now so later phases plug in):

```python
class DepartmentPlugin(Protocol):          # P2+
    code: str
    def plants(self) -> list[PlantSpec]: ...
    def scenarios(self) -> list[ScenarioSpec]: ...   # injected faults/incidents
    def graders(self) -> dict[str, Grader]: ...

class Block(Protocol):                     # P4
    inputs: list[Port]; outputs: list[Port]; params: dict; sample_time: float | None
    def output(self, t, x, u) -> y: ...
    def update(self, t, x, u) -> x_next: ...          # discrete
    def derivative(self, t, x, u) -> dx: ...          # continuous
    def emit_c(self, ctx: CodegenCtx) -> CFragment: ... # None if not codegen-supported
```

## 5. Database schema

SQLite, one file per learner workspace (`<home>/ecsim.db`), migrations in
`src/ecsim/db/migrations`. Content tables are *rebuilt from content packs*;
learner tables are *append-mostly evidence* and never deleted by sync.

### 5.1 Phase 1 tables [BUILT — migration 0001]

| Table | Key columns | Purpose |
|---|---|---|
| `schema_migrations` | version PK, applied_at | migration ledger |
| `competency` | code PK, title, weight | 9 weighted competencies (sum = 1.0, checked) |
| `department` | code PK, title, roadmap_phase, description | A–M + N (Industry Readiness Bridge) + P (Career) |
| `module` | id PK, department_code FK, title, ordinal, version | |
| `lesson` | id PK, module_id FK, title, body_md, ordinal | |
| `exercise` | id PK, module_id FK, kind, title, prompt_md, competency_code FK, level, points, practical, spec_json, hints_json, solution_md, content_hash | `level` ∈ beginner/intermediate/advanced/expert |
| `career_track` / `track_requirement` | track code; (track, competency, min_level) | 11 job roles |
| `learner` | id PK, name, track_code FK, created_at | |
| `lesson_progress` | (learner, lesson) PK, completed_at | |
| `attempt` | id PK, learner FK, exercise FK, content_hash, submitted_at, submission, passed, score, feedback, hints_used, solution_seen, ai_assisted, independent | evidence record; `independent` frozen at submit time |
| `hint_reveal` | (learner, exercise, level) PK | |
| `solution_reveal` | (learner, exercise) PK, explicit_request, revealed_at | |

### 5.2 Later migrations [PLANNED]

| Migration | Phase | Tables |
|---|---|---|
| 0002_projects | P5 | `project`(V-model workspace, git path), `lifecycle_stage`, `deliverable`, `review` |
| 0003_requirements | P5 | `requirement`(level: customer/system/functional/software, text, acceptance_criteria, status, version), `trace_link`(src, dst, kind: derives/satisfies/verifies), `change_request` |
| 0004_models | P4 | `model`(json graph, hash), `sim_run`(model hash, solver cfg, seed, result path) |
| 0005_tests | P5/P12 | `test_case`, `test_run`, `test_result`, `coverage`, `defect` |
| 0006_ecu | P2 | `ecu_config`, `fault_injection`, `dtc_event` |
| 0007_calibration | P12 | `cal_parameter`, `cal_dataset`, `cal_change` |
| 0008_jobsim | P14 | `company`, `persona`, `assignment`, `incident`, `assignment_event` |
| 0009_mentor | P13 | `mentor_session`, `review_finding`(class: correct/poor-design/unsafe/incorrect, reference) |
| 0010_interview | P14 | `interview`, `interview_turn`, `rubric_score` |

## 6. Curriculum and competency mapping
See `CURRICULUM.md`. Content packs carry the mapping per exercise
(`competency`, `level`, `practical`), so scoring is data-driven.

## 7. Simulation engine architecture [PLANNED P3–P5, P12]

- **Model graph.** Typed DAG of blocks + explicit state; algebraic loops rejected
  at compile time unless broken by a delay/integrator.
- **Rate groups.** Each block has an inherited or explicit sample time; the scheduler
  builds rate groups (base rate = GCD) like a multi-rate fixed-step solver.
  Continuous plants run with `scipy.integrate.solve_ivp` (RK45/LSODA) between
  controller ticks, or a fixed-step RK4 for deterministic SIL comparisons.
- **MIL.** Controller graph (float64) ↔ plant.
- **SIL.** Generated C (from P4 codegen) compiled to a shared library with a
  fixed ABI `init()/step(in*, out*)`, loaded via `ctypes`, driven by the same harness;
  MIL vs SIL compared with per-signal tolerance and back-to-back reports.
- **PIL** is only approximated (instruction-set simulator e.g. QEMU Cortex-M,
  optional); labelled as such.
- **Software HIL.** Virtual ECU (P2) runs the C application on a simulated scheduler
  with a CPU-time budget model, ADC/PWM/CAN peripherals, watchdog, reset; plant
  runs in lock-step. Fault injector manipulates the signal path (offset, stuck,
  noise, dropout, delay, bus-off, reset).
- **Reproducibility.** Every run stores model hash, solver settings, seed, toolchain
  versions.

## 8. Graphical model editor architecture [PLANNED P4]

- `QGraphicsScene`-based canvas; the **document is the graph JSON**, the canvas is a view.
- Undo/redo via `QUndoStack` commands over the graph model.
- Subsystems = nested graphs with port blocks; masks = parameter dialogs.
- State machines: hierarchical states, transitions with guard/action expressions
  (restricted expression language parsed to AST — no `eval`), SCXML import/export.
- Lookup tables 1-D/2-D with breakpoint editors; interpolation/extrapolation modes.
- Execution goes through the same engine as §7 — the editor never "simulates"
  anything itself (spec requirement: diagrams must execute).
- Codegen: per-block `emit_c`, MISRA-oriented style (fixed-width types, no dynamic
  memory, single exit), unsupported blocks reported, generated code compiled and
  back-to-back tested automatically.

## 9. AI mentor and assessment architecture

### 9.1 Assessment [BUILT core]
- **Graders** are deterministic: numeric (abs/rel tolerance, optional unit), choice,
  C function (compiled with `gcc -std=c99 -Wall -Wextra -Werror -pedantic`, run
  against a hidden harness with a timeout). Later: Python function, model
  (simulate & compare against acceptance criteria), report rubric.
- **Hint ladder.** Hints are revealed strictly in order. Hint *k* (k ≥ 2) requires
  at least *k−1* submitted attempts.
- **Solution gate.** Solution requires ≥1 attempt, unless the learner explicitly
  requests it (`force`) — recorded as `explicit_request=1`.
- **Independence.** An attempt is *independent* iff: solution not seen before it,
  hints used ≤ the exercise's `max_independent_hints` (default 1), and not flagged
  `ai_assisted`. Only independent passes count toward competency.
- **Levels.** Per competency, a level is *achieved* when ≥ 80 % of that level's
  points are earned by independent passes **and** the previous level is achieved.
  Advanced/expert additionally require ≥1 *practical* exercise passed at that level.
- **Weighted score** = Σ weight_c × (levels achieved_c / 4), reported 0–100.
  It is a progress indicator, explicitly not a certificate.

### 9.2 AI mentor [PLANNED P13]
- Local runtime adapter (llama.cpp server / Ollama-compatible HTTP on localhost) —
  no cloud call; disabled cleanly if absent.
- Retrieval over the *local* documentation pack (content packs, open docs that can be
  redistributed). Answers must cite a retrieved local source or say "no reference
  available"; standards are cited by title/clause topic only.
- Socratic policy layer *outside* the LLM: the engine decides whether the learner may
  see a solution (same gate as §9.1); the mentor cannot bypass it.
- Review output schema: findings classified **correct / works-but-poor-design /
  unsafe / incorrect**, each with evidence and reference. Deterministic checkers
  (compiler warnings, cppcheck, test results, simulation metrics) run first and are
  fed to the LLM so it reviews evidence, not vibes.
- Mentor use within an attempt sets `ai_assisted=1` → not independent evidence.

## 10. Offline, licensing and data
- No network at runtime. No accounts. All data under `ECSIM_HOME`.
- Backup/restore: consistent SQLite online backup to a single file [BUILT].
- Optional external tools and licences are listed in `TOOL_SUBSTITUTES.md`; the
  toolchain probe reports them in the Dashboard.
- PySide6 is LGPLv3 (dynamically linked) — compatible with distribution.

## 11. Quality
- pytest unit + integration + GUI smoke (Qt offscreen). Every C exercise's
  reference solution is compiled and graded in CI; a deliberately wrong stub must fail.
- Content validation is a test: weights sum to 1, every exercise maps to a known
  competency/level, every hint list non-empty, IDs unique.
