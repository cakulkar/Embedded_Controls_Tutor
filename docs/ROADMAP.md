# Development Roadmap

Each phase ships a working, tested feature before the next begins.

| Phase | Deliverable | Acceptance criteria (summary) |
|---|---|---|
| 1 | Application framework, database, learning engine | App boots offline; migrations idempotent; content pack validated & synced; numeric/choice/C graders; hint ladder & solution gate; independence rule; competency profile; backup/restore; GUI shell with 14 screens; CLI; automated tests green |
| 2 | Embedded C academy + virtual ECU | Dept A full pack; virtual ECU with periodic tasks, CPU-time budget, ADC/PWM/GPIO, watchdog, reset, DTC memory; motor-control firmware assignment graded on the virtual ECU |
| 3 | Control theory + Python/Xcos integration | Dept B pack; python-control labs graded on step-response metrics; Xcos model import/run when Scilab present |
| 4 | Graphical control-model editor | Canvas, required block library (spec table), subsystems, LUTs, state machines, sample times, C codegen for supported blocks |
| 5 | MIL/SIL engine + projects + requirements | MIL and SIL (compiled C via ctypes) back-to-back with tolerance report; V-model project workspace; requirements + traceability matrix |
| 6 | CAN & diagnostics lab | Bus simulator, DBC import/export via cantools, J1939 PGN/SPN, UDS subset server, logging/replay, bus-off |
| 7 | BMS & electric powertrain | Battery ECM plants, SOC/SOH, balancing, precharge FSM, motor/inverter plant, FOC concepts |
| 8 | Engine, transmission & driveline | Engine mean-value model, throttle, AT/clutch plants, shift logic, driveline oscillation |
| 9 | Body controls & ADAS | BCM state machines, sleep/wake; ACC/AEB/LKA with sensor abstractions |
| 10 | Agricultural & construction | Hydraulic pump/cylinder plants, PTO, hitch, sprayer, load sensing, anti-stall, ISOBUS concepts |
| 11 | AUTOSAR, safety, cybersecurity | SWC/RTE concept lab (ARXML read/write), HARA/FMEA/FTA workbooks, ISO 25119/19014 applicability module |
| 12 | Software HIL, calibration, validation, Industry Bridge | Lock-step vECU+plant with fault injection; A2L/cal datasets; coverage; Dept N |
| 13 | AI engineering mentor | Local LLM adapter, local RAG with citations, review classes, solution gate enforced outside LLM |
| 14 | Job simulation & interview engine | Virtual company, personas, assignments with injected incidents, follow-up-question interviews with rubric |
| 15 | Capstones & certification | 5 capstones, independent-work verification, practical exams |
