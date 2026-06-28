"""
M06 Hard Testing — 6-test matrix.

Proves M06 confirms project open, navigates via Alt+P, A when needed,
and confirms Activities workspace after navigation.
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
from m06_go_to_activities import (  # noqa: E402
    CONFIG_PATH,
    RunEvidence,
    load_json,
    run_m06,
    title_indicates_project_open,
)
from m06_hard_summary import write_hard_summary  # noqa: E402

STABILITY_WAIT = 2.5
NAV_STEP_MARKER = "navigate: alt+p, a (project -> activities)"

UNSAFE_STEP_MARKERS = (
    "hotkey('ctrl', 'w')",
    "hotkey(\"ctrl\", \"w\")",
    "press_key('y'",
    "press_key(\"y\"",
    "press yes",
    "press no",
    "hotkey('alt', 'y')",
    "hotkey(\"alt\", \"y\")",
    "hotkey('ctrl', 's')",
    "hotkey('ctrl', 'o')",
    "open_dialog_ctrl_o",
    "run_m03",
    "m03_open",
)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_test_evidence(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / matrix_run_id / "m06_hard_test_6" / f"test_{test_id}_{slug}"
    for sub in ("screenshots", "ocr", "classification", "popup"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    return RunEvidence(
        run_id=matrix_run_id,
        folder=folder,
        screenshots_dir=folder / "screenshots",
        ocr_dir=folder / "ocr",
        classification_dir=folder / "classification",
        popup_dir=folder / "popup",
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


def setup_navigate_activities(notes: List[str]) -> None:
    prepare_p6_for_test(p6_keyword())
    notes.append("Setup: Alt+P, A (ensure Activities workspace)")
    keyboard_tools.hotkey("alt", "p")
    time.sleep(0.5)
    keyboard_tools.press_key("a")
    time.sleep(STABILITY_WAIT)


def setup_p6_menu_hotkey(alt_letter: str) -> None:
    """Orchestrator setup only — documented P6 shortcuts (Phase 1 blocks alt+n)."""
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.5
    pyautogui.hotkey("alt", alt_letter)


def setup_navigate_projects(notes: List[str]) -> None:
    prepare_p6_for_test(p6_keyword())
    notes.append("Setup: Alt+N, P (Navigate -> Projects)")
    setup_p6_menu_hotkey("n")
    time.sleep(0.5)
    keyboard_tools.press_key("p")
    time.sleep(STABILITY_WAIT)


def setup_navigate_wbs(notes: List[str]) -> None:
    prepare_p6_for_test(p6_keyword())
    notes.append("Setup: Alt+P, W (Project -> WBS)")
    keyboard_tools.hotkey("alt", "p")
    time.sleep(0.5)
    keyboard_tools.press_key("w")
    time.sleep(STABILITY_WAIT)


def map_pollution_status(m06_result: Dict[str, Any]) -> str:
    reason = (m06_result.get("reason") or "").lower()
    error = (m06_result.get("error") or "") or ""
    if "ocr pollution" in reason or "ocr pollution" in error.lower():
        return "OCR_POLLUTION"
    return m06_result.get("status", "ERROR")


def navigation_used(m06_result: Dict[str, Any]) -> bool:
    for step in m06_result.get("steps", []):
        if NAV_STEP_MARKER in step.lower():
            return True
    return False


def detect_unsafe_actions(m06_result: Dict[str, Any]) -> bool:
    for step in m06_result.get("steps", []):
        lowered = step.lower()
        if any(marker in lowered for marker in UNSAFE_STEP_MARKERS):
            return True
    return False


def detect_full_screen_ocr(m06_result: Dict[str, Any]) -> bool:
    for path in m06_result.get("ocr_files", []):
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


def score_result(
    test_id: str,
    m06_status: str,
    expected: Set[str],
    m06_result: Dict[str, Any],
    *,
    navigation_required: bool,
    unsafe_action: bool,
    full_screen_ocr: bool,
) -> tuple[int, str, str]:
    if unsafe_action:
        return 0, "UNSAFE_ACTION", "Forbidden keyboard or open-project action in M06 steps"
    if full_screen_ocr:
        return 0, "FULL_SCREEN_OCR", "OCR evidence not from P6 crop only"
    if m06_status == "OCR_POLLUTION":
        return 0, "OCR_POLLUTION", "OCR pollution detected"
    if m06_status in ("CRASH", "ERROR"):
        return 0, m06_status, "Unhandled error or crash"

    nav_used = navigation_used(m06_result)

    if navigation_required and m06_status == "PASS_ALREADY_IN_ACTIVITIES":
        return (
            0,
            "FALSE_PASS",
            "PASS_ALREADY_IN_ACTIVITIES after non-Activities setup — navigation not exercised",
        )
    if navigation_required and m06_status == "PASS" and not nav_used:
        return 0, "FALSE_PASS", "PASS without Alt+P, A navigation step recorded"

    if m06_status in expected:
        if test_id == "06" and m06_status != "FAIL_PROJECT_NOT_OPEN":
            return 0, "FALSE_PASS", f"Test 06 expected FAIL_PROJECT_NOT_OPEN, got {m06_status}"
        if test_id == "02" and m06_status == "PASS" and not nav_used:
            return 0, "FALSE_PASS", "Test 02 PASS without Alt+P, A navigation"
        if test_id == "03" and m06_status == "PASS" and not nav_used:
            return 0, "FALSE_PASS", "Test 03 PASS without Alt+P, A navigation"
        return 1, m06_status, f"Expected outcome: {m06_status}"

    if m06_status in ("PASS", "PASS_ALREADY_IN_ACTIVITIES") and m06_status not in expected:
        return 0, "FALSE_PASS", f"Unexpected pass (expected one of {sorted(expected)})"

    if test_id == "05" and m06_status == "FAIL_P6_WINDOW_NOT_READY":
        return 1, m06_status, "P6 could not safely restore — acceptable for minimised test"

    if test_id == "06" and m06_status == "FAIL_PROJECT_NOT_OPEN":
        return 1, m06_status, "Project correctly reported not open"

    return 0, m06_status, f"Expected {sorted(expected)}, got {m06_status}"


def finish_hard_test(
    evidence: RunEvidence,
    test_def: Dict[str, Any],
    m06_result: Dict[str, Any],
    setup_notes: List[str],
) -> Dict[str, Any]:
    m06_status = map_pollution_status(m06_result)
    navigation_required = bool(test_def.get("navigation_required"))
    nav_used = navigation_used(m06_result)
    unsafe_action = detect_unsafe_actions(m06_result)
    full_screen_ocr = detect_full_screen_ocr(m06_result)
    score, status_label, score_reason = score_result(
        test_def["id"],
        m06_status,
        test_def["expected"],
        m06_result,
        navigation_required=navigation_required,
        unsafe_action=unsafe_action,
        full_screen_ocr=full_screen_ocr,
    )

    result = {
        "test_id": test_def["id"],
        "test_slug": test_def["slug"],
        "test_name": test_def["name"],
        "project_name": m06_result.get("project_name"),
        "m06_status": m06_status,
        "status": status_label,
        "score": score,
        "score_reason": score_reason,
        "expected": sorted(test_def["expected"]),
        "reason": m06_result.get("reason"),
        "window_title_before": m06_result.get("window_title_before"),
        "window_title_after": m06_result.get("window_title_after"),
        "before_screen_state": m06_result.get("before_screen_state"),
        "after_screen_state": m06_result.get("after_screen_state"),
        "confirmation_words": m06_result.get("confirmation_words", []),
        "manual_review_required": m06_result.get("manual_review_required", False),
        "navigation_required": navigation_required,
        "navigation_used": nav_used,
        "setup_notes": setup_notes,
        "screenshots": m06_result.get("screenshots", []),
        "ocr_files": m06_result.get("ocr_files", []),
        "classification_files": m06_result.get("classification_files", []),
        "popup_files": m06_result.get("popup_files", []),
        "m06_steps": m06_result.get("steps", []),
    }
    write_json(evidence.folder / "result.json", result)

    lines = [
        f"# M06 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- Matrix run ID: {evidence.run_id}",
        f"- Project: {m06_result.get('project_name')}",
        f"- M06 status: {m06_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Expected: {sorted(test_def['expected'])}",
        f"- Navigation required: {navigation_required}",
        f"- Navigation used (Alt+P, A): {nav_used}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(
        [
            "",
            "## M06 reason",
            m06_result.get("reason", ""),
            "",
            "## Window title before",
            m06_result.get("window_title_before", ""),
            "",
            "## Window title after",
            m06_result.get("window_title_after", ""),
            "",
            "## Before / after screen state",
            f"{m06_result.get('before_screen_state', '')} -> {m06_result.get('after_screen_state', '')}",
            "",
            "## M06 steps",
        ]
    )
    for step in m06_result.get("steps", []):
        lines.append(f"- {step}")

    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def run_test_01(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes = ensure_project_open(ctx["project"])
    setup_navigate_activities(notes)
    m06 = run_m06(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m06, notes)


def run_test_02(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes = ensure_project_open(ctx["project"])
    setup_navigate_projects(notes)
    m06 = run_m06(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m06, notes)


def run_test_03(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes = ensure_project_open(ctx["project"])
    setup_navigate_wbs(notes)
    m06 = run_m06(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m06, notes)


def run_test_04(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes = ensure_project_open(ctx["project"])
    setup_navigate_activities(notes)
    window_tools.activate_window_by_title("Cursor")
    time.sleep(0.6)
    notes.append("Cursor window brought to front before M06")
    m06 = run_m06(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m06, notes)


def run_test_05(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes = ensure_project_open(ctx["project"])
    prepare_p6_for_test(p6_keyword())
    window_tools.minimize_window_by_title(p6_keyword())
    time.sleep(0.8)
    notes.append("P6 minimised before M06")
    m06 = run_m06(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m06, notes)


def run_test_06(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup()
    notes: List[str] = ["Closing project via M05 setup (outside M06)"]
    m05 = run_m05(ctx["project"], run_id=f"{new_run_id()}_setup_close")
    notes.append(f"Setup M05 status: {m05.get('status')}")
    m06 = run_m06(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m06, notes)


HARD_TESTS: List[Dict[str, Any]] = [
    {
        "id": "01",
        "slug": "already_in_activities",
        "name": "Already in Activities",
        "expected": {"PASS_ALREADY_IN_ACTIVITIES", "PASS"},
        "navigation_required": False,
        "runner": run_test_01,
    },
    {
        "id": "02",
        "slug": "from_projects_workspace",
        "name": "From Projects workspace",
        "expected": {"PASS"},
        "navigation_required": True,
        "runner": run_test_02,
    },
    {
        "id": "03",
        "slug": "from_wbs_workspace",
        "name": "From WBS workspace",
        "expected": {"PASS"},
        "navigation_required": True,
        "runner": run_test_03,
    },
    {
        "id": "04",
        "slug": "p6_behind_cursor",
        "name": "P6 behind Cursor",
        "expected": {"PASS_ALREADY_IN_ACTIVITIES", "PASS"},
        "navigation_required": False,
        "runner": run_test_04,
    },
    {
        "id": "05",
        "slug": "p6_minimised",
        "name": "P6 minimised",
        "expected": {"PASS_ALREADY_IN_ACTIVITIES", "PASS", "FAIL_P6_WINDOW_NOT_READY"},
        "navigation_required": False,
        "runner": run_test_05,
    },
    {
        "id": "06",
        "slug": "project_not_open",
        "name": "Project not open / no current project",
        "expected": {"FAIL_PROJECT_NOT_OPEN"},
        "navigation_required": False,
        "runner": run_test_06,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / run_id
    (run_root / "m06_hard_test_6").mkdir(parents=True, exist_ok=True)

    print("M06 Hard Testing — 6-test matrix")
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
                "m06_status": "CRASH",
                "status": "CRASH",
                "score": 0,
                "score_reason": str(exc),
                "reason": traceback.format_exc(),
                "navigation_required": test_def.get("navigation_required", False),
                "navigation_used": False,
            }
            write_json(evidence.folder / "result.json", result)
            (evidence.folder / "report.md").write_text(
                f"# CRASH\n\n{traceback.format_exc()}\n", encoding="utf-8"
            )
        results.append(result)
        print(f"  -> {result.get('m06_status')} score={result.get('score')}")

    summary = write_hard_summary(run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']} / 6")
    print(f"OCR pollution: {summary['ocr_pollution_cases']}")
    print(f"Decision: {summary['decision']}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M06 hard 6-test matrix")
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
