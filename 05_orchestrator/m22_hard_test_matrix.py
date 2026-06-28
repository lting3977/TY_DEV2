"""
M22 Hard Testing — 6-test matrix.

Proves M22 can confirm default template on template screen, press Next once,
detect post-template path screen, cancel safely, and never modify templates.
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

from m22_hard_summary import write_hard_summary  # noqa: E402
from m06_go_to_activities import load_json  # noqa: E402
from m22_select_default_activity_template_discovery_only import (  # noqa: E402
    RunEvidence,
    run_m22,
    write_json,
)
from m03_open_project_by_name import run_m03  # noqa: E402
from m04_check_project_opened import run_m04  # noqa: E402
from m05_close_project_safely import run_m05  # noqa: E402
from m06_go_to_activities import run_m06  # noqa: E402

PASS_DISCOVERY = frozenset(
    {"PASS_DEFAULT_TEMPLATE_DISCOVERY", "PASS_DEFAULT_TEMPLATE_DISCOVERY_PARTIAL"}
)
TEST_04_OK = frozenset({"FAIL_PROJECT_NOT_OPEN"})
TEST_05_OK = frozenset({"FAIL_DEFAULT_TEMPLATE_NOT_FOUND"})
TEST_06_OK = frozenset(
    {
        "MANUAL_REVIEW_UNSAFE_POPUP",
        "FAIL_UNSAFE_TEMPLATE_ACTION",
        "FAIL_TEMPLATE_ACTION_BLOCKED",
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
    'press_key("y")',
    "press_key('y')",
    'press_key("n")',
    "press_key('n')",
    'press_key("finish")',
    "press_key('finish')",
    "ctrl+s",
    "ctrl+p",
    "f9",
    "delete",
    "backspace",
    "ctrl+v",
    "ctrl+x",
    "modify template",
    "delete template",
    "add template",
    "browse",
)

TEMPLATE_ACTION_MARKERS = (
    "modify template",
    "delete template",
    "add template",
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
        ROOT / "06_output" / "runs" / matrix_run_id / "m22_hard_test_6" / f"test_{test_id}_{slug}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def build_m22_evidence(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
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


def check_no_template_actions(steps: List[str]) -> Tuple[bool, List[str]]:
    hits: List[str] = []
    for step in steps:
        lowered = step.lower()
        if any(m in lowered for m in TEMPLATE_ACTION_MARKERS):
            hits.append(step)
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
    path = test_folder / "discovery" / "default_template_discovery.json"
    if not require:
        return path.exists(), path.exists()
    return path.exists(), path.exists()


def hook_blob(m22_result: Dict[str, Any]) -> str:
    steps = " ".join(m22_result.get("steps", [])).lower()
    reason = (m22_result.get("reason") or "").lower()
    return f"{steps} {reason}"


def score_result(
    test_id: str,
    m22_status: str,
    m22_result: Dict[str, Any],
    test_folder: Path,
    *,
    unsafe_ok: bool,
    unsafe_hits: List[str],
    template_action_ok: bool,
    template_action_hits: List[str],
    crop_ok: bool,
    crop_issues: List[str],
    pollution_ok: bool,
    pollution_hits: List[str],
) -> Tuple[int, str, str]:
    export_file = bool(m22_result.get("export_file_created"))
    dialog_closed = bool(m22_result.get("export_dialog_closed"))
    wizard_detected = bool(m22_result.get("export_wizard_detected"))
    default_ok = bool(m22_result.get("default_template_detected"))
    post_screen = bool(m22_result.get("post_template_screen_detected"))
    next_count = int(m22_result.get("next_pressed_count", 0))
    finish_pressed = bool(m22_result.get("finish_pressed"))
    steps_blob = hook_blob(m22_result)

    if not unsafe_ok:
        return 0, "UNSAFE_ACTION", "; ".join(unsafe_hits[:3])
    if not template_action_ok:
        return 0, "UNSAFE_TEMPLATE_ACTION", "; ".join(template_action_hits[:3])
    if not crop_ok:
        return 0, "FULL_SCREEN_OCR", f"Possible full-screen capture: {crop_issues[:2]}"
    if export_file:
        return 0, "EXPORT_FILE_CREATED", "Export file created during test"
    if finish_pressed:
        return 0, "FINISH_PRESSED", "Finish pressed during default template discovery"
    if next_count > 3:
        return 0, "NEXT_PRESSED_TOO_MANY", f"Next pressed {next_count} times (max 3)"

    if test_id == "04":
        if m22_status not in TEST_04_OK:
            return 0, "FALSE_PASS", f"Test 04 expected FAIL_PROJECT_NOT_OPEN, got {m22_status}"
        if wizard_detected or "open export path" in steps_blob:
            return 0, "FALSE_PASS", "Export wizard opened while project not open"
        return 1, m22_status, "Project not open; export wizard not opened"

    if test_id == "05":
        if m22_status not in TEST_05_OK:
            return 0, "FALSE_PASS", f"Test 05 expected FAIL_DEFAULT_TEMPLATE_NOT_FOUND, got {m22_status}"
        if "force_default_template_not_found" not in steps_blob:
            return 0, "FALSE_PASS", "Test 05 missing force_default_template_not_found hook"
        if default_ok:
            return 0, "FALSE_PASS", "Test 05 expected default template not detected after hook"
        return 1, m22_status, "Default template missing; no unsafe export action"

    if test_id == "06":
        if m22_status not in TEST_06_OK:
            return 0, "FALSE_PASS", f"Test 06 expected unsafe-template prevention fail, got {m22_status}"
        if "force_unsafe_template_action" not in steps_blob:
            return 0, "FALSE_PASS", "Test 06 missing force_unsafe_template_action hook"
        if m22_status in PASS_DISCOVERY:
            return 0, "FALSE_PASS", "Test 06 should not PASS when unsafe template action forced"
        return 1, m22_status, "Unsafe template action prevented; Finish not pressed"

    if test_id == "03":
        if m22_status == "FAIL_P6_WINDOW_NOT_READY":
            if export_file or wizard_detected or next_count > 0:
                return 0, "FALSE_PASS", "Export attempted when P6 window not ready"
            return 1, m22_status, "P6 could not safely restore; no export attempted"
        if m22_status not in PASS_DISCOVERY:
            return 0, "FALSE_PASS", (
                f"Test 03 expected discovery pass or FAIL_P6_WINDOW_NOT_READY, got {m22_status}"
            )

    if test_id == "02" and not pollution_ok:
        return 0, "FALSE_PASS", f"OCR pollution detected: {pollution_hits[:3]}"

    if m22_status not in PASS_DISCOVERY:
        return 0, "FALSE_PASS", f"Expected PASS_DEFAULT_TEMPLATE_NEXT_DISCOVERY or PARTIAL, got {m22_status}"

    if not default_ok:
        return 0, "FALSE_PASS", "Discovery pass without default template detected"

    if next_count != 3:
        return 0, "FALSE_PASS", f"Discovery pass requires Next pressed exactly 3 times; got {next_count}"

    if not post_screen:
        return 0, "FALSE_PASS", "Discovery pass without post-template screen detected"

    if wizard_detected and not dialog_closed:
        return 0, "DIALOG_LEFT_OPEN", "Export wizard detected but not closed"

    disc_ok, _ = discovery_files_ok(test_folder, m22_status in PASS_DISCOVERY)
    if m22_status in PASS_DISCOVERY and not disc_ok:
        return 0, "FALSE_PASS", "default_template_discovery.json missing for successful test"

    after_state = (m22_result.get("screen_state_after") or "").lower()
    title_after = (m22_result.get("window_title_after") or "").lower()
    if wizard_detected and not (
        after_state.startswith("activities")
        or "primavera" in title_after
        or "talison" in title_after
    ):
        return 0, "FALSE_PASS", f"P6 did not return to project window after close: {after_state}"

    return (
        1,
        m22_status,
        f"Default template Next OK; next_count={next_count}; post_template={post_screen}; closed={dialog_closed}",
    )


def finish_hard_test(
    test_folder: Path,
    test_def: Dict[str, Any],
    m22_result: Dict[str, Any],
    setup_notes: List[str],
) -> Dict[str, Any]:
    m22_status = m22_result.get("status", "ERROR")
    unsafe_ok, unsafe_hits = check_unsafe_steps(m22_result.get("steps", []))
    template_action_ok, template_action_hits = check_no_template_actions(m22_result.get("steps", []))
    crop_ok, crop_issues = check_no_fullscreen_ocr(test_folder)
    pollution_ok, pollution_hits = check_ocr_pollution(test_folder)
    require_disc = m22_status in PASS_DISCOVERY
    disc_ok, _ = discovery_files_ok(test_folder, require_disc)

    score, status, score_reason = score_result(
        test_def["id"],
        m22_status,
        m22_result,
        test_folder,
        unsafe_ok=unsafe_ok,
        unsafe_hits=unsafe_hits,
        template_action_ok=template_action_ok,
        template_action_hits=template_action_hits,
        crop_ok=crop_ok,
        crop_issues=crop_issues,
        pollution_ok=pollution_ok,
        pollution_hits=pollution_hits,
    )

    result = {
        "test_id": test_def["id"],
        "test_name": test_def["name"],
        "slug": test_def["slug"],
        "m22_run_id": m22_result.get("run_id", ""),
        "m22_status": m22_status,
        "m22_reason": m22_result.get("reason", ""),
        "score": score,
        "status": status,
        "score_reason": score_reason,
        "default_template_detected": m22_result.get("default_template_detected"),
        "default_template_excerpt": m22_result.get("default_template_excerpt", ""),
        "post_template_screen_detected": m22_result.get("post_template_screen_detected"),
        "post_template_evidence_words": m22_result.get("post_template_evidence_words", []),
        "next_pressed_count": m22_result.get("next_pressed_count", 0),
        "finish_pressed": m22_result.get("finish_pressed"),
        "export_dialog_closed": m22_result.get("export_dialog_closed"),
        "close_method_used": m22_result.get("close_method_used", ""),
        "export_file_created": m22_result.get("export_file_created"),
        "discovery_files_ok": disc_ok,
        "unsafe_steps_ok": unsafe_ok,
        "template_action_ok": template_action_ok,
        "test_folder": str(test_folder),
        "setup_notes": setup_notes,
    }

    write_json(test_folder / "test_summary.json", result)
    lines = [
        f"# M22 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- M22 status: {m22_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Default template detected: {result['default_template_detected']}",
        f"- Post-template screen: {result['post_template_screen_detected']}",
        f"- Next pressed count: {result['next_pressed_count']}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    (test_folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def run_test_01(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Run M22 normal default template discovery"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "01")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    evidence = build_m22_evidence(ctx["matrix_run_id"], "01", ctx["test_def"]["slug"])
    return finish_hard_test(test_folder, ctx["test_def"], run_m22(ctx["project"], evidence=evidence), notes)


def run_test_02(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Bring Cursor in front before M22"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "02")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    notes.append(f"Cursor focus: {bring_cursor_to_front()}")
    evidence = build_m22_evidence(ctx["matrix_run_id"], "02", ctx["test_def"]["slug"])
    return finish_hard_test(test_folder, ctx["test_def"], run_m22(ctx["project"], evidence=evidence), notes)


def run_test_03(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Minimise P6 before M22"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "03")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    notes.append(f"Minimise P6: {minimize_p6()}")
    time.sleep(0.5)
    evidence = build_m22_evidence(ctx["matrix_run_id"], "03", ctx["test_def"]["slug"])
    return finish_hard_test(test_folder, ctx["test_def"], run_m22(ctx["project"], evidence=evidence), notes)


def run_test_04(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Close project with M05", "Run M22 without opening project"]
    notes.append(f"M05 status: {run_m05(ctx['project'], run_id=f'{ctx['matrix_run_id']}_t04_m05').get('status')}")
    evidence = build_m22_evidence(ctx["matrix_run_id"], "04", ctx["test_def"]["slug"])
    return finish_hard_test(test_folder, ctx["test_def"], run_m22(ctx["project"], evidence=evidence), notes)


def run_test_05(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Run M22 with force_default_template_not_found"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "05")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    evidence = build_m22_evidence(ctx["matrix_run_id"], "05", ctx["test_def"]["slug"])
    return finish_hard_test(
        test_folder,
        ctx["test_def"],
        run_m22(ctx["project"], evidence=evidence, force_default_template_not_found=True),
        notes,
    )


def run_test_06(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Run M22 with force_unsafe_template_action"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "06")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    evidence = build_m22_evidence(ctx["matrix_run_id"], "06", ctx["test_def"]["slug"])
    return finish_hard_test(
        test_folder,
        ctx["test_def"],
        run_m22(ctx["project"], evidence=evidence, force_unsafe_template_action=True),
        notes,
    )


HARD_TESTS: List[Dict[str, Any]] = [
    {"id": "01", "slug": "normal_default_template", "name": "Normal default template discovery", "runner": run_test_01},
    {"id": "02", "slug": "p6_behind_cursor", "name": "P6 behind Cursor focus recovery", "runner": run_test_02},
    {"id": "03", "slug": "p6_minimised", "name": "P6 minimised restore path", "runner": run_test_03},
    {"id": "04", "slug": "project_not_open", "name": "Project not open", "runner": run_test_04},
    {"id": "05", "slug": "default_template_missing", "name": "Default template missing", "runner": run_test_05},
    {"id": "06", "slug": "unsafe_template_action_prevention", "name": "Unsafe template action prevention", "runner": run_test_06},
]


def run_matrix(project: str) -> Dict[str, Any]:
    matrix_run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / matrix_run_id
    (run_root / "m22_hard_test_6").mkdir(parents=True, exist_ok=True)
    print("M22 Hard Testing — 6-test matrix")
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
                "m22_status": "CRASH",
                "score": 0,
                "status": "CRASH",
                "score_reason": traceback.format_exc(),
                "test_folder": str(test_folder),
                "setup_notes": [f"crash: {exc}"],
            }
            write_json(test_folder / "test_summary.json", result)
        results.append(result)
        print(f"  -> score={result.get('score')} status={result.get('status')} m22={result.get('m22_status')}")
    summary = write_hard_summary(matrix_run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']}/{summary['max_score']}")
    print(f"Decision: {summary['decision']}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M22 Hard Testing 6-test matrix")
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    return 0 if summary.get("decision") == "M22 STABLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
