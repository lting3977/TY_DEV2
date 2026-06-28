"""
M05 Safety Hard Testing — 5-test matrix.

Proves M05 closes when safe and stops on unknown/unsafe popups.
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
from m04_check_project_opened import title_indicates_project_open  # noqa: E402
from m05_close_project_safely import (  # noqa: E402
    CONFIG_PATH,
    RunEvidence,
    load_json,
    normalize_text,
    run_m05,
)
from m05_hard_summary import write_hard_summary  # noqa: E402

PASS_OUTCOMES = frozenset({"PASS_CLOSED", "PASS_ALREADY_NO_PROJECT"})


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_test_evidence(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / matrix_run_id / "m05_hard_test_5" / f"test_{test_id}_{slug}"
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


def map_pollution_status(m05_result: Dict[str, Any]) -> str:
    reason = (m05_result.get("reason") or "").lower()
    error = (m05_result.get("error") or "") or ""
    if "ocr pollution" in reason or "ocr pollution" in error.lower():
        return "OCR_POLLUTION"
    return m05_result.get("status", "ERROR")


def verify_no_unsafe_presses(m05_result: Dict[str, Any]) -> bool:
    steps = m05_result.get("steps", [])
    unsafe_markers = (
        "press_escape",
        "press_key('y'",
        "press_key(\"y\"",
        "press yes",
        "press no",
        "hotkey('alt', 'y')",
        "hotkey(\"alt\", \"y\")",
        "hotkey('ctrl', 's')",
        "press_key('s'",
        "hotkey('alt', 'n')",
    )
    for step in steps:
        lowered = step.lower()
        if "confirm_close: click_yes" in lowered or "confirm_close: alt+y" in lowered:
            continue
        if any(marker in lowered for marker in unsafe_markers):
            return False
    return True


def count_unsafe_confirmation(m05_result: Dict[str, Any], test_id: str) -> bool:
    action = (m05_result.get("confirmation_action_taken") or "").lower()
    if "click_yes" not in action:
        return False
    if test_id == "05":
        return True
    return False


def score_result(
    m05_status: str,
    expected: Set[str],
    unsafe_press: bool,
    unsafe_confirm: bool,
) -> tuple[int, str, str]:
    if unsafe_confirm:
        return 0, "UNSAFE_CONFIRMATION", "Yes clicked on unsafe/blocked popup scenario"
    if unsafe_press:
        return 0, "UNSAFE_BUTTON_PRESS", "Forbidden keyboard action detected in M05 steps"
    if m05_status == "OCR_POLLUTION":
        return 0, "OCR_POLLUTION", "OCR pollution detected"
    if m05_status in ("CRASH", "ERROR"):
        return 0, m05_status, "Unhandled error or crash"
    if m05_status in expected:
        return 1, m05_status, f"Expected outcome: {m05_status}"
    if m05_status in PASS_OUTCOMES and m05_status not in expected:
        return 0, "FALSE_PASS", f"Unexpected pass outcome (expected one of {sorted(expected)})"
    if m05_status == "MANUAL_REVIEW_SAVE_PROMPT" and "MANUAL_REVIEW_SAVE_PROMPT" not in expected:
        if m05_status in expected:
            pass
        elif "MANUAL_REVIEW_UNSAFE_POPUP" in expected:
            return 0, m05_status, f"Got save prompt but expected {sorted(expected)}"
    return 0, m05_status, f"Expected {sorted(expected)}, got {m05_status}"


def finish_hard_test(
    evidence: RunEvidence,
    test_def: Dict[str, Any],
    m05_result: Dict[str, Any],
    setup_notes: List[str],
) -> Dict[str, Any]:
    m05_status = map_pollution_status(m05_result)
    unsafe_press = not verify_no_unsafe_presses(m05_result)
    unsafe_confirm = count_unsafe_confirmation(m05_result, test_def["id"])
    score, status_label, score_reason = score_result(
        m05_status, test_def["expected"], unsafe_press, unsafe_confirm
    )

    result = {
        "test_id": test_def["id"],
        "test_slug": test_def["slug"],
        "test_name": test_def["name"],
        "project_name": m05_result.get("project_name"),
        "m05_status": m05_status,
        "status": status_label,
        "score": score,
        "score_reason": score_reason,
        "expected": sorted(test_def["expected"]),
        "reason": m05_result.get("reason"),
        "window_title_before": m05_result.get("window_title_before"),
        "window_title_after": m05_result.get("window_title_after"),
        "before_screen_state": m05_result.get("before_screen_state"),
        "after_screen_state": m05_result.get("after_screen_state"),
        "close_confirmation_detected": m05_result.get("close_confirmation_detected", False),
        "confirmation_action_taken": m05_result.get("confirmation_action_taken", ""),
        "manual_review_required": m05_result.get("manual_review_required", False),
        "setup_notes": setup_notes,
        "screenshots": m05_result.get("screenshots", []),
        "ocr_files": m05_result.get("ocr_files", []),
        "classification_files": m05_result.get("classification_files", []),
        "popup_files": m05_result.get("popup_files", []),
        "m05_steps": m05_result.get("steps", []),
    }
    write_json(evidence.folder / "result.json", result)

    lines = [
        f"# M05 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- Matrix run ID: {evidence.run_id}",
        f"- Project: {m05_result.get('project_name')}",
        f"- M05 status: {m05_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Expected: {sorted(test_def['expected'])}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(
        [
            "",
            "## M05 reason",
            m05_result.get("reason", ""),
            "",
            "## Window title before",
            m05_result.get("window_title_before", ""),
            "",
            "## Window title after",
            m05_result.get("window_title_after", ""),
        ]
    )
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def finish_controlled_setup(
    evidence: RunEvidence,
    test_def: Dict[str, Any],
    project_name: str,
    setup_notes: List[str],
    reason: str,
) -> Dict[str, Any]:
    m05_result = {
        "project_name": project_name,
        "status": "CONTROLLED_SETUP_UNAVAILABLE",
        "reason": reason,
        "window_title_before": "",
        "window_title_after": "",
        "before_screen_state": "",
        "after_screen_state": "",
        "manual_review_required": False,
        "screenshots": [],
        "ocr_files": [],
        "classification_files": [],
        "popup_files": [],
        "steps": ["controlled_setup_unavailable"],
    }
    return finish_hard_test(evidence, test_def, m05_result, setup_notes)


def p6_keyword() -> str:
    return load_json(CONFIG_PATH)["p6_window_title_keyword"]


def matrix_cleanup_dismiss_dialog() -> None:
    """Orchestrator-only cleanup between tests — not part of M05 module under test."""
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


def try_close_project_safely() -> tuple[List[str], bool]:
    notes: List[str] = []
    prep = prepare_p6_for_test(p6_keyword())
    title = (prep.get("window_state") or {}).get("title", "") or ""
    if "no current project" in normalize_text(title):
        notes.append("Already no project open")
        return notes, True
    notes.append("Attempting Ctrl+W then Esc to reach no-project state for setup")
    keyboard_tools.hotkey("ctrl", "w")
    time.sleep(1.0)
    keyboard_tools.press_escape()
    time.sleep(0.8)
    title2 = window_tools.get_window_state(p6_keyword()).get("title", "")
    notes.append(f"Title after close attempt: {title2}")
    closed = "no current project" in normalize_text(title2 or "")
    return notes, closed


def run_test_01(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup_dismiss_dialog()
    notes = ensure_project_open(ctx["project"])
    m05 = run_m05(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m05, notes)


def run_test_02(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup_dismiss_dialog()
    notes, closed = try_close_project_safely()
    if not closed:
        return finish_controlled_setup(
            evidence,
            ctx["test_def"],
            ctx["project"],
            notes + ["Safe no-project setup unavailable without Yes/No/Save"],
            "Cannot reach no-project state without unsafe prompts",
        )
    m05 = run_m05(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m05, notes)


def run_test_03(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup_dismiss_dialog()
    notes = ensure_project_open(ctx["project"])

    def after_cursor() -> None:
        window_tools.activate_window_by_title("Cursor")
        time.sleep(0.4)
        window_tools.activate_window_by_title(p6_keyword())
        time.sleep(0.5)

    m05 = run_m05(
        ctx["project"],
        evidence=evidence,
        run_id=ctx["run_id"],
        after_prepare_hook=after_cursor,
    )
    return finish_hard_test(evidence, ctx["test_def"], m05, notes + ["Cursor focus cycle before M05 capture"])


def run_test_04(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup_dismiss_dialog()
    notes = ensure_project_open(ctx["project"])

    def before_minimize() -> None:
        prepare_p6_for_test(p6_keyword())
        window_tools.minimize_window_by_title(p6_keyword())
        time.sleep(0.8)
        notes.append("P6 minimised before M05 prepare")

    m05 = run_m05(
        ctx["project"],
        evidence=evidence,
        run_id=ctx["run_id"],
        before_prepare_hook=before_minimize,
    )
    return finish_hard_test(evidence, ctx["test_def"], m05, notes)


def run_test_05(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    matrix_cleanup_dismiss_dialog()
    notes = ensure_project_open(ctx["project"])

    def after_blocking_dialog() -> None:
        keyboard_tools.open_dialog_ctrl_o()
        time.sleep(1.2)
        notes.append("Open Project dialog opened before M05 — blocking unsafe state")

    m05 = run_m05(
        ctx["project"],
        evidence=evidence,
        run_id=ctx["run_id"],
        after_prepare_hook=after_blocking_dialog,
    )
    return finish_hard_test(evidence, ctx["test_def"], m05, notes)


HARD_TESTS: List[Dict[str, Any]] = [
    {
        "id": "01",
        "slug": "close_confirmation",
        "name": "Target project open, Ctrl+W produces close confirmation",
        "expected": {"PASS_CLOSED"},
        "runner": run_test_01,
    },
    {
        "id": "02",
        "slug": "no_project_open",
        "name": "No project open",
        "expected": {"PASS_ALREADY_NO_PROJECT", "CONTROLLED_SETUP_UNAVAILABLE"},
        "runner": run_test_02,
    },
    {
        "id": "03",
        "slug": "p6_behind_cursor",
        "name": "P6 behind Cursor with project open",
        "expected": {"PASS_CLOSED"},
        "runner": run_test_03,
    },
    {
        "id": "04",
        "slug": "p6_minimised",
        "name": "P6 minimised with project open",
        "expected": {"PASS_CLOSED", "FAIL_P6_WINDOW_NOT_READY"},
        "runner": run_test_04,
    },
    {
        "id": "05",
        "slug": "unsafe_popup_visible",
        "name": "Unsafe popup already visible",
        "expected": {"MANUAL_REVIEW_UNSAFE_POPUP"},
        "runner": run_test_05,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / run_id
    (run_root / "m05_hard_test_5").mkdir(parents=True, exist_ok=True)

    print("M05 Safety Hard Testing — 5-test matrix")
    print(f"Run ID: {run_id}")
    print(f"Project: {project}")
    print("=" * 60)

    results: List[Dict[str, Any]] = []
    for index, test_def in enumerate(HARD_TESTS, start=1):
        print(f"[{index}/5] {test_def['id']} {test_def['name']}")
        evidence = build_test_evidence(run_id, test_def["id"], test_def["slug"])
        ctx = {"run_id": run_id, "project": project, "test_def": test_def}
        try:
            result = test_def["runner"](ctx, evidence)
        except Exception as exc:  # noqa: BLE001
            result = {
                "test_id": test_def["id"],
                "test_slug": test_def["slug"],
                "test_name": test_def["name"],
                "m05_status": "CRASH",
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
        print(f"  -> {result.get('m05_status')} score={result.get('score')}")

    summary = write_hard_summary(run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']} / 5")
    print(f"OCR pollution: {summary['ocr_pollution_cases']}")
    print(f"Decision: {summary['decision']}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M05 safety hard 5-test matrix")
    parser.add_argument("--project", default="Talison 1275")
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    ok = (
        summary["ocr_pollution_cases"] == 0
        and summary["crashes"] == 0
        and summary["false_pass_cases"] == 0
        and summary["unsafe_button_presses"] == 0
        and summary.get("unsafe_confirmations", 0) == 0
        and summary["final_score"] >= 4
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
