# Curriculum, Competency and Career-Track Mapping

## 1. Competencies (weights from the product spec)

| Code | Competency | Weight |
|---|---|---|
| EMB | Embedded C and microcontrollers | 15 % |
| CTL | Control theory and algorithms | 15 % |
| MBD | Model-based development | 10 % |
| VEH | Vehicle systems knowledge | 15 % |
| COM | ECU architecture and communication | 10 % |
| ARC | AUTOSAR and software architecture | 10 % |
| FUS | Functional safety (incl. cybersecurity fundamentals) | 10 % |
| TST | Testing and validation | 10 % |
| DOC | Documentation and engineering reviews | 5 % |

Each competency has four assessment levels: **beginner, intermediate, advanced, expert**.
Advanced and expert require at least one *practical* (deliverable-producing) exercise.

## 2. Departments → primary competencies → roadmap phase

| Dept | Title | Primary | Secondary | Phase |
|---|---|---|---|---|
| A | Embedded systems foundations | EMB | TST | 2 |
| B | Control theory & model-based development | CTL, MBD | — | 3–4 |
| C | Driveline controls | VEH, CTL | FUS | 8 |
| D | Powertrain & engine controls | VEH, CTL | — | 7–8 |
| E | Battery management systems | VEH, CTL | FUS | 7 |
| F | ADAS & autonomous vehicle controls | VEH, CTL | FUS | 9 |
| G | Body controls | VEH, EMB | COM | 9 |
| H | Agricultural & construction equipment | VEH, CTL | COM, FUS | 10 |
| I | Communication & diagnostics | COM | TST | 6 |
| J | AUTOSAR & software architecture | ARC | COM | 11 |
| K | Functional safety & cybersecurity | FUS | DOC | 11 |
| L | Calibration, testing & validation | TST | CTL | 12 |
| M | Engineering tools & workflows | MBD, DOC | TST | 3–12 |
| N | Industry Readiness Bridge (Simulink/Stateflow/EC/CANape/INCA/DOORS/dSPACE concepts) | MBD | ARC, TST | 12 |
| P | Career preparation & interview simulator | DOC | all | 14 |

Phase 1 ships a **starter content pack** (A01, A02, B01) to exercise the learning
engine end-to-end with real graders. Full department content is delivered in the
phase listed — content is not bulk-generated ahead of its functional labs
(spec rule: no lessons without functional engineering exercises).

## 3. Career tracks → minimum competency levels

`b`=beginner `i`=intermediate `a`=advanced `e`=expert, `–` = no requirement.

| Track | EMB | CTL | MBD | VEH | COM | ARC | FUS | TST | DOC |
|---|---|---|---|---|---|---|---|---|---|
| Junior Embedded Controls Engineer | i | b | b | b | b | b | b | b | b |
| Embedded Software Engineer | a | b | b | b | i | i | b | i | b |
| Controls Algorithm Engineer | i | a | a | i | b | b | b | i | b |
| BMS Controls Engineer | i | a | i | a | i | b | i | i | b |
| Powertrain Controls Engineer | i | a | i | a | i | b | i | i | b |
| Driveline Controls Engineer | i | a | i | a | i | b | i | i | b |
| ADAS Controls Engineer | i | a | i | a | i | b | a | i | b |
| Agricultural Controls Engineer | i | i | i | a | a | b | i | i | b |
| Construction Equipment Controls Engineer | i | i | i | a | a | b | i | i | b |
| Senior Controls Engineer | a | e | a | a | a | i | a | a | a |
| Systems & Functional Safety Engineer | i | i | b | a | i | i | e | a | a |

## 4. Capstones (Phase 15) — each must produce the full V-model evidence set
1. Complete EV BMS · 2. Automatic transmission controller · 3. Agricultural tractor
control system (J1939 + ISOBUS) · 4. ADAS (ACC) · 5. Construction hydraulic controller.

## 5. Machinery-safety note
Off-highway content (Dept H/K) introduces ISO 25119 (agricultural/forestry tractors &
machinery) and ISO 19014 (earth-moving machinery) and explains why ISO 26262 is not
automatically applicable to such machines. The platform teaches concepts and
directs learners to the licensed standards; it does not reproduce their text.
