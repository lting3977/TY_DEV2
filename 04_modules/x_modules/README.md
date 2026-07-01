# X Modules — XER / SQLite Research

Research modules for parsing Primavera P6 `.xer` exports, inspecting P6 SQLite schemas, comparing them for mapping, and producing dry-run import plans — **without modifying production P6 data**.

## Important

- **X modules are separate from M modules** (M03–M24 GUI automation). Do not mix X research runs with M module test batches.
- **X01–X04 are safe read-only / dry-run research tools.** They do not open or modify live P6 application databases for write purposes.
- **X04 does not prove P6 SQLite write is safe.** A `PASS_X04_DRY_RUN_PLAN_CREATED` status only means planning artifacts were produced read-only.
- **X04 concluded Test1.xer is missing** RSRC, TASKRSRC, ACTVCODE, ACTVTYPE, and UDFVALUE. Resource assignments, activity codes, and UDFs cannot be imported from that export.
- **Before X05 or any write design**, obtain a fuller XER exported with resources, assignments, activity codes, and UDFs if available. Write mode is not built and not approved.

## Safety

- X modules **never** write to P6 application SQLite databases.
- X02/X03/X04 open inspected databases with SQLite `mode=ro` only.
- X04 is **dry-run only** — no inserts, updates, or schema changes on any database. `--mode write` returns `FAIL_WRITE_MODE_DISABLED`.
- Prefer inspecting a **copied** P6 database, not a live install path.
- All output lives under `06_output/x_modules/xer_sqlite_research/`.

## Modules

| ID | Script | Status | Purpose |
|----|--------|--------|---------|
| X01 | `x01_xer_to_analysis_sqlite.py` | STABLE / FROZEN | Parse XER → new `xer_analysis.sqlite` |
| X02 | `x02_inspect_sqlite_schema.py` | STABLE / FROZEN | Read-only schema inspection of a P6 SQLite DB |
| X03 | `x03_compare_xer_to_p6_sqlite.py` | STABLE / FROZEN | Compare X01 XER DB vs P6 DB schema + insert risk |
| X04 | `x04_plan_xer_to_p6_sqlite_sandbox_import.py` | STABLE / FROZEN — DRY-RUN ONLY | Dry-run sandbox import plan (ID remap, triggers, dependencies) |

## Run

```bat
TY_TEST_X01_XER_TO_ANALYSIS_SQLITE.bat
TY_TEST_X02_INSPECT_P6_SQLITE_SCHEMA.bat
TY_TEST_X03_COMPARE_XER_TO_P6_SQLITE.bat
TY_TEST_X04_PLAN_XER_TO_P6_SQLITE_SANDBOX_IMPORT.bat
```

Or:

```bat
python 04_modules\x_modules\x04_plan_xer_to_p6_sqlite_sandbox_import.py ^
  --xer-db "...\xer_analysis.sqlite" ^
  --p6-db "...\P6_TEST_COPY_001.db" ^
  --mode dry-run
```
