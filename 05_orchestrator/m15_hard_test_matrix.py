"""
M15 Hard Testing — 6-test matrix.

Proves M15 can generate clipboard-row planning health reports from M14 and M09
sources, handle warnings and controlled failures, and not touch P6 when source
folders are provided.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
ORCH = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ORCH))
sys.path.insert(0, str(ROOT / "04_modules"))

from m15_hard_summary import write_hard_summary  # noqa: E402
from m15_clipboard_multi_row_health_report import (  # noqa: E402
    MODULE_NAME as M15_MODULE_NAME,
    RunEvidence,
    run_m15,
    write_json,
)
from m03_open_project_by_name import run_m03  # noqa: E402
from m04_check_project_opened import run_m04  # noqa: E402
from m06_go_to_activities import run_m06  # noqa: E402
from m09_read_project_data_date import run_m09  # noqa: E402
from m14_copy_visible_activity_rows_multi_select import run_m14  # noqa: E402

PASS_REPORT = frozenset({"PASS", "PASS_WITH_WARNINGS"})
TEST_04_OK = frozenset({"FAIL_M14_SOURCE_NOT_FOUND"})
TEST_05_OK = frozenset({"FAIL_DATA_DATE_MISSING"})
TEST_06_OK = frozenset({"FAIL_NO_CLIPBOARD_ROWS"})

SIMPLE_M14 = Path(
    r"C:\TY_DEV2\06_output\runs\20260627_144001\m14_copy_visible_activity_rows_multi_select"
)
SIMPLE_M09 = Path(
    r"C:\TY_DEV2\06_output\runs\20260627_143926\m09_read_project_data_date"
)

REPORT_FILES = (
    "clipboard_health_report.md",
    "clipboard_health_report.json",
    "clipboard_activity_rows.csv",
    "clipboard_warning_register.csv",
)

P6_TOUCH_MARKERS = (
    "running m03",
    "prepare_p6",
    "run_m03",
    "run_m04",
    "run_m06",
    "run_m09",
    "run_m14",
    "upstream chain",
    "m03 status",
    "m04 status",
    "m06 status",
    "m09 status",
    "m14 status",
)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_test_folder(matrix_run_id: str, test_id: str, slug: str) -> Path:
    folder = (
        ROOT / "06_output" / "runs" / matrix_run_id / "m15_hard_test_6" / f"test_{test_id}_{slug}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def build_m15_evidence(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
    folder = build_test_folder(matrix_run_id, test_id, slug)
    report_dir = folder / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    return RunEvidence(
        run_id=f"{matrix_run_id}_t{test_id}",
        folder=folder,
        report_dir=report_dir,
    )


def report_files_ok(test_folder: Path, require_all: bool) -> Tuple[bool, Dict[str, bool]]:
    report = test_folder / "report"
    checks = {name: (report / name).exists() for name in REPORT_FILES}
    if not require_all:
        return checks.get("clipboard_health_report.md", False), checks
    return all(checks.values()), checks


def check_p6_not_touched(steps: List[str]) -> Tuple[bool, List[str]]:
    hits: List[str] = []
    blob = " ".join(steps).lower()
    for marker in P6_TOUCH_MARKERS:
        if marker in blob:
            hits.append(marker)
    return len(hits) == 0, hits


def check_limitation_stated(test_folder: Path) -> bool:
    md_path = test_folder / "report" / "clipboard_health_report.md"
    if not md_path.is_file():
        return False
    text = md_path.read_text(encoding="utf-8", errors="replace").lower()
    return "selected visible clipboard rows only" in text or "visible selected rows only" in text


def check_source_paths_preserved(
    m15_result: Dict[str, Any],
    expected_m14: str,
    expected_m09: str,
) -> Tuple[bool, str]:
    m14 = (m15_result.get("source_m14_folder") or "").replace("/", "\\")
    m09 = (m15_result.get("source_m09_folder") or "").replace("/", "\\")
    exp14 = expected_m14.replace("/", "\\")
    exp09 = expected_m09.replace("/", "\\")
    if exp14 and exp14.lower() not in m14.lower():
        return False, f"M14 path mismatch: expected {exp14}, got {m14}"
    if exp09 and exp09.lower() not in m09.lower():
        return False, f"M09 path mismatch: expected {exp09}, got {m09}"
    return True, ""


def create_synthetic_m14_folder(
    base: Path,
    *,
    rows: List[List[str]],
    raw_text: str,
    activity_like_count: int,
) -> Path:
    folder = base / "synthetic_m14_source"
    clip = folder / "clipboard"
    clip.mkdir(parents=True, exist_ok=True)

    (clip / "clipboard_raw.txt").write_text(raw_text, encoding="utf-8")
    table_payload = {
        "line_count": len(rows),
        "column_guess": max((len(r) for r in rows), default=0),
        "headers_detected": ["activity id", "activity name", "start", "finish"],
        "activity_like_row_count": activity_like_count,
        "rows": rows,
    }
    write_json(clip / "clipboard_table.json", table_payload)

    with (clip / "clipboard_table.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for row in rows:
            writer.writerow(row)

    write_json(
        clip / "clipboard_validation.json",
        {
            "line_count": len(rows),
            "parsed_row_count": len(rows),
            "column_guess": table_payload["column_guess"],
            "headers_detected": table_payload["headers_detected"],
            "activity_like_row_count": activity_like_count,
            "has_tabs": "\t" in raw_text,
            "table_like": activity_like_count >= 1,
            "clipboard_pollution_detected": False,
            "clipboard_pollution_words": [],
        },
    )
    write_json(
        clip / "row_selection_targets.json",
        {
            "visible_targets_count": max(0, activity_like_count),
            "max_rows_used": max(0, activity_like_count),
            "all_targets": [],
            "selected_first_target": {},
            "selected_last_target": {},
        },
    )
    write_json(folder / "result.json", {"status": "SYNTHETIC", "module": "synthetic_m14"})
    return folder


def copy_valid_m09_folder(base: Path) -> Path:
    folder = base / "synthetic_m09_valid"
    extracted = folder / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        SIMPLE_M09 / "extracted" / "data_date_result.json",
        extracted / "data_date_result.json",
    )
    shutil.copy2(
        SIMPLE_M09 / "extracted" / "data_date_candidates.json",
        extracted / "data_date_candidates.json",
    )
    write_json(folder / "result.json", {"status": "SYNTHETIC_COPY", "module": "synthetic_m09"})
    return folder


def create_synthetic_m09_no_date(base: Path) -> Path:
    folder = base / "synthetic_m09_no_date"
    extracted = folder / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    write_json(
        extracted / "data_date_result.json",
        {
            "data_date_found": False,
            "data_date_raw": "",
            "data_date_normalized_candidate": "",
            "confidence": 0.0,
            "candidate_count": 0,
            "label_visible": False,
        },
    )
    write_json(
        extracted / "data_date_candidates.json",
        {"candidate_count": 0, "candidates": []},
    )
    write_json(folder / "result.json", {"status": "SYNTHETIC", "module": "synthetic_m09"})
    return folder


def create_warning_m14_source(base: Path) -> Path:
    rows = [
        ["Activity ID", "Activity Name", "Start", "Finish", "Resources"],
        ["A2000", "Early Start Task", "15-Jun-26", "25-Jun-26", ""],
        ["A2010", "Early Finish Task", "25-Jun-26", "18-Jun-26", ""],
        ["A2020", "Bad Finish Date", "01-Jul-26", "17-Jul-za", ""],
        ["", "No ID Task", "01-Jul-26", "05-Jul-26", ""],
    ]
    raw_lines = [
        "Activity ID\tActivity Name\tStart\tFinish\tResources",
        "A2000\tEarly Start Task\t15-Jun-26\t25-Jun-26\t",
        "A2010\tEarly Finish Task\t25-Jun-26\t18-Jun-26\t",
        "A2020\tBad Finish Date\t01-Jul-26\t17-Jul-za\t",
        "\tNo ID Task\t01-Jul-26\t05-Jul-26\t",
    ]
    return create_synthetic_m14_folder(
        base,
        rows=rows,
        raw_text="\n".join(raw_lines) + "\n",
        activity_like_count=4,
    )


def create_empty_rows_m14_source(base: Path) -> Path:
    rows = [
        ["Activity ID", "Activity Name", "Start", "Finish", "Resources"],
        ["001", "", "22-Jun-26", "17-Jul-26", ""],
    ]
    raw = (
        "Activity ID\tActivity Name\tStart\tFinish\tResources\n"
        "001\t\t22-Jun-26\t17-Jul-26\t\n"
    )
    return create_synthetic_m14_folder(
        base,
        rows=rows,
        raw_text=raw,
        activity_like_count=0,
    )


def score_result(
    test_id: str,
    m15_status: str,
    m15_result: Dict[str, Any],
    test_folder: Path,
    *,
    source_mode: bool = False,
    expected_m14: str = "",
    expected_m09: str = "",
) -> Tuple[int, str, str]:
    steps = m15_result.get("steps", [])
    data_date_parsed = (m15_result.get("data_date_parsed") or "").strip()
    rows_checked = int(m15_result.get("clipboard_rows_checked", 0))
    warning_count = int(m15_result.get("warning_count", 0))

    if test_id == "04":
        if m15_status not in TEST_04_OK:
            return 0, "FALSE_PASS", f"Test 04 expected FAIL_M14_SOURCE_NOT_FOUND, got {m15_status}"
        if data_date_parsed:
            return 0, "DATA_DATE_INVENTED", "Data Date parsed when M14 source missing"
        return 1, m15_status, "Controlled failure for missing M14 source"

    if test_id == "05":
        if m15_status not in TEST_05_OK:
            return 0, "FALSE_PASS", f"Test 05 expected FAIL_DATA_DATE_MISSING, got {m15_status}"
        if data_date_parsed:
            return 0, "DATA_DATE_INVENTED", f"Data Date invented: {data_date_parsed}"
        return 1, m15_status, "Controlled failure without inventing Data Date"

    if test_id == "06":
        if m15_status not in TEST_06_OK:
            return 0, "FALSE_PASS", f"Test 06 expected FAIL_NO_CLIPBOARD_ROWS, got {m15_status}"
        if rows_checked > 0:
            return 0, "FALSE_PASS", f"Summary row 001 treated as activity ({rows_checked} rows)"
        if data_date_parsed and m15_status != "FAIL_NO_CLIPBOARD_ROWS":
            pass
        return 1, m15_status, "Controlled failure — no activity-like clipboard rows"

    if test_id == "02":
        if m15_status != "PASS":
            return 0, "FALSE_PASS", f"Test 02 expected PASS, got {m15_status}"
        p6_ok, p6_hits = check_p6_not_touched(steps)
        if not p6_ok:
            return 0, "P6_TOUCHED_WHEN_SOURCE_FOLDERS_PROVIDED", f"P6 chain markers: {p6_hits}"
        paths_ok, path_reason = check_source_paths_preserved(m15_result, expected_m14, expected_m09)
        if not paths_ok:
            return 0, "SOURCE_EVIDENCE_LOST", path_reason
        require_report = True
    elif test_id == "03":
        if m15_status != "PASS_WITH_WARNINGS":
            return 0, "FALSE_PASS", f"Test 03 expected PASS_WITH_WARNINGS, got {m15_status}"
        if warning_count < 1:
            return 0, "FALSE_PASS", "Expected warnings in synthetic warning source"
        warn_csv = test_folder / "report" / "clipboard_warning_register.csv"
        if not warn_csv.is_file():
            return 0, "REPORT_FILES_MISSING", "clipboard_warning_register.csv missing"
        warn_rows = list(csv.DictReader(warn_csv.open(encoding="utf-8")))
        if not warn_rows:
            return 0, "FALSE_PASS", "Warning register empty"
        sev_high = int(m15_result.get("high_severity_count", 0))
        sev_med = int(m15_result.get("medium_severity_count", 0))
        sev_low = int(m15_result.get("low_severity_count", 0))
        if sev_high + sev_med + sev_low < 1:
            return 0, "FALSE_PASS", "Severity counts not populated"
        require_report = True
    else:
        if m15_status not in PASS_REPORT:
            return 0, "FALSE_PASS", f"Test {test_id} expected PASS/PASS_WITH_WARNINGS, got {m15_status}"
        require_report = True

    if require_report:
        if not data_date_parsed:
            return 0, "FALSE_PASS", "Data Date not parsed for successful report test"
        if rows_checked < 1:
            return 0, "FALSE_PASS", "No clipboard rows checked for successful report test"
        files_ok, file_checks = report_files_ok(test_folder, True)
        if not files_ok:
            return 0, "REPORT_FILES_MISSING", f"Report files missing: {file_checks}"
        if not check_limitation_stated(test_folder):
            return 0, "FALSE_PASS", "Selected-visible-rows limitation not stated in report MD"

    if test_id == "01":
        return 1, m15_status, f"Full chain report: {rows_checked} row(s), {warning_count} warning(s)"

    if test_id == "02":
        return 1, m15_status, "Source-folder mode PASS without P6 touch"

    return 1, m15_status, f"Warning detection: {warning_count} warning(s)"


def finish_hard_test(
    test_folder: Path,
    test_def: Dict[str, Any],
    m15_result: Dict[str, Any],
    setup_notes: List[str],
    *,
    source_mode: bool = False,
    expected_m14: str = "",
    expected_m09: str = "",
) -> Dict[str, Any]:
    m15_status = m15_result.get("status", "ERROR")
    report_ok, report_checks = report_files_ok(
        test_folder, m15_status in PASS_REPORT
    )
    limitation = check_limitation_stated(test_folder) if report_ok else False

    score, status, score_reason = score_result(
        test_def["id"],
        m15_status,
        m15_result,
        test_folder,
        source_mode=source_mode,
        expected_m14=expected_m14,
        expected_m09=expected_m09,
    )

    result = {
        "test_id": test_def["id"],
        "test_name": test_def["name"],
        "slug": test_def["slug"],
        "m15_run_id": m15_result.get("run_id", ""),
        "m15_status": m15_status,
        "m15_reason": m15_result.get("reason", ""),
        "score": score,
        "status": status,
        "score_reason": score_reason,
        "source_m14_folder": m15_result.get("source_m14_folder", ""),
        "source_m09_folder": m15_result.get("source_m09_folder", ""),
        "data_date_raw": m15_result.get("data_date_raw", ""),
        "data_date_normalized_candidate": m15_result.get("data_date_normalized_candidate", ""),
        "data_date_parsed": m15_result.get("data_date_parsed", ""),
        "clipboard_rows_checked": m15_result.get("clipboard_rows_checked", 0),
        "start_before_data_date_count": m15_result.get("start_before_data_date_count", 0),
        "finish_before_data_date_count": m15_result.get("finish_before_data_date_count", 0),
        "date_parse_issue_count": m15_result.get("date_parse_issue_count", 0),
        "warning_count": m15_result.get("warning_count", 0),
        "high_severity_count": m15_result.get("high_severity_count", 0),
        "medium_severity_count": m15_result.get("medium_severity_count", 0),
        "low_severity_count": m15_result.get("low_severity_count", 0),
        "report_files_ok": report_ok,
        "report_file_checks": report_checks,
        "limitation_stated": limitation,
        "test_folder": str(test_folder),
        "setup_notes": setup_notes,
    }

    write_json(test_folder / "test_summary.json", result)
    lines = [
        f"# M15 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- M15 run ID: {m15_result.get('run_id', '')}",
        f"- M15 status: {m15_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Data Date parsed: {result['data_date_parsed']}",
        f"- Clipboard rows checked: {result['clipboard_rows_checked']}",
        f"- Warning count: {result['warning_count']}",
        f"- Source M14: {result['source_m14_folder']}",
        f"- Source M09: {result['source_m09_folder']}",
        f"- Limitation stated: {limitation}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## M15 reason", m15_result.get("reason", "")])
    (test_folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def chain_m03_m04_m06_m09_m14(project: str, matrix_run_id: str, test_id: str) -> Dict[str, Any]:
    prefix = f"{matrix_run_id}_t{test_id}"
    m03 = run_m03(project, run_id=f"{prefix}_m03")
    m04 = run_m04(project, run_id=f"{prefix}_m04")
    m06 = run_m06(project, run_id=f"{prefix}_m06")
    m09 = run_m09(project, run_id=f"{prefix}_m09")
    m14 = run_m14(project, max_rows=3, run_id=f"{prefix}_m14")
    return {"m03": m03, "m04": m04, "m06": m06, "m09": m09, "m14": m14}


def run_test_01(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06 -> M09 -> M14", "Run M15 against latest M14/M09"]
    chain = chain_m03_m04_m06_m09_m14(ctx["project"], ctx["matrix_run_id"], "01")
    notes.append(f"M09 chain status: {chain['m09'].get('status')}")
    notes.append(f"M14 chain status: {chain['m14'].get('status')}")
    evidence = build_m15_evidence(ctx["matrix_run_id"], "01", ctx["test_def"]["slug"])
    m15_result = run_m15(ctx["project"], evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m15_result, notes)


def run_test_02(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = [
        "Source-folder mode with simple-test M14/M09 folders",
        f"M14: {SIMPLE_M14}",
        f"M09: {SIMPLE_M09}",
    ]
    evidence = build_m15_evidence(ctx["matrix_run_id"], "02", ctx["test_def"]["slug"])
    m15_result = run_m15(
        ctx["project"],
        m14_folder=str(SIMPLE_M14),
        m09_folder=str(SIMPLE_M09),
        evidence=evidence,
    )
    return finish_hard_test(
        test_folder,
        ctx["test_def"],
        m15_result,
        notes,
        source_mode=True,
        expected_m14=str(SIMPLE_M14),
        expected_m09=str(SIMPLE_M09),
    )


def run_test_03(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Synthetic M14 with warning rows", "Valid M09 copy from simple test"]
    synth_root = test_folder / "fixtures"
    synth_root.mkdir(parents=True, exist_ok=True)
    m14_folder = create_warning_m14_source(synth_root)
    m09_folder = copy_valid_m09_folder(synth_root)
    notes.append(f"Synthetic M14: {m14_folder}")
    notes.append(f"M09 source: {m09_folder}")
    evidence = build_m15_evidence(ctx["matrix_run_id"], "03", ctx["test_def"]["slug"])
    m15_result = run_m15(
        ctx["project"],
        m14_folder=str(m14_folder),
        m09_folder=str(m09_folder),
        evidence=evidence,
    )
    return finish_hard_test(test_folder, ctx["test_def"], m15_result, notes)


def run_test_04(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Fake M14 folder path", "Valid M09 from simple test"]
    fake_m14 = str(ROOT / "06_output" / "runs" / "M14_FOLDER_DOES_NOT_EXIST" / "m14_copy_visible_activity_rows_multi_select")
    notes.append(f"Fake M14: {fake_m14}")
    evidence = build_m15_evidence(ctx["matrix_run_id"], "04", ctx["test_def"]["slug"])
    m15_result = run_m15(
        ctx["project"],
        m14_folder=fake_m14,
        m09_folder=str(SIMPLE_M09),
        evidence=evidence,
    )
    return finish_hard_test(test_folder, ctx["test_def"], m15_result, notes)


def run_test_05(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Synthetic M09 with no usable Data Date", "Valid M14 from simple test"]
    synth_root = test_folder / "fixtures"
    synth_root.mkdir(parents=True, exist_ok=True)
    m09_folder = create_synthetic_m09_no_date(synth_root)
    notes.append(f"Synthetic M09: {m09_folder}")
    evidence = build_m15_evidence(ctx["matrix_run_id"], "05", ctx["test_def"]["slug"])
    m15_result = run_m15(
        ctx["project"],
        m14_folder=str(SIMPLE_M14),
        m09_folder=str(m09_folder),
        evidence=evidence,
    )
    return finish_hard_test(test_folder, ctx["test_def"], m15_result, notes)


def run_test_06(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Synthetic M14 with header + summary row 001 only", "Valid M09 copy"]
    synth_root = test_folder / "fixtures"
    synth_root.mkdir(parents=True, exist_ok=True)
    m14_folder = create_empty_rows_m14_source(synth_root)
    m09_folder = copy_valid_m09_folder(synth_root)
    notes.append(f"Synthetic M14: {m14_folder}")
    evidence = build_m15_evidence(ctx["matrix_run_id"], "06", ctx["test_def"]["slug"])
    m15_result = run_m15(
        ctx["project"],
        m14_folder=str(m14_folder),
        m09_folder=str(m09_folder),
        evidence=evidence,
    )
    return finish_hard_test(test_folder, ctx["test_def"], m15_result, notes)


HARD_TESTS: List[Dict[str, Any]] = [
    {
        "id": "01",
        "slug": "full_chain_clean_clipboard_report",
        "name": "Full chain clean clipboard report",
        "runner": run_test_01,
    },
    {
        "id": "02",
        "slug": "source_folder_mode_no_p6_touch",
        "name": "Source-folder mode no P6 touch",
        "runner": run_test_02,
    },
    {
        "id": "03",
        "slug": "warning_detection_source",
        "name": "Warning detection source",
        "runner": run_test_03,
    },
    {
        "id": "04",
        "slug": "missing_m14_source_folder",
        "name": "Missing M14 source folder",
        "runner": run_test_04,
    },
    {
        "id": "05",
        "slug": "missing_invalid_data_date",
        "name": "Missing / invalid Data Date",
        "runner": run_test_05,
    },
    {
        "id": "06",
        "slug": "no_clipboard_activity_rows",
        "name": "No clipboard activity rows",
        "runner": run_test_06,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    matrix_run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / matrix_run_id
    (run_root / "m15_hard_test_6").mkdir(parents=True, exist_ok=True)

    print("M15 Hard Testing — 6-test matrix")
    print(f"Run ID: {matrix_run_id}")
    print(f"Project: {project}")
    print("=" * 60)

    results: List[Dict[str, Any]] = []
    for index, test_def in enumerate(HARD_TESTS, start=1):
        print(f"[{index}/6] {test_def['id']} {test_def['name']}")
        test_folder = build_test_folder(matrix_run_id, test_def["id"], test_def["slug"])
        ctx = {"matrix_run_id": matrix_run_id, "project": project, "test_def": test_def}
        try:
            result = test_def["runner"](ctx, test_folder)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            result = {
                "test_id": test_def["id"],
                "test_name": test_def["name"],
                "slug": test_def["slug"],
                "m15_run_id": "",
                "m15_status": "CRASH",
                "m15_reason": str(exc),
                "score": 0,
                "status": "CRASH",
                "score_reason": traceback.format_exc(),
                "test_folder": str(test_folder),
                "setup_notes": [f"crash: {exc}"],
            }
            write_json(test_folder / "test_summary.json", result)
        results.append(result)
        print(
            f"  -> score={result.get('score')} status={result.get('status')} "
            f"m15={result.get('m15_status')}"
        )

    summary = write_hard_summary(matrix_run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']}/{summary['max_score']}")
    print(f"Decision: {summary['decision']}")
    print(f"Summary: {run_root / 'm15_hard_test_6_summary.json'}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M15 Hard Testing 6-test matrix")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    if summary.get("decision") == "M15 STABLE":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
