# ECSim — Embedded Controls Engineer Job Simulator

An offline desktop engineering simulator for learning to work as an embedded
controls engineer (automotive, agricultural, construction). It is **not** a
tutorial website: every lesson is backed by graded engineering exercises, and
competency is credited only for independent work.

> **Status: Phase 1 of 15.** The framework, database, learning engine, graders,
> competency scoring, CLI and GUI shell work and are tested. The other 11
> screens show which phase delivers them. See `docs/PROGRESS.md`.

## Install

Needs Python 3.11+ and, for C exercises, GCC or Clang on `PATH`.

```bash
cd ecsim
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"            # add ,controls for NumPy/SciPy/python-control (Phase 3)
```

Windows C compiler: install MSYS2 and then `pacman -S mingw-w64-ucrt-x86_64-gcc`,
or use LLVM/Clang. Add its `bin` folder to `PATH`, then run `ecsim doctor` to
confirm it is detected.

## Run

```bash
ecsim-gui                          # desktop app (first launch asks for your name and track)
ecsim init --name "Your Name" --track BMS_CTL
ecsim list                         # modules, lessons, exercises (★ = independent pass)
ecsim show A01-L1                  # read a lesson (marks it read)
ecsim show A01-E3                  # exercise prompt and starter code
ecsim submit A02-E2 --answer "156.3 kPa"
ecsim submit A01-E3 --file my_field.c
ecsim hint A01-E3                  # hints unlock in order, later ones after attempts
ecsim solution A01-E3              # needs at least one attempt (--force overrides, not independent)
ecsim status                       # competency profile and track gaps
ecsim backup ~/ecsim-backup.db  /  ecsim restore ~/ecsim-backup.db
ecsim doctor                       # optional toolchains: gcc, cmake, gdb, cppcheck, scilab, omc, candump
```

Data lives in `~/.ecsim` (override with `ECSIM_HOME` or `--home`). Nothing
leaves the machine.

## Test

```bash
pytest                             # 71 tests: unit, integration, GUI (Qt offscreen)
```

The suite compiles every C exercise's reference solution (it must pass), every
starter (it must fail), and every *mutant* (a plausible wrong solution that must
compile and then be caught by the hidden tests). This checks that the tests are
strong enough to grade with.

## Pedagogy rules enforced by the engine
* Hints are revealed in order. Hint *k* (k ≥ 2) unlocks only after *k−1* attempts.
* The solution is shown only after one or more attempts, unless you explicitly ask (recorded).
* A pass counts as **independent** only if you didn't view the solution, used at most
  `max_independent_hints` hints (default 1), and didn't declare AI/outside help.
* A competency level is reached at 80 % of that level's points from independent passes, with
  the previous level achieved. Advanced and expert also need a practical exercise.

## Extend: add content
Add `src/ecsim/content/modules/<ID>_<name>.yaml` (copy an existing module). Supported
exercise kinds are `numeric`, `choice` and `c_function`. A `c_function` exercise needs
`prototypes`, `starter`, `harness` (uses `CHECK`, `CHECK_EQ_U`, `CHECK_EQ_I`,
`CHECK_NEAR`, `ECSIM_DONE()`), `reference` and ideally `mutants`. Then run
`pytest`: content validation and the reference/mutant checks run automatically.

## Troubleshooting
| Symptom | Fix |
|---|---|
| "C compiler 'gcc' not found" | Install GCC/Clang and add it to PATH, or set `"c_compiler": "clang"` in `~/.ecsim/settings.json`. The attempt is not recorded as a failure. |
| GUI doesn't start on a headless Linux box | Use the CLI, or set `QT_QPA_PLATFORM=offscreen` for tests. |
| `-Werror` rejects your code | That's intended. Production ECU code must build cleanly. Read the warning and fix it. |
| Corrupt DB | `ecsim restore <backup>`. Restore runs an integrity check before replacing anything. |

## Documentation
`docs/ARCHITECTURE.md` · `docs/CURRICULUM.md` · `docs/TOOL_SUBSTITUTES.md` ·
`docs/ROADMAP.md` · `docs/PROGRESS.md`
