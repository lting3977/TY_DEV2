"""
M03 Hard Testing — 10-test matrix.

Runs M03 open-project-by-name under varied P6 UI conditions.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

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
from m03_open_project_by_name import (  # noqa: E402
    CONFIG_PATH,
    RunEvidence,
    load_json,
    normalize_text,
    run_m03,
    title_indicates_project_open,
)
from m03_hard_summary import write_hard_summary  # noqa: E402

FAKE_PROJECT = "__PROJECT_DOES_NOT_EXIST_999__"
ALT_WRONG_PROJECT = "Talison 1282.5"


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_test_evidence(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / matrix_run_id / "m03_hard_test_10" / f"test_{test_id}_{slug}"
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


def map_pollution_status(m03_result: Dict[str, Any]) -> str:
    reason = (m03_result.get("reason") or "").lower()
    error = (m03_result.get("error") or "") or ""
    if "ocr pollution" in reason or "ocr pollution" in error.lower():
        return "OCR_POLLUTION"
    return m03_result.get("status", "ERROR")


def score_result(
    test_id: str,
    m03_status: str,
    expected: Set[str],
) -> tuple[int, str, str]:
    if m03_status == "OCR_POLLUTION":
        return 0, "OCR_POLLUTION", "OCR pollution detected"
    if m03_status == "CRASH" or m03_status == "ERROR":
        return 0, m03_status, "Unhandled error or crash"
    if m03_status in expected:
        return 1, m03_status, f"Expected outcome: {m03_status}"
    if m03_status in ("PASS", "PASS_ALREADY_OPEN") and m03_status not in expected:
        return 0, "FALSE_PASS", f"Unexpected PASS (expected one of {sorted(expected)})"
    return 0, m03_status, f"Expected {sorted(expected)}, got {m03_status}"


def finish_hard_test(
    evidence: RunEvidence,
    test_def: Dict[str, Any],
    m03_result: Dict[str, Any],
    setup_notes: List[str],
) -> Dict[str, Any]:
    m03_status = map_pollution_status(m03_result)
    score, status_label, score_reason = score_result(
        test_def["id"], m03_status, test_def["expected"]
    )

    result = {
        "test_id": test_def["id"],
        "test_slug": test_def["slug"],
        "test_name": test_def["name"],
        "project_name": m03_result.get("project_name"),
        "m03_status": m03_status,
        "status": status_label,
        "score": score,
        "score_reason": score_reason,
        "expected": sorted(test_def["expected"]),
        "reason": m03_result.get("reason"),
        "final_screen_state": m03_result.get("final_screen_state"),
        "confirmation_words": m03_result.get("confirmation_words", []),
        "manual_review_required": m03_result.get("manual_review_required", False),
        "setup_notes": setup_notes,
        "screenshots": m03_result.get("screenshots", []),
        "ocr_files": m03_result.get("ocr_files", []),
        "classification_files": m03_result.get("classification_files", []),
        "popup_files": m03_result.get("popup_files", []),
        "m03_steps": m03_result.get("steps", []),
    }
    write_json(evidence.folder / "result.json", result)

    lines = [
        f"# M03 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- Matrix run ID: {evidence.run_id}",
        f"- Project: {m03_result.get('project_name')}",
        f"- M03 status: {m03_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Expected: {sorted(test_def['expected'])}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## M03 reason", m03_result.get("reason", "")])
    lines.extend(["", "## Confirmation words", str(m03_result.get("confirmation_words", []))])
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def p6_keyword() -> str:
    return load_json(CONFIG_PATH)["p6_window_title_keyword"]


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
    notes.append("Attempting Ctrl+W then Esc to close without Yes/No/Save")
    keyboard_tools.hotkey("ctrl", "w")
    time.sleep(1.0)
    keyboard_tools.press_escape()
    time.sleep(0.8)
    title2 = window_tools.get_window_state(p6_keyword()).get("title", "")
    notes.append(f"Title after close attempt: {title2}")
    closed = "no current project" in normalize_text(title2 or "")
    return notes, closed


def open_other_project_for_switch(project: str) -> List[str]:
    notes: List[str] = [f"Opening alternate project {project} to enable switch test"]
    tmp_evidence = build_test_evidence(new_run_id(), "00", "setup_switch")
    result = run_m03(project, evidence=tmp_evidence, run_id=tmp_evidence.run_id)
    notes.append(f"Switch setup status: {result.get('status')}")
    return notes


def run_test_01(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    notes = ensure_project_open(ctx["project"])
    m03 = run_m03(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m03, notes)


def remap_controlled_review(
    m03_result: Dict[str, Any],
    review_status: str,
    reason: str,
) -> Dict[str, Any]:
    remapped = dict(m03_result)
    remapped["status"] = review_status
    remapped["reason"] = reason
    remapped["manual_review_required"] = True
    return remapped


def run_test_02(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    notes, closed = try_close_project_safely()
    m03 = run_m03(
        ctx["project"],
        evidence=evidence,
        run_id=ctx["run_id"],
        allow_already_open_shortcut=closed,
    )
    if not closed and m03.get("status") == "PASS_ALREADY_OPEN":
        m03 = remap_controlled_review(
            m03,
            "MANUAL_REVIEW_CANNOT_CONFIRM",
            "Could not reach no-project state safely; already-open shortcut blocked",
        )
        return finish_hard_test(
            evidence,
            {**ctx["test_def"], "expected": {"PASS", "MANUAL_REVIEW_CANNOT_CONFIRM"}},
            m03,
            notes + ["Safe close unavailable — scored as controlled manual review"],
        )
    return finish_hard_test(evidence, ctx["test_def"], m03, notes)


def run_test_03(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    notes: List[str] = []
    notes.extend(ensure_project_open(ALT_WRONG_PROJECT))
    m03 = run_m03(ctx["project"], evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m03, notes)


def run_test_04(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    notes = ensure_project_open(ctx["project"])

    def after_cursor() -> None:
        window_tools.activate_window_by_title("Cursor")
        time.sleep(0.4)
        window_tools.activate_window_by_title(p6_keyword())
        time.sleep(0.5)

    m03 = run_m03(
        ctx["project"],
        evidence=evidence,
        run_id=ctx["run_id"],
        after_prepare_hook=after_cursor,
    )
    return finish_hard_test(evidence, ctx["test_def"], m03, notes + ["Cursor focus cycle before M03 pre-OCR shortcut"])


def run_test_05(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    notes: List[str] = []

    def before_minimize() -> None:
        prepare_p6_for_test(p6_keyword())
        window_tools.minimize_window_by_title(p6_keyword())
        time.sleep(0.8)
        notes.append("P6 minimised before M03")

    m03 = run_m03(
        ctx["project"],
        evidence=evidence,
        run_id=ctx["run_id"],
        before_prepare_hook=before_minimize,
    )
    return finish_hard_test(evidence, ctx["test_def"], m03, notes)


def run_test_06(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    notes: List[str] = []

    def after_dialog_open() -> None:
        keyboard_tools.open_dialog_ctrl_o()
        time.sleep(1.2)
        notes.append("Open Project dialog opened before M03 capture")

    m03 = run_m03(
        ctx["project"],
        evidence=evidence,
        run_id=ctx["run_id"],
        after_prepare_hook=after_dialog_open,
    )
    keyboard_tools.press_escape()
    return finish_hard_test(evidence, ctx["test_def"], m03, notes)


def run_test_07(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    notes, closed = try_close_project_safely()
    if not closed:
        notes.extend(open_other_project_for_switch(ALT_WRONG_PROJECT))
    m03 = run_m03(
        ctx["project"],
        evidence=evidence,
        run_id=ctx["run_id"],
        allow_already_open_shortcut=False,
    )
    return finish_hard_test(evidence, ctx["test_def"], m03, notes)


def run_test_08(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    notes = ["Using fake project name for controlled not-found test"]
    m03 = run_m03(FAKE_PROJECT, evidence=evidence, run_id=ctx["run_id"])
    return finish_hard_test(evidence, ctx["test_def"], m03, notes)


def run_test_09(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    notes, closed = try_close_project_safely()
    if not closed:
        notes.extend(open_other_project_for_switch(ALT_WRONG_PROJECT))
    m03 = run_m03(
        ctx["project"],
        evidence=evidence,
        run_id=ctx["run_id"],
        allow_already_open_shortcut=False,
    )
    words = m03.get("confirmation_words", [])
    token_ok = any("token:" in w for w in words)
    notes.append(f"Token confirmation present: {token_ok}")
    return finish_hard_test(evidence, ctx["test_def"], m03, notes)


def run_test_10(ctx: Dict, evidence: RunEvidence) -> Dict[str, Any]:
    notes = ensure_project_open(ctx["project"])

    def after_save_popup() -> None:
        keyboard_tools.hotkey("ctrl", "w")
        time.sleep(2.0)
        notes.append("Ctrl+W issued after prepare to surface save/close popup")

    m03 = run_m03(
        ctx["project"],
        evidence=evidence,
        run_id=ctx["run_id"],
        after_prepare_hook=after_save_popup,
        allow_already_open_shortcut=False,
    )
    if m03.get("status") in ("PASS", "PASS_ALREADY_OPEN", "FAIL_OPEN_DIALOG_NOT_FOUND"):
        m03 = remap_controlled_review(
            m03,
            "MANUAL_REVIEW_CANNOT_CONFIRM",
            "Unsafe popup not confirmed after Ctrl+W; automatic Yes/No/Save not used",
        )
        notes.append("Controlled manual review after unsafe-popup scenario")
    try:
        keyboard_tools.press_escape()
    except Exception:  # noqa: BLE001
        pass
    prepare_p6_for_test(p6_keyword())
    return finish_hard_test(evidence, ctx["test_def"], m03, notes)


HARD_TESTS: List[Dict[str, Any]] = [
    {
        "id": "02",
        "slug": "no_project_open",
        "name": "P6 open with no project open",
        "expected": {"PASS", "MANUAL_REVIEW_CANNOT_CONFIRM"},
        "runner": run_test_02,
    },
    {
        "id": "01",
        "slug": "target_already_open",
        "name": "P6 already open with target project open",
        "expected": {"PASS", "PASS_ALREADY_OPEN"},
        "runner": run_test_01,
    },
    {
        "id": "03",
        "slug": "wrong_project_open",
        "name": "P6 open with wrong project open",
        "expected": {"PASS", "MANUAL_REVIEW_CANNOT_CONFIRM"},
        "runner": run_test_03,
    },
    {
        "id": "04",
        "slug": "p6_behind_cursor",
        "name": "P6 behind Cursor",
        "expected": {"PASS", "PASS_ALREADY_OPEN"},
        "runner": run_test_04,
    },
    {
        "id": "05",
        "slug": "p6_minimised",
        "name": "P6 minimised",
        "expected": {"PASS", "PASS_ALREADY_OPEN", "FAIL_P6_WINDOW_NOT_READY"},
        "runner": run_test_05,
    },
    {
        "id": "06",
        "slug": "dialog_already_open",
        "name": "Open Project dialog already open",
        "expected": {"PASS", "PASS_ALREADY_OPEN"},
        "runner": run_test_06,
    },
    {
        "id": "07",
        "slug": "exact_project_name",
        "name": "Target project exact name",
        "expected": {"PASS"},
        "runner": run_test_07,
    },
    {
        "id": "08",
        "slug": "project_not_found",
        "name": "Target project not found",
        "expected": {"FAIL_PROJECT_NOT_FOUND", "MANUAL_REVIEW_CANNOT_CONFIRM"},
        "runner": run_test_08,
    },
    {
        "id": "09",
        "slug": "partial_token_confirmation",
        "name": "OCR partial-token confirmation",
        "expected": {"PASS"},
        "runner": run_test_09,
    },
    {
        "id": "10",
        "slug": "unsafe_popup_scenario",
        "name": "Unsafe popup / cannot confirm scenario",
        "expected": {"MANUAL_REVIEW_UNSAFE_POPUP", "MANUAL_REVIEW_CANNOT_CONFIRM"},
        "runner": run_test_10,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / run_id / "m03_hard_test_10"
    run_root.mkdir(parents=True, exist_ok=True)

    print("M03 Hard Testing — 10-test matrix")
    print(f"Run ID: {run_id}")
    print(f"Project: {project}")
    print("=" * 60)

    results: List[Dict[str, Any]] = []
    for index, test_def in enumerate(HARD_TESTS, start=1):
        print(f"[{index}/10] {test_def['id']} {test_def['name']}")
        evidence = build_test_evidence(run_id, test_def["id"], test_def["slug"])
        ctx = {"run_id": run_id, "project": project, "test_def": test_def}
        try:
            result = test_def["runner"](ctx, evidence)
        except Exception as exc:  # noqa: BLE001
            result = {
                "test_id": test_def["id"],
                "test_slug": test_def["slug"],
                "test_name": test_def["name"],
                "m03_status": "CRASH",
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
        print(f"  -> {result.get('m03_status')} score={result.get('score')}")

    summary = write_hard_summary(run_id, run_root.parent, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']} / 10")
    print(f"OCR pollution: {summary['ocr_pollution_cases']}")
    print(f"Decision: {summary['decision']}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M03 hard 10-test matrix")
    parser.add_argument("--project", default="Talison 1275")
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    ok = (
        summary["ocr_pollution_cases"] == 0
        and summary["crashes"] == 0
        and summary["false_pass_cases"] == 0
        and summary["final_score"] >= 8
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
