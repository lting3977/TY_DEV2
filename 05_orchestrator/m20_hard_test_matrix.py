"""
M20 Hard Testing — 6-test matrix.

Proves M20 can select Activities export type, press Next once after Activities,
detect the post-Activities/template screen, cancel safely, and never Finish
or create export files.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
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
sys.path.insert(0, str(ROOT / "02_accessibility"))

from m20_hard_summary import write_hard_summary  # noqa: E402
from export_wizard_common import (  # noqa: E402
    m20_hard_dismiss_stale_dialogs,
    probe_export_wizard_open,
)
from m16_discover_p6_export_menu import (  # noqa: E402
    close_export_dialog,
    find_cancel_entry,
    find_export_evidence_words,
    refresh_p6_rect,
)
from m06_go_to_activities import confirms_activities_workspace  # noqa: E402
from eye.screenshot import P6Rect  # noqa: E402
from m20_select_activities_export_type_discovery_only import (  # noqa: E402
    RunEvidence,
    run_m20,
    write_json,
)
from m03_open_project_by_name import (  # noqa: E402
    build_evidence as m03_build_evidence,
    capture_p6_ocr_step as m03_capture_p6_ocr_step,
    click_entry_on_screen,
    confirm_open_with_alt_o,
    find_project_matches,
    open_project_dialog,
    run_m03,
    title_indicates_project_open,
)
from m04_check_project_opened import run_m04  # noqa: E402
from m05_close_project_safely import run_m05  # noqa: E402
from m06_go_to_activities import (  # noqa: E402
    CONFIG_PATH,
    SCREEN_RULE_PATH as WORKSPACE_SCREEN_RULE,
    capture_and_ocr_step,
    load_json,
    run_m06,
)
from eye.ocr import collect_text_blob, normalize_text  # noqa: E402
from hand.p6_prepare import get_fresh_p6_rect, prepare_p6_for_test  # noqa: E402
from accessibility.hand import keyboard_tools, window_tools  # noqa: E402

M03_CONFIG_PATH = ROOT / "01_config" / "ty_config.json"
M03_SCREEN_RULE_PATH = ROOT / "03_screen_library" / "p6_open_project" / "screen_rule.json"

PASS_DISCOVERY = frozenset(
    {"PASS_ACTIVITIES_NEXT_DISCOVERY", "PASS_ACTIVITIES_NEXT_DISCOVERY_PARTIAL"}
)
TEST_04_OK = frozenset({"FAIL_PROJECT_NOT_OPEN"})
TEST_05_OK = frozenset({"FAIL_ACTIVITIES_EXPORT_TYPE_NOT_FOUND"})
TEST_06_OK = frozenset(
    {
        "FAIL_ACTIVITIES_NEXT_SCREEN_NOT_FOUND",
        "MANUAL_REVIEW_CANNOT_CONFIRM",
        "MANUAL_REVIEW_UNSAFE_POPUP",
    }
)
TEST_06_HOOK = "force_post_activities_screen_not_found_after_second_next"
SETUP_FAILURE_STATUSES = frozenset(
    {
        "SETUP_FAILURE_P6_NOT_READY",
        "SETUP_FAILURE_EXPORT_WIZARD_NOT_OPENED",
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


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def get_p6_keyword() -> str:
    config_path = ROOT / "01_config" / "ty_config.json"
    if config_path.exists():
        return load_json(config_path).get("p6_window_title_keyword", "Primavera")
    return "Primavera"


def build_test_folder(matrix_run_id: str, test_id: str, slug: str) -> Path:
    folder = (
        ROOT / "06_output" / "runs" / matrix_run_id / "m20_hard_test_6" / f"test_{test_id}_{slug}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def build_m20_evidence(matrix_run_id: str, test_id: str, slug: str) -> RunEvidence:
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


def hard_prep_p6() -> List[str]:
    config_path = ROOT / "01_config" / "ty_config.json"
    screen_rule_path = ROOT / "03_screen_library" / "p6_project_workspace" / "screen_rule.json"
    config = load_json(config_path)
    screen_rule = load_json(screen_rule_path)
    p6_keyword = get_p6_keyword()
    min_confidence = float(config.get("min_ocr_confidence", 0.5))
    return m20_hard_dismiss_stale_dialogs(p6_keyword, config, screen_rule, min_confidence)


def ensure_clean_p6_for_m20_hard(
    project_name: str,
    matrix_run_id: str,
    *,
    require_project_open: bool = True,
    require_activities: bool = True,
) -> Dict[str, Any]:
    """Deterministic P6 precondition before each M20 hard test."""
    notes: List[str] = []
    config = load_json(CONFIG_PATH)
    screen_rule = load_json(WORKSPACE_SCREEN_RULE)
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))

    notes.extend(hard_prep_p6())
    window_tools.activate_window_by_title(p6_keyword)
    window_tools.maximize_window_by_title(p6_keyword)
    time.sleep(0.5)

    project_open = False
    window_title = window_tools.get_window_state(p6_keyword).get("title") or ""
    if title_indicates_project_open(window_title, project_name):
        project_open = True
        notes.append(f"Project open via title: {window_title}")
    else:
        m04 = run_m04(project_name, run_id=f"{matrix_run_id}_clean_m04")
        notes.append(f"M04 check: {m04.get('status')}")
        project_open = m04.get("status") == "PASS_PROJECT_OPEN" or title_indicates_project_open(
            m04.get("window_title", ""), project_name
        )

    if require_project_open and not project_open:
        m03 = run_m03(project_name, run_id=f"{matrix_run_id}_clean_m03")
        notes.append(f"M03 open: {m03.get('status')}")
        if m03.get("status") == "PASS":
            project_open = True
        elif m03.get("status") == "FAIL_OPEN_DIALOG_NOT_FOUND":
            ok, reason = hard_test_open_project_relaxed(project_name, matrix_run_id, 900)
            notes.append(f"Relaxed open: {reason}")
            project_open = ok
        if project_open:
            run_m06(project_name, run_id=f"{matrix_run_id}_clean_m06")

    prep = prepare_p6_for_test(p6_keyword)
    p6_rect: Optional[P6Rect] = prep.get("rect") if prep.get("success") else None
    activities_ok = False
    blocking_dialog = False
    export_wizard_visible = False
    open_project_visible = False

    if p6_rect is not None:
        cap = capture_and_ocr_step(
            _build_tmp_evidence(matrix_run_id, "clean_final"),
            "clean_final",
            p6_rect,
            config,
            screen_rule,
        )
        if cap.get("ok"):
            entries = cap.get("entries", [])
            blob = collect_text_blob(entries, min_confidence)
            activities_ok, _ = confirms_activities_workspace(entries, min_confidence)
            if require_activities and not activities_ok:
                notes.append("Not in Activities — running M06 navigation")
                m06 = run_m06(project_name, run_id=f"{matrix_run_id}_clean_m06_nav")
                notes.append(f"M06 nav: {m06.get('status')}")
                activities_ok = m06.get("status") in ("PASS", "PASS_ALREADY_IN_ACTIVITIES")
                p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
                cap2 = capture_and_ocr_step(
                    _build_tmp_evidence(matrix_run_id, "clean_after_m06"),
                    "clean_after_m06",
                    p6_rect,
                    config,
                    screen_rule,
                )
                if cap2.get("ok"):
                    activities_ok, _ = confirms_activities_workspace(cap2["entries"], min_confidence)
                    entries = cap2["entries"]
                    blob = collect_text_blob(entries, min_confidence)
            from export_wizard_common import (  # noqa: WPS433
                detect_m16_blocking_popup,
                export_wizard_open_in_capture,
                open_project_dialog_detected,
            )

            blocking_dialog, block_reason = detect_m16_blocking_popup(entries, min_confidence)
            export_wizard_visible = export_wizard_open_in_capture(entries, min_confidence)[0]
            open_project_visible = open_project_dialog_detected(cap, min_confidence)
            window_title = window_tools.get_window_state(p6_keyword).get("title") or ""
            if not project_open:
                project_open = title_indicates_project_open(window_title, project_name) or (
                    normalize_text(project_name) in normalize_text(blob)
                )
            if blocking_dialog:
                notes.append(f"Blocking dialog at end of clean setup: {block_reason}")

    ok = True
    reasons: List[str] = []
    if require_project_open and not project_open:
        ok = False
        reasons.append("project_not_open")
    if require_activities and not activities_ok:
        ok = False
        reasons.append("activities_workspace_not_confirmed")
    if blocking_dialog:
        ok = False
        reasons.append("blocking_dialog_visible")
    if export_wizard_visible:
        ok = False
        reasons.append("export_wizard_still_visible")
    if open_project_visible:
        ok = False
        reasons.append("open_project_dialog_visible")

    return {
        "ok": ok,
        "reason": "; ".join(reasons) if reasons else "clean",
        "notes": notes,
        "project_open": project_open,
        "activities_workspace": activities_ok,
        "window_title": window_title,
        "blocking_dialog": blocking_dialog,
        "export_wizard_visible": export_wizard_visible,
        "open_project_dialog_visible": open_project_visible,
    }


def _build_tmp_evidence(matrix_run_id: str, label: str) -> Any:
    """Minimal evidence object for setup captures."""
    from dataclasses import dataclass, field

    @dataclass
    class _Tmp:
        run_id: str
        folder: Path
        screenshots_dir: Path
        ocr_dir: Path
        classification_dir: Path
        popup_dir: Path
        discovery_dir: Path
        steps: List[str] = field(default_factory=list)
        screenshots: List[str] = field(default_factory=list)
        ocr_files: List[str] = field(default_factory=list)
        classification_files: List[str] = field(default_factory=list)
        popup_files: List[str] = field(default_factory=list)
        discovery_files: List[str] = field(default_factory=list)

    base = Path(tempfile.gettempdir()) / "m20_hard_clean" / matrix_run_id / label
    for sub in ("screenshots", "ocr", "classification", "popup", "discovery"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return _Tmp(
        run_id=f"{matrix_run_id}_{label}",
        folder=base,
        screenshots_dir=base / "screenshots",
        ocr_dir=base / "ocr",
        classification_dir=base / "classification",
        popup_dir=base / "popup",
        discovery_dir=base / "discovery",
    )


def hard_test_open_project_relaxed(project: str, matrix_run_id: str, attempt: int) -> Tuple[bool, str]:
    """Hard-test fallback when M03 strict dialog classification fails but list OCR shows project."""
    config = load_json(M03_CONFIG_PATH)
    screen_rule = load_json(M03_SCREEN_RULE_PATH)
    workspace_rule = load_json(WORKSPACE_SCREEN_RULE)
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))

    prep = prepare_p6_for_test(p6_keyword)
    if not prep.get("success") or not prep.get("rect"):
        return False, prep.get("message", "prepare_p6 failed")

    p6_rect = prep["rect"]
    evidence = m03_build_evidence(f"{matrix_run_id}_hard_open_{attempt}")
    open_project_dialog()
    time.sleep(1.2)
    fresh = get_fresh_p6_rect(p6_keyword)
    if fresh.get("success") and fresh.get("rect"):
        p6_rect = fresh["rect"]

    dialog = m03_capture_p6_ocr_step(
        evidence,
        "02_dialog",
        p6_rect,
        config,
        screen_rule,
        use_popup_crop=True,
    )
    entries = dialog.get("entries", []) if dialog.get("ok") else []
    if not entries:
        full = capture_and_ocr_step(evidence, "02_full", p6_rect, config, workspace_rule)
        if full.get("ok"):
            entries = full["entries"]
            dialog = full

    if not entries:
        keyboard_tools.press_escape()
        return False, "Open Project dialog capture failed"

    matches = find_project_matches(entries, project, min_confidence)
    if not matches:
        keyboard_tools.press_escape()
        return False, "Project row not found in Open Project OCR"

    high = [m for m in matches if m["confidence"] >= 0.75]
    selected = high[0] if len(high) == 1 else matches[0]
    crop_origin = dialog.get("crop_origin", (0, 0))
    click_entry_on_screen(selected, p6_rect, crop_origin)
    time.sleep(0.5)
    confirm_open_with_alt_o()
    time.sleep(2.5)

    title = window_tools.get_window_state(p6_keyword).get("title") or ""
    if title_indicates_project_open(title, project):
        return True, f"Relaxed open confirmed via title: {title}"
    return False, f"Relaxed open title not confirmed: {title}"


def run_clean_setup(
    project_name: str,
    matrix_run_id: str,
    *,
    require_project_open: bool = True,
    require_activities: bool = True,
) -> Dict[str, Any]:
    """Run ensure_clean_p6_for_m20_hard once; retry once on failure."""
    setup = ensure_clean_p6_for_m20_hard(
        project_name,
        matrix_run_id,
        require_project_open=require_project_open,
        require_activities=require_activities,
    )
    if setup.get("ok"):
        return setup
    time.sleep(1.0)
    setup2 = ensure_clean_p6_for_m20_hard(
        project_name,
        f"{matrix_run_id}_retry",
        require_project_open=require_project_open,
        require_activities=require_activities,
    )
    setup2["notes"] = setup.get("notes", []) + ["--- setup retry ---"] + setup2.get("notes", [])
    setup2["first_attempt_reason"] = setup.get("reason", "")
    return setup2


def to_wizard_evidence(m20_ev: RunEvidence) -> Any:
    from export_wizard_common import ExportWizardEvidence  # noqa: WPS433

    return ExportWizardEvidence(
        run_id=m20_ev.run_id,
        folder=m20_ev.folder,
        module_name="m20_hard_probe",
        screenshots_dir=m20_ev.screenshots_dir,
        ocr_dir=m20_ev.ocr_dir,
        classification_dir=m20_ev.classification_dir,
        popup_dir=m20_ev.popup_dir,
        discovery_dir=m20_ev.discovery_dir,
        steps=m20_ev.steps,
    )


def close_probe_wizard(m20_ev: RunEvidence, p6_rect: P6Rect) -> str:
    config = load_json(CONFIG_PATH)
    screen_rule = load_json(WORKSPACE_SCREEN_RULE)
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))
    cap = capture_and_ocr_step(m20_ev, "probe_close", p6_rect, config, screen_rule)
    if not cap.get("ok"):
        keyboard_tools.press_escape()
        return "esc"
    entries = cap.get("entries", [])
    blob = collect_text_blob(entries, min_confidence)
    words = find_export_evidence_words(blob)
    closed, method, _ = close_export_dialog(
        to_wizard_evidence(m20_ev),
        p6_keyword,
        p6_rect,
        config,
        screen_rule,
        entries,
        words,
    )
    if not closed:
        cancel = find_cancel_entry(entries, min_confidence)
        if cancel is not None:
            from m16_discover_p6_export_menu import click_ocr_entry  # noqa: WPS433

            click_ocr_entry(p6_rect, cancel)
            return "cancel_click"
        keyboard_tools.press_escape()
        return "esc"
    return method or "cancel_click"


def probe_export_wizard_for_hard_test(
    test_folder: Path,
    m20_ev: RunEvidence,
    attempt: int,
) -> Dict[str, Any]:
    config = load_json(CONFIG_PATH)
    screen_rule = load_json(WORKSPACE_SCREEN_RULE)
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))
    prep = prepare_p6_for_test(p6_keyword)
    if not prep.get("success") or not prep.get("rect"):
        payload = {"wizard_opened": False, "error": prep.get("message", "prepare_p6 failed")}
        write_json(test_folder / f"export_open_attempt_{attempt}.json", payload)
        return payload

    p6_rect: P6Rect = prep["rect"]
    wiz_ev = to_wizard_evidence(m20_ev)
    p6_rect, payload = probe_export_wizard_open(
        wiz_ev,
        p6_keyword,
        p6_rect,
        config,
        screen_rule,
        min_confidence,
        label=f"probe_{attempt}",
    )
    write_json(test_folder / f"export_open_attempt_{attempt}.json", payload)
    if payload.get("wizard_opened"):
        payload["close_method"] = close_probe_wizard(m20_ev, p6_rect)
        write_json(test_folder / f"export_open_attempt_{attempt}.json", payload)
        hard_prep_p6()
    return payload


def finish_setup_failure(
    test_folder: Path,
    test_def: Dict[str, Any],
    setup_kind: str,
    reason: str,
    setup_notes: List[str],
    *,
    setup_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result = {
        "test_id": test_def["id"],
        "test_name": test_def["name"],
        "slug": test_def["slug"],
        "m20_run_id": "",
        "m20_status": setup_kind,
        "m20_reason": reason,
        "score": 0,
        "status": setup_kind,
        "score_reason": reason,
        "setup_failure": True,
        "setup_payload": setup_payload or {},
        "test_folder": str(test_folder),
        "setup_notes": setup_notes,
    }
    write_json(test_folder / "test_summary.json", result)
    write_json(test_folder / "result.json", result)
    lines = [
        f"# M20 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- Status: {setup_kind}",
        f"- Reason: {reason}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    (test_folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def ensure_matrix_baseline(project: str, matrix_run_id: str) -> List[str]:
    """Require clean P6 with Talison project open before hard-test matrix."""
    setup = run_clean_setup(project, matrix_run_id)
    notes = list(setup.get("notes", []))
    if setup.get("ok"):
        notes.insert(0, "Matrix baseline: clean P6 ready")
        return notes
    raise SystemExit(
        "ABORT: Project must be open before M20 hard test. "
        f"Setup failed: {setup.get('reason')}. "
        "Open Talison 1275 in P6 manually, then re-run TY_TEST_M20_HARD_6.bat"
    )


def chain_m03_m04_m06(project: str, matrix_run_id: str, test_id: str) -> Dict[str, Any]:
    prep_notes = hard_prep_p6()
    prefix = f"{matrix_run_id}_t{test_id}"
    m03 = run_m03(project, run_id=f"{prefix}_m03")
    if m03.get("status") != "PASS":
        prep_notes.extend(hard_prep_p6())
        time.sleep(1.5)
        m03 = run_m03(project, run_id=f"{prefix}_m03_retry")
        prep_notes.append(f"M03 retry status: {m03.get('status')}")
        if m03.get("status") == "FAIL_OPEN_DIALOG_NOT_FOUND":
            ok, reason = hard_test_open_project_relaxed(project, matrix_run_id, int(test_id))
            prep_notes.append(f"M03 relaxed open: {reason}")
            if ok:
                m03 = {"status": "PASS", "reason": reason}
    m04 = run_m04(project, run_id=f"{prefix}_m04")
    m06 = run_m06(project, run_id=f"{prefix}_m06")
    return {"m03": m03, "m04": m04, "m06": m06, "prep_notes": prep_notes}


def restore_project_for_hard_chain(project: str, matrix_run_id: str) -> List[str]:
    """Re-open project after test 04 so tests 05-06 can chain M03-M06."""
    notes: List[str] = []
    for attempt in range(3):
        notes.extend(hard_prep_p6())
        time.sleep(1.0)
        suffix = "" if attempt == 0 else f"_r{attempt}"
        run_id = f"{matrix_run_id}_t04_restore{suffix}"
        m03 = run_m03(project, run_id=run_id)
        notes.append(f"Post-test-04 restore M03 attempt {attempt}: {m03.get('status')}")
        if m03.get("status") == "PASS":
            m06 = run_m06(project, run_id=f"{run_id}_m06")
            notes.append(f"Post-test-04 restore M06: {m06.get('status')}")
            return notes
        if m03.get("status") == "FAIL_OPEN_DIALOG_NOT_FOUND":
            ok, reason = hard_test_open_project_relaxed(project, matrix_run_id, 100 + attempt)
            notes.append(f"Post-test-04 relaxed open attempt {attempt}: {reason}")
            if ok:
                m06 = run_m06(project, run_id=f"{run_id}_m06")
                notes.append(f"Post-test-04 restore M06 after relaxed open: {m06.get('status')}")
                return notes
        time.sleep(2.0)
    notes.append("Post-test-04 restore: M03 did not PASS after 3 attempts")
    return notes


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
    path = test_folder / "discovery" / "post_activities_next_screen_evidence.json"
    legacy = test_folder / "discovery" / "post_activities_discovery.json"
    exists = path.exists() or legacy.exists()
    if not require:
        return exists, exists
    return exists, exists


def hook_blob(m20_result: Dict[str, Any]) -> str:
    steps = " ".join(m20_result.get("steps", [])).lower()
    reason = (m20_result.get("reason") or "").lower()
    return f"{steps} {reason}"


def score_result(
    test_id: str,
    m20_status: str,
    m20_result: Dict[str, Any],
    test_folder: Path,
    *,
    unsafe_ok: bool,
    unsafe_hits: List[str],
    crop_ok: bool,
    crop_issues: List[str],
    pollution_ok: bool,
    pollution_hits: List[str],
) -> Tuple[int, str, str]:
    export_file = bool(m20_result.get("export_file_created"))
    dialog_closed = bool(m20_result.get("export_dialog_closed"))
    wizard_detected = bool(m20_result.get("export_wizard_detected"))
    next_count = int(m20_result.get("next_pressed_count", 0))
    finish_pressed = bool(m20_result.get("finish_pressed"))
    activities_selected = bool(m20_result.get("activities_export_type_selected"))
    post_screen = bool(m20_result.get("post_activities_screen_detected"))
    post_words = m20_result.get("post_activities_evidence_words") or []
    screen_type = m20_result.get("post_activities_screen_type", "")
    steps_blob = hook_blob(m20_result)

    if not unsafe_ok:
        return 0, "UNSAFE_ACTION", "; ".join(unsafe_hits[:3])
    if not crop_ok:
        return 0, "FULL_SCREEN_OCR", f"Possible full-screen capture: {crop_issues[:2]}"
    if export_file:
        return 0, "EXPORT_FILE_CREATED", "Export file created during test"
    if finish_pressed:
        return 0, "FINISH_PRESSED", "Finish pressed during discovery"
    if next_count > 2:
        return 0, "NEXT_PRESSED_TOO_MANY", f"Next pressed {next_count} times (max 2)"

    if test_id == "04":
        if m20_status not in TEST_04_OK:
            return 0, "FALSE_PASS", f"Test 04 expected FAIL_PROJECT_NOT_OPEN, got {m20_status}"
        if wizard_detected or "open export path" in steps_blob:
            return 0, "FALSE_PASS", "Export wizard opened while project not open"
        if next_count > 0:
            return 0, "FALSE_PASS", "Next pressed while project not open"
        return 1, m20_status, "Project not open; export wizard not opened; Next not pressed"

    if test_id == "05":
        if m20_status not in TEST_05_OK:
            return 0, "FALSE_PASS", f"Test 05 expected FAIL_ACTIVITIES_EXPORT_TYPE_NOT_FOUND, got {m20_status}"
        if "force_activities_export_type_not_found" not in steps_blob:
            return 0, "FALSE_PASS", "Test 05 missing force_activities_export_type_not_found hook"
        if activities_selected:
            return 0, "FALSE_PASS", "Test 05 expected Activities not selected after hook"
        return 1, m20_status, "Activities export type blocked; no unsafe export action"

    if test_id == "06":
        forced = m20_result.get("forced_hook_activation") or {}
        hook_reached = bool(forced.get("hook_applied_after_second_next"))
        hook_in_steps = TEST_06_HOOK in steps_blob or "force_post_activities_screen_not_found_after_second_next" in steps_blob

        if m20_status in SETUP_FAILURE_STATUSES:
            return 0, m20_status, "Test 06 setup failed before forced post-Activities block"
        if m20_status == "FAIL_EXPORT_WIZARD_NOT_FOUND":
            return (
                0,
                "SETUP_FAILURE_EXPORT_WIZARD_NOT_OPENED",
                "Export wizard never opened; not a valid test 06 module failure",
            )
        if not hook_reached and not hook_in_steps:
            return 0, "FALSE_PASS", f"Test 06 missing {TEST_06_HOOK} hook after second Next"
        if m20_status not in TEST_06_OK:
            return 0, "FALSE_PASS", f"Test 06 expected controlled fail, got {m20_status}"
        if m20_status in PASS_DISCOVERY:
            return 0, "FALSE_PASS", "Test 06 should not PASS with forced post-Activities block"
        if m20_status == "MANUAL_REVIEW_UNSAFE_POPUP" and not unsafe_ok:
            return 0, "UNSAFE_ACTION", "; ".join(unsafe_hits[:3])
        return 1, m20_status, "Controlled post-Activities failure; Finish not pressed"

    if test_id == "03":
        if m20_status == "FAIL_P6_WINDOW_NOT_READY":
            if export_file or wizard_detected or next_count > 0:
                return 0, "FALSE_PASS", "Export attempted when P6 window not ready"
            return 1, m20_status, "P6 could not safely restore; no export attempted"
        if m20_status not in PASS_DISCOVERY:
            return 0, "FALSE_PASS", (
                f"Test 03 expected discovery pass or FAIL_P6_WINDOW_NOT_READY, got {m20_status}"
            )

    if test_id == "02" and not pollution_ok:
        return 0, "FALSE_PASS", f"OCR pollution detected: {pollution_hits[:3]}"

    if m20_status not in PASS_DISCOVERY:
        return 0, "FALSE_PASS", f"Expected PASS_ACTIVITIES_NEXT_DISCOVERY or PARTIAL, got {m20_status}"

    if not activities_selected:
        return 0, "FALSE_PASS", "Discovery pass without Activities export type selected"

    if next_count != 2:
        return 0, "FALSE_PASS", f"Discovery pass requires Next pressed exactly twice; got {next_count}"

    if not post_screen:
        return 0, "FALSE_PASS", "Discovery pass without post-Activities screen detected"

    if m20_status == "PASS_ACTIVITIES_NEXT_DISCOVERY" and len(post_words) < 3:
        if screen_type not in ("projects_to_export", "template", "file_path"):
            return 0, "FALSE_PASS", f"Full PASS requires >=3 post-Activities evidence words; got {len(post_words)}"

    if wizard_detected and not dialog_closed:
        return 0, "DIALOG_LEFT_OPEN", "Export wizard detected but not closed"

    disc_ok, _ = discovery_files_ok(test_folder, m20_status in PASS_DISCOVERY)
    if m20_status in PASS_DISCOVERY and not disc_ok:
        return 0, "FALSE_PASS", "post_activities_next_screen_evidence.json missing for successful test"

    after_state = (m20_result.get("screen_state_after") or "").lower()
    title_after = (m20_result.get("window_title_after") or "").lower()
    if wizard_detected and not (
        after_state.startswith("activities")
        or "primavera" in title_after
        or "talison" in title_after
    ):
        return 0, "FALSE_PASS", f"P6 did not return to project window after close: {after_state}"

    return (
        1,
        m20_status,
        f"Activities Next discovery OK; next_count={next_count}; post_words={len(post_words)}; closed={dialog_closed}",
    )


def finish_hard_test(
    test_folder: Path,
    test_def: Dict[str, Any],
    m20_result: Dict[str, Any],
    setup_notes: List[str],
) -> Dict[str, Any]:
    m20_status = m20_result.get("status", "ERROR")
    unsafe_ok, unsafe_hits = check_unsafe_steps(m20_result.get("steps", []))
    crop_ok, crop_issues = check_no_fullscreen_ocr(test_folder)
    pollution_ok, pollution_hits = check_ocr_pollution(test_folder)
    require_disc = m20_status in PASS_DISCOVERY
    disc_ok, _ = discovery_files_ok(test_folder, require_disc)

    score, status, score_reason = score_result(
        test_def["id"],
        m20_status,
        m20_result,
        test_folder,
        unsafe_ok=unsafe_ok,
        unsafe_hits=unsafe_hits,
        crop_ok=crop_ok,
        crop_issues=crop_issues,
        pollution_ok=pollution_ok,
        pollution_hits=pollution_hits,
    )

    result = {
        "test_id": test_def["id"],
        "test_name": test_def["name"],
        "slug": test_def["slug"],
        "m20_run_id": m20_result.get("run_id", ""),
        "m20_status": m20_status,
        "m20_reason": m20_result.get("reason", ""),
        "score": score,
        "status": status,
        "score_reason": score_reason,
        "window_title_before": m20_result.get("window_title_before", ""),
        "window_title_after": m20_result.get("window_title_after", ""),
        "screen_state_before": m20_result.get("screen_state_before", ""),
        "screen_state_after": m20_result.get("screen_state_after", ""),
        "export_wizard_detected": m20_result.get("export_wizard_detected"),
        "activities_export_type_selected": m20_result.get("activities_export_type_selected"),
        "next_pressed_count": m20_result.get("next_pressed_count", 0),
        "post_activities_screen_detected": m20_result.get("post_activities_screen_detected"),
        "post_activities_evidence_words": m20_result.get("post_activities_evidence_words", []),
        "finish_pressed": m20_result.get("finish_pressed"),
        "export_dialog_closed": m20_result.get("export_dialog_closed"),
        "close_method_used": m20_result.get("close_method_used", ""),
        "export_file_created": m20_result.get("export_file_created"),
        "discovery_files_ok": disc_ok,
        "unsafe_steps_ok": unsafe_ok,
        "fullscreen_ocr_ok": crop_ok,
        "ocr_pollution_ok": pollution_ok,
        "test_folder": str(test_folder),
        "setup_notes": setup_notes,
    }

    write_json(test_folder / "test_summary.json", result)
    write_json(test_folder / "result.json", result)
    lines = [
        f"# M20 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- M20 run ID: {m20_result.get('run_id', '')}",
        f"- M20 status: {m20_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Activities selected: {result['activities_export_type_selected']}",
        f"- Next pressed count: {result['next_pressed_count']}",
        f"- Post-Activities screen: {result['post_activities_screen_detected']}",
        f"- Post evidence words: {result['post_activities_evidence_words']}",
        f"- Finish pressed: {result['finish_pressed']}",
        f"- Export dialog closed: {result['export_dialog_closed']}",
        f"- Export file created: {result['export_file_created']}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## M20 reason", m20_result.get("reason", "")])
    (test_folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def _apply_clean_setup(
    ctx: Dict[str, Any],
    test_folder: Path,
    *,
    require_project_open: bool = True,
    require_activities: bool = True,
) -> Tuple[List[str], Optional[Dict[str, Any]]]:
    """Run clean setup; return notes and setup-failure result if setup did not succeed."""
    test_def = ctx["test_def"]
    setup = run_clean_setup(
        ctx["project"],
        f"{ctx['matrix_run_id']}_t{test_def['id']}",
        require_project_open=require_project_open,
        require_activities=require_activities,
    )
    write_json(test_folder / "setup_precheck.json", setup)
    notes = list(setup.get("notes", []))
    notes.append(f"Clean setup ok={setup.get('ok')} reason={setup.get('reason')}")
    if setup.get("ok"):
        return notes, None
    reason = setup.get("reason", "clean setup failed")
    return notes, finish_setup_failure(
        test_folder,
        test_def,
        "SETUP_FAILURE_P6_NOT_READY",
        f"P6 precondition failed after retry: {reason}",
        notes,
        setup_payload=setup,
    )


def run_test_01(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes, setup_fail = _apply_clean_setup(ctx, test_folder)
    if setup_fail:
        return setup_fail
    notes.append("Run M20 normal Activities Next discovery")
    evidence = build_m20_evidence(ctx["matrix_run_id"], "01", ctx["test_def"]["slug"])
    m20_result = run_m20(ctx["project"], evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m20_result, notes)


def run_test_02(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes, setup_fail = _apply_clean_setup(ctx, test_folder)
    if setup_fail:
        return setup_fail
    notes.append("Bring Cursor in front before M20")
    cursor = bring_cursor_to_front()
    notes.append(f"Cursor focus: {cursor}")
    evidence = build_m20_evidence(ctx["matrix_run_id"], "02", ctx["test_def"]["slug"])
    m20_result = run_m20(ctx["project"], evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m20_result, notes)


def run_test_03(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes, setup_fail = _apply_clean_setup(ctx, test_folder)
    if setup_fail:
        return setup_fail
    notes.append("Minimise P6 before M20")
    mini = minimize_p6()
    notes.append(f"Minimise P6: {mini}")
    time.sleep(0.5)
    evidence = build_m20_evidence(ctx["matrix_run_id"], "03", ctx["test_def"]["slug"])
    m20_result = run_m20(ctx["project"], evidence=evidence)
    return finish_hard_test(test_folder, ctx["test_def"], m20_result, notes)


def run_test_04(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes, setup_fail = _apply_clean_setup(ctx, test_folder, require_project_open=True)
    if setup_fail:
        return setup_fail
    notes.append("Close project with M05; run M20 without opening project")
    close = run_m05(ctx["project"], run_id=f"{ctx['matrix_run_id']}_t04_m05")
    notes.append(f"M05 status: {close.get('status')}")
    if close.get("status") != "PASS_CLOSED":
        notes.extend(hard_prep_p6())
    evidence = build_m20_evidence(ctx["matrix_run_id"], "04", ctx["test_def"]["slug"])
    m20_result = run_m20(ctx["project"], evidence=evidence)
    result = finish_hard_test(test_folder, ctx["test_def"], m20_result, notes)
    restore = run_clean_setup(ctx["project"], f"{ctx['matrix_run_id']}_t04_restore")
    restore_notes = list(restore.get("notes", []))
    restore_notes.append(f"Post-test-04 restore ok={restore.get('ok')}")
    result["setup_notes"] = notes + restore_notes
    write_json(test_folder / "test_summary.json", result)
    write_json(test_folder / "result.json", result)
    return result


def run_test_05(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes, setup_fail = _apply_clean_setup(ctx, test_folder)
    if setup_fail:
        return setup_fail
    notes.append("Run M20 with force_activities_export_type_not_found")
    evidence = build_m20_evidence(ctx["matrix_run_id"], "05", ctx["test_def"]["slug"])
    m20_result = run_m20(
        ctx["project"],
        evidence=evidence,
        force_activities_export_type_not_found=True,
    )
    return finish_hard_test(test_folder, ctx["test_def"], m20_result, notes)


def run_test_06(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes, setup_fail = _apply_clean_setup(ctx, test_folder)
    if setup_fail:
        return setup_fail

    notes.append("Test 06 pre-check: probe export wizard open before forced hook")
    probe_ev = build_m20_evidence(ctx["matrix_run_id"], "06", ctx["test_def"]["slug"])
    probe1 = probe_export_wizard_for_hard_test(test_folder, probe_ev, 1)
    notes.append(f"Export probe attempt 1 wizard_opened={probe1.get('wizard_opened')}")

    if not probe1.get("wizard_opened"):
        notes.extend(hard_prep_p6())
        window_tools.activate_window_by_title(get_p6_keyword())
        time.sleep(0.8)
        probe2 = probe_export_wizard_for_hard_test(test_folder, probe_ev, 2)
        notes.append(f"Export probe attempt 2 wizard_opened={probe2.get('wizard_opened')}")
        if not probe2.get("wizard_opened"):
            return finish_setup_failure(
                test_folder,
                ctx["test_def"],
                "SETUP_FAILURE_EXPORT_WIZARD_NOT_OPENED",
                "Export wizard did not open after two probe attempts; test setup failed before forced hook",
                notes,
                setup_payload={"probe_1": probe1, "probe_2": probe2},
            )

    notes.append(f"Run M20 with {TEST_06_HOOK}")
    evidence = build_m20_evidence(ctx["matrix_run_id"], "06", ctx["test_def"]["slug"])
    m20_result = run_m20(
        ctx["project"],
        evidence=evidence,
        force_post_activities_screen_not_found_after_second_next=True,
    )

    forced_path = evidence.discovery_dir / "forced_hook_activation.json"
    forced = m20_result.get("forced_hook_activation")
    if forced:
        write_json(test_folder / "forced_hook_activation.json", forced)
    elif forced_path.exists():
        write_json(test_folder / "forced_hook_activation.json", load_json(forced_path))

    open_attempt = evidence.discovery_dir / "export_open_attempt.json"
    if open_attempt.exists() and not (test_folder / "export_open_attempt_1.json").exists():
        write_json(test_folder / "export_open_attempt_1.json", load_json(open_attempt))

    m20_module_result = evidence.folder / "result.json"
    if m20_module_result.exists():
        write_json(test_folder / "m20_module_result.json", load_json(m20_module_result))

    result = finish_hard_test(test_folder, ctx["test_def"], m20_result, notes)
    result["forced_hook_activation"] = forced or (
        load_json(test_folder / "forced_hook_activation.json")
        if (test_folder / "forced_hook_activation.json").exists()
        else {}
    )
    result["export_probe_1"] = probe1
    write_json(test_folder / "test_summary.json", result)
    write_json(test_folder / "result.json", result)
    return result


HARD_TESTS: List[Dict[str, Any]] = [
    {
        "id": "01",
        "slug": "normal_activities_next_discovery",
        "name": "Normal Activities Next discovery",
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
        "slug": "activities_export_type_blocked",
        "name": "Activities export type missing / blocked",
        "runner": run_test_05,
    },
    {
        "id": "06",
        "slug": "post_activities_screen_blocked",
        "name": "Post-Activities screen blocked / unclear",
        "runner": run_test_06,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    matrix_run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / matrix_run_id
    (run_root / "m20_hard_test_6").mkdir(parents=True, exist_ok=True)

    print("M20 Hard Testing — 6-test matrix")
    print(f"Run ID: {matrix_run_id}")
    print(f"Project: {project}")
    print("=" * 60)

    baseline_notes = ensure_matrix_baseline(project, matrix_run_id)
    for note in baseline_notes:
        print(f"Baseline: {note}")

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
                "m20_run_id": "",
                "m20_status": "CRASH",
                "m20_reason": str(exc),
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
            f"m20={result.get('m20_status')}"
        )
        if result.get("setup_failure") or result.get("status") in SETUP_FAILURE_STATUSES:
            print(f"  -> STOPPED_FOR_REVIEW: setup failed on test {test_def['id']}")
            summary = write_hard_summary(matrix_run_id, run_root, results, project)
            summary["decision"] = "STOPPED_FOR_REVIEW"
            summary["next_recommendation"] = "FIX M20 HARD TEST SETUP"
            summary["stopped_at_test"] = test_def["id"]
            summary["stop_reason"] = result.get("score_reason") or result.get("m20_reason", "")
            write_json(run_root / "m20_hard_test_6_summary.json", summary)
            print("=" * 60)
            print(f"Final score: {summary['final_score']}/{summary['max_score']} (partial run)")
            print(f"Decision: {summary['decision']}")
            print(f"Summary: {run_root / 'm20_hard_test_6_summary.json'}")
            return summary

    summary = write_hard_summary(matrix_run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']}/{summary['max_score']}")
    print(f"Decision: {summary['decision']}")
    print(f"Summary: {run_root / 'm20_hard_test_6_summary.json'}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M20 Hard Testing 6-test matrix")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    if summary.get("decision") == "M20 STABLE":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
