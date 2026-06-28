"""
M07 Hard Testing — 6-test matrix.

Proves M07 safely captures Activities table snapshot, flexible headers,
footer filtering, and never changes P6 data.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set

ROOT = Path(__file__).resolve().parents[1]
ORCH = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ORCH))
sys.path.insert(0, str(ROOT / "04_modules"))
sys.path.insert(0, str(ROOT / "02_eye"))
sys.path.insert(0, str(ROOT / "02_hand"))
sys.path.insert(0, str(ROOT / "02_accessibility"))

from ty_run import bootstrap_packages  # noqa: E402

bootstrap_packages()

from accessibility.hand import keyboard_tools, window_tools  # noqa: E402
from hand.p6_prepare import prepare_p6_for_test  # noqa: E402
from m03_open_project_by_name import run_m03  # noqa: E402
from m05_close_project_safely import run_m05  # noqa: E402
from m06_go_to_activities import CONFIG_PATH, load_json, run_m06, title_indicates_project_open  # noqa: E402
from m07_hard_summary import write_hard_summary  # noqa: E402
from m07_read_activity_table_snapshot import RunEvidence, run_m07  # noqa: E402

STABILITY_WAIT = 2.5
PASS_OUTCOMES = frozenset({"PASS", "PASS_PARTIAL_SNAPSHOT"})

UNSAFE_STEP_MARKERS = (
    "hotkey('ctrl', 'w')",
    "hotkey(\"ctrl\", \"w\")",
    "press_key('y'",
    "press_key(\"y\"",
    "press yes",
    "press no",
    "hotkey('alt', 'y')",
    "hotkey('ctrl', 's')",
    "hotkey('ctrl', 'o')",
    "open_dialog_ctrl_o",
    "run_m03",
    "navigate: alt+p",
    "alt+v",
    "open_layout",
)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_test_evidence(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / matrix_run_id / "m07_hard_test_6" / f"test_{test_id}_{slug}"
    for sub in ("screenshots", "ocr", "classification", "popup", "extracted"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    return RunEvidence(
        run_id=matrix_run_id,
        folder=folder,
        screenshots_dir=folder / "screenshots",
        ocr_dir=folder / "ocr",
        classification_dir=folder / "classification",
        popup_dir=folder / "popup",
        extracted_dir=folder / "extracted",
    )


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def p6_keyword() -> str:
    return load_json(CONFIG_PATH)["p6_window_title_keyword"]


def matrix_cleanup() -> None:
    try:
        keyboard_tools.press_escape()
        time.sleep(0.5)
    except Exception:  # noqa: BLE001
        pass
    prepare_p6_for_test(p6_keyword())


def ensure_project_open(project: str) -> List[str]:
    notes: List[str] = []
    prep = prepare_p6_for_test(p6_keyword())
    title = (prep.get("window_state") or {}).get("title", "")
    if title_indicates_project_open(title, project):
        notes.append(f"Project already open: {title}")
        return notes
    notes.append("Opening target project via M03")
    tmp = run_m03(project, run_id=f"{new_run_id()}_setup")
    notes.append(f"Setup M03 status: {tmp.get('status')}")
    return notes


def ensure_activities(project: str, notes: List[str]) -> None:
    notes.append("Ensuring Activities via M06")
    m06 = run_m06(project, run_id=f"{new_run_id()}_setup_m06")
    notes.append(f"Setup M06 status: {m06.get('status')}")


def map_pollution_status(m07_result: Dict[str, Any]) -> str:
    reason = (m07_result.get("reason") or "").lower()
    error = (m07_result.get("error") or "") or ""
    if "ocr pollution" in reason or "ocr pollution" in error.lower():
        return "OCR_POLLUTION"
    return m07_result.get("status", "ERROR")


def detect_unsafe_actions(m07_result: Dict[str, Any]) -> bool:
    for step in m07_result.get("steps", []):
        lowered = step.lower()
        if "navigate: alt+p, a" in lowered:
            continue
        if any(marker in lowered for marker in UNSAFE_STEP_MARKERS):
            return True
    return False


def detect_full_screen_ocr(m07_result: Dict[str, Any]) -> bool:
    for path in m07_result.get("ocr_files", []):
        try:
            data = load_json(Path(path))
        except Exception:  # noqa: BLE001
            continue
        meta = data.get("capture_metadata") or {}
        if meta.get("source") and meta.get("source") != "p6_crop_only":
            return True
        if meta.get("used_for_ocr") is False:
            return True
    return False


def flexible_header_ok(m07_result: Dict[str, Any]) -> bool:
    headers = [h.lower() for h in m07_result.get("detected_headers", [])]
    has_id_col = "activity id" in headers or "activity" in headers
    return (
        has_id_col
        and "activity name" in headers
        and "start" in headers
        and "finish" in headers
        and m07_result.get("header_detected", False)
    )


def footer_in_activity_rows(m07_result: Dict[str, Any]) -> bool:
    for row in m07_result.get("sample_rows", []):
        blob = (row.get("raw_line") or "").lower()
        if "access mode" in blob or "data date" in blob or "baseline:" in blob:
            return True
    return False


def score_result(
    test_id: str,
    m07_status: str,
    expected: Set[str],
    m07_result: Dict[str, Any],
    *,
    require_header: bool,
    require_footer_filter: bool,
    require_activity_row: bool,
    unsafe_action: bool,
    full_screen_ocr: bool,
) -> tuple[int, str, str]:
    if unsafe_action:
        return 0, "UNSAFE_ACTION", "Forbidden action detected in M07 steps"
    if full_screen_ocr:
        return 0, "FULL_SCREEN_OCR", "OCR evidence not from P6 crop only"
    if m07_status == "OCR_POLLUTION":
        return 0, "OCR_POLLUTION", "OCR pollution detected"
    if m07_status in ("CRASH", "ERROR"):
        return 0, m07_status, "Unhandled error or crash"

    if m07_status in PASS_OUTCOMES and m07_status not in expected:
        return 0, "FALSE_PASS", f"Unexpected pass (expected one of {sorted(expected)})"

    if test_id == "06" and m07_status != "FAIL_PROJECT_NOT_OPEN":
        return 0, "FALSE_PASS", f"Test 06 expected FAIL_PROJECT_NOT_OPEN, got {m07_status}"

    if test_id == "05" and m07_status == "FAIL_P6_WINDOW_NOT_READY":
        return 1, m07_status, "P6 could not safely restore — acceptable for minimised test"

    if test_id == "06" and m07_status == "FAIL_PROJECT_NOT_OPEN":
        return 1, m07_status, "Project correctly reported not open"

    if require_header and not flexible_header_ok(m07_result):
        if m07_status == "PASS":
            return 0, "FALSE_PASS", "PASS without flexible header detection"
        if m07_status not in expected:
            return 0, m07_status, "Flexible header not detected"

    if require_footer_filter and footer_in_activity_rows(m07_result):
        return 0, "FALSE_PASS", "Footer/status row included in activity rows"

    if require_activity_row and m07_status in PASS_OUTCOMES:
        if int(m07_result.get("visible_row_count", 0)) < 1:
            return 0, "FALSE_PASS", "No visible activity row extracted"

    if m07_status in expected:
        return 1, m07_status, f"Expected outcome: {m07_status}"

    if test_id == "02" and flexible_header_ok(m07_result) and m07_status == "PASS":
        return 1, m07_status, "Flexible header detected: Activity/Activity Name/Start/Finish"

    return 0, m07_status, f"Expected {sorted(expected)}, got {m07_status}"


def finish_hard_test(
    evidence: RunEvidence,
    test_def: Dict[str, Any],
    m07_result: Dict[str, Any],
    setup_notes: List[str],
) -> Dict[str, Any]:
    m07_status = map_pollution_status(m07_result)
    unsafe_action = detect_unsafe_actions(m07_result)
    full_screen_ocr = detect_full_screen_ocr(m07_result)
    score, status_label, score_reason = score_result(
        test_def["id"],
        m07_status,
        test_def["expected"],
        m07_result,
        require_header=bool(test_def.get("require_header")),
        require_footer_filter=bool(test_def.get("require_footer_filter")),
        require_activity_row=bool(test_def.get("require_activity_row")),
        unsafe_action=unsafe_action,
        full_screen_ocr=full_screen_ocr,
    )

    extracted_ok = all(
        (evidence.extracted_dir / name).exists()
        for name in (
            "activity_table_raw_lines.json",
            "activity_table_rows.json",
            "activity_table_snapshot.csv",
        )
    )

    result = {
        "test_id": test_def["id"],
        "test_slug": test_def["slug"],
        "test_name": test_def["name"],
        "project_name": m07_result.get("project_name"),
        "m07_status": m07_status,
        "status": status_label,
        "score": score,
        "score_reason": score_reason,
        "expected": sorted(test_def["expected"]),
        "reason": m07_result.get("reason"),
        "window_title": m07_result.get("window_title"),
        "screen_state": m07_result.get("screen_state"),
        "table_detected": m07_result.get("table_detected"),
        "header_detected": m07_result.get("header_detected"),
        "detected_headers": m07_result.get("detected_headers", []),
        "visible_row_count": m07_result.get("visible_row_count", 0),
        "footer_filtered_count": m07_result.get("footer_filtered_count", 0),
        "extracted_files_ok": extracted_ok,
        "manual_review_required": m07_result.get("manual_review_required", False),
        "setup_notes": setup_notes,
        "screenshots": m07_result.get("screenshots", []),
        "ocr_files": m07_result.get("ocr_files", []),
        "extracted_files": m07_result.get("extracted_files", []),
        "m07_steps": m07_result.get("steps", []),
    }
    write_json(evidence.folder / "result.json", result)

    lines = [
        f"# M07 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- Matrix run ID: {evidence.run_id}",
        f"- M07 status: {m07_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Header detected: {m07_result.get('header_detected')}",
        f"- Detected headers: {m07_result.get('detected_headers', [])}",
        f"- Visible row count: {m07_result.get('visible_row_count', 0)}",
        f"- Footer filtered: {m07_result.get('footer_filtered_count', 0)}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## M07 reason", m07_result.get("reason", "")])
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def run_test_01(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes = ensure_project_open(ctx["project"])
    ensure_activities(ctx["project"], notes)
    m07 = run_m07(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m07, notes)


def run_test_02(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes = ensure_project_open(ctx["project"])
    ensure_activities(ctx["project"], notes)
    m07 = run_m07(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m07, notes)


def run_test_03(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes = ensure_project_open(ctx["project"])
    ensure_activities(ctx["project"], notes)
    m07 = run_m07(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m07, notes)


def run_test_04(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes = ensure_project_open(ctx["project"])
    ensure_activities(ctx["project"], notes)
    window_tools.activate_window_by_title("Cursor")
    time.sleep(0.6)
    notes.append("Cursor window brought to front before M07")
    m07 = run_m07(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m07, notes)


def run_test_05(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes = ensure_project_open(ctx["project"])
    ensure_activities(ctx["project"], notes)
    prepare_p6_for_test(p6_keyword())
    window_tools.minimize_window_by_title(p6_keyword())
    time.sleep(0.8)
    notes.append("P6 minimised before M07")
    m07 = run_m07(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m07, notes)


def run_test_06(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes: List[str] = ["Closing project via M05 setup (outside M07)"]
    m05 = run_m05(ctx["project"], run_id=f"{new_run_id()}_setup_close")
    notes.append(f"Setup M05 status: {m05.get('status')}")
    m07 = run_m07(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m07, notes)


HARD_TESTS: List[Dict[str, Any]] = [
    {
        "id": "01",
        "slug": "normal_activities_table",
        "name": "Normal Activities table",
        "expected": {"PASS", "PASS_PARTIAL_SNAPSHOT"},
        "require_activity_row": True,
        "runner": run_test_01,
    },
    {
        "id": "02",
        "slug": "header_detection_flexible",
        "name": "Header detection flexible",
        "expected": {"PASS"},
        "require_header": True,
        "require_activity_row": True,
        "runner": run_test_02,
    },
    {
        "id": "03",
        "slug": "footer_status_filtering",
        "name": "Footer/status row filtering",
        "expected": {"PASS", "PASS_PARTIAL_SNAPSHOT"},
        "require_footer_filter": True,
        "require_activity_row": True,
        "runner": run_test_03,
    },
    {
        "id": "04",
        "slug": "p6_behind_cursor",
        "name": "P6 behind Cursor",
        "expected": {"PASS", "PASS_PARTIAL_SNAPSHOT"},
        "require_activity_row": True,
        "runner": run_test_04,
    },
    {
        "id": "05",
        "slug": "p6_minimised",
        "name": "P6 minimised",
        "expected": {"PASS", "PASS_PARTIAL_SNAPSHOT", "FAIL_P6_WINDOW_NOT_READY"},
        "runner": run_test_05,
    },
    {
        "id": "06",
        "slug": "project_not_open",
        "name": "Project not open",
        "expected": {"FAIL_PROJECT_NOT_OPEN"},
        "runner": run_test_06,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / run_id
    (run_root / "m07_hard_test_6").mkdir(parents=True, exist_ok=True)

    print("M07 Hard Testing — 6-test matrix")
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
                "m07_status": "CRASH",
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
        print(f"  -> {result.get('m07_status')} score={result.get('score')}")

    summary = write_hard_summary(run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']} / 6")
    print(f"OCR pollution: {summary['ocr_pollution_cases']}")
    print(f"Decision: {summary['decision']}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M07 hard 6-test matrix")
    parser.add_argument("--project", default="Talison 1275")
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    ok = (
        summary["ocr_pollution_cases"] == 0
        and summary["crashes"] == 0
        and summary["false_pass_cases"] == 0
        and summary["full_screen_ocr_cases"] == 0
        and summary["unsafe_actions"] == 0
        and summary["final_score"] >= 5
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
