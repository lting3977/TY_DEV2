"""
M24 Hard Testing — 6-test matrix.

Proves M24 can safely cancel the export wizard at each depth (format, export type,
template, post-template), handle unsafe popups, and refuse when no wizard exists.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
ORCH = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ORCH))
sys.path.insert(0, str(ROOT / "04_modules"))
sys.path.insert(0, str(ROOT / "02_accessibility"))

from m24_hard_summary import write_hard_summary  # noqa: E402
from m06_go_to_activities import load_json  # noqa: E402
from m24_export_wizard_cancel_recovery_from_known_screens import RunEvidence, run_m24, write_json  # noqa: E402
from m03_open_project_by_name import run_m03  # noqa: E402
from m04_check_project_opened import run_m04  # noqa: E402
from m05_close_project_safely import run_m05  # noqa: E402
from m06_go_to_activities import run_m06  # noqa: E402

PASS_CANCEL = frozenset({"PASS_CANCEL_RECOVERY", "PASS_CANCEL_RECOVERY_PARTIAL"})
TEST_05_OK = frozenset({"FAIL_DIALOG_STILL_OPEN", "MANUAL_REVIEW_UNSAFE_POPUP"})
TEST_06_OK = frozenset({"FAIL_NO_EXPORT_WIZARD_FOUND", "FAIL_EXPORT_WIZARD_NOT_FOUND"})

OCR_POLLUTION_WORDS = (
    "chatgpt",
    "cursor",
    "composer",
    "ty_dev2",
    "hard testing summary",
)

FORBIDDEN_STEP_MARKERS = (
    'press_key("y")',
    "press_key('y')",
    'press_key("n")',
    "press_key('n')",
    'press_key("finish")',
    "press_key('finish')",
    "ctrl+s",
    "ctrl+p",
    "f9",
    "browse",
    "modify template",
    "delete template",
)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def get_p6_keyword() -> str:
    config_path = ROOT / "01_config" / "ty_config.json"
    if config_path.exists():
        return load_json(config_path).get("p6_window_title_keyword", "Primavera")
    return "Primavera"


def build_test_folder(matrix_run_id: str, test_id: str, slug: str) -> Path:
    folder = (
        ROOT / "06_output" / "runs" / matrix_run_id / "m24_hard_test_6" / f"test_{test_id}_{slug}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def build_m24_evidence(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
    folder = build_test_folder(matrix_run_id, test_id, slug)
    for sub in ("screenshots", "ocr", "classification", "popup", "discovery"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    return RunEvidence(
        run_id=f"{matrix_run_id}_t{test_id}",
        folder=folder,
        screenshots_dir=folder / "screenshots",
        ocr_dir=folder / "ocr",
        classification_dir=folder / "classification",
        popup_dir=folder / "popup",
        discovery_dir=folder / "discovery",
    )


def chain_m03_m04_m06(project: str, matrix_run_id: str, test_id: str) -> Dict[str, Any]:
    prefix = f"{matrix_run_id}_t{test_id}"
    return {
        "m03": run_m03(project, run_id=f"{prefix}_m03"),
        "m04": run_m04(project, run_id=f"{prefix}_m04"),
        "m06": run_m06(project, run_id=f"{prefix}_m06"),
    }


def check_unsafe_steps(steps: List[str]) -> Tuple[bool, List[str]]:
    hits: List[str] = []
    for step in steps:
        lowered = step.lower()
        for marker in FORBIDDEN_STEP_MARKERS:
            if marker in lowered:
                hits.append(f"{step} ({marker})")
    return len(hits) == 0, hits


def check_no_fullscreen_ocr(test_folder: Path) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    shots = test_folder / "screenshots"
    if shots.exists():
        for shot in shots.glob("*.png"):
            name = shot.name.lower()
            if "desktop" in name or "fullscreen" in name or "full_screen" in name:
                issues.append(shot.name)
    return len(issues) == 0, issues


def hook_blob(m24_result: Dict[str, Any]) -> str:
    steps = " ".join(m24_result.get("steps", [])).lower()
    reason = (m24_result.get("reason") or "").lower()
    return f"{steps} {reason}"


def score_result(
    test_id: str,
    m24_status: str,
    m24_result: Dict[str, Any],
    *,
    unsafe_ok: bool,
    unsafe_hits: List[str],
    crop_ok: bool,
    crop_issues: List[str],
    expected_depth: str = "",
) -> Tuple[int, str, str]:
    export_file = bool(m24_result.get("export_file_created"))
    dialog_closed = bool(m24_result.get("export_dialog_closed"))
    wizard_detected = bool(m24_result.get("export_wizard_detected"))
    finish_pressed = bool(m24_result.get("finish_pressed"))
    cancel_depth = (m24_result.get("screen_depth") or m24_result.get("cancel_at_depth") or "").lower()
    steps_blob = hook_blob(m24_result)

    if not unsafe_ok:
        return 0, "UNSAFE_ACTION", "; ".join(unsafe_hits[:3])
    if not crop_ok:
        return 0, "FULL_SCREEN_OCR", f"Possible full-screen capture: {crop_issues[:2]}"
    if export_file:
        return 0, "EXPORT_FILE_CREATED", "Export file created during cancel test"
    if finish_pressed:
        return 0, "FINISH_PRESSED", "Finish pressed during cancel test"

    if test_id == "05":
        if m24_status not in TEST_05_OK:
            return 0, "FALSE_PASS", f"Test 05 expected cancel failure handling, got {m24_status}"
        if "force_cancel_recovery_fail" not in steps_blob:
            return 0, "FALSE_PASS", "Test 05 missing force_cancel_recovery_fail hook"
        if dialog_closed:
            return 0, "FALSE_PASS", "Test 05 expected dialog still open after forced cancel fail"
        return 1, m24_status, "Cancel blocked safely; dialog not falsely closed"

    if test_id == "06":
        if m24_status not in TEST_06_OK:
            return 0, "FALSE_PASS", f"Test 06 expected no wizard fail, got {m24_status}"
        if "force_skip_export_open" not in steps_blob:
            return 0, "FALSE_PASS", "Test 06 missing force_skip_export_open hook"
        if wizard_detected:
            return 0, "FALSE_PASS", "Export wizard detected when skip_wizard_open set"
        return 1, m24_status, "No wizard opened; safe no-op cancel path"

    if m24_status not in PASS_CANCEL:
        return 0, "FALSE_PASS", f"Expected PASS_CANCEL_RECOVERY, got {m24_status}"

    cancel_depth = (m24_result.get("screen_depth") or expected_depth or "").lower()
    if expected_depth and cancel_depth != expected_depth:
        return 0, "FALSE_PASS", f"Expected cancel at {expected_depth}, got {cancel_depth}"

    if wizard_detected and not dialog_closed:
        return 0, "DIALOG_LEFT_OPEN", "Export wizard not closed after cancel"

    after_state = (m24_result.get("screen_state_after") or "").lower()
    title_after = (m24_result.get("window_title_after") or "").lower()
    if wizard_detected and not (
        after_state.startswith("activities")
        or "primavera" in title_after
        or "talison" in title_after
    ):
        return 0, "FALSE_PASS", f"P6 did not return to project window: {after_state}"

    return (
        1,
        m24_status,
        f"Cancel safe at depth={cancel_depth or expected_depth}; closed={dialog_closed}",
    )


def finish_hard_test(
    test_folder: Path,
    test_def: Dict[str, Any],
    m24_result: Dict[str, Any],
    setup_notes: List[str],
    *,
    expected_depth: str = "",
) -> Dict[str, Any]:
    m24_status = m24_result.get("status", "ERROR")
    unsafe_ok, unsafe_hits = check_unsafe_steps(m24_result.get("steps", []))
    crop_ok, crop_issues = check_no_fullscreen_ocr(test_folder)

    score, status, score_reason = score_result(
        test_def["id"],
        m24_status,
        m24_result,
        unsafe_ok=unsafe_ok,
        unsafe_hits=unsafe_hits,
        crop_ok=crop_ok,
        crop_issues=crop_issues,
        expected_depth=expected_depth,
    )

    result = {
        "test_id": test_def["id"],
        "test_name": test_def["name"],
        "slug": test_def["slug"],
        "m24_run_id": m24_result.get("run_id", ""),
        "m24_status": m24_status,
        "m24_reason": m24_result.get("reason", ""),
        "score": score,
        "status": status,
        "score_reason": score_reason,
        "cancel_at_depth": m24_result.get("screen_depth", expected_depth),
        "export_wizard_detected": m24_result.get("export_wizard_detected"),
        "export_dialog_closed": m24_result.get("export_dialog_closed"),
        "close_method_used": m24_result.get("close_method_used", ""),
        "export_file_created": m24_result.get("export_file_created"),
        "finish_pressed": m24_result.get("finish_pressed"),
        "screen_state_after": m24_result.get("screen_state_after", ""),
        "test_folder": str(test_folder),
        "setup_notes": setup_notes,
    }

    write_json(test_folder / "test_summary.json", result)
    (test_folder / "report.md").write_text(
        f"# M24 Hard Test {test_def['id']} — {test_def['name']}\n\n"
        f"- Score: {score}\n- M24 status: {m24_status}\n- Reason: {score_reason}\n",
        encoding="utf-8",
    )
    return result


def run_cancel_test(
    ctx: Dict[str, Any],
    test_folder: Path,
    test_id: str,
    depth: str,
    notes: List[str],
    **kwargs: Any,
) -> Dict[str, Any]:
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], test_id)
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    evidence = build_m24_evidence(ctx["matrix_run_id"], test_id, ctx["test_def"]["slug"])
    m24_result = run_m24(ctx["project"], evidence=evidence, screen_depth=depth, **kwargs)
    return finish_hard_test(test_folder, ctx["test_def"], m24_result, notes, expected_depth=depth)


def run_test_01(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    return run_cancel_test(
        ctx,
        test_folder,
        "01",
        "format",
        ["Chain M03 -> M04 -> M06", "Cancel at format screen"],
    )


def run_test_02(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    return run_cancel_test(
        ctx,
        test_folder,
        "02",
        "export_type",
        ["Chain M03 -> M04 -> M06", "Cancel at export type screen"],
    )


def run_test_03(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    return run_cancel_test(
        ctx,
        test_folder,
        "03",
        "template",
        ["Chain M03 -> M04 -> M06", "Cancel at template screen"],
    )


def run_test_04(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    return run_cancel_test(
        ctx,
        test_folder,
        "04",
        "post_template",
        ["Chain M03 -> M04 -> M06", "Cancel at post-template screen"],
    )


def run_test_05(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Run M24 with force_cancel_recovery_fail"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "05")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    evidence = build_m24_evidence(ctx["matrix_run_id"], "05", ctx["test_def"]["slug"])
    m24_result = run_m24(
        ctx["project"],
        evidence=evidence,
        screen_depth="format",
        force_cancel_recovery_fail=True,
    )
    return finish_hard_test(test_folder, ctx["test_def"], m24_result, notes)


def run_test_06(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Run M24 with force_skip_export_open (no wizard)"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "06")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    evidence = build_m24_evidence(ctx["matrix_run_id"], "06", ctx["test_def"]["slug"])
    m24_result = run_m24(
        ctx["project"],
        evidence=evidence,
        screen_depth="format",
        skip_wizard_open=True,
    )
    return finish_hard_test(test_folder, ctx["test_def"], m24_result, notes)


HARD_TESTS: List[Dict[str, Any]] = [
    {"id": "01", "slug": "cancel_format", "name": "Cancel at format screen", "runner": run_test_01},
    {"id": "02", "slug": "cancel_export_type", "name": "Cancel at export type screen", "runner": run_test_02},
    {"id": "03", "slug": "cancel_template", "name": "Cancel at template screen", "runner": run_test_03},
    {"id": "04", "slug": "cancel_post_template", "name": "Cancel at post-template screen", "runner": run_test_04},
    {"id": "05", "slug": "unsafe_popup", "name": "Unsafe popup blocked", "runner": run_test_05},
    {"id": "06", "slug": "no_wizard", "name": "No wizard / skip export open", "runner": run_test_06},
]


def run_matrix(project: str) -> Dict[str, Any]:
    matrix_run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / matrix_run_id
    (run_root / "m24_hard_test_6").mkdir(parents=True, exist_ok=True)
    print("M24 Hard Testing — 6-test matrix")
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
                "m24_status": "CRASH",
                "score": 0,
                "status": "CRASH",
                "score_reason": traceback.format_exc(),
                "test_folder": str(test_folder),
            }
            write_json(test_folder / "test_summary.json", result)
        results.append(result)
        print(f"  -> score={result.get('score')} m24={result.get('m24_status')}")
    summary = write_hard_summary(matrix_run_id, run_root, results, project)
    print(f"Final score: {summary['final_score']}/{summary['max_score']} — {summary['decision']}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M24 Hard Testing 6-test matrix")
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    return 0 if summary.get("decision") == "M24 STABLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
