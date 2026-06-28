"""
M14 Hard Testing — 6-test matrix.

Proves M14 can safely shift-select multiple visible P6 activity rows, copy
table-like clipboard data, reject polluted content, restore clipboard, and
stop when row targets cannot be confirmed.
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

from m14_hard_summary import write_hard_summary  # noqa: E402
from m06_go_to_activities import load_json  # noqa: E402
from m14_copy_visible_activity_rows_multi_select import (  # noqa: E402
    CLIPBOARD_POLLUTION_WORDS,
    RunEvidence,
    run_m14,
    write_clipboard_text,
    write_json,
)
from m03_open_project_by_name import run_m03  # noqa: E402
from m04_check_project_opened import run_m04  # noqa: E402
from m05_close_project_safely import run_m05  # noqa: E402
from m06_go_to_activities import run_m06  # noqa: E402

PASS_CLIPBOARD = frozenset({"PASS", "PASS_PARTIAL_CLIPBOARD"})
TEST_06_OK = frozenset(
    {
        "FAIL_ACTIVITIES_NOT_FOUND",
        "FAIL_TABLE_NOT_DETECTED",
        "FAIL_NOT_ENOUGH_VISIBLE_ROWS",
        "MANUAL_REVIEW_CANNOT_CONFIRM",
    }
)
POLLUTION_SEED = "ChatGPT Cursor TY_DEV2 HARD TESTING SUMMARY Evidence path composer sandbox"
FORBIDDEN_STEP_MARKERS = (
    "ctrl+x",
    "ctrl+v",
    "delete",
    "backspace",
    "f9",
    "ctrl+s",
    "ctrl+p",
    "export",
    "import",
    "ctrl+w",
    "ctrl+a",
    "press_key(\"y\")",
    "press_key('y')",
    "press_key(\"n\")",
)

CLIPBOARD_FILES = (
    "clipboard_raw.txt",
    "clipboard_table.csv",
    "clipboard_table.json",
    "clipboard_validation.json",
    "row_selection_targets.json",
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
        ROOT / "06_output" / "runs" / matrix_run_id / "m14_hard_test_6" / f"test_{test_id}_{slug}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def build_m14_evidence(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
    folder = build_test_folder(matrix_run_id, test_id, slug)
    for sub in ("screenshots", "ocr", "classification", "popup", "clipboard"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    return RunEvidence(
        run_id=f"{matrix_run_id}_t{test_id}",
        folder=folder,
        screenshots_dir=folder / "screenshots",
        ocr_dir=folder / "ocr",
        classification_dir=folder / "classification",
        popup_dir=folder / "popup",
        clipboard_dir=folder / "clipboard",
    )


def chain_m03_m04_m06(project: str, matrix_run_id: str, test_id: str) -> Dict[str, Any]:
    prefix = f"{matrix_run_id}_t{test_id}"
    m03 = run_m03(project, run_id=f"{prefix}_m03")
    m04 = run_m04(project, run_id=f"{prefix}_m04")
    m06 = run_m06(project, run_id=f"{prefix}_m06")
    return {"m03": m03, "m04": m04, "m06": m06}


def bring_cursor_to_front() -> Dict[str, Any]:
    try:
        import pygetwindow as gw  # noqa: WPS433

        for window in gw.getAllWindows():
            title = window.title or ""
            if "cursor" in title.lower():
                window.activate()
                time.sleep(0.6)
                return {"success": True, "title": title}
        return {"success": False, "message": "No Cursor window found"}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": str(exc)}


def minimize_p6() -> Dict[str, Any]:
    from accessibility.hand import window_tools  # noqa: WPS433

    return window_tools.minimize_window_by_title(get_p6_keyword())


def clipboard_files_ok(test_folder: Path, require_all: bool) -> Tuple[bool, Dict[str, bool]]:
    clip = test_folder / "clipboard"
    checks = {name: (clip / name).exists() for name in CLIPBOARD_FILES}
    if not require_all:
        return checks.get("clipboard_raw.txt", False), checks
    return all(checks.values()), checks


def check_no_fullscreen_ocr(test_folder: Path) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    shots = test_folder / "screenshots"
    if shots.exists():
        for shot in shots.glob("*.png"):
            name = shot.name.lower()
            if "desktop" in name or "fullscreen" in name or "full_screen" in name:
                issues.append(shot.name)
    return len(issues) == 0, issues


def check_unsafe_steps(m14_result: Dict[str, Any]) -> Tuple[bool, List[str]]:
    hits: List[str] = []
    for step in m14_result.get("steps", []):
        lowered = step.lower()
        for marker in FORBIDDEN_STEP_MARKERS:
            if marker in lowered:
                hits.append(f"{step} ({marker})")
    return len(hits) == 0, hits


def check_pollution_in_text(text: str) -> Tuple[bool, List[str]]:
    blob = text.lower()
    hits = [w for w in CLIPBOARD_POLLUTION_WORDS if w in blob]
    for token in ("chatgpt", "cursor", "ty_dev2", "evidence path", "hard testing summary"):
        if token in blob and token not in hits:
            hits.append(token)
    return len(hits) == 0, sorted(set(hits))


def check_targets_inside_p6(m14_result: Dict[str, Any]) -> Tuple[bool, str]:
    for key in ("selected_first_target", "selected_last_target"):
        target = m14_result.get(key) or {}
        if not target:
            continue
        x = float(target.get("x", 0))
        y = float(target.get("y", 0))
        if x < 0 or y < 0:
            return False, f"{key} negative coords ({x}, {y})"
        if x > 5000 or y > 5000:
            return False, f"{key} implausible coords ({x}, {y})"
    return True, ""


def read_clipboard_raw(test_folder: Path) -> str:
    raw_path = test_folder / "clipboard" / "clipboard_raw.txt"
    if raw_path.is_file():
        return raw_path.read_text(encoding="utf-8", errors="replace")
    return ""


def score_result(
    test_id: str,
    m14_status: str,
    m14_result: Dict[str, Any],
    test_folder: Path,
    *,
    unsafe_ok: bool,
    unsafe_hits: List[str],
    crop_ok: bool,
    crop_issues: List[str],
    grid_ok: bool,
    grid_reason: str,
    clipboard_ok: bool,
    clipboard_checks: Dict[str, bool],
) -> Tuple[int, str, str]:
    if not unsafe_ok:
        return 0, "UNSAFE_ACTION", "; ".join(unsafe_hits[:3])
    if not crop_ok:
        return 0, "FALSE_PASS", f"Possible full-screen capture: {crop_issues[:2]}"

    pollution_detected = bool(m14_result.get("clipboard_pollution_detected"))
    copy_method = (m14_result.get("copy_method_used") or "").strip()
    selection_method = (m14_result.get("selection_method_used") or "").strip()
    fg_confirmed = bool(m14_result.get("p6_foreground_confirmed_before_copy"))
    had_text = bool(m14_result.get("clipboard_had_text_before"))
    restored = bool(m14_result.get("clipboard_restored"))
    activity_rows = int(m14_result.get("activity_like_row_count", 0))
    raw_text = read_clipboard_raw(test_folder)
    text_clean, pollution_words = check_pollution_in_text(raw_text)
    steps_blob = " ".join(m14_result.get("steps", [])).lower()
    shift_used = "shift-select" in steps_blob or "shift_click" in selection_method.lower()

    if test_id == "05":
        if m14_status != "FAIL_PROJECT_NOT_OPEN":
            return 0, "FALSE_PASS", f"Test 05 expected FAIL_PROJECT_NOT_OPEN, got {m14_status}"
        if copy_method or shift_used or m14_result.get("clipboard_changed_from_sentinel"):
            return 0, "FALSE_PASS", "Selection or copy attempted while project not open"
        return 1, m14_status, "Project not open; M14 did not open project or copy rows"

    if test_id == "06":
        if m14_status not in TEST_06_OK:
            return 0, "FALSE_PASS", f"Test 06 expected controlled fail, got {m14_status}"
        if copy_method or shift_used or m14_result.get("clipboard_changed_from_sentinel"):
            return 0, "FALSE_PASS", "Shift-click or copy attempted without enough row targets"
        if fg_confirmed and copy_method:
            return 0, "FALSE_PASS", "Copy attempted without confirmed row targets"
        return 1, m14_status, "Controlled failure without unsafe multi-row selection"

    if test_id == "03":
        if m14_status == "FAIL_P6_WINDOW_NOT_READY":
            if copy_method or m14_result.get("clipboard_changed_from_sentinel"):
                return 0, "FALSE_PASS", "Copy attempted when P6 window not ready"
            return 1, m14_status, "P6 could not safely restore; no polluted copy"
        if m14_status not in PASS_CLIPBOARD:
            return 0, "FALSE_PASS", f"Test 03 expected pass or FAIL_P6_WINDOW_NOT_READY, got {m14_status}"

    if test_id == "04":
        if m14_status == "FAIL_CLIPBOARD_NOT_TABLE":
            if pollution_detected or pollution_words:
                return 1, m14_status, "Pollution correctly detected in clipboard result"
            return 0, "FALSE_PASS", "FAIL_CLIPBOARD_NOT_TABLE without pollution detection"
        if m14_status not in PASS_CLIPBOARD:
            return 0, "FALSE_PASS", f"Test 04 expected pass or pollution fail, got {m14_status}"

    if m14_status in ("CRASH", "ERROR"):
        return 0, m14_status, m14_result.get("reason", "Unhandled error")

    if m14_status not in PASS_CLIPBOARD:
        return 0, "FALSE_PASS", f"Expected PASS or PASS_PARTIAL_CLIPBOARD, got {m14_status}"

    if not fg_confirmed:
        return 0, "P6_FOREGROUND_NOT_CONFIRMED_BEFORE_COPY", "P6 foreground not confirmed before copy"
    if not grid_ok:
        return 0, "GRID_CLICK_OUTSIDE_P6", grid_reason or "Row click target invalid"
    if activity_rows < 1:
        return 0, "FALSE_PASS", "No activity-like rows in clipboard"
    if pollution_detected or not text_clean:
        words = m14_result.get("clipboard_pollution_words") or pollution_words
        return 0, "CLIPBOARD_POLLUTION", f"Pollution in clipboard: {words}"
    if not clipboard_ok:
        return 0, "FALSE_PASS", f"Clipboard files missing: {clipboard_checks}"
    if had_text and not restored:
        return 0, "CLIPBOARD_NOT_RESTORED", m14_result.get("clipboard_restore_reason", "not restored")

    fg_title = (
        m14_result.get("foreground_before_selection")
        or m14_result.get("foreground_before_copy")
        or ""
    ).lower()
    if "primavera" not in fg_title and "p6" not in fg_title:
        return 0, "P6_FOREGROUND_NOT_CONFIRMED_BEFORE_COPY", f"Foreground before copy: {fg_title[:60]}"

    return 1, m14_status, f"Clipboard table-like with {activity_rows} activity row(s)"


def finish_hard_test(
    test_folder: Path,
    test_def: Dict[str, Any],
    m14_result: Dict[str, Any],
    setup_notes: List[str],
) -> Dict[str, Any]:
    m14_status = m14_result.get("status", "ERROR")
    unsafe_ok, unsafe_hits = check_unsafe_steps(m14_result)
    crop_ok, crop_issues = check_no_fullscreen_ocr(test_folder)
    grid_ok, grid_reason = check_targets_inside_p6(m14_result)
    require_clipboard = m14_status in PASS_CLIPBOARD
    clipboard_ok, clipboard_checks = clipboard_files_ok(test_folder, require_clipboard)

    score, status, score_reason = score_result(
        test_def["id"],
        m14_status,
        m14_result,
        test_folder,
        unsafe_ok=unsafe_ok,
        unsafe_hits=unsafe_hits,
        crop_ok=crop_ok,
        crop_issues=crop_issues,
        grid_ok=grid_ok,
        grid_reason=grid_reason,
        clipboard_ok=clipboard_ok,
        clipboard_checks=clipboard_checks,
    )

    result = {
        "test_id": test_def["id"],
        "test_name": test_def["name"],
        "slug": test_def["slug"],
        "m14_run_id": m14_result.get("run_id", ""),
        "m14_status": m14_status,
        "m14_reason": m14_result.get("reason", ""),
        "score": score,
        "status": status,
        "score_reason": score_reason,
        "foreground_before_selection": m14_result.get("foreground_before_selection", ""),
        "foreground_after_selection": m14_result.get("foreground_after_selection", ""),
        "p6_foreground_confirmed_before_copy": m14_result.get("p6_foreground_confirmed_before_copy"),
        "visible_activity_targets_count": m14_result.get("visible_activity_targets_count", 0),
        "selected_first_target": m14_result.get("selected_first_target", {}),
        "selected_last_target": m14_result.get("selected_last_target", {}),
        "selection_method_used": m14_result.get("selection_method_used", ""),
        "copy_method_used": m14_result.get("copy_method_used", ""),
        "clipboard_sentinel_used": m14_result.get("clipboard_sentinel_used"),
        "clipboard_changed_from_sentinel": m14_result.get("clipboard_changed_from_sentinel"),
        "clipboard_pollution_detected": m14_result.get("clipboard_pollution_detected"),
        "clipboard_pollution_words": m14_result.get("clipboard_pollution_words", []),
        "clipboard_line_count": m14_result.get("clipboard_line_count", 0),
        "clipboard_column_guess": m14_result.get("clipboard_column_guess", 0),
        "activity_like_row_count": m14_result.get("activity_like_row_count", 0),
        "headers_detected": m14_result.get("headers_detected", []),
        "clipboard_restored": m14_result.get("clipboard_restored"),
        "clipboard_had_text_before": m14_result.get("clipboard_had_text_before"),
        "clipboard_files_ok": clipboard_ok,
        "unsafe_steps_ok": unsafe_ok,
        "fullscreen_ocr_ok": crop_ok,
        "test_folder": str(test_folder),
        "setup_notes": setup_notes,
    }

    write_json(test_folder / "test_summary.json", result)
    lines = [
        f"# M14 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- M14 run ID: {m14_result.get('run_id', '')}",
        f"- M14 status: {m14_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Foreground before selection: {result['foreground_before_selection']}",
        f"- P6 foreground confirmed: {result['p6_foreground_confirmed_before_copy']}",
        f"- Visible targets: {result['visible_activity_targets_count']}",
        f"- Selection method: {result['selection_method_used']}",
        f"- Copy method: {result['copy_method_used']}",
        f"- Activity-like rows: {result['activity_like_row_count']}",
        f"- Clipboard pollution: {result['clipboard_pollution_detected']}",
        f"- Clipboard restored: {result['clipboard_restored']}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## M14 reason", m14_result.get("reason", "")])
    (test_folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def run_test_01(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Run M14 with --max-rows 3"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "01")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    evidence = build_m14_evidence(ctx["matrix_run_id"], "01", ctx["test_def"]["slug"])
    m14_result = run_m14(ctx["project"], max_rows=3, evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m14_result, notes)


def run_test_02(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Bring Cursor in front before M14"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "02")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    cursor = bring_cursor_to_front()
    notes.append(f"Cursor focus: {cursor}")
    evidence = build_m14_evidence(ctx["matrix_run_id"], "02", ctx["test_def"]["slug"])
    m14_result = run_m14(ctx["project"], max_rows=3, evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m14_result, notes)


def run_test_03(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Minimise P6 before M14"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "03")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    mini = minimize_p6()
    notes.append(f"Minimise P6: {mini}")
    time.sleep(0.5)
    evidence = build_m14_evidence(ctx["matrix_run_id"], "03", ctx["test_def"]["slug"])
    m14_result = run_m14(ctx["project"], max_rows=3, evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m14_result, notes)


def run_test_04(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Seed polluted clipboard before M14"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "04")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    write_clipboard_text(POLLUTION_SEED)
    notes.append(f"Seeded clipboard: {POLLUTION_SEED[:55]}...")
    time.sleep(0.3)
    evidence = build_m14_evidence(ctx["matrix_run_id"], "04", ctx["test_def"]["slug"])
    m14_result = run_m14(ctx["project"], max_rows=3, evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m14_result, notes)


def run_test_05(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Close project with M05", "Run M14 without opening project"]
    close = run_m05(ctx["project"], run_id=f"{ctx['matrix_run_id']}_t05_m05")
    notes.append(f"M05 status: {close.get('status')}")
    evidence = build_m14_evidence(ctx["matrix_run_id"], "05", ctx["test_def"]["slug"])
    m14_result = run_m14(ctx["project"], max_rows=3, evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m14_result, notes)


def run_test_06(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = [
        "Chain M03 -> M04 -> M06",
        "Run M14 with force_insufficient_row_targets (hard test mode)",
    ]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "06")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    evidence = build_m14_evidence(ctx["matrix_run_id"], "06", ctx["test_def"]["slug"])
    m14_result = run_m14(
        ctx["project"],
        max_rows=3,
        evidence=evidence,
        force_insufficient_row_targets=True,
    )
    return finish_hard_test(test_folder, ctx["test_def"], m14_result, notes)


HARD_TESTS: List[Dict[str, Any]] = [
    {
        "id": "01",
        "slug": "normal_multi_row_copy",
        "name": "Normal multi-row copy",
        "runner": run_test_01,
    },
    {
        "id": "02",
        "slug": "p6_behind_cursor_focus_recovery",
        "name": "P6 behind Cursor focus recovery",
        "runner": run_test_02,
    },
    {
        "id": "03",
        "slug": "p6_minimised_restore_path",
        "name": "P6 minimised restore path",
        "runner": run_test_03,
    },
    {
        "id": "04",
        "slug": "clipboard_pollution_seed_replacement",
        "name": "Clipboard pollution seed replacement",
        "runner": run_test_04,
    },
    {
        "id": "05",
        "slug": "project_not_open",
        "name": "Project not open",
        "runner": run_test_05,
    },
    {
        "id": "06",
        "slug": "cannot_confirm_enough_row_targets",
        "name": "Cannot confirm enough visible row targets",
        "runner": run_test_06,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    matrix_run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / matrix_run_id
    (run_root / "m14_hard_test_6").mkdir(parents=True, exist_ok=True)

    print("M14 Hard Testing — 6-test matrix")
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
                "slug": test_def["slug"],
                "m14_run_id": "",
                "m14_status": "CRASH",
                "m14_reason": str(exc),
                "score": 0,
                "status": "CRASH",
                "score_reason": traceback.format_exc(),
                "test_folder": str(test_folder),
                "setup_notes": [f"crash: {exc}"],
            }
            write_json(test_folder / "test_summary.json", result)
        results.append(result)
        print(f"  -> score={result.get('score')} status={result.get('status')} m14={result.get('m14_status')}")

    summary = write_hard_summary(matrix_run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']}/{summary['max_score']}")
    print(f"Decision: {summary['decision']}")
    print(f"Summary: {run_root / 'm14_hard_test_6_summary.json'}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M14 Hard Testing 6-test matrix")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    if summary.get("decision") == "M14 STABLE":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
