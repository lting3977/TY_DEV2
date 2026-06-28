"""
M08 Hard Testing — 6-test matrix.

Proves M08 reliably converts M07 snapshot output into structured rows
while preserving raw OCR evidence and staying read-only.
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

from m08_hard_summary import write_hard_summary  # noqa: E402
from m08_read_activity_table_structured import (  # noqa: E402
    MODULE_NAME,
    M07_MODULE_NAME,
    RunEvidence,
    load_json,
    run_m08,
    write_json,
)

PASS_OUTCOMES = frozenset({"PASS", "PASS_WITH_LOW_CONFIDENCE_ROWS"})
KNOWN_M07_HARD_FOLDER = (
    ROOT
    / "06_output"
    / "runs"
    / "20260626_134543"
    / "m07_hard_test_6"
    / "test_01_normal_activities_table"
)
FAKE_M07_FOLDER = ROOT / "06_output" / "runs_M07_FOLDER_DOES_NOT_EXIST_"

P6_CHAIN_MARKERS = (
    "running m03",
    "m03 status",
    "m04 status",
    "m06 status",
    "m07 status",
    "chain_m03",
    "chain_m04",
    "chain_m06",
    "chain_m07",
    "running m03 -> m04 -> m06 -> m07",
)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_test_evidence(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / matrix_run_id / "m08_hard_test_6" / f"test_{test_id}_{slug}"
    structured = folder / "structured"
    structured.mkdir(parents=True, exist_ok=True)
    return RunEvidence(run_id=matrix_run_id, folder=folder, structured_dir=structured)


def detect_p6_touched(m08_result: Dict[str, Any]) -> bool:
    for step in m08_result.get("steps", []):
        lowered = step.lower()
        if any(marker in lowered for marker in P6_CHAIN_MARKERS):
            return True
    return False


def load_m07_activity_rows(m07_folder: Path) -> List[Dict[str, Any]]:
    rows_path = m07_folder / "extracted" / "activity_table_rows.json"
    if not rows_path.exists():
        return []
    data = load_json(rows_path)
    rows = [r for r in data.get("raw_rows", []) if r.get("row_type") == "activity"]
    if not rows:
        rows = [r for r in data.get("normalized_rows", []) if r.get("row_type") == "activity"]
    return rows


def check_raw_ocr_preserved(
    m08_result: Dict[str, Any],
    m07_folder: Optional[Path],
) -> Tuple[bool, List[str]]:
    if not m07_folder or m08_result.get("status") not in PASS_OUTCOMES:
        return True, []

    m07_rows = load_m07_activity_rows(m07_folder)
    if not m07_rows:
        return True, []

    structured_rows = m08_result.get("sample_rows") or []
    structured_path = None
    for path in m08_result.get("structured_files", []):
        if path.endswith("activity_table_structured.json"):
            structured_path = Path(path)
            break
    if structured_path and structured_path.exists():
        structured_rows = load_json(structured_path).get("rows", structured_rows)

    issues: List[str] = []
    m07_by_line = {r.get("raw_line", ""): r for r in m07_rows}
    raw_fields = ("activity_id_raw", "activity_name_raw", "start_raw", "finish_raw")

    for row in structured_rows:
        raw_line = row.get("row_text_raw", "")
        source = m07_by_line.get(raw_line)
        if not source:
            continue

        source_cells = source.get("cells") or []
        for field in raw_fields:
            val = row.get(field)
            if not val:
                continue
            if val not in source_cells:
                issues.append(
                    f"row {row.get('row_index')}: {field}={val!r} not in M07 raw cells"
                )

        for nc in source.get("normalized_cells") or []:
            source_raw = nc.get("raw")
            normalized = nc.get("normalized_candidate") or nc.get("date_candidate")
            if not source_raw or not normalized or source_raw == normalized:
                continue
            for field in raw_fields:
                val = row.get(field)
                if val == normalized and val != source_raw:
                    issues.append(
                        f"row {row.get('row_index')}: {field} uses normalized {val!r} "
                        f"instead of raw {source_raw!r}"
                    )

    return len(issues) == 0, issues


def count_normalized_candidates(m08_result: Dict[str, Any]) -> int:
    count = 0
    structured_path = None
    for path in m08_result.get("structured_files", []):
        if path.endswith("activity_table_structured.json"):
            structured_path = Path(path)
            break
    if not structured_path or not structured_path.exists():
        return count
    for row in load_json(structured_path).get("rows", []):
        if row.get("activity_id_normalized_candidate"):
            count += 1
        if row.get("start_normalized_candidate"):
            count += 1
        if row.get("finish_normalized_candidate"):
            count += 1
    return count


def low_confidence_file_ok(evidence: RunEvidence) -> bool:
    return (evidence.structured_dir / "activity_table_low_confidence_rows.json").exists()


def structured_outputs_ok(evidence: RunEvidence) -> bool:
    names = (
        "activity_table_structured.json",
        "activity_table_structured.csv",
        "activity_table_low_confidence_rows.json",
    )
    return all((evidence.structured_dir / name).exists() for name in names)


def find_low_confidence_row_with_pattern(
    evidence: RunEvidence,
    pattern: str,
) -> bool:
    path = evidence.structured_dir / "activity_table_low_confidence_rows.json"
    if not path.exists():
        return False
    data = load_json(path)
    for row in data.get("rows", []):
        if pattern in (row.get("row_text_raw") or ""):
            return True
    return False


def find_a10z0_row(evidence: RunEvidence) -> Optional[Dict[str, Any]]:
    path = evidence.structured_dir / "activity_table_structured.json"
    if not path.exists():
        return None
    for row in load_json(path).get("rows", []):
        blob = json.dumps(row)
        if "A10z0" in blob or "a10z0" in blob.lower():
            return row
    return None


def score_result(
    test_id: str,
    m08_status: str,
    expected: Set[str],
    *,
    require_rows: bool,
    require_no_p6: bool,
    p6_touched: bool,
    raw_preserved: bool,
    raw_issues: List[str],
    structured_ok: bool,
    low_conf_ok: bool,
    a10z0_ok: bool,
) -> Tuple[int, str, str]:
    if not raw_preserved:
        return 0, "RAW_OCR_OVERWRITTEN", "; ".join(raw_issues[:3]) or "Raw OCR overwritten"
    if require_no_p6 and p6_touched:
        return 0, "P6_TOUCHED_WHEN_M07_FOLDER_PROVIDED", "P6 chain invoked when --m07-folder provided"

    if m08_status in ("CRASH", "ERROR"):
        return 0, m08_status, "Unhandled error or crash"

    if m08_status in PASS_OUTCOMES and m08_status not in expected:
        return 0, "FALSE_PASS", f"Unexpected pass (expected one of {sorted(expected)})"

    if test_id == "05":
        if m08_status == "FAIL_M07_SOURCE_NOT_FOUND":
            return 1, m08_status, "Controlled failure for missing M07 folder"
        return 0, "FALSE_PASS", f"Test 05 expected FAIL_M07_SOURCE_NOT_FOUND, got {m08_status}"

    if test_id == "06":
        if m08_status == "FAIL_NO_ACTIVITY_ROWS":
            return 1, m08_status, "Controlled failure for no activity-like rows"
        return 0, "FALSE_PASS", f"Test 06 expected FAIL_NO_ACTIVITY_ROWS, got {m08_status}"

    if m08_status in PASS_OUTCOMES:
        if require_rows and not structured_ok:
            return 0, "FALSE_PASS", "PASS without structured output files"
        if test_id == "03" and not low_conf_ok:
            return 0, "FALSE_PASS", "Low-confidence row not preserved in low_confidence_rows.json"
        if test_id == "04" and not a10z0_ok:
            return 0, "FALSE_PASS", "A10z0 raw/candidate requirements not met"

    if m08_status in expected:
        return 1, m08_status, f"Expected outcome: {m08_status}"

    return 0, m08_status, f"Expected {sorted(expected)}, got {m08_status}"


def finish_hard_test(
    evidence: RunEvidence,
    test_def: Dict[str, Any],
    m08_result: Dict[str, Any],
    setup_notes: List[str],
    *,
    m07_folder: Optional[Path] = None,
) -> Dict[str, Any]:
    m08_status = m08_result.get("status", "ERROR")
    p6_touched = detect_p6_touched(m08_result) if test_def.get("require_no_p6") else False
    raw_preserved, raw_issues = check_raw_ocr_preserved(m08_result, m07_folder)
    structured_ok = structured_outputs_ok(evidence) if m08_status in PASS_OUTCOMES else True
    low_conf_ok = True
    if test_def["id"] == "03":
        low_conf_ok = find_low_confidence_row_with_pattern(evidence, "001 | 22-Jun-28 | 17-Jul-za")

    a10z0_ok = True
    if test_def["id"] == "04":
        row = find_a10z0_row(evidence)
        if not row:
            a10z0_ok = False
        else:
            raw_has = "A10z0" in (row.get("activity_id_raw") or "") or "A10z0" in (
                row.get("row_text_raw") or ""
            )
            candidate = row.get("activity_id_normalized_candidate")
            a10z0_ok = raw_has and candidate == "A1020"

    score, status_label, score_reason = score_result(
        test_def["id"],
        m08_status,
        test_def["expected"],
        require_rows=bool(test_def.get("require_rows")),
        require_no_p6=bool(test_def.get("require_no_p6")),
        p6_touched=p6_touched,
        raw_preserved=raw_preserved,
        raw_issues=raw_issues,
        structured_ok=structured_ok,
        low_conf_ok=low_conf_ok,
        a10z0_ok=a10z0_ok,
    )

    csv_saved = (evidence.structured_dir / "activity_table_structured.csv").exists()

    result = {
        "test_id": test_def["id"],
        "test_slug": test_def["slug"],
        "test_name": test_def["name"],
        "project_name": m08_result.get("project_name"),
        "m08_status": m08_status,
        "status": status_label,
        "score": score,
        "score_reason": score_reason,
        "expected": sorted(test_def["expected"]),
        "reason": m08_result.get("reason"),
        "source_m07_folder": m08_result.get("source_m07_folder", ""),
        "row_count": m08_result.get("row_count", 0),
        "high_confidence_count": m08_result.get("high_confidence_count", 0),
        "low_confidence_count": m08_result.get("low_confidence_count", 0),
        "structured_files_ok": structured_ok,
        "raw_ocr_preserved": raw_preserved,
        "raw_ocr_issues": raw_issues,
        "p6_touched": p6_touched,
        "normalized_candidate_count": count_normalized_candidates(m08_result),
        "csv_saved": csv_saved,
        "setup_notes": setup_notes,
        "structured_files": m08_result.get("structured_files", []),
        "source_files": m08_result.get("source_files", []),
        "m08_steps": m08_result.get("steps", []),
        "sample_rows": m08_result.get("sample_rows", []),
        "low_confidence_examples": m08_result.get("low_confidence_examples", []),
    }
    write_json(evidence.folder / "result.json", result)

    lines = [
        f"# M08 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- Matrix run ID: {evidence.run_id}",
        f"- M08 status: {m08_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Source M07 folder: {m08_result.get('source_m07_folder', '')}",
        f"- Row count: {m08_result.get('row_count', 0)}",
        f"- High confidence: {m08_result.get('high_confidence_count', 0)}",
        f"- Low confidence: {m08_result.get('low_confidence_count', 0)}",
        f"- Raw OCR preserved: {raw_preserved}",
        f"- P6 touched: {p6_touched}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## M08 reason", m08_result.get("reason", "")])
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def run_m07_chain_for_test(project: str) -> Tuple[Optional[Path], List[str]]:
    from m03_open_project_by_name import run_m03  # noqa: WPS433
    from m04_check_project_opened import run_m04  # noqa: WPS433
    from m06_go_to_activities import run_m06  # noqa: WPS433
    from m07_read_activity_table_snapshot import run_m07  # noqa: WPS433

    notes: List[str] = ["Running M03 -> M04 -> M06 -> M07 setup chain"]
    m03 = run_m03(project, run_id=f"{new_run_id()}_setup_m03")
    notes.append(f"Setup M03 status: {m03.get('status')}")
    m04 = run_m04(project, run_id=f"{new_run_id()}_setup_m04")
    notes.append(f"Setup M04 status: {m04.get('status')}")
    m06 = run_m06(project, run_id=f"{new_run_id()}_setup_m06")
    notes.append(f"Setup M06 status: {m06.get('status')}")
    m07 = run_m07(project, run_id=f"{new_run_id()}_setup_m07")
    notes.append(f"Setup M07 status: {m07.get('status')}")
    if m07.get("status") not in ("PASS", "PASS_PARTIAL_SNAPSHOT"):
        notes.append("M07 chain did not produce usable snapshot")
        return None, notes
    folder = ROOT / "06_output" / "runs" / m07["run_id"] / M07_MODULE_NAME
    notes.append(f"M07 evidence folder: {folder}")
    return folder, notes


def run_test_01(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    m07_folder, notes = run_m07_chain_for_test(ctx["project"])
    if not m07_folder:
        m08 = run_m08(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
        return finish_hard_test(evidence, ctx["test_def"], m08, notes, m07_folder=None)
    m08 = run_m08(
        ctx["project"],
        m07_folder=str(m07_folder),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(evidence, ctx["test_def"], m08, notes, m07_folder=m07_folder)


def run_test_02(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    notes: List[str] = []
    m07_folder = KNOWN_M07_HARD_FOLDER
    if not m07_folder.exists():
        notes.append(f"Known M07 folder missing: {m07_folder}")
        m08 = run_m08(ctx["project"], m07_folder=str(m07_folder), evidence=evidence, run_id=ctx["run_id"])
        return finish_hard_test(evidence, ctx["test_def"], m08, notes, m07_folder=m07_folder)
    notes.append(f"Using existing M07 hard-test folder: {m07_folder}")
    m08 = run_m08(
        ctx["project"],
        m07_folder=str(m07_folder),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(evidence, ctx["test_def"], m08, notes, m07_folder=m07_folder)


def run_test_03(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    notes = [f"Using M07 source with low-confidence row: {KNOWN_M07_HARD_FOLDER}"]
    m08 = run_m08(
        ctx["project"],
        m07_folder=str(KNOWN_M07_HARD_FOLDER),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(
        evidence,
        ctx["test_def"],
        m08,
        notes,
        m07_folder=KNOWN_M07_HARD_FOLDER if KNOWN_M07_HARD_FOLDER.exists() else None,
    )


def run_test_04(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    notes = [f"Using M07 source with OCR ID A10z0: {KNOWN_M07_HARD_FOLDER}"]
    m08 = run_m08(
        ctx["project"],
        m07_folder=str(KNOWN_M07_HARD_FOLDER),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(
        evidence,
        ctx["test_def"],
        m08,
        notes,
        m07_folder=KNOWN_M07_HARD_FOLDER if KNOWN_M07_HARD_FOLDER.exists() else None,
    )


def run_test_05(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    notes = [f"Using fake M07 folder: {FAKE_M07_FOLDER}"]
    m08 = run_m08(
        ctx["project"],
        m07_folder=str(FAKE_M07_FOLDER),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(evidence, ctx["test_def"], m08, notes, m07_folder=None)


def create_no_activity_m07_source(temp_root: Path) -> Path:
    extracted = temp_root / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)

    rows_payload = {
        "visible_row_count": 0,
        "footer_filtered_count": 2,
        "detected_headers": [],
        "header_detected": False,
        "raw_rows": [
            {
                "row_type": "footer",
                "raw_line": "Access Mode: Read Only | Data Date: 25-Jun-26",
                "cells": ["Access Mode: Read Only", "Data Date: 25-Jun-26"],
                "normalized_cells": [],
            },
            {
                "row_type": "footer",
                "raw_line": "Baseline: Current Project",
                "cells": ["Baseline: Current Project"],
                "normalized_cells": [],
            },
        ],
        "normalized_rows": [],
        "filtered_footer_rows": [
            {
                "row_type": "footer",
                "raw_line": "Access Mode: Read Only | Data Date: 25-Jun-26",
                "cells": ["Access Mode: Read Only", "Data Date: 25-Jun-26"],
            }
        ],
    }
    write_json(extracted / "activity_table_rows.json", rows_payload)
    write_json(
        extracted / "activity_table_raw_lines.json",
        {"lines": [r["raw_line"] for r in rows_payload["raw_rows"]]},
    )
    with (extracted / "activity_table_snapshot.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["row_type", "raw_line"])
        for row in rows_payload["raw_rows"]:
            writer.writerow([row["row_type"], row["raw_line"]])
    return temp_root


def run_test_06(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    temp_m07 = evidence.folder / "synthetic_m07_source"
    if temp_m07.exists():
        shutil.rmtree(temp_m07)
    create_no_activity_m07_source(temp_m07)
    notes = [f"Created synthetic M07 source with footer-only rows: {temp_m07}"]
    m08 = run_m08(
        ctx["project"],
        m07_folder=str(temp_m07),
        evidence=evidence,
        run_id=ctx["run_id"],
    )
    return finish_hard_test(evidence, ctx["test_def"], m08, notes, m07_folder=temp_m07)


HARD_TESTS: List[Dict[str, Any]] = [
    {
        "id": "01",
        "slug": "normal_m07_source",
        "name": "Normal M07 source",
        "expected": PASS_OUTCOMES,
        "require_rows": True,
        "runner": run_test_01,
    },
    {
        "id": "02",
        "slug": "existing_m07_hard_test_source",
        "name": "Existing M07 hard-test source",
        "expected": PASS_OUTCOMES,
        "require_rows": True,
        "require_no_p6": True,
        "runner": run_test_02,
    },
    {
        "id": "03",
        "slug": "low_confidence_row_handling",
        "name": "Low-confidence row handling",
        "expected": PASS_OUTCOMES,
        "require_rows": True,
        "runner": run_test_03,
    },
    {
        "id": "04",
        "slug": "ocr_activity_id_normalization",
        "name": "OCR activity ID normalization",
        "expected": PASS_OUTCOMES,
        "require_rows": True,
        "runner": run_test_04,
    },
    {
        "id": "05",
        "slug": "missing_invalid_m07_folder",
        "name": "Missing/invalid M07 folder",
        "expected": {"FAIL_M07_SOURCE_NOT_FOUND"},
        "runner": run_test_05,
    },
    {
        "id": "06",
        "slug": "no_activity_like_rows_source",
        "name": "No activity-like rows source",
        "expected": {"FAIL_NO_ACTIVITY_ROWS"},
        "runner": run_test_06,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / run_id
    (run_root / "m08_hard_test_6").mkdir(parents=True, exist_ok=True)

    print("M08 Hard Testing — 6-test matrix")
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
                "m08_status": "CRASH",
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
        print(f"  -> {result.get('m08_status')} score={result.get('score')}")

    summary = write_hard_summary(run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']} / 6")
    print(f"Raw OCR overwritten: {summary['raw_ocr_overwritten_cases']}")
    print(f"P6 touched (--m07-folder): {summary['p6_touched_when_m07_folder_provided']}")
    print(f"Decision: {summary['decision']}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M08 hard 6-test matrix")
    parser.add_argument("--project", default="Talison 1275")
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    ok = (
        summary["crashes"] == 0
        and summary["false_pass_cases"] == 0
        and summary["raw_ocr_overwritten_cases"] == 0
        and summary["p6_touched_when_m07_folder_provided"] == 0
        and summary["final_score"] >= 5
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
