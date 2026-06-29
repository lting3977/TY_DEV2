# TY_DEV2 Module Index

| ID | Module | Status | Description |
|----|--------|--------|-------------|
| M00 | `02_hand/p6_prepare.py` | Active (foundation) | Prepare P6 window — restore, focus, maximise, return fresh rect |
| M01 | `m01_open_p6` | Inactive | Launch P6 — assume P6 opened manually |
| M02 | `m02_login_p6` | Inactive | Login to P6 — assume already logged in manually |
| M03 | `m03_open_project_by_name.py` | **Frozen (STABLE)** | Open a named project via Open Project dialog (P6-only OCR) |
| M04 | `m04_check_project_opened.py` | **Frozen (STABLE)** | Read-only check whether a named project is currently open |
| M05 | `m05_close_project_safely.py` | **Frozen (CLOSE-STABLE)** | Close current P6 project — auto-confirms normal close dialog |
| M06 | `m06_go_to_activities.py` | **Frozen (STABLE)** | Navigate to Activities workspace (Alt+P, A) when project is open |
| M07 | `m07_read_activity_table_snapshot.py` | **Frozen (STABLE)** | Read-only snapshot of visible Activities table (P6-only OCR) |
| M08 | `m08_read_activity_table_structured.py` | **Frozen (STABLE)** | Parse M07 snapshot into structured activity rows (read-only) |
| M09 | `m09_read_project_data_date.py` | **Frozen (STABLE)** | Read-only Data Date from Activities/status area (P6-only OCR) |
| M10 | `m10_compare_data_date_to_activity_dates.py` | **Frozen (STABLE)** | Compare M09 Data Date to M08 activity dates (read-only) |
| M11 | `m11_generate_planning_health_report.py` | **Frozen (STABLE)** | Generate planner-readable health report from M08/M09/M10 (no P6) |
| M12 | `m12_run_read_only_health_check.py` | **Frozen (STABLE)** | Master read-only orchestrator M03→M11 health check workflow |
| M13 | `m13_copy_visible_activity_table_to_clipboard_csv.py` | **Frozen (STABLE)** | Read-only clipboard copy of visible Activities table |
| M14 | `m14_copy_visible_activity_rows_multi_select.py` | **Frozen (STABLE)** | Read-only multi-row shift-select clipboard copy from Activities table |
| M15 | `m15_clipboard_multi_row_health_report.py` | **Frozen (STABLE)** | Read-only clipboard multi-row health report vs M09 Data Date |
| M16 | `m16_discover_p6_export_menu.py` | **Frozen (STABLE)** | Export-path discovery — open File > Export, capture evidence, cancel |
| M17 | `m17_discover_export_format_options.py` | **Frozen (STABLE)** | Export-format discovery — OCR format options on first wizard screen |
| M18 | `m18_select_spreadsheet_export_format_discovery_only.py` | **Frozen (STABLE)** | Spreadsheet export discovery — select XLSX, Next once, capture next screen |
| M19 | `m19_discover_spreadsheet_export_type_options.py` | **Frozen (STABLE)** | Export Type discovery — OCR export type options after Spreadsheet Next |
| M20 | `m20_select_activities_export_type_discovery_only.py` | **Frozen (STABLE)** | Activities export discovery — Spreadsheet, Activities, Next twice, post-Activities screen, cancel |
| M21 | `m21_discover_activity_export_template_screen.py` | **Frozen (STABLE)** | Activity export template discovery — Projects-to-export, third Next, validation/template screen, cancel |
| M22 | `m22_select_project_on_projects_to_export_discovery_only.py` | **Frozen (STABLE)** | Project selection on Projects-to-export — select 001 Talison 1275, Next once, template screen discovery, cancel |

## Phase 2 — M03 (Frozen)

**Status:** STABLE — run `20260625_212954` (10/10 hard matrix). Do not modify unless a later module exposes a real shared bug.

**Command:**

```bat
python 04_modules\m03_open_project_by_name.py --project "Talison 1275"
```

**Batch test:**

```bat
TY_TEST_M03_OPEN_PROJECT.bat
TY_TEST_M03_HARD_10.bat
```

**Output:**

`06_output\runs\<run_id>\m03_open_project_by_name\`

## Phase 3 — M04 (Frozen)

**Status:** STABLE — run `20260625_214858` (6/6 hard matrix). Do not modify unless a later module exposes a real shared bug.

**Command:**

```bat
python 04_modules\m04_check_project_opened.py --project "Talison 1275"
```

**Batch test:**

```bat
TY_TEST_M04_CHECK_PROJECT.bat
TY_TEST_M04_HARD_6.bat
```

**Output:**

`06_output\runs\<run_id>\m04_check_project_opened\`

## Phase 4 — M05 (Frozen)

**Status:** CLOSE-STABLE — run `20260625_224530` (5/5 hard matrix). Do not modify unless a later module exposes a real shared bug.

**Command:**

```bat
python 04_modules\m05_close_project_safely.py --project "Talison 1275"
```

**Batch test:**

```bat
TY_TEST_M05_CLOSE_PROJECT.bat
TY_TEST_M05_HARD_5.bat
```

**Output:**

`06_output\runs\<run_id>\m05_close_project_safely\`

## Phase 5 — M06 (Frozen)

**Status:** STABLE — run `20260625_231210` (6/6 hard matrix). Do not modify unless a later module exposes a real shared bug.

**Command:**

```bat
python 04_modules\m06_go_to_activities.py --project "Talison 1275"
```

**Batch test:**

```bat
TY_TEST_M06_GO_TO_ACTIVITIES.bat
TY_TEST_M06_HARD_6.bat
```

**Output:**

`06_output\runs\<run_id>\m06_go_to_activities\`

**Behaviour:** Confirms project open (M04-style), navigates via Alt+P, A only. No schedule edit, save, close, import, export, or print.

## Phase 6 — M07 (Frozen)

**Status:** STABLE — run `20260626_134543` (6/6 hard matrix). Do not modify unless a later module exposes a real shared bug.

**Command:**

```bat
python 04_modules\m07_read_activity_table_snapshot.py --project "Talison 1275"
```

**Batch test:**

```bat
TY_TEST_M07_READ_ACTIVITY_TABLE.bat
TY_TEST_M07_HARD_6.bat
```

**Output:**

`06_output\runs\<run_id>\m07_read_activity_table_snapshot\`

**Behaviour:** Read-only Activities table snapshot. Confirms project open and Activities workspace. No layout change, schedule edit, save, export, or print.

## Phase 7 — M08 (Frozen)

**Status:** STABLE — run `20260626_154709` (6/6 hard matrix). Do not modify unless a later module exposes a real shared bug.

**Command:**

```bat
python 04_modules\m08_read_activity_table_structured.py --project "Talison 1275"
```

**With existing M07 folder:**

```bat
python 04_modules\m08_read_activity_table_structured.py --project "Talison 1275" --m07-folder "C:\TY_DEV2\06_output\runs\<run_id>\m07_read_activity_table_snapshot"
```

**Batch test:**

```bat
TY_TEST_M08_READ_ACTIVITY_STRUCTURED.bat
```

**Output:**

`06_output\runs\<run_id>\m08_read_activity_table_structured\`

**Behaviour:** Read-only parser over M07 extracted files. No P6 interaction unless chain is required.

## Phase 8 — M09 (Frozen)

**Status:** STABLE — run `20260626_164106` (6/6 hard matrix). Do not modify unless a later module exposes a real shared bug.

**Command:**

```bat
python 04_modules\m09_read_project_data_date.py --project "Talison 1275"
```

**Batch test:**

```bat
TY_TEST_M09_READ_DATA_DATE.bat
```

**Output:**

`06_output\runs\<run_id>\m09_read_project_data_date\`

**Behaviour:** Read-only Data Date capture from Activities workspace / status footer. No schedule edit, F9, data date change, save, export, or print.

## Phase 9 — M10 (Frozen)

**Status:** STABLE — run `20260626_172358` (6/6 hard matrix). Do not modify unless a later module exposes a real shared bug.

**Command:**

```bat
python 04_modules\m10_compare_data_date_to_activity_dates.py --project "Talison 1275"
```

**With existing M08 and M09 folders:**

```bat
python 04_modules\m10_compare_data_date_to_activity_dates.py --project "Talison 1275" --m08-folder "C:\TY_DEV2\06_output\runs\<run_id>\m08_read_activity_table_structured" --m09-folder "C:\TY_DEV2\06_output\runs\<run_id>\m09_read_project_data_date"
```

**Batch test:**

```bat
TY_TEST_M10_COMPARE_DATA_DATE.bat
```

**Output:**

`06_output\runs\<run_id>\m10_compare_data_date_to_activity_dates\`

**Behaviour:** Read-only comparison of M09 Data Date against M08 activity Start/Finish dates. No P6 interaction unless chain is required.

## Phase 10 — M11

**Command:**

```bat
python 04_modules\m11_generate_planning_health_report.py --project "Talison 1275"
```

**With existing M08, M09, and M10 folders:**

```bat
python 04_modules\m11_generate_planning_health_report.py --project "Talison 1275" --m08-folder "C:\TY_DEV2\06_output\runs\<run_id>\m08_read_activity_table_structured" --m09-folder "C:\TY_DEV2\06_output\runs\<run_id>\m09_read_project_data_date" --m10-folder "C:\TY_DEV2\06_output\runs\<run_id>\m10_compare_data_date_to_activity_dates"
```

**Batch test:**

```bat
TY_TEST_M11_HEALTH_REPORT.bat
```

**Output:**

`06_output\runs\<run_id>\m11_generate_planning_health_report\`

**Behaviour:** Report generation only from M08/M09/M10 outputs. No direct P6 interaction when source folders are provided.

**Status:** STABLE — run `20260627_000604` (6/6 hard matrix). Do not modify unless a later module exposes a real shared bug.

## Phase 11 — M12

**Command:**

```bat
python 04_modules\m12_run_read_only_health_check.py --project "Talison 1275"
```

**Batch run:**

```bat
TY_RUN_READ_ONLY_HEALTH_CHECK.bat
```

**Output:**

`06_output\runs\<run_id>\m12_run_read_only_health_check\`

**Behaviour:** Read-only master orchestrator running M03 → M04 → M06 → M07 → M08 → M09 → M10 → M11 in sequence. Stops on critical step failure. No schedule edit, layout change, export, print, save, or close.

**Status:** STABLE — run `20260627_001636` (6/6 hard matrix). Do not modify unless a later module exposes a real shared bug.

## Phase 12 — M13

**Command:**

```bat
python 04_modules\m13_copy_visible_activity_table_to_clipboard_csv.py --project "Talison 1275"
```

**Batch test:**

```bat
TY_TEST_M13_COPY_VISIBLE_TABLE.bat
```

**Output:**

`06_output\runs\<run_id>\m13_copy_visible_activity_table_to_clipboard_csv\`

**Behaviour:** Read-only clipboard extraction from visible Activities table. Ctrl+C only after grid focus. Preserves/restores clipboard. No export wizard, schedule edit, or data changes.

**Status:** STABLE — run `20260627_134623` (6/6 hard matrix). Do not modify unless a later module exposes a real shared bug.

## Phase 13 — M14

**Command:**

```bat
python 04_modules\m14_copy_visible_activity_rows_multi_select.py --project "Talison 1275" --max-rows 3
```

**Batch test:**

```bat
TY_TEST_M14_COPY_MULTI_ROWS.bat
```

**Output:**

`06_output\runs\<run_id>\m14_copy_visible_activity_rows_multi_select\`

**Behaviour:** Read-only multi-row clipboard extraction via shift-click on confirmed visible activity rows. Builds on M13. No Ctrl+A, no drag, no data changes.

**Status:** STABLE — run `20260627_142244` (6/6 hard matrix). Do not modify unless a later module exposes a real shared bug.

**Hard test batch:**

```bat
TY_TEST_M14_HARD_6.bat
```

**Notes:**

- Multi-row clipboard extraction from P6 Activities table is stable.
- Uses confirmed P6 foreground and confirmed OCR activity-row targets.
- Uses shift-click multi-row selection and Ctrl+C only.
- Restores clipboard after each run.
- No export wizard, no schedule edit, no unsafe keys.
- Read-only clipboard extraction.

## Phase 14 — M15

**Command:**

```bat
python 04_modules\m15_clipboard_multi_row_health_report.py --project "Talison 1275"
```

**With existing M14 and M09 folders:**

```bat
python 04_modules\m15_clipboard_multi_row_health_report.py --project "Talison 1275" --m14-folder "C:\TY_DEV2\06_output\runs\<m14_run>\m14_copy_visible_activity_rows_multi_select" --m09-folder "C:\TY_DEV2\06_output\runs\<m09_run>\m09_read_project_data_date"
```

**Batch test:**

```bat
TY_TEST_M15_CLIPBOARD_HEALTH_REPORT.bat
```

**Output:**

`06_output\runs\<run_id>\m15_clipboard_multi_row_health_report\`

**Behaviour:** Read-only report from M14 clipboard table data and M09 Data Date. Compares visible clipboard rows against Data Date. No P6 interaction when source folders are provided.

**Status:** STABLE — run `20260627_145125` (6/6 hard matrix). Do not modify unless a later module exposes a real shared bug.

**Hard test batch:**

```bat
TY_TEST_M15_HARD_6.bat
```

**Notes:**

- Clipboard-row health report is stable.
- Uses M14 clipboard multi-row output and M09 Data Date output.
- Generates planner-readable clipboard health report.
- Handles clean rows, warning rows, missing M14 source, missing Data Date, and no clipboard rows.
- Does not touch P6 when source folders are provided.
- Does not export, edit, save, schedule, or open export wizard.
- Clearly states selected-visible-clipboard-rows-only limitation.

## Phase 15 — M16

**Command:**

```bat
python 04_modules\m16_discover_p6_export_menu.py --project "Talison 1275"
```

**Batch test:**

```bat
TY_TEST_M16_DISCOVER_EXPORT_MENU.bat
```

**Output:**

`06_output\runs\<run_id>\m16_discover_p6_export_menu\`

**Behaviour:** Export-path discovery only. Opens File > Export via Alt+F, E, captures P6-only OCR evidence, then safely cancels. Does not complete export, choose format, click Next/Finish, or save files.

**Status:** STABLE — run `20260627_211126` (6/6 hard matrix). Do not modify unless a later module exposes a real shared bug.

**Hard test batch:**

```bat
TY_TEST_M16_HARD_6.bat
```

**Notes:**

- Export menu/wizard discovery is stable.
- Opens File > Export safely.
- Captures export wizard evidence words: export, export format, XER, XML, spreadsheet, next, finish, cancel.
- Closes the export wizard using Cancel.
- Does not press Next or Finish.
- Does not create export files.
- Does not modify schedule, layout, data date, or project data.
- Discovery only; not a real export module.

## Phase 16 — M17

**Command:**

```bat
python 04_modules\m17_discover_export_format_options.py --project "Talison 1275"
```

**Batch test:**

```bat
TY_TEST_M17_DISCOVER_EXPORT_FORMATS.bat
```

**Output:**

`06_output\runs\<run_id>\m17_discover_export_format_options\`

**Behaviour:** Export-format discovery only. Opens File > Export, OCR-reads format options on first wizard screen, then safely cancels. Does not press Next, Finish, or save files.

**Status:** STABLE — simple test `20260627_213834`, hard test `20260627_214244` (6/6). Do not modify unless a later module exposes a real shared bug.

**Hard test batch:**

```bat
TY_TEST_M17_HARD_6.bat
```

**Notes:**

- Detects canonical format options: XER, XML, Spreadsheet, Microsoft Project, Primavera PM.
- OCR-only discovery on first wizard screen; no format option clicking.
- Closes export wizard via Cancel click; Next/Finish never pressed.
- No export files created; P6 returns to Activities after close.

## Phase 17 — M18

**Command:**

```bat
python 04_modules\m18_select_spreadsheet_export_format_discovery_only.py --project "Talison 1275"
```

**Batch test:**

```bat
TY_TEST_M18_DISCOVER_SPREADSHEET_EXPORT_NEXT.bat
```

**Output:**

`06_output\runs\<run_id>\m18_select_spreadsheet_export_format_discovery_only\`

**Behaviour:** Spreadsheet export discovery only. Opens File > Export, OCR-clicks Spreadsheet/XLSX, presses Next once, captures next wizard screen evidence, then safely cancels. Does not press Finish or save files.

**Status:** STABLE — simple test `20260627_220416`, hard test `20260627_223010` (6/6). Do not modify unless a later module exposes a real shared bug.

**Hard test batch:**

```bat
TY_TEST_M18_HARD_6.bat
```

**Notes:**

- Selects Spreadsheet/XLSX via OCR click; presses Next exactly once.
- Detects Export Type next screen: activities, resources, relationships, etc.
- Closes export wizard via Cancel; Finish never pressed.
- No export files created; P6 returns to Activities after close.

## Phase 18 — M19

**Command:**

```bat
python 04_modules\m19_discover_spreadsheet_export_type_options.py --project "Talison 1275"
```

**Batch test:**

```bat
TY_TEST_M19_DISCOVER_SPREADSHEET_EXPORT_TYPES.bat
```

**Output:**

`06_output\runs\<run_id>\m19_discover_spreadsheet_export_type_options\`

**Behaviour:** Export Type discovery only. Opens File > Export, selects Spreadsheet/XLSX, presses Next once, OCR-reads export type options, then safely cancels. Does not select export types, press Next again, Finish, or save files.

**Status:** STABLE — simple test `20260627_231459`, hard test `20260627_232030` (6/6). Do not modify unless a later module exposes a real shared bug.

**Hard test batch:**

```bat
TY_TEST_M19_HARD_6.bat
```

**Notes:**

- Selects Spreadsheet/XLSX, presses Next exactly once, detects Export Type screen.
- OCR-detects export type options: Activities, Activity Relationships, Resources, Resource Assignments, Expenses, etc.
- No export type selected; Finish never pressed; wizard closed via Cancel.
- No export files created; P6 returns to Activities after close.

## Phase 19 — M20

**Command:**

```bat
python 04_modules\m20_select_activities_export_type_discovery_only.py --project "Talison 1275"
```

**Batch test:**

```bat
TY_TEST_M20_DISCOVER_ACTIVITIES_EXPORT_NEXT.bat
TY_TEST_M20_HARD_6.bat
```

**Diagnostic batch:**

```bat
TY_TEST_M20_DIAGNOSTIC.bat
```

**Output:**

`06_output\runs\<run_id>\m20_select_activities_export_type_discovery_only\`

**Behaviour:** Activities export-type discovery only. Opens File > Export, selects Spreadsheet/XLSX, Next once to Export Type, OCR-clicks Activities, Next once more, classifies post-Activities screen (Projects-to-export, template, file/path, or generic partial), then safely cancels. Does not press Finish, select template, or create export files.

**Status:** STABLE / FROZEN — simple test `20260628_124800`, hard test `20260628_232920` (6/6). Do not modify unless a later module exposes a real shared bug.

**Hard test batch:**

```bat
TY_TEST_M20_HARD_6.bat
```

**Notes:**

- Post-Activities valid full PASS screen types: `projects_to_export`, `template`, `file_path`.
- `generic_wizard` scores as `PASS_ACTIVITIES_NEXT_DISCOVERY_PARTIAL` only when safe.
- Next pressed exactly twice on discovery paths; wizard closed via Cancel.
- No Finish pressed; no export files created; P6 returns to Activities after close.
- M20 preflight clears stale Open Project and export wizard dialogs before File > Export.
- Hard matrix uses `ensure_clean_p6_for_m20_hard()` before each test; test 06 probes export wizard open before applying late hook.
- Test 06 hook `force_post_activities_screen_not_found_after_second_next` activates only after Spreadsheet → first Next → Export Type → Activities → second Next.
- Export wizard open retry (max 1) on File > Export failure; setup failures scored separately from module FALSE_PASS.
- Shared helpers in `export_wizard_common.py` (M20+ only; M03–M19 frozen).

## Phase 20 — M21

**Command:**

```bat
python 04_modules\m21_discover_activity_export_template_screen.py --project "Talison 1275"
```

**Batch test:**

```bat
TY_TEST_M21_DISCOVER_ACTIVITY_TEMPLATE_SCREEN.bat
TY_TEST_M21_HARD_6.bat
```

**Output:**

`06_output\runs\<run_id>\m21_discover_activity_export_template_screen\`

**Behaviour:** Activity export template discovery only. Spreadsheet → Export Type → Activities → Projects-to-export → third Next → classify post-Projects screen (template, validation popup, file/path, or partial), then safely cancel. Does not press Finish, select template, type path, or create export files.

**Status:** STABLE / FROZEN — simple test `20260629_130413`, hard test `20260629_163456` (6/6). Do not modify unless a later module exposes a real shared bug.

**Hard test batch:**

```bat
TY_TEST_M21_HARD_6.bat
```

**Notes:**

- Valid PASS statuses: `PASS_TEMPLATE_SCREEN_DISCOVERY`, `PASS_TEMPLATE_SCREEN_DISCOVERY_PARTIAL`, `PASS_POST_PROJECTS_SCREEN_DISCOVERY`.
- Validation popup after third Next (`projects_validation_popup`) is valid discovery; popup dismissed via OCR OK or Esc.
- Next pressed exactly 3 times on discovery paths; wizard closed via Cancel.
- No Finish pressed; no export files created; no Browse/path/template edit.
- Hard matrix uses `ensure_clean_p6_for_m21_hard()` — self-restores Talison 1275 + Activities (M03/M04/M06 + Open Project fallback).
- Test 04 closes project via M05; expects `FAIL_PROJECT_NOT_OPEN`; post-test restore before tests 05–06.
- Test 05 hook `force_projects_export_blocked_after_third_next` activates only after full wizard path + third Next.
- Test 06 hook `force_post_projects_next_screen_not_found_after_third_next` activates only after third Next with stage evidence.
- Shared helpers in `export_wizard_common.py` (M21+ only; M03–M20 frozen).

## Phase 21 — M22

**Command:**

```bat
python 04_modules\m22_select_project_on_projects_to_export_discovery_only.py --project "Talison 1275"
```

**Batch test:**

```bat
TY_TEST_M22_SELECT_PROJECT_ON_PROJECTS_TO_EXPORT.bat
TY_TEST_M22_HARD_6.bat
```

**Output:**

`06_output\runs\<run_id>\m22_select_project_on_projects_to_export_discovery_only\`

**Behaviour:** Project selection discovery only. Spreadsheet → Export Type → Activities → Projects-to-export → select 001 Talison 1275 (Export-column checkbox) → Next once → classify post-selection screen (template or partial), then safely cancel. Does not press Finish, edit template, type path, or create export files.

**Status:** STABLE / FROZEN — simple test `20260629_180232`, hard test `20260630_010538` (6/6). Do not modify unless a later module exposes a real shared bug.

**Hard test batch:**

```bat
TY_TEST_M22_HARD_6.bat
```

**Notes:**

- Valid PASS statuses: `PASS_PROJECT_SELECTION_NEXT_DISCOVERY`, `PASS_PROJECT_SELECTION_NEXT_DISCOVERY_PARTIAL`.
- Next pressed exactly 3 times on discovery paths (Spreadsheet/Export Type/Activities + one after project select); wizard closed via Cancel.
- No Finish pressed; no export files created; no Browse/path/template edit.
- Hard matrix uses `ensure_clean_p6_for_m22_hard()` — M21 restore chain + neutral mouse inside P6 before each test.
- Hard matrix skips redundant M22 clean-restore when precheck already restored (`skip_project_restore=True`).
- PyAutoGUI click/move guarded via `m22_safe_pyautogui_*` — validates P6 bounds, corner margin, catches FailSafeException.
- Test 04 closes project via M05; expects `FAIL_PROJECT_NOT_OPEN`; post-test restore.
- Test 05 hook `force_project_row_not_found` activates only after Projects-to-export screen reached.
- Test 06 hook `force_post_project_selection_screen_not_found` activates only after project selection + Next from Projects-to-export.
- Shared helpers in `export_wizard_common.py` (M22+ only; M03–M21 frozen).

## Safety (all modules)

- P6-window crop OCR only — never full desktop
- No Yes / No / Save / Delete / Remove / Overwrite on unsafe popups
- Stop on `MANUAL_REVIEW_UNSAFE_POPUP` or `MANUAL_REVIEW_CANNOT_CONFIRM`
