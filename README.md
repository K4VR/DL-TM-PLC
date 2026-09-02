# DL-TM-PLC

Studio 5000 import of the USS Fairfield Dual Line Temper Mill PLC program **TEMPER**, converted from a mid-1990s GE Logicmaster 90-30 printout to a ControlLogix **1756-L81E** project.

## Import file

Open this file in Studio 5000 Logix Designer **v32.11 or newer** (File → Open):

`import/TEMPER_1756-L81E.L5X`

A conversion log listing remaining review items is next to it:

`import/TEMPER_1756-L81E.report.txt`

The original Logicmaster printout is in `source/` (`TemperMillProgram.docx` and an extracted `TemperMillProgram.txt`).

## Tag rules

| GE reference | Logix tag | Type |
|---|---|---|
| `%I0005` | `I0005` | BOOL |
| `%Q0129` | `Q0129` | BOOL |
| `%M0717` | `M0717` | BOOL |
| `%T0001` | `T0001` | BOOL |
| `%S0001` / `%SA009` / `%SC009` | `S0001` / `SA009` / `SC009` | BOOL |
| `%R00631` | `R00631` | INT (16-bit) |
| `%AI0044` | `AI0044` | INT |
| `%AQ0041` | `AQ0041` | INT |

- The **% is stripped**. Ladder never uses `%`.
- A GE **nickname** becomes a Logix **alias** of that base tag. Logic uses the alias when one exists (`PRSLED` → `I0005`).
- Hyphens in nicknames become underscores (`S07-01A` → `S07_01A`).
- Tag descriptions come from the Logicmaster variable table.

## Program layout

- Controller: `TEMPER`, processor `1756-L81E`, product code 164, revision **32.11**.
- CPU in slot 0 of a 10-slot 1756 backplane. No I/O modules are configured; GE addresses are memory-image tags for later remapping onto 1756 I/O.
- Continuous task `MainTask` runs program `TEMPER`.
- `_MAIN` is Logix `MainRoutine`. It JSRs `SYSBITS` first (GE `%S` clocks and first-scan), then the original CALL order.
- Each GE block is a subroutine of the same name (`AGC`, `ESTOP`, `RUNPERM`, …).
- Every Logix rung comment starts with **`GE Rung N`** from the printout.

`SYSBITS` emulates:

- `%S0001` `FST_SCN` — true on the first scan only
- `%S0007` `ALW_ON` — always on
- `%S0004` / `%S0005` / `%S0006` — 100 ms / 1 s / 60 s pulses

`%S0008` `ALW_OFF` is left at 0.

## How to regenerate

```bash
python3 -m pip install -r requirements.txt
python3 -m tools.lm90_to_l5x source/TemperMillProgram.txt import/TEMPER_1756-L81E.L5X
python3 -m pytest tests/test_lm90_import.py -v
```

## Conversion limits (review before download)

- **No 1756 I/O map.** `%I` / `%Q` / `%AI` / `%AQ` are controller tags, not module aliases.
- **COMM_REQ, SVC_REQ, DO_IO** become `NOP()` and are listed in the report. Replace with MSG / GSV / IOT on the real chassis.
- GE **DINT** math uses companion `RxxxxD` tags; declared `%R` tags stay INT as specified.
- GE table MOVE between `%M`/`%Q` bits and `%R` words is emitted as `COP` and may need a BOOL array or BTD on the real controller.
- `%S` clocks are PLC pulses, not GE hardware timing.
- GE continuation rails (`<+>`) become `CONT_<block>_<rung>` bits linking split print rungs.
- Retentive GE `(M)` coils are `OTE` (Logix tags retain by default). Falling-edge `(v)` coils are a hold bit plus `ONS`.
- Studio older than v32.11 cannot open this L5X; newer Studio can.
