"""
M23 Hard Testing — 6-test matrix.

Self-restores Talison 1275 + Activities before each test via ensure_clean_p6_for_m21_hard.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
ORCH = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ORCH))
sys.path.insert(0, str(ROOT / "04_modules"))

from m06_go_to_activities import CONFIG_PATH, SCREEN_RULE_PATH, load_json  # noqa: E402
from export_wizard_common import (  # noqa: E402
    M22FailSafeError,
    ensure_clean_p6_for_m23_hard,
    m20_hard_dismiss_stale_dialogs,
    m22_move_mouse_p6_neutral,
)
from m23_hard_summary import write_hard_summary  # noqa: E402
from m04_check_project_opened import run_m04  # noqa: E402
from m20_hard_test_matrix import (  # noqa: E402
    bring_cursor_to_front,
    check_no_fullscreen_ocr,
    check_ocr_pollution,
    check_unsafe_steps,
    minimize_p6,
)
from m23_discover_template_screen_discovery_only import (  # noqa: E402
    RunEvidence,
    run_m23,
    write_json,
)
from m05_close_project_safely import run_m05  # noqa: E402

PASS_DISCOVERY = frozenset(
    {
        "PASS_TEMPLATE_SCREEN_DISCOVERY",
        "PASS_TEMPLATE_SCREEN_DISCOVERY_PARTIAL",
    }
)
TEST_04_OK = frozenset({"FAIL_PROJECT_NOT_OPEN"})
TEST_05_OK = frozenset({"FAIL_PROJECT_ROW_NOT_FOUND"})
TEST_06_OK = frozenset(
    {
        "FAIL_TEMPLATE_SCREEN_NOT_FOUND",
        "MANUAL_REVIEW_CANNOT_CONFIRM",
    }
)
EARLY_SETUP_FAILURE_STATUSES = frozenset(
    {
        "FAIL_PROJECT_NOT_OPEN",
        "FAIL_P6_WINDOW_NOT_READY",
        "FAIL_EXPORT_WIZARD_NOT_FOUND",
        "FAIL_SPREADSHEET_OPTION_NOT_FOUND",
        "FAIL_EXPORT_TYPE_SCREEN_NOT_FOUND",
        "FAIL_ACTIVITIES_EXPORT_TYPE_NOT_FOUND",
        "FAIL_TEMPLATE_SCREEN_NOT_FOUND",
        "FAIL_PROJECT_RESTORE_FAILED",
        "ERROR",
    }
)
SETUP_STOP_STATUSES = frozenset(
    {
        "SETUP_PROJECT_RESTORE_FAILED",
        "SETUP_FAILURE_P6_NOT_READY",
        "SETUP_FAILURE_EXPORT_WIZARD_NOT_OPENED",
        "SETUP_FAILURE_PYAUTOGUI_FAILSAFE",
        "SETUP_FAILURE_UNSAFE_CLICK_POINT",
    }
)

FORBIDDEN_STEP_MARKERS = (
    'press_key("y")',
    "press_key('y')",
    'press_key("n")',
    "press_key('n')",
    'press_key("finish")',
    "press_key('finish')",
    "ctrl+s",
    "f9",
    "browse",
    "modify template",
    "delete template",
)

OPEN_PROJECT_STATUSES = frozenset({"PASS", "PASS_PROJECT_OPEN"})


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_test_folder_m23(matrix_run_id: str, test_id: str, slug: str) -> Path:
    folder = ROOT / "06_output" / "runs" / matrix_run_id / "m23_hard_test_6" / f"test_{test_id}_{slug}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def build_m23_evidence_v2(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
    folder = build_test_folder_m23(matrix_run_id, test_id, slug)
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


def write_test_report(test_folder: Path, result: Dict[str, Any]) -> None:
    lines = [
        f"# M23 Hard Test {result.get('test_id')} — {result.get('test_name')}",
        "",
        f"- Score: {result.get('score')}",
        f"- Status: {result.get('status')}",
        f"- M22 status: {result.get('m23_status')}",
        f"- Reason: {result.get('score_reason', result.get('m23_reason', ''))}",
        "",
        "## Setup notes",
    ]
    for note in result.get("setup_notes", []):
        lines.append(f"- {note}")
    (test_folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_precheck_evidence(test_folder: Path, precheck: Dict[str, Any]) -> None:
    write_json(test_folder / "setup_precheck.json", precheck)
    write_json(test_folder / "project_restore_attempts.json", precheck.get("attempts", []))


def run_m23_precheck(
    project: str,
    matrix_run_id: str,
    test_folder: Path,
    label: str,
    *,
    retry_once: bool = True,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    attempts: List[Dict[str, Any]] = []
    notes: List[str] = []
    last: Dict[str, Any] = {}
    tries = 2 if retry_once else 1
    for try_i in range(tries):
        run_label = f"{matrix_run_id}_{label}" if try_i == 0 else f"{matrix_run_id}_{label}_retry"
        last = ensure_clean_p6_for_m23_hard(project, run_label)
        attempts.append(last)
        notes.extend(last.get("notes", []))
        notes.append(f"precheck {label} try {try_i + 1}: ok={last.get('ok')} status={last.get('status')}")
        if last.get("ok"):
            precheck = {**last, "attempts": attempts, "label": label}
            write_precheck_evidence(test_folder, precheck)
            return True, notes, precheck
        time.sleep(1.5)
    precheck = {**last, "attempts": attempts, "label": label, "ok": False}
    write_precheck_evidence(test_folder, precheck)
    return False, notes, precheck


def setup_failure_result(
    test_folder: Path,
    test_def: Dict[str, Any],
    notes: List[str],
    reason: str,
    *,
    status: str = "SETUP_PROJECT_RESTORE_FAILED",
) -> Dict[str, Any]:
    result = {
        "test_id": test_def["id"],
        "test_name": test_def["name"],
        "slug": test_def["slug"],
        "m23_run_id": "",
        "m23_status": "",
        "m23_reason": "",
        "score": 0,
        "status": status,
        "score_reason": reason,
        "setup_failure": True,
        "test_folder": str(test_folder),
        "setup_notes": notes,
    }
    write_json(test_folder / "result.json", result)
    write_test_report(test_folder, result)
    return result


def hook_blob(m22_result: Dict[str, Any]) -> str:
    steps = " ".join(m22_result.get("steps", [])).lower()
    reason = (m22_result.get("reason") or "").lower()
    return f"{steps} {reason}"


def hook_stage_ok_test05(forced: Dict[str, Any]) -> bool:
    if not forced.get("hook_applied_at_expected_stage"):
        return False
    return all(
        forced.get(k)
        for k in (
            "spreadsheet_selected",
            "export_type_screen_detected",
            "activities_selected",
            "projects_to_export_screen_detected",
            "project_001_talison_detected",
        )
    ) and not forced.get("project_row_detected", True)


def hook_stage_ok_test06(forced: Dict[str, Any]) -> bool:
    if not forced.get("hook_applied_at_expected_stage"):
        return False
    return all(
        forced.get(k)
        for k in (
            "spreadsheet_selected",
            "export_type_screen_detected",
            "activities_selected",
            "projects_to_export_screen_detected",
            "project_row_detected",
            "project_selection_attempted",
            "next_from_projects_pressed",
        )
    )


def hook_stage_ok(forced: Dict[str, Any]) -> bool:
    return hook_stage_ok_test06(forced)


def score_result(
    test_id: str,
    m23_status: str,
    m22_result: Dict[str, Any],
    test_folder: Path,
    *,
    unsafe_ok: bool,
    unsafe_hits: List[str],
    crop_ok: bool,
    crop_issues: List[str],
    pollution_ok: bool,
    pollution_hits: List[str],
) -> Tuple[int, str, str]:
    export_file = bool(m22_result.get("export_file_created"))
    dialog_closed = bool(m22_result.get("export_dialog_closed"))
    wizard_detected = bool(m22_result.get("export_wizard_detected"))
    next_count = int(m22_result.get("next_pressed_count_total", 0))
    finish_pressed = bool(m22_result.get("finish_pressed"))
    projects_ok = bool(m22_result.get("projects_to_export_screen_detected"))
    post_ok = bool(m22_result.get("template_screen_detected"))
    forced = m22_result.get("forced_hook_activation") or {}

    if not unsafe_ok:
        return 0, "UNSAFE_ACTION", "; ".join(unsafe_hits[:3])
    if not crop_ok:
        return 0, "FULL_SCREEN_OCR", f"Possible full-screen capture: {crop_issues[:2]}"
    if export_file:
        return 0, "EXPORT_FILE_CREATED", "Export file created during test"
    if finish_pressed:
        return 0, "FINISH_PRESSED", "Finish pressed during discovery"
    if next_count > 3:
        return 0, "NEXT_PRESSED_TOO_MANY", f"Next pressed {next_count} times (max 3)"

    if m23_status in ("SETUP_FAILURE_PYAUTOGUI_FAILSAFE", "SETUP_FAILURE_UNSAFE_CLICK_POINT"):
        return 0, m23_status, m22_result.get("reason", m23_status)

    if test_id == "04":
        if m22_result.get("test04_project_still_open") and m23_status in PASS_DISCOVERY:
            return 0, "FALSE_PASS", "Project still open after M05 close attempts"
        if m23_status not in TEST_04_OK:
            return 0, "FALSE_PASS", f"Test 04 expected FAIL_PROJECT_NOT_OPEN, got {m23_status}"
        if wizard_detected or next_count > 0:
            return 0, "FALSE_PASS", "Export attempted while project not open"
        return 1, m23_status, "Project not open; export wizard not opened"

    if test_id == "05":
        if not hook_stage_ok_test05(forced):
            return 0, "SETUP_FAILURE", "Test 05 hook not applied at expected wizard stage"
        if m23_status not in TEST_05_OK:
            return 0, "FALSE_PASS", f"Test 05 unexpected status: {m23_status}"
        if not projects_ok:
            return 0, "FALSE_PASS", "Test 05 FAIL before Projects-to-export screen"
        if finish_pressed or export_file:
            return 0, "UNSAFE_ACTION", "Test 05 unsafe action detected"
        return 1, m23_status, "Project row not found at expected hook stage"

    if test_id == "06":
        if not hook_stage_ok_test06(forced) and not forced.get("hook_applied_after_template_screen"):
            return 0, "SETUP_FAILURE", "Test 06 hook not applied after Template screen"
        if not hook_stage_ok_test06(forced):
            return 0, "SETUP_FAILURE", "Test 06 missing required M23 wizard stage evidence"
        if m23_status in PASS_DISCOVERY:
            return 0, "FALSE_PASS", "Test 06 should not PASS with forced template block"
        if m23_status not in TEST_06_OK:
            return 0, "FALSE_PASS", f"Test 06 expected controlled fail, got {m23_status}"
        if finish_pressed or export_file:
            return 0, "UNSAFE_ACTION", "Test 06 unsafe action detected"
        return 1, m23_status, "Controlled template screen failure; Finish not pressed"

    if test_id == "03" and m23_status == "FAIL_P6_WINDOW_NOT_READY":
        if export_file or wizard_detected or next_count > 0:
            return 0, "FALSE_PASS", "Export attempted when P6 not ready"
        return 1, m23_status, "P6 could not safely restore; no export attempted"

    if test_id == "03" and m23_status in PASS_DISCOVERY:
        return 1, m23_status, "P6 minimised restore path succeeded"

    if test_id == "02" and not pollution_ok and m23_status in PASS_DISCOVERY:
        return 0, "FALSE_PASS", f"OCR pollution: {pollution_hits[:3]}"

    if m23_status not in PASS_DISCOVERY:
        return 0, "FALSE_PASS", f"Expected template screen discovery PASS, got {m23_status}"

    if not projects_ok:
        return 0, "FALSE_PASS", "Discovery pass without Projects-to-export screen"
    if next_count != 3:
        return 0, "FALSE_PASS", f"Discovery pass requires Next pressed exactly 3 times; got {next_count}"
    if not post_ok and m23_status == "PASS_TEMPLATE_SCREEN_DISCOVERY":
        return 0, "FALSE_PASS", "Full PASS without post-project-selection screen detected"
    if wizard_detected and not dialog_closed:
        return 0, "DIALOG_LEFT_OPEN", "Export wizard not closed"

    return 1, m23_status, f"M23 template screen discovery OK; next_count={next_count}; closed={dialog_closed}"


def finish_hard_test(
    test_folder: Path,
    test_def: Dict[str, Any],
    m22_result: Dict[str, Any],
    setup_notes: List[str],
) -> Dict[str, Any]:
    m23_status = m22_result.get("status", "ERROR")
    unsafe_ok, unsafe_hits = check_unsafe_steps(m22_result.get("steps", []))
    crop_ok, crop_issues = check_no_fullscreen_ocr(test_folder)
    pollution_ok, pollution_hits = check_ocr_pollution(test_folder)

    score, status, score_reason = score_result(
        test_def["id"],
        m23_status,
        m22_result,
        test_folder,
        unsafe_ok=unsafe_ok,
        unsafe_hits=unsafe_hits,
        crop_ok=crop_ok,
        crop_issues=crop_issues,
        pollution_ok=pollution_ok,
        pollution_hits=pollution_hits,
    )

    forced = m22_result.get("forced_hook_activation") or {}
    if forced and test_def["id"] in ("05", "06"):
        write_json(test_folder / "forced_hook_activation.json", forced)
        dest = test_folder / "discovery" / "forced_hook_activation.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_json(dest, forced)

    result = {
        "test_id": test_def["id"],
        "test_name": test_def["name"],
        "slug": test_def["slug"],
        "m23_run_id": m22_result.get("run_id", ""),
        "m23_status": m23_status,
        "m23_reason": m22_result.get("reason", ""),
        "score": score,
        "status": status,
        "score_reason": score_reason,
        "export_wizard_detected": m22_result.get("export_wizard_detected"),
        "projects_to_export_screen_detected": m22_result.get("projects_to_export_screen_detected"),
        "project_001_talison_detected": m22_result.get("project_001_talison_detected"),
        "project_row_detected": m22_result.get("project_row_detected"),
        "project_row_selected": m22_result.get("project_row_selected"),
        "project_selection_attempted": m22_result.get("project_selection_attempted"),
        "next_pressed_count_total": m22_result.get("next_pressed_count_total", 0),
        "template_screen_detected": m22_result.get("template_screen_detected"),
        "template_evidence_words": m22_result.get("template_evidence_words", ""),
        "template_names_detected": m22_result.get("template_names_detected", []),
        "validation_popup_detected": m22_result.get("validation_popup_detected"),
        "validation_popup_dismissed": m22_result.get("validation_popup_dismissed"),
        "template_screen_detected": m22_result.get("template_screen_detected"),
        "finish_pressed": m22_result.get("finish_pressed"),
        "export_dialog_closed": m22_result.get("export_dialog_closed"),
        "close_method_used": m22_result.get("close_method_used", ""),
        "export_file_created": m22_result.get("export_file_created"),
        "pyautogui_failsafe": bool(m22_result.get("pyautogui_failsafe")),
        "forced_hook_activation": forced or None,
        "test_folder": str(test_folder),
        "setup_notes": setup_notes,
    }
    write_json(test_folder / "test_summary.json", result)
    write_json(test_folder / "result.json", result)
    write_test_report(test_folder, result)
    return result


def precheck_or_fail(
    ctx: Dict[str, Any],
    test_folder: Path,
    label: str,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    ok, notes, precheck = run_m23_precheck(
        ctx["project"],
        ctx["matrix_run_id"],
        test_folder,
        label,
        retry_once=True,
    )
    if ok:
        mouse_neutral_from_precheck(precheck, notes)
        return None, notes
    return (
        setup_failure_result(
            test_folder,
            ctx["test_def"],
            notes,
            precheck.get("reason", "SETUP_PROJECT_RESTORE_FAILED"),
            status="SETUP_PROJECT_RESTORE_FAILED",
        ),
        notes,
    )


def mouse_neutral_from_precheck(precheck: Dict[str, Any], notes: List[str]) -> None:
    rect = (precheck.get("rect_after_clip") or {}) if isinstance(precheck.get("rect_after_clip"), dict) else {}
    if not rect:
        return
    try:
        from eye.screenshot import P6Rect  # noqa: WPS433

        p6_rect = P6Rect(
            left=int(rect.get("left", 0)),
            top=int(rect.get("top", 0)),
            width=int(rect.get("width", 0)),
            height=int(rect.get("height", 0)),
        )
        move = m22_move_mouse_p6_neutral(p6_rect)
        notes.append(f"mouse neutral: ok={move.get('ok')} point={move.get('click_point')}")
    except M22FailSafeError as exc:
        notes.append(f"mouse neutral failsafe: {exc}")


def run_m23_after_hard_precheck(
    ctx: Dict[str, Any],
    evidence: RunEvidence,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Hard matrix precheck already restored P6; skip redundant M22 clean-restore."""
    return run_m23(ctx["project"], evidence=evidence, skip_project_restore=True, **kwargs)


def run_test_01(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    fail, notes = precheck_or_fail(ctx, test_folder, "t01_precheck")
    if fail:
        return fail
    evidence = build_m23_evidence_v2(ctx["matrix_run_id"], "01", ctx["test_def"]["slug"])
    return finish_hard_test(test_folder, ctx["test_def"], run_m23_after_hard_precheck(ctx, evidence), notes)


def run_test_02(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    fail, notes = precheck_or_fail(ctx, test_folder, "t02_precheck")
    if fail:
        return fail
    notes.append(f"Cursor focus: {bring_cursor_to_front()}")
    evidence = build_m23_evidence_v2(ctx["matrix_run_id"], "02", ctx["test_def"]["slug"])
    return finish_hard_test(test_folder, ctx["test_def"], run_m23_after_hard_precheck(ctx, evidence), notes)


def run_test_03(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    fail, notes = precheck_or_fail(ctx, test_folder, "t03_precheck")
    if fail:
        return fail
    notes.append(f"Minimise P6: {minimize_p6()}")
    time.sleep(0.5)
    evidence = build_m23_evidence_v2(ctx["matrix_run_id"], "03", ctx["test_def"]["slug"])
    return finish_hard_test(test_folder, ctx["test_def"], run_m23_after_hard_precheck(ctx, evidence), notes)


def run_test_04(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    fail, notes = precheck_or_fail(ctx, test_folder, "t04_precheck_open")
    if fail:
        return fail
    config = load_json(CONFIG_PATH)
    screen_rule = load_json(SCREEN_RULE_PATH)
    min_confidence = float(config.get("min_ocr_confidence", 0.5))
    p6_keyword = config["p6_window_title_keyword"]
    close: Dict[str, Any] = {}
    closed_check: Dict[str, Any] = {}
    for attempt in range(5):
        notes.extend(m20_hard_dismiss_stale_dialogs(p6_keyword, config, screen_rule, min_confidence))
        close = run_m05(ctx["project"], run_id=f"{ctx['matrix_run_id']}_t04_m05_{attempt}")
        notes.append(f"M05 attempt {attempt + 1} status: {close.get('status')}")
        if close.get("status") in ("MANUAL_REVIEW_UNKNOWN_POPUP", "MANUAL_REVIEW_CANNOT_CONFIRM"):
            notes.extend(m20_hard_dismiss_stale_dialogs(p6_keyword, config, screen_rule, min_confidence))
        closed_check = run_m04(ctx["project"], run_id=f"{ctx['matrix_run_id']}_t04_m04check_{attempt}")
        notes.append(f"M04 after close attempt {attempt + 1}: {closed_check.get('status')}")
        if closed_check.get("status") not in OPEN_PROJECT_STATUSES:
            break
        time.sleep(1.0)
    project_still_open = closed_check.get("status") in OPEN_PROJECT_STATUSES
    notes.append(f"project_still_open_before_m23={project_still_open}")
    evidence = build_m23_evidence_v2(ctx["matrix_run_id"], "04", ctx["test_def"]["slug"])
    m22_result = run_m23(ctx["project"], evidence=evidence, skip_project_restore=True)
    m22_result["test04_project_still_open"] = project_still_open
    result = finish_hard_test(test_folder, ctx["test_def"], m22_result, notes)
    restore = ensure_clean_p6_for_m23_hard(ctx["project"], f"{ctx['matrix_run_id']}_t04_post_restore")
    restore_notes = list(restore.get("notes", []))
    restore_notes.append(f"Post-test-04 restore ok={restore.get('ok')} status={restore.get('status')}")
    write_json(test_folder / "project_restore_attempts.json", restore.get("attempts", []))
    result["setup_notes"] = notes + restore_notes
    if not restore.get("ok"):
        result["status"] = "SETUP_PROJECT_RESTORE_FAILED"
        result["score"] = 0
        result["setup_failure"] = True
        result["score_reason"] = "Post-test-04 project restore failed"
        write_json(test_folder / "result.json", result)
        write_test_report(test_folder, result)
    return result


def run_test_05(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    fail, notes = precheck_or_fail(ctx, test_folder, "t05_precheck")
    if fail:
        return fail
    evidence = build_m23_evidence_v2(ctx["matrix_run_id"], "05", ctx["test_def"]["slug"])
    m22_result = run_m23_after_hard_precheck(
        ctx,
        evidence,
        force_project_row_not_found=True,
    )
    import json

    forced_path = evidence.discovery_dir / "forced_hook_activation.json"
    if forced_path.exists():
        write_json(test_folder / "forced_hook_activation.json", json.loads(forced_path.read_text(encoding="utf-8")))
    return finish_hard_test(test_folder, ctx["test_def"], m22_result, notes)


def run_test_06(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    fail, notes = precheck_or_fail(ctx, test_folder, "t06_precheck")
    if fail:
        return fail
    evidence = build_m23_evidence_v2(ctx["matrix_run_id"], "06", ctx["test_def"]["slug"])
    m22_result = run_m23_after_hard_precheck(
        ctx,
        evidence,
        force_template_screen_not_found=True,
    )
    import json

    forced_path = evidence.discovery_dir / "forced_hook_activation.json"
    if forced_path.exists():
        write_json(test_folder / "forced_hook_activation.json", json.loads(forced_path.read_text(encoding="utf-8")))
    return finish_hard_test(test_folder, ctx["test_def"], m22_result, notes)


HARD_TESTS: List[Dict[str, Any]] = [
    {"id": "01", "slug": "normal_template_screen_discovery", "name": "Normal template screen discovery", "runner": run_test_01},
    {"id": "02", "slug": "p6_behind_cursor", "name": "P6 behind Cursor focus recovery", "runner": run_test_02},
    {"id": "03", "slug": "p6_minimised_restore", "name": "P6 minimised restore path", "runner": run_test_03},
    {"id": "04", "slug": "project_not_open", "name": "Project not open", "runner": run_test_04},
    {"id": "05", "slug": "project_row_not_found", "name": "Project row missing/blocked", "runner": run_test_05},
    {"id": "06", "slug": "template_screen_blocked", "name": "Template screen blocked", "runner": run_test_06},
]


def run_baseline_precheck(project: str, matrix_run_id: str, run_root: Path) -> Tuple[bool, Dict[str, Any]]:
    baseline = ensure_clean_p6_for_m23_hard(project, f"{matrix_run_id}_baseline")
    write_json(run_root / "baseline_restore.json", baseline)
    md_lines = [
        "# M23 Hard Test Baseline Restore",
        "",
        f"- Run ID: {matrix_run_id}",
        f"- OK: {baseline.get('ok')}",
        f"- Status: {baseline.get('status')}",
        f"- Reason: {baseline.get('reason')}",
        f"- Window title: {baseline.get('window_title', '')}",
        f"- Screen state: {baseline.get('screen_state', '')}",
        "",
        "## Notes",
    ]
    for note in baseline.get("notes", []):
        md_lines.append(f"- {note}")
    (run_root / "baseline_restore_report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return bool(baseline.get("ok")), baseline


def should_stop_matrix(result: Dict[str, Any]) -> bool:
    if result.get("setup_failure"):
        return True
    if result.get("status") in SETUP_STOP_STATUSES:
        return True
    return False


def run_matrix(project: str) -> Dict[str, Any]:
    matrix_run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / matrix_run_id
    (run_root / "m23_hard_test_6").mkdir(parents=True, exist_ok=True)

    print("M23 Hard Testing — 6-test matrix")
    print(f"Run ID: {matrix_run_id}")

    baseline_ok, baseline = run_baseline_precheck(project, matrix_run_id, run_root)
    for note in baseline.get("notes", [])[:5]:
        print(f"Baseline: {note}")
    print(f"Baseline restore ok={baseline_ok} status={baseline.get('status')}")

    if not baseline_ok:
        summary = write_hard_summary(matrix_run_id, run_root, [], project)
        summary["decision"] = "STOPPED_FOR_REVIEW"
        summary["baseline_restore_success"] = False
        summary["baseline_status"] = baseline.get("status")
        summary["baseline_reason"] = baseline.get("reason")
        write_json(run_root / "m23_hard_test_6_summary.json", summary)
        print(f"ABORT: Baseline restore failed — {baseline.get('reason')}")
        return summary

    results: List[Dict[str, Any]] = []
    for index, test_def in enumerate(HARD_TESTS, start=1):
        print(f"[{index}/6] {test_def['id']} {test_def['name']}")
        test_folder = build_test_folder_m23(matrix_run_id, test_def["id"], test_def["slug"])
        ctx = {"matrix_run_id": matrix_run_id, "project": project, "test_def": test_def}
        try:
            result = test_def["runner"](ctx, test_folder)
        except M22FailSafeError as exc:
            result = {
                "test_id": test_def["id"],
                "test_name": test_def["name"],
                "slug": test_def["slug"],
                "m23_status": "SETUP_FAILURE_PYAUTOGUI_FAILSAFE",
                "score": 0,
                "status": "SETUP_FAILURE_PYAUTOGUI_FAILSAFE",
                "score_reason": str(exc),
                "pyautogui_failsafe": True,
                "test_folder": str(test_folder),
            }
            write_json(test_folder / "result.json", result)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            result = {
                "test_id": test_def["id"],
                "test_name": test_def["name"],
                "slug": test_def["slug"],
                "m23_status": "CRASH",
                "score": 0,
                "status": "CRASH",
                "score_reason": str(exc),
                "test_folder": str(test_folder),
            }
            write_json(test_folder / "result.json", result)
        results.append(result)
        print(f"  -> score={result.get('score')} status={result.get('status')} m22={result.get('m23_status')}")
        if should_stop_matrix(result):
            summary = write_hard_summary(matrix_run_id, run_root, results, project)
            summary["decision"] = "STOPPED_FOR_REVIEW"
            summary["baseline_restore_success"] = True
            write_json(run_root / "m23_hard_test_6_summary.json", summary)
            return summary

    summary = write_hard_summary(matrix_run_id, run_root, results, project)
    summary["baseline_restore_success"] = True
    write_json(run_root / "m23_hard_test_6_summary.json", summary)
    print(f"Final score: {summary['final_score']}/{summary['max_score']} Decision: {summary['decision']}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M23 Hard Testing 6-test matrix")
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    return 0 if summary.get("decision") == "M23 STABLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
