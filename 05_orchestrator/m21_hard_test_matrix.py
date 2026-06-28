"""
M21 Hard Testing — 6-test matrix.

Proves M21 can reach the template screen after Activities selection, OCR-read
template options, cancel safely, and never modify templates or create files.
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

from m21_hard_summary import write_hard_summary  # noqa: E402
from m06_go_to_activities import load_json  # noqa: E402
from m21_discover_activity_export_template_screen import RunEvidence, run_m21, write_json  # noqa: E402
from m03_open_project_by_name import run_m03  # noqa: E402
from m04_check_project_opened import run_m04  # noqa: E402
from m05_close_project_safely import run_m05  # noqa: E402
from m06_go_to_activities import run_m06  # noqa: E402

PASS_DISCOVERY = frozenset(
    {"PASS_TEMPLATE_SCREEN_DISCOVERY", "PASS_TEMPLATE_SCREEN_DISCOVERY_PARTIAL"}
)
TEST_04_OK = frozenset({"FAIL_PROJECT_NOT_OPEN"})
TEST_05_OK = frozenset({"FAIL_TEMPLATE_SCREEN_NOT_FOUND"})
TEST_06_OK = frozenset(
    {
        "FAIL_ACTIVITIES_EXPORT_TYPE_NOT_FOUND",
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
        ROOT / "06_output" / "runs" / matrix_run_id / "m21_hard_test_6" / f"test_{test_id}_{slug}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def build_m21_evidence(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
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
    path = test_folder / "discovery" / "template_options.json"
    if not require:
        return path.exists(), path.exists()
    return path.exists(), path.exists()


def hook_blob(m21_result: Dict[str, Any]) -> str:
    steps = " ".join(m21_result.get("steps", [])).lower()
    reason = (m21_result.get("reason") or "").lower()
    return f"{steps} {reason}"


def score_result(
    test_id: str,
    m21_status: str,
    m21_result: Dict[str, Any],
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
    export_file = bool(m21_result.get("export_file_created"))
    dialog_closed = bool(m21_result.get("export_dialog_closed"))
    wizard_detected = bool(m21_result.get("export_wizard_detected"))
    template_screen = bool(m21_result.get("template_screen_detected"))
    template_options = m21_result.get("template_options_detected") or []
    next_count = int(m21_result.get("next_pressed_count", 0))
    finish_pressed = bool(m21_result.get("finish_pressed"))
    steps_blob = hook_blob(m21_result)

    if not unsafe_ok:
        return 0, "UNSAFE_ACTION", "; ".join(unsafe_hits[:3])
    if not template_action_ok:
        return 0, "UNSAFE_TEMPLATE_ACTION", "; ".join(template_action_hits[:3])
    if not crop_ok:
        return 0, "FULL_SCREEN_OCR", f"Possible full-screen capture: {crop_issues[:2]}"
    if export_file:
        return 0, "EXPORT_FILE_CREATED", "Export file created during test"
    if finish_pressed:
        return 0, "FINISH_PRESSED", "Finish pressed during template discovery"
    if next_count > 2:
        return 0, "NEXT_PRESSED_TOO_MANY", f"Next pressed {next_count} times (max 2 before template screen)"

    if test_id == "04":
        if m21_status not in TEST_04_OK:
            return 0, "FALSE_PASS", f"Test 04 expected FAIL_PROJECT_NOT_OPEN, got {m21_status}"
        if wizard_detected or "open export path" in steps_blob:
            return 0, "FALSE_PASS", "Export wizard opened while project not open"
        return 1, m21_status, "Project not open; export wizard not opened"

    if test_id == "05":
        if m21_status not in TEST_05_OK:
            return 0, "FALSE_PASS", f"Test 05 expected FAIL_TEMPLATE_SCREEN_NOT_FOUND, got {m21_status}"
        if "force_template_screen_not_found" not in steps_blob:
            return 0, "FALSE_PASS", "Test 05 missing force_template_screen_not_found hook"
        if template_screen:
            return 0, "FALSE_PASS", "Test 05 expected no template screen after force hook"
        return 1, m21_status, "Template screen blocked; no unsafe export action"

    if test_id == "06":
        if m21_status not in TEST_06_OK:
            return 0, "FALSE_PASS", f"Test 06 expected controlled fail, got {m21_status}"
        if "force_activities_export_type_not_found" not in steps_blob:
            return 0, "FALSE_PASS", "Test 06 missing force_activities_export_type_not_found hook"
        if m21_status in PASS_DISCOVERY:
            return 0, "FALSE_PASS", "Test 06 should not PASS with forced template screen block"
        return 1, m21_status, "Controlled template screen failure; Finish not pressed"

    if test_id == "03":
        if m21_status == "FAIL_P6_WINDOW_NOT_READY":
            if export_file or wizard_detected or next_count > 0:
                return 0, "FALSE_PASS", "Export attempted when P6 window not ready"
            return 1, m21_status, "P6 could not safely restore; no export attempted"
        if m21_status not in PASS_DISCOVERY:
            return 0, "FALSE_PASS", (
                f"Test 03 expected discovery pass or FAIL_P6_WINDOW_NOT_READY, got {m21_status}"
            )

    if test_id == "02" and not pollution_ok:
        return 0, "FALSE_PASS", f"OCR pollution detected: {pollution_hits[:3]}"

    if m21_status not in PASS_DISCOVERY:
        return 0, "FALSE_PASS", f"Expected PASS_TEMPLATE_DISCOVERY or PARTIAL, got {m21_status}"

    if not template_screen:
        return 0, "FALSE_PASS", "Discovery pass without template screen detected"

    template_words = m21_result.get("template_evidence_words") or []
    if m21_status == "PASS_TEMPLATE_SCREEN_DISCOVERY" and len(template_words) < 2:
        return 0, "FALSE_PASS", "Full PASS requires >=2 template evidence words"

    if wizard_detected and not dialog_closed:
        return 0, "DIALOG_LEFT_OPEN", "Export wizard detected but not closed"

    disc_ok, _ = discovery_files_ok(test_folder, m21_status in PASS_DISCOVERY)
    if m21_status in PASS_DISCOVERY and not disc_ok:
        return 0, "FALSE_PASS", "template_screen_discovery.json missing for successful test"

    after_state = (m21_result.get("screen_state_after") or "").lower()
    title_after = (m21_result.get("window_title_after") or "").lower()
    if wizard_detected and not (
        after_state.startswith("activities")
        or "primavera" in title_after
        or "talison" in title_after
    ):
        return 0, "FALSE_PASS", f"P6 did not return to project window after close: {after_state}"

    return (
        1,
        m21_status,
        f"Template discovery OK; evidence_words={len(template_words)}; closed={dialog_closed}",
    )


def finish_hard_test(
    test_folder: Path,
    test_def: Dict[str, Any],
    m21_result: Dict[str, Any],
    setup_notes: List[str],
) -> Dict[str, Any]:
    m21_status = m21_result.get("status", "ERROR")
    unsafe_ok, unsafe_hits = check_unsafe_steps(m21_result.get("steps", []))
    template_action_ok, template_action_hits = check_no_template_actions(m21_result.get("steps", []))
    crop_ok, crop_issues = check_no_fullscreen_ocr(test_folder)
    pollution_ok, pollution_hits = check_ocr_pollution(test_folder)
    require_disc = m21_status in PASS_DISCOVERY
    disc_ok, _ = discovery_files_ok(test_folder, require_disc)

    score, status, score_reason = score_result(
        test_def["id"],
        m21_status,
        m21_result,
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
        "m21_run_id": m21_result.get("run_id", ""),
        "m21_status": m21_status,
        "m21_reason": m21_result.get("reason", ""),
        "score": score,
        "status": status,
        "score_reason": score_reason,
        "window_title_before": m21_result.get("window_title_before", ""),
        "window_title_after": m21_result.get("window_title_after", ""),
        "screen_state_before": m21_result.get("screen_state_before", ""),
        "screen_state_after": m21_result.get("screen_state_after", ""),
        "export_wizard_detected": m21_result.get("export_wizard_detected"),
        "template_screen_detected": m21_result.get("template_screen_detected"),
        "template_options_detected": m21_result.get("template_options_detected", []),
        "template_evidence_words": m21_result.get("template_evidence_words", []),
        "next_pressed_count": m21_result.get("next_pressed_count", 0),
        "finish_pressed": m21_result.get("finish_pressed"),
        "export_dialog_closed": m21_result.get("export_dialog_closed"),
        "close_method_used": m21_result.get("close_method_used", ""),
        "export_file_created": m21_result.get("export_file_created"),
        "discovery_files_ok": disc_ok,
        "unsafe_steps_ok": unsafe_ok,
        "template_action_ok": template_action_ok,
        "fullscreen_ocr_ok": crop_ok,
        "ocr_pollution_ok": pollution_ok,
        "test_folder": str(test_folder),
        "setup_notes": setup_notes,
    }

    write_json(test_folder / "test_summary.json", result)
    lines = [
        f"# M21 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- M21 run ID: {m21_result.get('run_id', '')}",
        f"- M21 status: {m21_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Template screen detected: {result['template_screen_detected']}",
        f"- Template options detected: {result['template_options_detected']}",
        f"- Next pressed count: {result['next_pressed_count']}",
        f"- Finish pressed: {result['finish_pressed']}",
        f"- Export dialog closed: {result['export_dialog_closed']}",
        f"- Export file created: {result['export_file_created']}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## M21 reason", m21_result.get("reason", "")])
    (test_folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def run_test_01(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Run M21 normal template discovery"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "01")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    evidence = build_m21_evidence(ctx["matrix_run_id"], "01", ctx["test_def"]["slug"])
    m21_result = run_m21(ctx["project"], evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m21_result, notes)


def run_test_02(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Bring Cursor in front before M21"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "02")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    cursor = bring_cursor_to_front()
    notes.append(f"Cursor focus: {cursor}")
    evidence = build_m21_evidence(ctx["matrix_run_id"], "02", ctx["test_def"]["slug"])
    m21_result = run_m21(ctx["project"], evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m21_result, notes)


def run_test_03(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Chain M03 -> M04 -> M06", "Minimise P6 before M21"]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "03")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    mini = minimize_p6()
    notes.append(f"Minimise P6: {mini}")
    time.sleep(0.5)
    evidence = build_m21_evidence(ctx["matrix_run_id"], "03", ctx["test_def"]["slug"])
    m21_result = run_m21(ctx["project"], evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m21_result, notes)


def run_test_04(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Close project with M05", "Run M21 without opening project"]
    close = run_m05(ctx["project"], run_id=f"{ctx['matrix_run_id']}_t04_m05")
    notes.append(f"M05 status: {close.get('status')}")
    evidence = build_m21_evidence(ctx["matrix_run_id"], "04", ctx["test_def"]["slug"])
    m21_result = run_m21(ctx["project"], evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m21_result, notes)


def run_test_05(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = [
        "Chain M03 -> M04 -> M06",
        "Run M21 with force_template_screen_not_found",
    ]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "05")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    evidence = build_m21_evidence(ctx["matrix_run_id"], "05", ctx["test_def"]["slug"])
    m21_result = run_m21(
        ctx["project"],
        evidence=evidence,
        force_template_screen_not_found=True,
    )
    return finish_hard_test(test_folder, ctx["test_def"], m21_result, notes)


def run_test_06(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = [
        "Chain M03 -> M04 -> M06",
        "Run M21 with force_activities_export_type_not_found",
    ]
    chain = chain_m03_m04_m06(ctx["project"], ctx["matrix_run_id"], "06")
    notes.append(f"M06 chain status: {chain['m06'].get('status')}")
    evidence = build_m21_evidence(ctx["matrix_run_id"], "06", ctx["test_def"]["slug"])
    m21_result = run_m21(
        ctx["project"],
        evidence=evidence,
        force_activities_export_type_not_found=True,
    )
    return finish_hard_test(test_folder, ctx["test_def"], m21_result, notes)


HARD_TESTS: List[Dict[str, Any]] = [
    {
        "id": "01",
        "slug": "normal_template_discovery",
        "name": "Normal template discovery",
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
        "slug": "template_options_blocked",
        "name": "Template options blocked",
        "runner": run_test_05,
    },
    {
        "id": "06",
        "slug": "template_screen_unclear",
        "name": "Template screen blocked / unclear condition",
        "runner": run_test_06,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    matrix_run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / matrix_run_id
    (run_root / "m21_hard_test_6").mkdir(parents=True, exist_ok=True)

    print("M21 Hard Testing — 6-test matrix")
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
                "m21_run_id": "",
                "m21_status": "CRASH",
                "m21_reason": str(exc),
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
            f"m21={result.get('m21_status')}"
        )

    summary = write_hard_summary(matrix_run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']}/{summary['max_score']}")
    print(f"Decision: {summary['decision']}")
    print(f"Summary: {run_root / 'm21_hard_test_6_summary.json'}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M21 Hard Testing 6-test matrix")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    if summary.get("decision") == "M21 STABLE":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
