"""
M19 Hard Testing — 6-test matrix.

Proves M19 can safely open the P6 export wizard, select Spreadsheet/XLSX,
press Next exactly once, detect Export Type options, cancel safely, and never
select export types or create export files.
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

from m19_hard_summary import write_hard_summary  # noqa: E402
from m06_go_to_activities import load_json  # noqa: E402
from m19_discover_spreadsheet_export_type_options import (  # noqa: E402
    RunEvidence,
    run_m19,
    write_json,
)
from m03_open_project_by_name import run_m03  # noqa: E402
from m04_check_project_opened import run_m04  # noqa: E402
from m05_close_project_safely import run_m05  # noqa: E402
from m06_go_to_activities import run_m06  # noqa: E402

PASS_DISCOVERY = frozenset(
    {"PASS_EXPORT_TYPE_DISCOVERY", "PASS_EXPORT_TYPE_DISCOVERY_PARTIAL"}
)
TEST_04_OK = frozenset({"FAIL_PROJECT_NOT_OPEN"})
TEST_05_OK = frozenset({"FAIL_EXPORT_TYPE_OPTIONS_NOT_FOUND"})
TEST_06_OK = frozenset(
    {
        "FAIL_EXPORT_TYPE_SCREEN_NOT_FOUND",
        "MANUAL_REVIEW_CANNOT_CONFIRM",
        "MANUAL_REVIEW_UNSAFE_POPUP",
    }
)

OCR_POLLUTION_WORDS = (
    "chatgpt",
    "cursor",
    "composer",
    "ty_dev2",
    "hard testing summary",
    "evidence path",
    "user message",
)

FORBIDDEN_STEP_MARKERS = (
    "press_key(\"y\")",
    "press_key('y')",
    "press_key(\"n\")",
    "press_key('n')",
    "press_key(\"finish\")",
    "press_key('finish')",
    "ctrl+s",
    "ctrl+p",
    "f9",
    "delete",
    "backspace",
    "ctrl+v",
    "ctrl+x",
)

WRONG_FORMAT_MARKERS = (
    "ocr click on '(xer)",
    "ocr click on 'xer",
    "ocr click on 'xml",
    "ocr click on 'microsoft project",
    "ocr click on 'primavera pm",
    "ocr click on 'primavera contractor",
    "ocr click on 'uncefact",
)

EXPORT_TYPE_CLICK_MARKERS = (
    "select export type",
    "export type option click",
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
        ROOT / "06_output" / "runs" / matrix_run_id / "m19_hard_test_6" / f"test_{test_id}_{slug}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def build_m19_evidence(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
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


def check_unsafe_steps(steps: List[str]) -> Tuple[bool, List[str]]:
    hits: List[str] = []
    for step in steps:
        lowered = step.lower()
        for marker in FORBIDDEN_STEP_MARKERS:
            if marker in lowered:
                hits.append(f"{step} ({marker})")
    return len(hits) == 0, hits


def check_wrong_format(steps: List[str]) -> Tuple[bool, List[str]]:
    hits: List[str] = []
    for step in steps:
        lowered = step.lower()
        if "select spreadsheet option" not in lowered:
            continue
        if any(m in lowered for m in WRONG_FORMAT_MARKERS):
            hits.append(step)
        elif "spreadsheet" not in lowered and "xlsx" not in lowered:
            hits.append(step)
    return len(hits) == 0, hits


def check_export_type_selected(steps: List[str], m19_result: Dict[str, Any]) -> Tuple[bool, List[str]]:
    hits: List[str] = []
    for step in steps:
        lowered = step.lower()
        if any(m in lowered for m in EXPORT_TYPE_CLICK_MARKERS):
            hits.append(step)
    if m19_result.get("export_type_selected"):
        hits.append("export_type_selected flag true")
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


def check_ocr_pollution(test_folder: Path) -> Tuple[bool, List[str]]:
    hits: List[str] = []
    ocr_dir = test_folder / "ocr"
    if not ocr_dir.exists():
        return True, hits
    for ocr_file in ocr_dir.glob("*_ocr.json"):
        try:
            data = load_json(ocr_file)
            blob = " ".join(e.get("text", "") for e in data.get("entries", [])).lower()
            for word in OCR_POLLUTION_WORDS:
                if word in blob:
                    hits.append(f"{ocr_file.name}:{word}")
        except Exception:  # noqa: BLE001
            continue
    return len(hits) == 0, hits


def discovery_files_ok(test_folder: Path, require: bool) -> Tuple[bool, bool]:
    sel = test_folder / "discovery" / "spreadsheet_selection_evidence.json"
    screen = test_folder / "discovery" / "export_type_screen_evidence.json"
    opts = test_folder / "discovery" / "export_type_options.json"
    if not require:
        return any(p.exists() for p in (sel, screen, opts)), sel.exists() and screen.exists() and opts.exists()
    return sel.exists() and screen.exists() and opts.exists(), sel.exists() and screen.exists() and opts.exists()


def count_next_pressed(m19_result: Dict[str, Any]) -> int:
    return int(m19_result.get("next_pressed_count", 0))


def score_result(
    test_id: str,
    m19_status: str,
    m19_result: Dict[str, Any],
    test_folder: Path,
    *,
    unsafe_ok: bool,
    unsafe_hits: List[str],
    crop_ok: bool,
    crop_issues: List[str],
    pollution_ok: bool,
    pollution_hits: List[str],
    format_ok: bool,
    format_hits: List[str],
    type_select_ok: bool,
    type_select_hits: List[str],
) -> Tuple[int, str, str]:
    export_file = bool(m19_result.get("export_file_created"))
    dialog_closed = bool(m19_result.get("export_dialog_closed"))
    wizard_detected = bool(m19_result.get("export_wizard_detected"))
    next_count = count_next_pressed(m19_result)
    finish_pressed = bool(m19_result.get("finish_pressed"))
    next_after_type = bool(m19_result.get("next_pressed_after_export_type"))
    spreadsheet_detected = bool(m19_result.get("spreadsheet_option_detected"))
    type_screen = bool(m19_result.get("export_type_screen_detected"))
    type_options = m19_result.get("export_type_options_detected") or []
    steps_blob = " ".join(m19_result.get("steps", [])).lower()

    if not unsafe_ok:
        return 0, "UNSAFE_ACTION", "; ".join(unsafe_hits[:3])
    if not crop_ok:
        return 0, "FULL_SCREEN_OCR", f"Possible full-screen capture: {crop_issues[:2]}"
    if not format_ok:
        return 0, "WRONG_FORMAT_SELECTED", "; ".join(format_hits[:3])
    if not type_select_ok:
        return 0, "EXPORT_TYPE_SELECTED", "; ".join(type_select_hits[:3])
    if export_file:
        return 0, "EXPORT_FILE_CREATED", "Export file created during test"
    if finish_pressed:
        return 0, "FINISH_PRESSED", "Finish pressed during discovery"
    if next_count > 1:
        return 0, "NEXT_PRESSED_MORE_THAN_ONCE", f"Next pressed {next_count} times"
    if next_after_type:
        return 0, "NEXT_PRESSED_AFTER_EXPORT_TYPE", "Next pressed after Export Type screen"
    if 'press_key("finish")' in steps_blob or "press_key('finish')" in steps_blob:
        return 0, "FINISH_PRESSED", "Finish key in steps"

    if test_id == "04":
        if m19_status not in TEST_04_OK:
            return 0, "FALSE_PASS", f"Test 04 expected FAIL_PROJECT_NOT_OPEN, got {m19_status}"
        if wizard_detected or "open export path" in steps_blob:
            return 0, "FALSE_PASS", "Export wizard opened while project not open"
        if next_count > 0 or "press next once" in steps_blob:
            return 0, "FALSE_PASS", "Next pressed while project not open"
        return 1, m19_status, "Project not open; export wizard not opened; Next not pressed"

    if test_id == "05":
        if m19_status not in TEST_05_OK:
            return 0, "FALSE_PASS", f"Test 05 expected FAIL_EXPORT_TYPE_OPTIONS_NOT_FOUND, got {m19_status}"
        if "force_export_type_options_not_found" not in steps_blob:
            return 0, "FALSE_PASS", "Test 05 missing force_export_type_options_not_found hook"
        if next_count != 1:
            return 0, "FALSE_PASS", f"Test 05 expected Next pressed once; got {next_count}"
        if not type_screen:
            return 0, "FALSE_PASS", "Test 05 expected Export Type screen detected before options block"
        if type_options:
            return 0, "FALSE_PASS", "Test 05 expected no export type options after force hook"
        return 1, m19_status, "Export Type options blocked; no type selected; Next once only"

    if test_id == "06":
        if m19_status not in TEST_06_OK:
            return 0, "FALSE_PASS", f"Test 06 expected controlled fail, got {m19_status}"
        if "force_export_type_screen_not_found" not in steps_blob:
            return 0, "FALSE_PASS", "Test 06 missing force_export_type_screen_not_found hook"
        if next_count > 1:
            return 0, "NEXT_PRESSED_MORE_THAN_ONCE", "Test 06: Next pressed more than once"
        if m19_status in PASS_DISCOVERY:
            return 0, "FALSE_PASS", "Test 06 should not PASS with forced Export Type screen block"
        return 1, m19_status, "Controlled Export Type screen failure; Finish not pressed"

    if test_id == "03":
        if m19_status == "FAIL_P6_WINDOW_NOT_READY":
            if export_file or wizard_detected or next_count > 0:
                return 0, "FALSE_PASS", "Export attempted when P6 window not ready"
            return 1, m19_status, "P6 could not safely restore; no export attempted"
        if m19_status not in PASS_DISCOVERY:
            return 0, "FALSE_PASS", (
                f"Test 03 expected discovery pass or FAIL_P6_WINDOW_NOT_READY, got {m19_status}"
            )

    if test_id == "02" and not pollution_ok:
        return 0, "FALSE_PASS", f"OCR pollution detected: {pollution_hits[:3]}"

    if m19_status not in PASS_DISCOVERY:
        return 0, "FALSE_PASS", (
            f"Expected PASS_EXPORT_TYPE_DISCOVERY or PARTIAL, got {m19_status}"
        )

    if not spreadsheet_detected:
        return 0, "FALSE_PASS", "Discovery pass without Spreadsheet option detected"

    if next_count != 1:
        return 0, "FALSE_PASS", f"Discovery pass requires Next pressed exactly once; got {next_count}"

    if not type_screen:
        return 0, "FALSE_PASS", "Discovery pass without Export Type screen detected"

    if m19_status == "PASS_EXPORT_TYPE_DISCOVERY" and len(type_options) < 2:
        return 0, "FALSE_PASS", f"Full PASS requires >=2 export type options; got {len(type_options)}"

    if wizard_detected and not dialog_closed:
        return 0, "DIALOG_LEFT_OPEN", "Export wizard detected but not closed"

    disc_ok, _ = discovery_files_ok(test_folder, m19_status in PASS_DISCOVERY)
    if m19_status in PASS_DISCOVERY and not disc_ok:
        return 0, "FALSE_PASS", "discovery JSON files missing for successful test"

    after_state = (m19_result.get("screen_state_after") or "").lower()
    title_after = (m19_result.get("window_title_after") or "").lower()
    if wizard_detected and not (
        after_state.startswith("activities")
        or "primavera" in title_after
        or "talison" in title_after
    ):
        return 0, "FALSE_PASS", f"P6 did not return to project window after close: {after_state}"

    return (
        1,
        m19_status,
        f"Export Type discovery OK; next_count={next_count}; options={len(type_options)}; closed={dialog_closed}",
    )


def finish_hard_test(
    test_folder: Path,
    test_def: Dict[str, Any],
    m19_result: Dict[str, Any],
    setup_notes: List[str],
) -> Dict[str, Any]:
    m19_status = m19_result.get("status", "ERROR")
    unsafe_ok, unsafe_hits = check_unsafe_steps(m19_result.get("steps", []))
    crop_ok, crop_issues = check_no_fullscreen_ocr(test_folder)
    pollution_ok, pollution_hits = check_ocr_pollution(test_folder)
    format_ok, format_hits = check_wrong_format(m19_result.get("steps", []))
    type_select_ok, type_select_hits = check_export_type_selected(
        m19_result.get("steps", []), m19_result
    )
    require_disc = m19_status in PASS_DISCOVERY
    disc_ok, _ = discovery_files_ok(test_folder, require_disc)

    score, status, score_reason = score_result(
        test_def["id"],
        m19_status,
        m19_result,
        test_folder,
        unsafe_ok=unsafe_ok,
        unsafe_hits=unsafe_hits,
        crop_ok=crop_ok,
        crop_issues=crop_issues,
        pollution_ok=pollution_ok,
        pollution_hits=pollution_hits,
        format_ok=format_ok,
        format_hits=format_hits,
        type_select_ok=type_select_ok,
        type_select_hits=type_select_hits,
    )

    result = {
        "test_id": test_def["id"],
        "test_name": test_def["name"],
        "slug": test_def["slug"],
        "m19_run_id": m19_result.get("run_id", ""),
        "m19_status": m19_status,
        "m19_reason": m19_result.get("reason", ""),
        "score": score,
        "status": status,
        "score_reason": score_reason,
        "window_title_before": m19_result.get("window_title_before", ""),
        "window_title_after": m19_result.get("window_title_after", ""),
        "screen_state_before": m19_result.get("screen_state_before", ""),
        "screen_state_after": m19_result.get("screen_state_after", ""),
        "export_wizard_detected": m19_result.get("export_wizard_detected"),
        "spreadsheet_option_detected": m19_result.get("spreadsheet_option_detected"),
        "spreadsheet_option_text": m19_result.get("spreadsheet_option_text", ""),
        "spreadsheet_option_selected": m19_result.get("spreadsheet_option_selected"),
        "next_pressed_count": m19_result.get("next_pressed_count", 0),
        "export_type_screen_detected": m19_result.get("export_type_screen_detected"),
        "export_type_evidence_words": m19_result.get("export_type_evidence_words", []),
        "export_type_options_detected": m19_result.get("export_type_options_detected", []),
        "export_type_selected": m19_result.get("export_type_selected"),
        "next_pressed_after_export_type": m19_result.get("next_pressed_after_export_type"),
        "finish_button_detected": m19_result.get("finish_button_detected"),
        "finish_pressed": m19_result.get("finish_pressed"),
        "cancel_button_detected": m19_result.get("cancel_button_detected"),
        "export_dialog_closed": m19_result.get("export_dialog_closed"),
        "close_method_used": m19_result.get("close_method_used", ""),
        "export_file_created": m19_result.get("export_file_created"),
        "discovery_files_ok": disc_ok,
        "unsafe_steps_ok": unsafe_ok,
        "fullscreen_ocr_ok": crop_ok,
        "ocr_pollution_ok": pollution_ok,
        "wrong_format_ok": format_ok,
        "export_type_select_ok": type_select_ok,
        "test_folder": str(test_folder),
        "setup_notes": setup_notes,
    }

    write_json(test_folder / "test_summary.json", result)
    lines = [
        f"# M19 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- M19 run ID: {m19_result.get('run_id', '')}",
        f"- M19 status: {m19_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Spreadsheet option detected: {result['spreadsheet_option_detected']}",
        f"- Spreadsheet option selected: {result['spreadsheet_option_selected']}",
        f"- Next pressed count: {result['next_pressed_count']}",
        f"- Export Type screen detected: {result['export_type_screen_detected']}",
        f"- Export Type options detected: {result['export_type_options_detected']}",
        f"- Export Type selected: {result['export_type_selected']}",
        f"- Next pressed after Export Type: {result['next_pressed_after_export_type']}",
        f"- Finish pressed: {result['finish_pressed']}",
        f"- Export dialog closed: {result['export_dialog_closed']}",
        f"- Close method: {result['close_method_used']}",
        f"- Export file created: {result['export_file_created']}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## M19 reason", m19_result.get("reason", "")])
    (test_folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def run_test_01(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Run M19 normal Export Type discovery"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "01")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    evidence = build_m19_evidence(ctx["matrix_run_id"], "01", ctx["test_def"]["slug"])
    m19_result = run_m19(ctx["project"], evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m19_result, notes)


def run_test_02(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Bring Cursor in front before M19"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "02")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    cursor = bring_cursor_to_front()
    notes.append(f"Cursor focus: {cursor}")
    evidence = build_m19_evidence(ctx["matrix_run_id"], "02", ctx["test_def"]["slug"])
    m19_result = run_m19(ctx["project"], evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m19_result, notes)


def run_test_03(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Minimise P6 before M19"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "03")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    mini = minimize_p6()
    notes.append(f"Minimise P6: {mini}")
    time.sleep(0.5)
    evidence = build_m19_evidence(ctx["matrix_run_id"], "03", ctx["test_def"]["slug"])
    m19_result = run_m19(ctx["project"], evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m19_result, notes)


def run_test_04(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Close project with M05", "Run M19 without opening project"]
    close = run_m05(ctx["project"], run_id=f"{ctx['matrix_run_id']}_t04_m05")
    notes.append(f"M05 status: {close.get('status')}")
    evidence = build_m19_evidence(ctx["matrix_run_id"], "04", ctx["test_def"]["slug"])
    m19_result = run_m19(ctx["project"], evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m19_result, notes)


def run_test_05(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = [
        "Chain M03 -> M04 -> M06",
        "Run M19 with force_export_type_options_not_found",
    ]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "05")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    evidence = build_m19_evidence(ctx["matrix_run_id"], "05", ctx["test_def"]["slug"])
    m19_result = run_m19(
        ctx["project"],
        evidence=evidence,
        force_export_type_options_not_found=True,
    )
    return finish_hard_test(test_folder, ctx["test_def"], m19_result, notes)


def run_test_06(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = [
        "Chain M03 -> M04 -> M06",
        "Run M19 with force_export_type_screen_not_found",
    ]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "06")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    evidence = build_m19_evidence(ctx["matrix_run_id"], "06", ctx["test_def"]["slug"])
    m19_result = run_m19(
        ctx["project"],
        evidence=evidence,
        force_export_type_screen_not_found=True,
    )
    return finish_hard_test(test_folder, ctx["test_def"], m19_result, notes)


HARD_TESTS: List[Dict[str, Any]] = [
    {
        "id": "01",
        "slug": "normal_export_type_discovery",
        "name": "Normal Export Type discovery",
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
        "slug": "project_not_open",
        "name": "Project not open",
        "runner": run_test_04,
    },
    {
        "id": "05",
        "slug": "export_type_options_blocked",
        "name": "Export Type options missing / blocked condition",
        "runner": run_test_05,
    },
    {
        "id": "06",
        "slug": "export_type_screen_blocked",
        "name": "Export Type screen blocked / unclear condition",
        "runner": run_test_06,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    matrix_run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / matrix_run_id
    (run_root / "m19_hard_test_6").mkdir(parents=True, exist_ok=True)

    print("M19 Hard Testing — 6-test matrix")
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
                "m19_run_id": "",
                "m19_status": "CRASH",
                "m19_reason": str(exc),
                "score": 0,
                "status": "CRASH",
                "score_reason": traceback.format_exc(),
                "test_folder": str(test_folder),
                "setup_notes": [f"crash: {exc}"],
            }
            write_json(test_folder / "test_summary.json", result)
        results.append(result)
        print(
            f"  -> score={result.get('score')} status={result.get('status')} "
            f"m19={result.get('m19_status')}"
        )

    summary = write_hard_summary(matrix_run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']}/{summary['max_score']}")
    print(f"Decision: {summary['decision']}")
    print(f"Summary: {run_root / 'm19_hard_test_6_summary.json'}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M19 Hard Testing 6-test matrix")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    if summary.get("decision") == "M19 STABLE":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
