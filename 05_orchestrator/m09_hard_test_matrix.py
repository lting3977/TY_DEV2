"""
M09 Hard Testing — 6-test matrix.

Proves M09 safely reads visible P6 Data Date evidence without changing anything.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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
from m09_hard_summary import write_hard_summary  # noqa: E402
from m09_read_project_data_date import (  # noqa: E402
    TEST_OCR_P6_HEIGHT,
    RunEvidence,
    build_synthetic_ocr_entries,
    run_m09,
    write_json,
    write_test_ocr_fixture,
)

PASS_OUTCOMES = frozenset({"PASS", "PASS_WITH_DATE_CANDIDATES"})
STRONG_LABELS = frozenset({"data date", "current data date", "project data date"})

P6_TOUCH_MARKERS = (
    "prepare_p6_for_test",
    "capture data_date_screen",
    "navigate: alt+p",
    "running m03",
)

UNSAFE_STEP_MARKERS = (
    "hotkey('ctrl', 's')",
    "hotkey('alt', 'f9')",
    "press_key('f9'",
    "press yes",
    "press no",
    "hotkey('ctrl', 'w')",
)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_test_evidence(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / matrix_run_id / "m09_hard_test_6" / f"test_{test_id}_{slug}"
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


def detect_p6_touched(m09_result: Dict[str, Any], test_used_ocr_fixture: bool) -> bool:
    if not test_used_ocr_fixture:
        return False
    for step in m09_result.get("steps", []):
        lowered = step.lower()
        if any(marker in lowered for marker in P6_TOUCH_MARKERS):
            return True
    return False


def detect_unsafe_actions(m09_result: Dict[str, Any]) -> bool:
    if m09_result.get("status") == "MANUAL_REVIEW_UNSAFE_POPUP":
        return True
    for step in m09_result.get("steps", []):
        lowered = step.lower()
        if any(marker in lowered for marker in UNSAFE_STEP_MARKERS):
            return True
    return False


def detect_ocr_pollution(m09_result: Dict[str, Any]) -> bool:
    reason = (m09_result.get("reason") or "").lower()
    if "ocr pollution" in reason:
        return True
    for path in m09_result.get("ocr_files", []):
        try:
            data = load_json(Path(path))
        except Exception:  # noqa: BLE001
            continue
        blob = " ".join(e.get("normalized", "") for e in data.get("entries", []))
        for word in ("cursor", "chatgpt", "copilot", "ty_dev2"):
            if word in blob:
                return True
    return False


def detect_full_screen_ocr(m09_result: Dict[str, Any]) -> bool:
    for path in m09_result.get("ocr_files", []):
        try:
            data = load_json(Path(path))
        except Exception:  # noqa: BLE001
            continue
        meta = data.get("capture_metadata") or {}
        source = meta.get("source", "")
        if source and source not in ("p6_crop_only", "test_fixture"):
            return True
        if meta.get("used_for_ocr") is False:
            return True
    return False


def extracted_files_ok(evidence: RunEvidence) -> bool:
    return (
        (evidence.extracted_dir / "data_date_candidates.json").exists()
        and (evidence.extracted_dir / "data_date_result.json").exists()
    )


def best_candidate_label(m09_result: Dict[str, Any]) -> str:
    for cand in m09_result.get("candidates") or []:
        if cand.get("date_raw") == m09_result.get("data_date_raw"):
            return str(cand.get("label") or "")
    extracted = m09_result.get("extracted_files") or []
    for path in extracted:
        if str(path).endswith("data_date_result.json"):
            try:
                best = load_json(Path(path)).get("best_candidate") or {}
                return str(best.get("label") or "")
            except Exception:  # noqa: BLE001
                pass
    return ""


def is_clear_pass(m09_result: Dict[str, Any]) -> bool:
    if m09_result.get("status") != "PASS":
        return False
    if float(m09_result.get("confidence", 0)) < 0.75:
        return False
    label = best_candidate_label(m09_result).lower()
    if label in STRONG_LABELS:
        return True
    for cand in m09_result.get("candidates") or []:
        if cand.get("inline_with_label") and cand.get("label") in STRONG_LABELS:
            return True
    return bool(m09_result.get("data_date_found"))


def score_result(
    test_id: str,
    m09_status: str,
    expected: Set[str],
    m09_result: Dict[str, Any],
    *,
    test_used_ocr_fixture: bool,
    require_confidence_075: bool,
    forbid_clear_pass: bool,
    p6_touched: bool,
    unsafe_action: bool,
    ocr_pollution: bool,
    full_screen_ocr: bool,
    extracted_ok: bool,
) -> Tuple[int, str, str]:
    if unsafe_action:
        return 0, "UNSAFE_ACTION", "Forbidden action detected in M09 steps"
    if ocr_pollution:
        return 0, "OCR_POLLUTION", "OCR pollution detected"
    if full_screen_ocr:
        return 0, "FULL_SCREEN_OCR", "OCR evidence not from P6 crop or test fixture"
    if p6_touched:
        return 0, "P6_TOUCHED_WHEN_TEST_OCR_SOURCE_PROVIDED", "P6 touched when test OCR source provided"
    if m09_status in ("CRASH", "ERROR"):
        return 0, m09_status, "Unhandled error or crash"

    if forbid_clear_pass and is_clear_pass(m09_result):
        return 0, "FALSE_PASS", "Clear PASS claimed when Data Date label/context is unclear"

    if m09_status in PASS_OUTCOMES and m09_status not in expected:
        if test_id != "05" or m09_status != "MANUAL_REVIEW_CANNOT_CONFIRM":
            if m09_status == "PASS" and "PASS" not in expected:
                return 0, "FALSE_PASS", f"Unexpected PASS (expected {sorted(expected)})"

    if test_id == "01":
        if m09_status != "PASS":
            return 0, "FALSE_PASS", f"Test 01 expected PASS, got {m09_status}"
        if float(m09_result.get("confidence", 0)) < 0.75:
            return 0, "FALSE_PASS", "Test 01 PASS but confidence < 0.75"
        if not m09_result.get("data_date_found"):
            return 0, "FALSE_PASS", "Test 01 PASS without data_date_found"
        return 1, m09_status, "Data Date found with confidence >= 0.75"

    if test_id == "04":
        if m09_status != "FAIL_PROJECT_NOT_OPEN":
            return 0, "FALSE_PASS", f"Test 04 expected FAIL_PROJECT_NOT_OPEN, got {m09_status}"
        for step in m09_result.get("steps", []):
            if "m03" in step.lower() and "setup" not in step.lower():
                return 0, "FALSE_PASS", "M09 attempted to open project"
        return 1, m09_status, "Project correctly reported not open"

    if test_id == "05":
        if m09_status not in expected:
            return 0, "FALSE_PASS", f"Test 05 expected {sorted(expected)}, got {m09_status}"
        if is_clear_pass(m09_result):
            return 0, "FALSE_PASS", "Clear PASS when label/context unclear"
        if not extracted_ok:
            return 0, "FALSE_PASS", "Missing extracted files"
        return 1, m09_status, "Date candidates without clear Data Date PASS"

    if test_id == "06":
        if m09_status != "FAIL_DATA_DATE_NOT_FOUND":
            return 0, "FALSE_PASS", f"Test 06 expected FAIL_DATA_DATE_NOT_FOUND, got {m09_status}"
        if not extracted_ok:
            return 0, "FALSE_PASS", "Missing extracted files"
        return 1, m09_status, "Controlled failure for no data date evidence"

    if test_id == "03" and m09_status == "FAIL_P6_WINDOW_NOT_READY":
        return 1, m09_status, "P6 could not safely restore — acceptable for minimised test"

    if require_confidence_075 and m09_status == "PASS" and float(m09_result.get("confidence", 0)) < 0.75:
        return 0, "FALSE_PASS", "PASS with confidence below 0.75"

    if m09_status in expected:
        return 1, m09_status, f"Expected outcome: {m09_status}"

    return 0, m09_status, f"Expected {sorted(expected)}, got {m09_status}"


def finish_hard_test(
    evidence: RunEvidence,
    test_def: Dict[str, Any],
    m09_result: Dict[str, Any],
    setup_notes: List[str],
) -> Dict[str, Any]:
    test_used_ocr = bool(test_def.get("test_ocr_fixture"))
    p6_touched = detect_p6_touched(m09_result, test_used_ocr)
    unsafe_action = detect_unsafe_actions(m09_result)
    ocr_pollution = detect_ocr_pollution(m09_result)
    full_screen_ocr = detect_full_screen_ocr(m09_result)
    extracted_ok = extracted_files_ok(evidence)

    m09_status = m09_result.get("status", "ERROR")
    score, status_label, score_reason = score_result(
        test_def["id"],
        m09_status,
        test_def["expected"],
        m09_result,
        test_used_ocr_fixture=test_used_ocr,
        require_confidence_075=bool(test_def.get("require_confidence_075")),
        forbid_clear_pass=bool(test_def.get("forbid_clear_pass")),
        p6_touched=p6_touched,
        unsafe_action=unsafe_action,
        ocr_pollution=ocr_pollution,
        full_screen_ocr=full_screen_ocr,
        extracted_ok=extracted_ok,
    )

    result = {
        "test_id": test_def["id"],
        "test_slug": test_def["slug"],
        "test_name": test_def["name"],
        "project_name": m09_result.get("project_name"),
        "m09_status": m09_status,
        "status": status_label,
        "score": score,
        "score_reason": score_reason,
        "expected": sorted(test_def["expected"]),
        "reason": m09_result.get("reason"),
        "window_title": m09_result.get("window_title", ""),
        "screen_state": m09_result.get("screen_state", ""),
        "data_date_found": m09_result.get("data_date_found", False),
        "data_date_raw": m09_result.get("data_date_raw", ""),
        "data_date_normalized_candidate": m09_result.get("data_date_normalized_candidate", ""),
        "confidence": m09_result.get("confidence", 0.0),
        "candidate_count": m09_result.get("candidate_count", 0),
        "extracted_files_ok": extracted_ok,
        "p6_touched": p6_touched,
        "ocr_pollution": ocr_pollution,
        "full_screen_ocr": full_screen_ocr,
        "unsafe_action": unsafe_action,
        "setup_notes": setup_notes,
        "screenshots": m09_result.get("screenshots", []),
        "ocr_files": m09_result.get("ocr_files", []),
        "classification_files": m09_result.get("classification_files", []),
        "popup_files": m09_result.get("popup_files", []),
        "extracted_files": m09_result.get("extracted_files", []),
        "m09_steps": m09_result.get("steps", []),
        "candidates": m09_result.get("candidates", []),
    }
    write_json(evidence.folder / "result.json", result)

    lines = [
        f"# M09 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- Matrix run ID: {evidence.run_id}",
        f"- M09 status: {m09_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Data date raw: {m09_result.get('data_date_raw', '')}",
        f"- Confidence: {m09_result.get('confidence', 0.0)}",
        f"- Candidate count: {m09_result.get('candidate_count', 0)}",
        f"- Extracted files OK: {extracted_ok}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## M09 reason", m09_result.get("reason", "")])
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def run_test_01(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes = ensure_project_open(ctx["project"])
    ensure_activities(ctx["project"], notes)
    m09 = run_m09(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m09, notes)


def run_test_02(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes = ensure_project_open(ctx["project"])
    window_tools.activate_window_by_title("Cursor")
    time.sleep(0.6)
    notes.append("Cursor window brought to front before M09")
    m09 = run_m09(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m09, notes)


def run_test_03(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes = ensure_project_open(ctx["project"])
    prepare_p6_for_test(p6_keyword())
    window_tools.minimize_window_by_title(p6_keyword())
    time.sleep(0.8)
    notes.append("P6 minimised before M09")
    m09 = run_m09(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    result = finish_hard_test(evidence, ctx["test_def"], m09, notes)
    prepare_p6_for_test(p6_keyword())
    return result


def run_test_04(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes: List[str] = ["Closing project via M05 setup (outside M09)"]
    m05 = run_m05(ctx["project"], run_id=f"{new_run_id()}_setup_close")
    notes.append(f"Setup M05 status: {m05.get('status')}")
    m09 = run_m09(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m09, notes)


def run_test_05(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    footer_y = int(TEST_OCR_P6_HEIGHT * 0.82)
    entries = build_synthetic_ocr_entries(
        [
            ("Activities layout filter all", None),
            ("Activity Start 22-Jun-28 Finish 28-Jun-28", None),
            ("Baseline 20-Jun-26", footer_y),
        ]
    )
    ocr_path = write_test_ocr_fixture(evidence, entries, "05_unclear_label")
    notes = [
        "Synthetic OCR fixture: dates without clear Data Date label",
        f"Fixture: {ocr_path}",
    ]
    m09 = run_m09(
        ctx["project"],
        evidence=evidence,
        run_id=ctx["run_id"],
        ocr_json=ocr_path,
    )
    return finish_hard_test(evidence, ctx["test_def"], m09, notes)


def run_test_06(ctx: Dict[str, Any], evidence: RunEvidence) -> Dict[str, Any]:
    entries = build_synthetic_ocr_entries(
        [
            ("Activities workspace layout filter all", None),
            ("No schedule information here", None),
            ("Access mode shared user admin", int(TEST_OCR_P6_HEIGHT * 0.82)),
        ]
    )
    ocr_path = write_test_ocr_fixture(evidence, entries, "06_no_data_date")
    notes = [
        "Synthetic OCR fixture: no Data Date label and no date candidates",
        f"Fixture: {ocr_path}",
    ]
    m09 = run_m09(
        ctx["project"],
        evidence=evidence,
        run_id=ctx["run_id"],
        ocr_json=ocr_path,
    )
    return finish_hard_test(evidence, ctx["test_def"], m09, notes)


HARD_TESTS: List[Dict[str, Any]] = [
    {
        "id": "01",
        "slug": "normal_data_date_read",
        "name": "Normal data date read",
        "expected": {"PASS"},
        "require_confidence_075": True,
        "runner": run_test_01,
    },
    {
        "id": "02",
        "slug": "p6_behind_cursor",
        "name": "P6 behind Cursor",
        "expected": {"PASS", "PASS_WITH_DATE_CANDIDATES"},
        "runner": run_test_02,
    },
    {
        "id": "03",
        "slug": "p6_minimised",
        "name": "P6 minimised",
        "expected": {"PASS", "PASS_WITH_DATE_CANDIDATES", "FAIL_P6_WINDOW_NOT_READY"},
        "runner": run_test_03,
    },
    {
        "id": "04",
        "slug": "project_not_open",
        "name": "Project not open",
        "expected": {"FAIL_PROJECT_NOT_OPEN"},
        "runner": run_test_04,
    },
    {
        "id": "05",
        "slug": "date_candidates_unclear_label",
        "name": "Date candidates but unclear label",
        "expected": {"PASS_WITH_DATE_CANDIDATES", "MANUAL_REVIEW_CANNOT_CONFIRM"},
        "forbid_clear_pass": True,
        "test_ocr_fixture": True,
        "runner": run_test_05,
    },
    {
        "id": "06",
        "slug": "no_data_date_evidence",
        "name": "No data date evidence",
        "expected": {"FAIL_DATA_DATE_NOT_FOUND"},
        "test_ocr_fixture": True,
        "runner": run_test_06,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / run_id
    (run_root / "m09_hard_test_6").mkdir(parents=True, exist_ok=True)

    print("M09 Hard Testing — 6-test matrix")
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
                "m09_status": "CRASH",
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
        print(f"  -> {result.get('m09_status')} score={result.get('score')}")

    summary = write_hard_summary(run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']} / 6")
    print(f"OCR pollution: {summary['ocr_pollution_cases']}")
    print(f"Decision: {summary['decision']}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M09 hard 6-test matrix")
    parser.add_argument("--project", default="Talison 1275")
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    ok = (
        summary["ocr_pollution_cases"] == 0
        and summary["crashes"] == 0
        and summary["false_pass_cases"] == 0
        and summary["full_screen_ocr_cases"] == 0
        and summary["unsafe_actions"] == 0
        and summary["p6_touched_when_test_ocr_source_provided"] == 0
        and summary["final_score"] >= 5
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
