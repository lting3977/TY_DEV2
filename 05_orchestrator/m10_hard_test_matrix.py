"""
M10 Hard Testing — 6-test matrix.

Proves M10 reliably compares M08 activity dates against M09 Data Date
while preserving evidence and staying read-only.
"""

from __future__ import annotations

import argparse
import json
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

from m10_hard_summary import write_hard_summary  # noqa: E402
from m10_compare_data_date_to_activity_dates import (  # noqa: E402
    M08_MODULE_NAME,
    M09_MODULE_NAME,
    RunEvidence,
    load_json,
    run_m10,
    write_json,
)

PASS_OUTCOMES = frozenset({"PASS", "PASS_WITH_WARNINGS"})

KNOWN_M08_FOLDER = (
    ROOT / "06_output" / "runs" / "20260626_170657" / "m08_read_activity_table_structured"
)
KNOWN_M09_FOLDER = (
    ROOT / "06_output" / "runs" / "20260626_170659" / "m09_read_project_data_date"
)
FAKE_M08_FOLDER = ROOT / "06_output" / "runs_M08_FOLDER_DOES_NOT_EXIST_"

P6_TOUCH_MARKERS = (
    "running m03",
    "running m03 -> m04",
    "prepare_p6_for_test",
    "capture data_date_screen",
    "running m03 -> m04 -> m06 -> m07 -> m08 -> m09",
    "upstream chain",
    "chain_m03",
    "chain_m07",
    "chain_m08",
    "chain_m09",
)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_test_evidence(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / matrix_run_id / "m10_hard_test_6" / f"test_{test_id}_{slug}"
    analysis = folder / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    return RunEvidence(run_id=matrix_run_id, folder=folder, analysis_dir=analysis)


def detect_p6_touched(m10_result: Dict[str, Any], uses_source_folders: bool) -> bool:
    if not uses_source_folders:
        return False
    for step in m10_result.get("steps", []):
        lowered = step.lower()
        if any(marker in lowered for marker in P6_TOUCH_MARKERS):
            return True
    return False


def analysis_files_ok(evidence: RunEvidence) -> Tuple[bool, bool, bool]:
    analysis = evidence.analysis_dir
    json_ok = (analysis / "data_date_activity_comparison.json").exists()
    csv_ok = (analysis / "data_date_activity_comparison.csv").exists()
    warnings_ok = (analysis / "warnings.json").exists()
    return json_ok and csv_ok and warnings_ok, warnings_ok, csv_ok


def check_raw_evidence_preserved(
    m10_result: Dict[str, Any],
    m08_folder: Optional[Path],
    m09_folder: Optional[Path],
) -> Tuple[bool, List[str]]:
    if m10_result.get("status") not in PASS_OUTCOMES:
        return True, []

    issues: List[str] = []
    if m09_folder:
        m09_path = m09_folder / "extracted" / "data_date_result.json"
        if m09_path.exists():
            source = load_json(m09_path)
            source_raw = (source.get("data_date_raw") or "").strip()
            result_raw = (m10_result.get("data_date_raw") or "").strip()
            if source_raw and result_raw and source_raw != result_raw:
                issues.append(f"data_date_raw changed: {source_raw!r} -> {result_raw!r}")
            if m10_result.get("data_date_parsed") and not source_raw and not source.get(
                "data_date_normalized_candidate"
            ):
                issues.append("data_date_parsed but M09 source has no raw/normalized date")

    if m08_folder and m10_result.get("analysis_files"):
        comp_path = None
        for path in m10_result.get("analysis_files", []):
            if str(path).endswith("data_date_activity_comparison.json"):
                comp_path = Path(path)
                break
        if comp_path and comp_path.exists():
            m08_rows = load_json(m08_folder / "structured" / "activity_table_structured.json").get(
                "rows", []
            )
            m08_by_index = {r.get("row_index"): r for r in m08_rows}
            for row in load_json(comp_path).get("comparisons", []):
                src = m08_by_index.get(row.get("row_index"))
                if not src:
                    continue
                for field in ("start_raw", "finish_raw", "activity_id_raw", "row_text_raw"):
                    if row.get(field) != src.get(field):
                        issues.append(
                            f"row {row.get('row_index')}: {field} mismatch "
                            f"{src.get(field)!r} -> {row.get(field)!r}"
                        )
    return len(issues) == 0, issues


def check_data_date_not_invented(
    m10_result: Dict[str, Any],
    m09_folder: Optional[Path],
) -> Tuple[bool, str]:
    parsed = (m10_result.get("data_date_parsed") or "").strip()
    if not parsed or not m09_folder:
        return True, ""
    m09_path = m09_folder / "extracted" / "data_date_result.json"
    if not m09_path.exists():
        return True, ""
    source = load_json(m09_path)
    raw = (source.get("data_date_raw") or "").strip()
    norm = (source.get("data_date_normalized_candidate") or "").strip()
    if not raw and not norm and parsed:
        return False, "Data Date parsed without M09 raw/normalized source"
    return True, ""


def score_result(
    test_id: str,
    m10_status: str,
    expected: Set[str],
    m10_result: Dict[str, Any],
    *,
    uses_source_folders: bool,
    require_warnings: bool,
    require_rows: bool,
    require_data_date: bool,
    p6_touched: bool,
    raw_preserved: bool,
    raw_issues: List[str],
    data_date_invented: bool,
    invented_reason: str,
    analysis_ok: bool,
    warnings_ok: bool,
) -> Tuple[int, str, str]:
    if p6_touched:
        return 0, "P6_TOUCHED_WHEN_SOURCE_FOLDERS_PROVIDED", "P6 touched when source folders provided"
    if data_date_invented:
        return 0, "DATA_DATE_INVENTED", invented_reason or "Data Date invented"
    if not raw_preserved:
        return 0, "RAW_EVIDENCE_LOST", "; ".join(raw_issues[:3]) or "Raw evidence lost"

    if m10_status in ("CRASH", "ERROR"):
        return 0, m10_status, "Unhandled error or crash"

    if test_id == "04":
        if m10_status == "FAIL_M08_SOURCE_NOT_FOUND":
            return 1, m10_status, "Controlled failure for missing M08 folder"
        return 0, "FALSE_PASS", f"Test 04 expected FAIL_M08_SOURCE_NOT_FOUND, got {m10_status}"

    if test_id == "05":
        if m10_status == "FAIL_DATA_DATE_MISSING":
            return 1, m10_status, "Controlled failure for missing Data Date"
        return 0, "FALSE_PASS", f"Test 05 expected FAIL_DATA_DATE_MISSING, got {m10_status}"

    if test_id == "06":
        if m10_status == "FAIL_NO_ACTIVITY_ROWS":
            return 1, m10_status, "Controlled failure for no activity rows"
        return 0, "FALSE_PASS", f"Test 06 expected FAIL_NO_ACTIVITY_ROWS, got {m10_status}"

    if test_id == "03":
        if m10_status != "PASS_WITH_WARNINGS":
            return 0, "FALSE_PASS", f"Test 03 expected PASS_WITH_WARNINGS, got {m10_status}"
        if int(m10_result.get("warning_count", 0)) < 1:
            return 0, "FALSE_PASS", "Test 03 expected warnings but warning_count is 0"
        if not warnings_ok:
            return 0, "FALSE_PASS", "Test 03 missing warnings.json"
        return 1, m10_status, "Warnings detected and saved"

    if m10_status in PASS_OUTCOMES:
        if require_data_date and not m10_result.get("data_date_parsed"):
            return 0, "FALSE_PASS", "PASS without parsed Data Date"
        if require_rows and int(m10_result.get("activity_rows_checked", 0)) < 1:
            return 0, "FALSE_PASS", "PASS without activity rows checked"
        if require_warnings and int(m10_result.get("warning_count", 0)) < 1:
            return 0, "FALSE_PASS", "Expected warnings but none found"
        if m10_status not in expected:
            return 0, "FALSE_PASS", f"Unexpected status (expected {sorted(expected)})"
        if not analysis_ok:
            return 0, "FALSE_PASS", "Comparison analysis files missing"
        return 1, m10_status, f"Expected outcome: {m10_status}"

    if m10_status in expected:
        return 1, m10_status, f"Expected outcome: {m10_status}"

    return 0, m10_status, f"Expected {sorted(expected)}, got {m10_status}"


def finish_hard_test(
    evidence: RunEvidence,
    test_def: Dict[str, Any],
    m10_result: Dict[str, Any],
    setup_notes: List[str],
    *,
    m08_folder: Optional[Path] = None,
    m09_folder: Optional[Path] = None,
) -> Dict[str, Any]:
    uses_source_folders = bool(test_def.get("uses_source_folders"))
    p6_touched = detect_p6_touched(m10_result, uses_source_folders)
    raw_preserved, raw_issues = check_raw_evidence_preserved(m10_result, m08_folder, m09_folder)
    not_invented, invented_reason = check_data_date_not_invented(m10_result, m09_folder)
    analysis_ok, warnings_ok, csv_ok = analysis_files_ok(evidence)

    m10_status = m10_result.get("status", "ERROR")
    score, status_label, score_reason = score_result(
        test_def["id"],
        m10_status,
        test_def["expected"],
        m10_result,
        uses_source_folders=uses_source_folders,
        require_warnings=bool(test_def.get("require_warnings")),
        require_rows=bool(test_def.get("require_rows")),
        require_data_date=bool(test_def.get("require_data_date")),
        p6_touched=p6_touched,
        raw_preserved=raw_preserved,
        raw_issues=raw_issues,
        data_date_invented=not not_invented,
        invented_reason=invented_reason,
        analysis_ok=analysis_ok if m10_status in PASS_OUTCOMES else True,
        warnings_ok=warnings_ok,
    )

    result = {
        "test_id": test_def["id"],
        "test_slug": test_def["slug"],
        "test_name": test_def["name"],
        "project_name": m10_result.get("project_name"),
        "m10_status": m10_status,
        "status": status_label,
        "score": score,
        "score_reason": score_reason,
        "expected": sorted(test_def["expected"]),
        "reason": m10_result.get("reason"),
        "source_m08_folder": m10_result.get("source_m08_folder", ""),
        "source_m09_folder": m10_result.get("source_m09_folder", ""),
        "data_date_raw": m10_result.get("data_date_raw", ""),
        "data_date_normalized_candidate": m10_result.get("data_date_normalized_candidate", ""),
        "data_date_parsed": m10_result.get("data_date_parsed", ""),
        "activity_rows_checked": m10_result.get("activity_rows_checked", 0),
        "start_before_data_date_count": m10_result.get("start_before_data_date_count", 0),
        "finish_before_data_date_count": m10_result.get("finish_before_data_date_count", 0),
        "date_parse_issue_count": m10_result.get("date_parse_issue_count", 0),
        "low_confidence_count": m10_result.get("low_confidence_count", 0),
        "warning_count": m10_result.get("warning_count", 0),
        "analysis_files_ok": analysis_ok,
        "warnings_json_saved": warnings_ok,
        "comparison_csv_saved": csv_ok,
        "raw_evidence_preserved": raw_preserved,
        "raw_evidence_issues": raw_issues,
        "p6_touched": p6_touched,
        "setup_notes": setup_notes,
        "analysis_files": m10_result.get("analysis_files", []),
        "sample_warnings": m10_result.get("sample_warnings", []),
        "m10_steps": m10_result.get("steps", []),
    }
    write_json(evidence.folder / "result.json", result)

    lines = [
        f"# M10 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- Matrix run ID: {evidence.run_id}",
        f"- M10 status: {m10_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Data Date parsed: {m10_result.get('data_date_parsed', '')}",
        f"- Activity rows checked: {m10_result.get('activity_rows_checked', 0)}",
        f"- Warning count: {m10_result.get('warning_count', 0)}",
        f"- Analysis files OK: {analysis_ok}",
        f"- Raw evidence preserved: {raw_preserved}",
        f"- P6 touched: {p6_touched}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## M10 reason", m10_result.get("reason", "")])
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def run_upstream_chain(project: str) -> Tuple[Optional[Path], Optional[Path], List[str]]:
    from m03_open_project_by_name import run_m03  # noqa: WPS433
    from m04_check_project_opened import run_m04  # noqa: WPS433
    from m06_go_to_activities import run_m06  # noqa: WPS433
    from m07_read_activity_table_snapshot import run_m07  # noqa: WPS433
    from m08_read_activity_table_structured import run_m08  # noqa: WPS433
    from m09_read_project_data_date import run_m09  # noqa: WPS433

    notes: List[str] = ["Running M03 -> M04 -> M06 -> M07 -> M08 -> M09 setup chain"]
    m03 = run_m03(project, run_id=f"{new_run_id()}_setup_m03")
    notes.append(f"Setup M03 status: {m03.get('status')}")
    m04 = run_m04(project, run_id=f"{new_run_id()}_setup_m04")
    notes.append(f"Setup M04 status: {m04.get('status')}")
    m06 = run_m06(project, run_id=f"{new_run_id()}_setup_m06")
    notes.append(f"Setup M06 status: {m06.get('status')}")
    m07 = run_m07(project, run_id=f"{new_run_id()}_setup_m07")
    notes.append(f"Setup M07 status: {m07.get('status')}")
    if m07.get("status") not in ("PASS", "PASS_PARTIAL_SNAPSHOT"):
        return None, None, notes

    m07_folder = ROOT / "06_output" / "runs" / m07["run_id"] / "m07_read_activity_table_snapshot"
    m08 = run_m08(project, m07_folder=str(m07_folder), run_id=f"{new_run_id()}_setup_m08")
    notes.append(f"Setup M08 status: {m08.get('status')}")
    if m08.get("status") not in ("PASS", "PASS_WITH_LOW_CONFIDENCE_ROWS"):
        return None, None, notes

    m09 = run_m09(project, run_id=f"{new_run_id()}_setup_m09")
    notes.append(f"Setup M09 status: {m09.get('status')}")
    if m09.get("status") not in ("PASS", "PASS_WITH_DATE_CANDIDATES"):
        return None, None, notes

    m08_folder = ROOT / "06_output" / "runs" / m08["run_id"] / M08_MODULE_NAME
    m09_folder = ROOT / "06_output" / "runs" / m09["run_id"] / M09_MODULE_NAME
    notes.append(f"M08 evidence folder: {m08_folder}")
    notes.append(f"M09 evidence folder: {m09_folder}")
    return m08_folder, m09_folder, notes


def create_synthetic_m09_no_data_date(temp_root: Path) -> Path:
    extracted = temp_root / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    write_json(
        extracted / "data_date_result.json",
        {
            "data_date_found": False,
            "data_date_raw": "",
            "data_date_normalized_candidate": "",
            "confidence": 0.0,
            "candidate_count": 0,
            "best_candidate": None,
            "label_visible": False,
        },
    )
    return temp_root


def create_synthetic_m08_no_rows(temp_root: Path) -> Path:
    structured = temp_root / "structured"
    structured.mkdir(parents=True, exist_ok=True)
    write_json(
        structured / "activity_table_structured.json",
        {
            "row_count": 0,
            "high_confidence_count": 0,
            "low_confidence_count": 0,
            "rows": [],
        },
    )
    return temp_root


def copy_m08_structure(source: Path, dest: Path) -> Path:
    src_file = source / "structured" / "activity_table_structured.json"
    dest_structured = dest / "structured"
    dest_structured.mkdir(parents=True, exist_ok=True)
    write_json(dest_structured / "activity_table_structured.json", load_json(src_file))
    return dest


def run_test_01(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    m08_folder, m09_folder, notes = run_upstream_chain(ctx["project"])
    if not m08_folder or not m09_folder:
        m10 = run_m10(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
        return finish_hard_test(evidence, ctx["test_def"], m10, notes)
    m10 = run_m10(
        ctx["project"],
        m08_folder=str(m08_folder),
        m09_folder=str(m09_folder),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(
        evidence, ctx["test_def"], m10, notes, m08_folder=m08_folder, m09_folder=m09_folder
    )


def run_test_02(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    notes = [
        f"Using known M08 folder: {KNOWN_M08_FOLDER}",
        f"Using known M09 folder: {KNOWN_M09_FOLDER}",
    ]
    m10 = run_m10(
        ctx["project"],
        m08_folder=str(KNOWN_M08_FOLDER),
        m09_folder=str(KNOWN_M09_FOLDER),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(
        evidence,
        ctx["test_def"],
        m10,
        notes,
        m08_folder=KNOWN_M08_FOLDER if KNOWN_M08_FOLDER.exists() else None,
        m09_folder=KNOWN_M09_FOLDER if KNOWN_M09_FOLDER.exists() else None,
    )


def run_test_03(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    notes = [
        "Using warning-source M08/M09 folders from simple test",
        f"M08: {KNOWN_M08_FOLDER}",
        f"M09: {KNOWN_M09_FOLDER}",
    ]
    m10 = run_m10(
        ctx["project"],
        m08_folder=str(KNOWN_M08_FOLDER),
        m09_folder=str(KNOWN_M09_FOLDER),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(
        evidence,
        ctx["test_def"],
        m10,
        notes,
        m08_folder=KNOWN_M08_FOLDER if KNOWN_M08_FOLDER.exists() else None,
        m09_folder=KNOWN_M09_FOLDER if KNOWN_M09_FOLDER.exists() else None,
    )


def run_test_04(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    notes = [
        f"Fake M08 folder: {FAKE_M08_FOLDER}",
        f"Valid M09 folder: {KNOWN_M09_FOLDER}",
    ]
    m10 = run_m10(
        ctx["project"],
        m08_folder=str(FAKE_M08_FOLDER),
        m09_folder=str(KNOWN_M09_FOLDER),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(
        evidence,
        ctx["test_def"],
        m10,
        notes,
        m09_folder=KNOWN_M09_FOLDER if KNOWN_M09_FOLDER.exists() else None,
    )


def run_test_05(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    temp_m08 = evidence.folder / "synthetic_m08_source"
    temp_m09 = evidence.folder / "synthetic_m09_source"
    if temp_m08.exists():
        import shutil

        shutil.rmtree(temp_m08)
    if temp_m09.exists():
        import shutil

        shutil.rmtree(temp_m09)
    copy_m08_structure(KNOWN_M08_FOLDER, temp_m08)
    create_synthetic_m09_no_data_date(temp_m09)
    notes = [
        f"Valid M08 copy: {temp_m08}",
        f"Synthetic M09 without Data Date: {temp_m09}",
    ]
    m10 = run_m10(
        ctx["project"],
        m08_folder=str(temp_m08),
        m09_folder=str(temp_m09),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(
        evidence, ctx["test_def"], m10, notes, m08_folder=temp_m08, m09_folder=temp_m09
    )


def run_test_06(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    temp_m08 = evidence.folder / "synthetic_m08_source"
    temp_m09 = evidence.folder / "synthetic_m09_copy"
    if temp_m08.exists():
        import shutil

        shutil.rmtree(temp_m08)
    if temp_m09.exists():
        import shutil

        shutil.rmtree(temp_m09)
    create_synthetic_m08_no_rows(temp_m08)
    temp_m09.mkdir(parents=True, exist_ok=True)
    extracted = temp_m09 / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    write_json(
        extracted / "data_date_result.json",
        load_json(KNOWN_M09_FOLDER / "extracted" / "data_date_result.json"),
    )
    notes = [
        f"Synthetic M08 with no rows: {temp_m08}",
        f"Valid M09 copy: {temp_m09}",
    ]
    m10 = run_m10(
        ctx["project"],
        m08_folder=str(temp_m08),
        m09_folder=str(temp_m09),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(
        evidence, ctx["test_def"], m10, notes, m08_folder=temp_m08, m09_folder=temp_m09
    )


HARD_TESTS: List[Dict[str, Any]] = [
    {
        "id": "01",
        "slug": "full_chain_normal_source",
        "name": "Full chain normal source",
        "expected": PASS_OUTCOMES,
        "require_rows": True,
        "require_data_date": True,
        "runner": run_test_01,
    },
    {
        "id": "02",
        "slug": "source_folder_mode_no_p6",
        "name": "Source-folder mode no P6 touch",
        "expected": PASS_OUTCOMES,
        "uses_source_folders": True,
        "require_rows": True,
        "require_data_date": True,
        "runner": run_test_02,
    },
    {
        "id": "03",
        "slug": "warning_detection_source",
        "name": "Warning detection source",
        "expected": {"PASS_WITH_WARNINGS"},
        "uses_source_folders": True,
        "require_warnings": True,
        "require_rows": True,
        "require_data_date": True,
        "runner": run_test_03,
    },
    {
        "id": "04",
        "slug": "missing_m08_source_folder",
        "name": "Missing M08 source folder",
        "expected": {"FAIL_M08_SOURCE_NOT_FOUND"},
        "uses_source_folders": True,
        "runner": run_test_04,
    },
    {
        "id": "05",
        "slug": "missing_invalid_data_date",
        "name": "Missing / invalid Data Date",
        "expected": {"FAIL_DATA_DATE_MISSING"},
        "uses_source_folders": True,
        "runner": run_test_05,
    },
    {
        "id": "06",
        "slug": "no_activity_rows",
        "name": "No activity rows",
        "expected": {"FAIL_NO_ACTIVITY_ROWS"},
        "uses_source_folders": True,
        "runner": run_test_06,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / run_id
    (run_root / "m10_hard_test_6").mkdir(parents=True, exist_ok=True)

    print("M10 Hard Testing — 6-test matrix")
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
                "m10_status": "CRASH",
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
        print(f"  -> {result.get('m10_status')} score={result.get('score')}")

    summary = write_hard_summary(run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']} / 6")
    print(f"P6 touched (source folders): {summary['p6_touched_when_source_folders_provided']}")
    print(f"Decision: {summary['decision']}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M10 hard 6-test matrix")
    parser.add_argument("--project", default="Talison 1275")
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    ok = (
        summary["crashes"] == 0
        and summary["false_pass_cases"] == 0
        and summary["p6_touched_when_source_folders_provided"] == 0
        and summary["data_date_invented_cases"] == 0
        and summary["raw_evidence_lost_cases"] == 0
        and summary["final_score"] >= 5
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
