"""
M11 Hard Testing — 6-test matrix.

Proves M11 reliably generates planner-readable health reports from M08/M09/M10
outputs while preserving source evidence and staying report-only (no P6 touch
when source folders are provided).
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
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
ORCH = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ORCH))
sys.path.insert(0, str(ROOT / "04_modules"))

from m11_generate_planning_health_report import (  # noqa: E402
    M08_MODULE_NAME,
    M09_MODULE_NAME,
    M10_MODULE_NAME,
    RunEvidence,
    load_json,
    run_m11,
    write_json,
)
from m11_hard_summary import write_hard_summary  # noqa: E402

PASS_OUTCOMES = frozenset({"PASS", "PASS_WITH_WARNINGS"})

KNOWN_M08_FOLDER = (
    ROOT / "06_output" / "runs" / "20260626_174042" / "m08_read_activity_table_structured"
)
KNOWN_M09_FOLDER = (
    ROOT / "06_output" / "runs" / "20260626_174043" / "m09_read_project_data_date"
)
KNOWN_M10_FOLDER = (
    ROOT / "06_output" / "runs" / "20260626_174113" / "m10_compare_data_date_to_activity_dates"
)
FAKE_M10_FOLDER = ROOT / "06_output" / "runs_M10_FOLDER_DOES_NOT_EXIST_"

P6_TOUCH_MARKERS = (
    "running m03",
    "chain_m03",
    "chain_m04",
    "chain_m06",
    "chain_m07",
    "chain_m08",
    "chain_m09",
    "chain_m10",
    "upstream chain",
    "read-only chain",
)

REPORT_FILES = (
    "planning_health_report.md",
    "planning_health_report.json",
    "planning_health_summary.csv",
    "warning_register.csv",
)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_test_evidence(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / matrix_run_id / "m11_hard_test_6" / f"test_{test_id}_{slug}"
    report_dir = folder / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    return RunEvidence(run_id=matrix_run_id, folder=folder, report_dir=report_dir)


def detect_p6_touched(m11_result: Dict[str, Any], uses_source_folders: bool) -> bool:
    if not uses_source_folders:
        return False
    for step in m11_result.get("steps", []):
        lowered = step.lower()
        if any(marker in lowered for marker in P6_TOUCH_MARKERS):
            return True
    return False


def report_files_ok(evidence: RunEvidence) -> Tuple[bool, Dict[str, bool]]:
    report = evidence.report_dir
    checks = {name: (report / name).exists() for name in REPORT_FILES}
    return all(checks.values()), checks


def check_report_md_sections(md_path: Path) -> Dict[str, bool]:
    if not md_path.exists():
        return {
            "executive_summary_present": False,
            "warning_register_present": False,
            "limitations_present": False,
            "next_recommendation_present": False,
            "visible_table_only_limitation_stated": False,
        }
    text = md_path.read_text(encoding="utf-8").lower()
    return {
        "executive_summary_present": "## executive summary" in text,
        "warning_register_present": "## warning register" in text,
        "limitations_present": "## limitations" in text,
        "next_recommendation_present": "## next recommendation" in text,
        "visible_table_only_limitation_stated": "visible activities table only" in text
        or "visible activity table only" in text,
    }


def check_source_evidence_preserved(
    m11_result: Dict[str, Any],
    m08_folder: Optional[Path],
    m09_folder: Optional[Path],
    m10_folder: Optional[Path],
) -> Tuple[bool, List[str]]:
    if m11_result.get("status") not in PASS_OUTCOMES:
        return True, []

    issues: List[str] = []
    result_m08 = (m11_result.get("source_m08_folder") or "").strip()
    result_m09 = (m11_result.get("source_m09_folder") or "").strip()
    result_m10 = (m11_result.get("source_m10_folder") or "").strip()

    if m08_folder and result_m08 and str(m08_folder.resolve()) != str(Path(result_m08).resolve()):
        issues.append(f"M08 folder mismatch: {m08_folder} vs {result_m08}")
    if m09_folder and result_m09 and str(m09_folder.resolve()) != str(Path(result_m09).resolve()):
        issues.append(f"M09 folder mismatch: {m09_folder} vs {result_m09}")
    if m10_folder and result_m10 and str(m10_folder.resolve()) != str(Path(result_m10).resolve()):
        issues.append(f"M10 folder mismatch: {m10_folder} vs {result_m10}")

    if m09_folder and m11_result.get("data_date"):
        m09_path = m09_folder / "extracted" / "data_date_result.json"
        if m09_path.exists():
            source = load_json(m09_path)
            norm = (source.get("data_date_normalized_candidate") or "").strip()
            raw = (source.get("data_date_raw") or "").strip()
            result_date = (m11_result.get("data_date") or "").strip()
            if norm and result_date and norm != result_date and raw != result_date:
                issues.append(f"data_date changed: source={norm!r}/{raw!r} result={result_date!r}")

    return len(issues) == 0, issues


def count_warning_csv_rows(warning_csv: Path) -> int:
    if not warning_csv.exists():
        return 0
    with warning_csv.open("r", encoding="utf-8", newline="") as handle:
        return max(0, sum(1 for _ in csv.DictReader(handle)))


def score_result(
    test_id: str,
    m11_status: str,
    expected: Set[str],
    m11_result: Dict[str, Any],
    *,
    uses_source_folders: bool,
    require_warnings: bool,
    require_no_warnings: bool,
    require_rows: bool,
    require_data_date: bool,
    require_report_sections: bool,
    expected_warning_count: Optional[int],
    p6_touched: bool,
    source_preserved: bool,
    source_issues: List[str],
    report_ok: bool,
    sections: Dict[str, bool],
    warning_csv_rows: int,
) -> Tuple[int, str, str]:
    if p6_touched:
        return 0, "P6_TOUCHED_WHEN_SOURCE_FOLDERS_PROVIDED", "P6 touched when source folders provided"
    if not source_preserved:
        return 0, "SOURCE_EVIDENCE_LOST", "; ".join(source_issues[:3]) or "Source evidence lost"

    if m11_status in ("CRASH", "ERROR"):
        return 0, m11_status, "Unhandled error or crash"

    if test_id == "04":
        if m11_status == "FAIL_M10_SOURCE_NOT_FOUND":
            return 1, m11_status, "Controlled failure for missing M10 folder"
        return 0, "FALSE_PASS", f"Test 04 expected FAIL_M10_SOURCE_NOT_FOUND, got {m11_status}"

    if test_id == "05":
        if m11_status == "FAIL_REPORT_SOURCE_INVALID":
            return 1, m11_status, "Controlled failure for invalid report source"
        return 0, "FALSE_PASS", f"Test 05 expected FAIL_REPORT_SOURCE_INVALID, got {m11_status}"

    if m11_status in PASS_OUTCOMES:
        if not report_ok:
            return 0, "REPORT_FILES_MISSING", "One or more report files missing"
        if require_data_date and not m11_result.get("data_date"):
            return 0, "FALSE_PASS", "PASS without Data Date"
        if require_rows and int(m11_result.get("activity_rows_checked", 0)) < 1:
            return 0, "FALSE_PASS", "PASS without activity rows checked"
        if require_warnings and int(m11_result.get("warning_count", 0)) < 1:
            return 0, "FALSE_PASS", "Expected warnings but none found"
        if require_no_warnings and int(m11_result.get("warning_count", 0)) > 0:
            return 0, "FALSE_PASS", "Expected no warnings but warnings found"
        if expected_warning_count is not None and int(m11_result.get("warning_count", 0)) != expected_warning_count:
            return (
                0,
                "FALSE_PASS",
                f"Expected {expected_warning_count} warnings, got {m11_result.get('warning_count', 0)}",
            )
        if require_report_sections:
            missing = [k for k, ok in sections.items() if not ok]
            if missing:
                return 0, "FALSE_PASS", f"Report sections missing: {', '.join(missing)}"
        if test_id == "06":
            if m11_status != "PASS":
                return 0, "FALSE_PASS", f"Test 06 expected PASS, got {m11_status}"
            if warning_csv_rows > 0:
                return 0, "FALSE_PASS", "Test 06 expected empty warning register"
        if m11_status not in expected:
            return 0, "FALSE_PASS", f"Unexpected status (expected {sorted(expected)})"
        return 1, m11_status, f"Expected outcome: {m11_status}"

    if m11_status in expected:
        return 1, m11_status, f"Expected outcome: {m11_status}"

    return 0, m11_status, f"Expected {sorted(expected)}, got {m11_status}"


def finish_hard_test(
    evidence: RunEvidence,
    test_def: Dict[str, Any],
    m11_result: Dict[str, Any],
    setup_notes: List[str],
    *,
    m08_folder: Optional[Path] = None,
    m09_folder: Optional[Path] = None,
    m10_folder: Optional[Path] = None,
) -> Dict[str, Any]:
    uses_source_folders = bool(test_def.get("uses_source_folders"))
    p6_touched = detect_p6_touched(m11_result, uses_source_folders)
    source_preserved, source_issues = check_source_evidence_preserved(
        m11_result, m08_folder, m09_folder, m10_folder
    )
    report_ok, report_checks = report_files_ok(evidence)
    sections = check_report_md_sections(evidence.report_dir / "planning_health_report.md")
    warning_csv_rows = count_warning_csv_rows(evidence.report_dir / "warning_register.csv")

    m11_status = m11_result.get("status", "ERROR")
    score, status_label, score_reason = score_result(
        test_def["id"],
        m11_status,
        test_def["expected"],
        m11_result,
        uses_source_folders=uses_source_folders,
        require_warnings=bool(test_def.get("require_warnings")),
        require_no_warnings=bool(test_def.get("require_no_warnings")),
        require_rows=bool(test_def.get("require_rows")),
        require_data_date=bool(test_def.get("require_data_date")),
        require_report_sections=bool(test_def.get("require_report_sections")),
        expected_warning_count=test_def.get("expected_warning_count"),
        p6_touched=p6_touched,
        source_preserved=source_preserved,
        source_issues=source_issues,
        report_ok=report_ok if m11_status in PASS_OUTCOMES else True,
        sections=sections,
        warning_csv_rows=warning_csv_rows,
    )

    result = {
        "test_id": test_def["id"],
        "test_slug": test_def["slug"],
        "test_name": test_def["name"],
        "project_name": m11_result.get("project_name"),
        "m11_status": m11_status,
        "status": status_label,
        "score": score,
        "score_reason": score_reason,
        "expected": sorted(test_def["expected"]),
        "reason": m11_result.get("reason"),
        "source_m08_folder": m11_result.get("source_m08_folder", ""),
        "source_m09_folder": m11_result.get("source_m09_folder", ""),
        "source_m10_folder": m11_result.get("source_m10_folder", ""),
        "data_date": m11_result.get("data_date", ""),
        "activity_rows_checked": m11_result.get("activity_rows_checked", 0),
        "warning_count": m11_result.get("warning_count", 0),
        "high_severity_count": m11_result.get("high_severity_count", 0),
        "medium_severity_count": m11_result.get("medium_severity_count", 0),
        "low_severity_count": m11_result.get("low_severity_count", 0),
        "report_files_ok": report_ok,
        "planning_health_report_md_saved": report_checks.get("planning_health_report.md", False),
        "planning_health_report_json_saved": report_checks.get("planning_health_report.json", False),
        "planning_health_summary_csv_saved": report_checks.get("planning_health_summary.csv", False),
        "warning_register_csv_saved": report_checks.get("warning_register.csv", False),
        "warning_register_row_count": warning_csv_rows,
        "source_evidence_preserved": source_preserved,
        "source_evidence_issues": source_issues,
        "p6_touched": p6_touched,
        "setup_notes": setup_notes,
        "report_files": m11_result.get("report_files", []),
        "m11_steps": m11_result.get("steps", []),
        **sections,
    }
    write_json(evidence.folder / "result.json", result)

    lines = [
        f"# M11 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- Matrix run ID: {evidence.run_id}",
        f"- M11 status: {m11_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Data Date: {m11_result.get('data_date', '')}",
        f"- Activity rows checked: {m11_result.get('activity_rows_checked', 0)}",
        f"- Warning count: {m11_result.get('warning_count', 0)}",
        f"- Report files OK: {report_ok}",
        f"- Source evidence preserved: {source_preserved}",
        f"- P6 touched: {p6_touched}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## M11 reason", m11_result.get("reason", "")])
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def run_upstream_chain(project: str) -> Tuple[Optional[Path], Optional[Path], Optional[Path], List[str]]:
    from m03_open_project_by_name import run_m03  # noqa: WPS433
    from m04_check_project_opened import run_m04  # noqa: WPS433
    from m06_go_to_activities import run_m06  # noqa: WPS433
    from m07_read_activity_table_snapshot import run_m07  # noqa: WPS433
    from m08_read_activity_table_structured import run_m08  # noqa: WPS433
    from m09_read_project_data_date import run_m09  # noqa: WPS433
    from m10_compare_data_date_to_activity_dates import run_m10  # noqa: WPS433

    notes: List[str] = ["Running M03 -> M04 -> M06 -> M07 -> M08 -> M09 -> M10 setup chain"]
    m03 = run_m03(project, run_id=f"{new_run_id()}_setup_m03")
    notes.append(f"Setup M03 status: {m03.get('status')}")
    m04 = run_m04(project, run_id=f"{new_run_id()}_setup_m04")
    notes.append(f"Setup M04 status: {m04.get('status')}")
    m06 = run_m06(project, run_id=f"{new_run_id()}_setup_m06")
    notes.append(f"Setup M06 status: {m06.get('status')}")
    m07 = run_m07(project, run_id=f"{new_run_id()}_setup_m07")
    notes.append(f"Setup M07 status: {m07.get('status')}")
    if m07.get("status") not in ("PASS", "PASS_PARTIAL_SNAPSHOT"):
        return None, None, None, notes

    m07_folder = ROOT / "06_output" / "runs" / m07["run_id"] / "m07_read_activity_table_snapshot"
    m08 = run_m08(project, m07_folder=str(m07_folder), run_id=f"{new_run_id()}_setup_m08")
    notes.append(f"Setup M08 status: {m08.get('status')}")
    if m08.get("status") not in ("PASS", "PASS_WITH_LOW_CONFIDENCE_ROWS"):
        return None, None, None, notes

    m08_folder = ROOT / "06_output" / "runs" / m08["run_id"] / M08_MODULE_NAME
    m09 = run_m09(project, run_id=f"{new_run_id()}_setup_m09")
    notes.append(f"Setup M09 status: {m09.get('status')}")
    if m09.get("status") not in ("PASS", "PASS_WITH_DATE_CANDIDATES"):
        return None, None, None, notes

    m09_folder = ROOT / "06_output" / "runs" / m09["run_id"] / M09_MODULE_NAME
    m10 = run_m10(
        project,
        m08_folder=str(m08_folder),
        m09_folder=str(m09_folder),
        run_id=f"{new_run_id()}_setup_m10",
    )
    notes.append(f"Setup M10 status: {m10.get('status')}")
    if m10.get("status") not in ("PASS", "PASS_WITH_WARNINGS"):
        return None, None, None, notes

    m10_folder = ROOT / "06_output" / "runs" / m10["run_id"] / M10_MODULE_NAME
    notes.append(f"M08 evidence folder: {m08_folder}")
    notes.append(f"M09 evidence folder: {m09_folder}")
    notes.append(f"M10 evidence folder: {m10_folder}")
    return m08_folder, m09_folder, m10_folder, notes


def copy_module_tree(source: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    return dest


def create_synthetic_invalid_sources(temp_root: Path) -> Tuple[Path, Path, Path]:
    m08 = temp_root / "synthetic_m08"
    m09 = temp_root / "synthetic_m09"
    m10 = temp_root / "synthetic_m10"
    copy_module_tree(KNOWN_M08_FOLDER, m08)
    copy_module_tree(KNOWN_M09_FOLDER, m09)
    copy_module_tree(KNOWN_M10_FOLDER, m10)
    bad_json = m08 / "structured" / "activity_table_structured.json"
    bad_json.write_text("{ invalid json content", encoding="utf-8")
    return m08, m09, m10


def create_synthetic_clean_sources(temp_root: Path) -> Tuple[Path, Path, Path]:
    m08 = temp_root / "synthetic_m08_clean"
    m09 = temp_root / "synthetic_m09_clean"
    m10 = temp_root / "synthetic_m10_clean"

    structured = m08 / "structured"
    structured.mkdir(parents=True, exist_ok=True)
    row = {
        "row_index": 1,
        "activity_id_raw": "A1010",
        "activity_id_normalized_candidate": "A1010",
        "activity_name_raw": "Clean Activity",
        "start_raw": "22-Jun-28",
        "start_normalized_candidate": "22-Jun-28",
        "finish_raw": "28-Jun-28",
        "finish_normalized_candidate": "28-Jun-28",
        "remaining_text": "",
        "row_text_raw": "A1010 | Clean Activity | 22-Jun-28 | 28-Jun-28",
        "confidence": 1.0,
    }
    write_json(
        structured / "activity_table_structured.json",
        {
            "row_count": 1,
            "high_confidence_count": 1,
            "low_confidence_count": 0,
            "rows": [row],
        },
    )
    write_json(structured / "activity_table_low_confidence_rows.json", {"low_confidence_count": 0, "rows": []})
    with (structured / "activity_table_structured.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "row_index",
                "activity_id_raw",
                "activity_id_normalized_candidate",
                "activity_name_raw",
                "start_raw",
                "start_normalized_candidate",
                "finish_raw",
                "finish_normalized_candidate",
                "remaining_text",
                "row_text_raw",
                "confidence",
            ],
        )
        writer.writeheader()
        writer.writerow(row)

    extracted = m09 / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    write_json(
        extracted / "data_date_result.json",
        {
            "data_date_found": True,
            "data_date_raw": "20-jun-26",
            "data_date_normalized_candidate": "20-Jun-26",
            "confidence": 1.0,
            "candidate_count": 1,
            "best_candidate": {
                "date_raw": "20-jun-26",
                "date_normalized_candidate": "20-Jun-26",
                "label": "data date",
                "source": "inline_blob",
                "context": "data date: 20-jun-26",
                "confidence": 1.0,
            },
            "label_visible": True,
        },
    )
    write_json(
        extracted / "data_date_candidates.json",
        {
            "candidate_count": 1,
            "label_visible": True,
            "candidates": [
                {
                    "date_raw": "20-jun-26",
                    "date_normalized_candidate": "20-Jun-26",
                    "label": "data date",
                    "source": "inline_blob",
                    "context": "data date: 20-jun-26",
                    "confidence": 1.0,
                }
            ],
        },
    )

    analysis = m10 / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    comparison = {
        "data_date_parsed": "2026-06-20",
        "data_date_raw": "20-jun-26",
        "data_date_normalized_candidate": "20-Jun-26",
        "activity_rows_checked": 1,
        "comparisons": [
            {
                "row_index": 1,
                "activity_id_raw": "A1010",
                "activity_id_normalized_candidate": "A1010",
                "activity_name_raw": "Clean Activity",
                "start_raw": "22-Jun-28",
                "finish_raw": "28-Jun-28",
                "confidence": 1.0,
                "row_text_raw": "A1010 | Clean Activity | 22-Jun-28 | 28-Jun-28",
                "start_parsed": "2028-06-22",
                "finish_parsed": "2028-06-28",
                "start_before_data_date": False,
                "finish_before_data_date": False,
                "start_parse_issue": False,
                "finish_parse_issue": False,
                "low_confidence_row": False,
                "warnings": [],
            }
        ],
    }
    write_json(analysis / "data_date_activity_comparison.json", comparison)
    with (analysis / "data_date_activity_comparison.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "row_index",
                "activity_id_raw",
                "activity_name_raw",
                "start_raw",
                "finish_raw",
                "start_parsed",
                "finish_parsed",
                "confidence",
                "start_before_data_date",
                "finish_before_data_date",
                "start_parse_issue",
                "finish_parse_issue",
                "low_confidence_row",
                "warnings",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "row_index": 1,
                "activity_id_raw": "A1010",
                "activity_name_raw": "Clean Activity",
                "start_raw": "22-Jun-28",
                "finish_raw": "28-Jun-28",
                "start_parsed": "2028-06-22",
                "finish_parsed": "2028-06-28",
                "confidence": 1.0,
                "start_before_data_date": False,
                "finish_before_data_date": False,
                "start_parse_issue": False,
                "finish_parse_issue": False,
                "low_confidence_row": False,
                "warnings": "",
            }
        )
    write_json(analysis / "warnings.json", {"warning_count": 0, "warnings": []})
    write_json(
        m10 / "result.json",
        {
            "status": "PASS",
            "data_date_parsed": "2026-06-20",
            "activity_rows_checked": 1,
            "warning_count": 0,
            "start_before_data_date_count": 0,
            "finish_before_data_date_count": 0,
            "date_parse_issue_count": 0,
            "low_confidence_count": 0,
        },
    )
    return m08, m09, m10


def run_test_01(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    m08_folder, m09_folder, m10_folder, notes = run_upstream_chain(ctx["project"])
    if not m08_folder or not m09_folder or not m10_folder:
        m11 = run_m11(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
        return finish_hard_test(evidence, ctx["test_def"], m11, notes)
    notes.append("Running M11 against latest chain M08/M09/M10 folders")
    m11 = run_m11(
        ctx["project"],
        m08_folder=str(m08_folder),
        m09_folder=str(m09_folder),
        m10_folder=str(m10_folder),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(
        evidence,
        ctx["test_def"],
        m11,
        notes,
        m08_folder=m08_folder,
        m09_folder=m09_folder,
        m10_folder=m10_folder,
    )


def run_test_02(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    notes = [
        f"Using known M08 folder: {KNOWN_M08_FOLDER}",
        f"Using known M09 folder: {KNOWN_M09_FOLDER}",
        f"Using known M10 folder: {KNOWN_M10_FOLDER}",
        "M11 must not touch P6 when all source folders are provided",
    ]
    m11 = run_m11(
        ctx["project"],
        m08_folder=str(KNOWN_M08_FOLDER),
        m09_folder=str(KNOWN_M09_FOLDER),
        m10_folder=str(KNOWN_M10_FOLDER),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(
        evidence,
        ctx["test_def"],
        m11,
        notes,
        m08_folder=KNOWN_M08_FOLDER if KNOWN_M08_FOLDER.exists() else None,
        m09_folder=KNOWN_M09_FOLDER if KNOWN_M09_FOLDER.exists() else None,
        m10_folder=KNOWN_M10_FOLDER if KNOWN_M10_FOLDER.exists() else None,
    )


def run_test_03(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    notes = [
        "Using warning-source M08/M09/M10 folders from simple test",
        f"M08: {KNOWN_M08_FOLDER}",
        f"M09: {KNOWN_M09_FOLDER}",
        f"M10: {KNOWN_M10_FOLDER}",
        "Expect 5 warnings: 1 low confidence + 4 date parse",
    ]
    m11 = run_m11(
        ctx["project"],
        m08_folder=str(KNOWN_M08_FOLDER),
        m09_folder=str(KNOWN_M09_FOLDER),
        m10_folder=str(KNOWN_M10_FOLDER),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(
        evidence,
        ctx["test_def"],
        m11,
        notes,
        m08_folder=KNOWN_M08_FOLDER if KNOWN_M08_FOLDER.exists() else None,
        m09_folder=KNOWN_M09_FOLDER if KNOWN_M09_FOLDER.exists() else None,
        m10_folder=KNOWN_M10_FOLDER if KNOWN_M10_FOLDER.exists() else None,
    )


def run_test_04(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    notes = [
        f"Valid M08 folder: {KNOWN_M08_FOLDER}",
        f"Valid M09 folder: {KNOWN_M09_FOLDER}",
        f"Fake M10 folder: {FAKE_M10_FOLDER}",
    ]
    m11 = run_m11(
        ctx["project"],
        m08_folder=str(KNOWN_M08_FOLDER),
        m09_folder=str(KNOWN_M09_FOLDER),
        m10_folder=str(FAKE_M10_FOLDER),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(
        evidence,
        ctx["test_def"],
        m11,
        notes,
        m08_folder=KNOWN_M08_FOLDER if KNOWN_M08_FOLDER.exists() else None,
        m09_folder=KNOWN_M09_FOLDER if KNOWN_M09_FOLDER.exists() else None,
    )


def run_test_05(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    m08, m09, m10 = create_synthetic_invalid_sources(evidence.folder)
    notes = [
        f"Synthetic M08 with invalid JSON: {m08}",
        f"Synthetic M09 copy: {m09}",
        f"Synthetic M10 copy: {m10}",
    ]
    m11 = run_m11(
        ctx["project"],
        m08_folder=str(m08),
        m09_folder=str(m09),
        m10_folder=str(m10),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(
        evidence, ctx["test_def"], m11, notes, m08_folder=m08, m09_folder=m09, m10_folder=m10
    )


def run_test_06(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    m08, m09, m10 = create_synthetic_clean_sources(evidence.folder)
    notes = [
        f"Synthetic clean M08: {m08}",
        f"Synthetic clean M09: {m09}",
        f"Synthetic clean M10 (zero warnings): {m10}",
    ]
    m11 = run_m11(
        ctx["project"],
        m08_folder=str(m08),
        m09_folder=str(m09),
        m10_folder=str(m10),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(
        evidence, ctx["test_def"], m11, notes, m08_folder=m08, m09_folder=m09, m10_folder=m10
    )


HARD_TESTS: List[Dict[str, Any]] = [
    {
        "id": "01",
        "slug": "full_chain_normal_report",
        "name": "Full chain normal report",
        "expected": PASS_OUTCOMES,
        "require_rows": True,
        "require_data_date": True,
        "runner": run_test_01,
    },
    {
        "id": "02",
        "slug": "source_folder_mode_no_p6",
        "name": "Source-folder mode no P6 touch",
        "expected": {"PASS_WITH_WARNINGS"},
        "uses_source_folders": True,
        "require_rows": True,
        "require_data_date": True,
        "runner": run_test_02,
    },
    {
        "id": "03",
        "slug": "warning_report_content",
        "name": "Warning report content",
        "expected": {"PASS_WITH_WARNINGS"},
        "uses_source_folders": True,
        "require_warnings": True,
        "require_rows": True,
        "require_data_date": True,
        "require_report_sections": True,
        "expected_warning_count": 5,
        "runner": run_test_03,
    },
    {
        "id": "04",
        "slug": "missing_m10_source_folder",
        "name": "Missing M10 source folder",
        "expected": {"FAIL_M10_SOURCE_NOT_FOUND"},
        "uses_source_folders": True,
        "runner": run_test_04,
    },
    {
        "id": "05",
        "slug": "invalid_report_source",
        "name": "Invalid report source",
        "expected": {"FAIL_REPORT_SOURCE_INVALID"},
        "uses_source_folders": True,
        "runner": run_test_05,
    },
    {
        "id": "06",
        "slug": "no_warning_report",
        "name": "No-warning report",
        "expected": {"PASS"},
        "uses_source_folders": True,
        "require_no_warnings": True,
        "require_rows": True,
        "require_data_date": True,
        "require_report_sections": True,
        "runner": run_test_06,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / run_id
    (run_root / "m11_hard_test_6").mkdir(parents=True, exist_ok=True)

    print("M11 Hard Testing — 6-test matrix")
    print(f"Run ID: {run_id}")
    print(f"Project: {project}")
    print("=" * 60)

    results: List[Dict[str, Any]] = []
    for index, test_def in enumerate(HARD_TESTS, start=1):
        print(f"[{index}/6] {test_def['id']} {test_def['name']}")
        evidence = build_test_evidence(run_id, test_def["id"], test_def["slug"])
        ctx = {"run_id": run_id, "project": project, "test_def": test_def}
        try:
            result = test_def["runner"](ctx, evidence)
        except Exception as exc:  # noqa: BLE001
            result = {
                "test_id": test_def["id"],
                "test_slug": test_def["slug"],
                "test_name": test_def["name"],
                "m11_status": "CRASH",
                "status": "CRASH",
                "score": 0,
                "score_reason": str(exc),
                "reason": traceback.format_exc(),
            }
            write_json(evidence.folder / "result.json", result)
            (evidence.folder / "report.md").write_text(
                f"# CRASH\n\n{traceback.format_exc()}\n", encoding="utf-8"
            )
        results.append(result)
        print(f"  -> {result.get('m11_status')} score={result.get('score')}")

    summary = write_hard_summary(run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']} / 6")
    print(f"P6 touched (source folders): {summary['p6_touched_when_source_folders_provided']}")
    print(f"Decision: {summary['decision']}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M11 hard 6-test matrix")
    parser.add_argument("--project", default="Talison 1275")
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    ok = (
        summary["crashes"] == 0
        and summary["false_pass_cases"] == 0
        and summary["p6_touched_when_source_folders_provided"] == 0
        and summary["source_evidence_lost_cases"] == 0
        and summary["report_files_missing_cases"] == 0
        and summary["final_score"] >= 5
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
