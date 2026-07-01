# X Module Index

| ID | Module | Status | Scope | Stable run |
|----|--------|--------|-------|------------|
| X01 | `x01_xer_to_analysis_sqlite.py` | **STABLE / FROZEN** | Convert XER to analysis SQLite only | `20260701_154557` |
| X02 | `x02_inspect_sqlite_schema.py` | **STABLE / FROZEN** | Read-only SQLite schema inspection only | `20260701_161516` |
| X03 | `x03_compare_xer_to_p6_sqlite.py` | **STABLE / FROZEN** | Read-only schema/mapping comparison only | `20260701_162007` |
| X04 | `x04_plan_xer_to_p6_sqlite_sandbox_import.py` | **STABLE / FROZEN — DRY-RUN ONLY** | Dry-run import plan only; no writes | `20260701_162808` |

### X04 write posture

- **Write readiness:** BLOCKED / NEEDS FULLER XER
- **Write mode:** DISABLED
- X04 pass means the dry-run plan was created — it does **not** approve P6 SQLite writes.

## Output layout

```
06_output/x_modules/xer_sqlite_research/
  runs/<run_id>/x01_xer_to_analysis_sqlite/
  schema_reports/<run_id>/x02_inspect_sqlite_schema/
  mapping_reports/<run_id>/x03_compare_xer_to_p6_sqlite/
  mapping_reports/<run_id>/x04_plan_xer_to_p6_sqlite_sandbox_import/
    x04_dry_run_summary.json
    x04_dry_run_report.md
    proposed_id_remap_*.csv
    insert_order_plan.csv
    missing_dependency_report.csv
    trigger_risk_report.csv
    write_readiness_checklist.md
  sandbox_dbs/
  verification_reports/
```

## Test batches

```bat
TY_TEST_X01_XER_TO_ANALYSIS_SQLITE.bat
TY_TEST_X02_INSPECT_P6_SQLITE_SCHEMA.bat
TY_TEST_X03_COMPARE_XER_TO_P6_SQLITE.bat
TY_TEST_X04_PLAN_XER_TO_P6_SQLITE_SANDBOX_IMPORT.bat
```
