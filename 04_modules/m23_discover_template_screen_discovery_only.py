"""
M23 — Discover Template Screen Discovery Only.

Spreadsheet -> Export Type -> Activities -> Projects-to-export ->
select 001 Talison 1275 -> Next -> Template screen -> read-only evidence -> cancel safely.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "04_modules"))

from m06_go_to_activities import (  # noqa: E402
    CONFIG_PATH,
    SCREEN_RULE_PATH,
    capture_and_ocr_step,
    load_json,
    write_json,
)
from eye.ocr import is_easyocr_available  # noqa: E402
from eye.screenshot import P6Rect  # noqa: E402
from accessibility.hand import window_tools  # noqa: E402
from hand.p6_prepare import prepare_p6_for_test  # noqa: E402
from m16_discover_p6_export_menu import (  # noqa: E402
    close_export_dialog,
    detect_m16_blocking_popup,
    export_file_created,
    find_export_evidence_words,
    refresh_p6_rect,
    snapshot_export_files,
)
from m18_select_spreadsheet_export_format_discovery_only import (  # noqa: E402
    detect_wizard_buttons,
    finish_pressed_in_steps,
)
from export_wizard_common import (  # noqa: E402
    M22FailSafeError,
    M22UnsafeClickError,
    M23_MAX_RUN_SEC,
    count_next_presses,
    ensure_clean_p6_for_m21_hard,
    m21_dirty_start_preflight_once,
    m21_dismiss_projects_validation_popup,
    m21_install_rect_clip_capture_patch,
    m21_preflight_with_restore_loop,
    m21_remove_rect_clip_capture_patch,
    m22_install_pyautogui_guard,
    m22_move_mouse_p6_neutral,
    m22_remove_pyautogui_guard,
    m23_controlled_wizard_to_template_screen,
    safe_cancel_export_wizard_if_open,
    unsafe_steps_detected,
)

MODULE_NAME = "m23_discover_template_screen_discovery_only"

PASS_STATUSES = frozenset(
    {
        "PASS_TEMPLATE_SCREEN_DISCOVERY",
        "PASS_TEMPLATE_SCREEN_DISCOVERY_PARTIAL",
    }
)


@dataclass
class RunEvidence:
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


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_evidence(run_id: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / run_id / MODULE_NAME
    for sub in ("screenshots", "ocr", "classification", "popup", "discovery"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    return RunEvidence(
        run_id=run_id,
        folder=folder,
        screenshots_dir=folder / "screenshots",
        ocr_dir=folder / "ocr",
        classification_dir=folder / "classification",
        popup_dir=folder / "popup",
        discovery_dir=folder / "discovery",
    )


def save_discovery(evidence: RunEvidence, filename: str, payload: Dict[str, Any]) -> str:
    path = evidence.discovery_dir / filename
    write_json(path, payload)
    evidence.discovery_files.append(str(path))
    return str(path)


def browse_or_path_in_steps(steps: List[str]) -> Tuple[bool, bool]:
    browse = any("browse" in s.lower() for s in steps)
    path_typed = any(
        marker in s.lower()
        for s in steps
        for marker in ("type path", "enter path", "file name", "output file", "ctrl+v")
    )
    return browse, path_typed


def template_action_in_steps(steps: List[str]) -> Tuple[bool, bool, bool, bool]:
    add_clicked = False
    delete_clicked = False
    template_modified = False
    template_selected = False
    for step in steps:
        lowered = step.lower()
        if "click" in lowered and " add" in lowered and "add template" not in lowered.replace("click add", ""):
            if "add" in lowered.split("click")[-1][:30]:
                add_clicked = True
        if "add template" in lowered or "ocr-click add" in lowered:
            add_clicked = True
        if "delete template" in lowered or ("delete" in lowered and "click" in lowered):
            delete_clicked = True
        if any(m in lowered for m in ("modify template", "delete template", "add template")):
            template_modified = True
        if "select template:" in lowered or "template row" in lowered:
            template_selected = True
    return add_clicked, delete_clicked, template_modified, template_selected


def decide_status(
    *,
    wizard_detected: bool,
    spreadsheet_detected: bool,
    export_type_screen_ok: bool,
    activities_selected: bool,
    projects_to_export_ok: bool,
    project_001_talison: bool,
    project_row_selected: bool,
    project_selection_attempted: bool,
    next_pressed_count: int,
    template_screen_detected: bool,
    template_partial: bool,
    validation_after_selection: bool,
    dialog_closed: bool,
    file_created: bool,
    finish_pressed: bool,
    blocking_after: bool,
    browse_clicked: bool,
    path_typed: bool,
    template_modified: bool,
    template_selected: bool,
    add_clicked: bool,
    delete_clicked: bool,
    cleanup_success: bool,
) -> Tuple[str, str]:
    if file_created or finish_pressed:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Export file may have been created or Finish pressed"
    if browse_clicked or path_typed or template_modified or template_selected or add_clicked or delete_clicked:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Unsafe template wizard action detected"
    if blocking_after:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Blocking popup after close attempt"
    if next_pressed_count > 3:
        return "MANUAL_REVIEW_UNSAFE_POPUP", f"Next pressed {next_pressed_count} times (max 3 allowed)"
    if not wizard_detected:
        return "FAIL_EXPORT_WIZARD_NOT_FOUND", "Export wizard not opened"
    if not spreadsheet_detected:
        return "FAIL_SPREADSHEET_OPTION_NOT_FOUND", "Spreadsheet option not found"
    if not export_type_screen_ok:
        return "FAIL_EXPORT_TYPE_SCREEN_NOT_FOUND", "Export Type screen not reached"
    if not activities_selected:
        return "FAIL_ACTIVITIES_EXPORT_TYPE_NOT_FOUND", "Activities export type not confirmed"
    if not projects_to_export_ok:
        return "FAIL_PROJECTS_TO_EXPORT_SCREEN_NOT_FOUND", "Projects-to-export screen not confirmed"
    if not project_001_talison:
        return "FAIL_PROJECT_ROW_NOT_FOUND", "001/Talison 1275 not visible on Projects-to-export screen"
    if not project_selection_attempted:
        return "FAIL_PROJECT_ROW_NOT_FOUND", "Project row selection not attempted"
    if validation_after_selection:
        return "FAIL_PROJECT_SELECTION_NOT_CONFIRMED", "Validation popup after project selection Next"
    if next_pressed_count < 3:
        return "FAIL_TEMPLATE_SCREEN_NOT_FOUND", "Third Next after project select not confirmed"
    if not project_row_selected:
        return "FAIL_PROJECT_SELECTION_NOT_CONFIRMED", "Project row not confirmed selected"
    if not dialog_closed and not cleanup_success:
        return "MANUAL_REVIEW_CANNOT_CONFIRM", "Export wizard not closed safely after discovery"
    if template_screen_detected and (dialog_closed or cleanup_success):
        return "PASS_TEMPLATE_SCREEN_DISCOVERY", "Template screen discovered; wizard closed safely"
    if template_partial and (dialog_closed or cleanup_success):
        return (
            "PASS_TEMPLATE_SCREEN_DISCOVERY_PARTIAL",
            "Partial template screen evidence; wizard closed safely",
        )
    return "FAIL_TEMPLATE_SCREEN_NOT_FOUND", "Template screen not confirmed after project selection Next"


def finish_result(evidence: RunEvidence, project_name: str, status: str, reason: str, **fields: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "run_id": evidence.run_id,
        "module": MODULE_NAME,
        "project_name": project_name,
        "status": status,
        "reason": reason,
        "screenshots": evidence.screenshots,
        "ocr_files": evidence.ocr_files,
        "classification_files": evidence.classification_files,
        "popup_files": evidence.popup_files,
        "discovery_files": evidence.discovery_files,
        "steps": evidence.steps,
    }
    result.update(fields)
    write_json(evidence.folder / "result.json", result)
    write_report(evidence, result)
    return result


def write_report(evidence: RunEvidence, result: Dict[str, Any]) -> None:
    lines = [
        "# M23 Discover Template Screen Discovery Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Project row selected: {result.get('project_row_selected')}",
        f"- Next pressed total: {result.get('next_pressed_count_total')}",
        f"- Template screen detected: {result.get('template_screen_detected')}",
        f"- Template evidence words: {result.get('template_evidence_words', [])}",
        f"- Template names detected: {result.get('template_names_detected', [])}",
        f"- Modify Template button: {result.get('modify_template_button_detected')}",
        f"- Add button: {result.get('add_button_detected')}",
        f"- Delete button: {result.get('delete_button_detected')}",
        f"- Cleanup success: {result.get('cleanup_success')}",
        f"- Export dialog closed: {result.get('export_dialog_closed')}",
        "",
        "## Final decision",
        result["status"],
    ]
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _base_result_fields(
    *,
    window_title_before: str,
    screen_state_before: str,
    preflight_meta: Dict[str, Any],
    ctx: Dict[str, Any],
    cleanup: Dict[str, Any],
    export_snap_before: set,
) -> Dict[str, Any]:
    finish_pressed = finish_pressed_in_steps(ctx.get("steps_ref", []))
    browse_clicked, path_typed = browse_or_path_in_steps(ctx.get("steps_ref", []))
    add_clicked, delete_clicked, template_modified, template_selected = template_action_in_steps(
        ctx.get("steps_ref", [])
    )
    next_count = count_next_presses(ctx.get("steps_ref", []))
    file_created = export_file_created(export_snap_before, snapshot_export_files())
    closed = bool(cleanup.get("cleanup_success")) or bool(ctx.get("export_dialog_closed"))
    tmpl = ctx.get("template_evidence") or {}
    return {
        "window_title_before": window_title_before,
        "screen_state_before": screen_state_before,
        "screen_state_after": ctx.get("screen_state_after", "unknown"),
        "project_restore_attempts": preflight_meta.get("project_restore_attempts", 0),
        "project_restore_success": preflight_meta.get("project_restore_success", False),
        "export_wizard_detected": ctx.get("wizard_detected", False),
        "spreadsheet_option_detected": ctx.get("spreadsheet_detected", False),
        "spreadsheet_option_selected": ctx.get("spreadsheet_selected", False),
        "export_type_screen_detected": ctx.get("export_type_screen_ok", False),
        "activities_export_type_detected": bool(ctx.get("activities_selected")),
        "activities_export_type_selected": bool(ctx.get("activities_selected")),
        "projects_to_export_screen_detected": bool(ctx.get("projects_to_export_screen_detected")),
        "project_001_talison_detected": bool(ctx.get("project_001_talison_detected")),
        "project_row_selected": bool(ctx.get("project_row_selected")),
        "template_screen_detected": bool(tmpl.get("template_screen_detected") or ctx.get("template_screen_detected")),
        "template_evidence_words": tmpl.get("template_evidence_words", ctx.get("template_evidence_words", [])),
        "template_names_detected": tmpl.get("template_names_detected", ctx.get("template_names_detected", [])),
        "modify_template_button_detected": bool(
            tmpl.get("modify_template_button_detected", ctx.get("modify_template_button_detected"))
        ),
        "add_button_detected": bool(tmpl.get("add_button_detected", ctx.get("add_button_detected"))),
        "delete_button_detected": bool(tmpl.get("delete_button_detected", ctx.get("delete_button_detected"))),
        "next_pressed_count_total": next_count,
        "template_selected": template_selected,
        "template_modified": template_modified,
        "add_clicked": add_clicked,
        "delete_clicked": delete_clicked,
        "browse_clicked": browse_clicked,
        "path_typed": path_typed,
        "finish_button_detected": bool(tmpl.get("finish_button_detected", ctx.get("finish_button_detected"))),
        "finish_pressed": finish_pressed,
        "export_file_created": file_created,
        "export_dialog_closed": closed,
        "close_method_used": ctx.get("close_method_used") or cleanup.get("cleanup_method", ""),
        "cleanup_attempted": cleanup.get("cleanup_attempted", False),
        "cleanup_success": cleanup.get("cleanup_success", False),
        "cleanup_method": cleanup.get("cleanup_method", ""),
        "cleanup_reason": cleanup.get("cleanup_reason", ""),
        "manual_review_required": False,
        "error": None,
        "rect_before_clip": preflight_meta.get("rect_before_clip"),
        "rect_after_clip": preflight_meta.get("rect_after_clip"),
    }


def run_m23(
    project_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    skip_project_restore: bool = False,
    force_project_row_not_found: bool = False,
    force_template_screen_not_found: bool = False,
) -> Dict[str, Any]:
    evidence = evidence or build_evidence(run_id or new_run_id())
    config = load_json(CONFIG_PATH)
    screen_rule = load_json(SCREEN_RULE_PATH)
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))

    project_name = (project_name or "").strip()
    if not project_name:
        return finish_result(evidence, "", "FAIL_PROJECT_NAME_EMPTY", "project_name is empty")

    if not is_easyocr_available():
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            "EasyOCR not installed",
            error="pip install easyocr",
        )

    timer_start = time.monotonic()
    export_snap_before = snapshot_export_files()
    window_title_before = ""
    screen_state_before = ""
    preflight_meta: Dict[str, Any] = {}
    ctx: Dict[str, Any] = {}
    p6_rect: Optional[P6Rect] = None
    cleanup: Dict[str, Any] = {
        "cleanup_attempted": False,
        "cleanup_success": False,
        "cleanup_method": "",
        "cleanup_reason": "",
    }
    wizard_flow_started = False
    screen_state_after = "unknown"
    window_title_after = ""

    def timed_out() -> bool:
        return (time.monotonic() - timer_start) > M23_MAX_RUN_SEC

    def wizard_needs_cleanup() -> bool:
        return wizard_flow_started or count_next_presses(evidence.steps) > 0 or bool(ctx.get("wizard_detected"))

    def do_safe_cleanup() -> None:
        nonlocal p6_rect, cleanup, screen_state_after, window_title_after
        if cleanup.get("cleanup_attempted"):
            return
        if p6_rect is None:
            prep = prepare_p6_for_test(p6_keyword)
            if prep.get("success") and prep.get("rect"):
                p6_rect = prep["rect"]
            else:
                cleanup = {
                    "cleanup_attempted": True,
                    "cleanup_success": False,
                    "cleanup_method": "",
                    "cleanup_reason": "P6 rect unavailable for cleanup",
                }
                save_discovery(evidence, "cleanup_evidence.json", cleanup)
                return
        cleanup, p6_rect = safe_cancel_export_wizard_if_open(
            evidence,
            p6_keyword,
            p6_rect,
            config,
            screen_rule,
            min_confidence,
            cached_bounds=ctx.get("wizard_bounds"),
        )
        save_discovery(evidence, "cleanup_evidence.json", cleanup)
        after = capture_and_ocr_step(evidence, "10_after_cleanup", p6_rect, config, screen_rule)
        if after.get("ok"):
            screen_state_after = after.get("screen_state", "unknown")
        window_title_after = window_tools.get_window_state(p6_keyword).get("title") or ""

    m21_install_rect_clip_capture_patch()
    m22_install_pyautogui_guard(lambda: p6_rect, lambda: ctx.get("wizard_bounds"), evidence)
    try:
        if skip_project_restore:
            p6_rect, window_title_before, screen_state_before, dirty_meta, prep_err = (
                m21_dirty_start_preflight_once(
                    evidence, project_name, p6_keyword, config, screen_rule, min_confidence
                )
            )
            preflight_meta = {
                "project_restore_attempts": 0,
                "project_restore_success": False,
                **dirty_meta,
            }
        else:
            clean = ensure_clean_p6_for_m21_hard(project_name, f"{evidence.run_id}_m23_clean")
            preflight_meta = {
                "project_restore_attempts": len(clean.get("attempts", [])),
                "project_restore_success": bool(clean.get("ok")),
                "clean_restore_notes": clean.get("notes", []),
                "rect_before_clip": clean.get("rect_before_clip"),
                "rect_after_clip": clean.get("rect_after_clip"),
            }
            if not clean.get("ok"):
                do_safe_cleanup()
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_PROJECT_RESTORE_FAILED",
                    clean.get("reason", "Project restore failed"),
                    **_base_result_fields(
                        window_title_before=clean.get("window_title", ""),
                        screen_state_before=clean.get("screen_state", ""),
                        preflight_meta=preflight_meta,
                        ctx={"steps_ref": evidence.steps},
                        cleanup=cleanup,
                        export_snap_before=export_snap_before,
                    ),
                )
            p6_rect, window_title_before, screen_state_before, dirty_meta, prep_err = (
                m21_dirty_start_preflight_once(
                    evidence, project_name, p6_keyword, config, screen_rule, min_confidence
                )
            )
            preflight_meta.update(dirty_meta)
            preflight_meta["project_restore_success"] = True

        if prep_err:
            status = prep_err.get("status", "FAIL_P6_WINDOW_NOT_READY")
            if status == "FAIL_PROJECT_RESTORE_FAILED":
                status = "FAIL_PROJECT_RESTORE_FAILED"
            do_safe_cleanup()
            fields = _base_result_fields(
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                preflight_meta=preflight_meta,
                ctx={"steps_ref": evidence.steps},
                cleanup=cleanup,
                export_snap_before=export_snap_before,
            )
            fields["window_title_after"] = window_title_after
            return finish_result(evidence, project_name, status, prep_err.get("reason", ""), **fields)

        if timed_out():
            do_safe_cleanup()
            return finish_result(
                evidence,
                project_name,
                "FAIL_P6_WINDOW_NOT_READY",
                f"M23 exceeded {M23_MAX_RUN_SEC}s after preflight",
                **_base_result_fields(
                    window_title_before=window_title_before,
                    screen_state_before=screen_state_before,
                    preflight_meta=preflight_meta,
                    ctx={"steps_ref": evidence.steps},
                    cleanup=cleanup,
                    export_snap_before=export_snap_before,
                ),
            )

        timer_start = time.monotonic()
        assert p6_rect is not None
        m22_move_mouse_p6_neutral(p6_rect, evidence=evidence)

        p6_rect, ctx, path_err = m23_controlled_wizard_to_template_screen(
            evidence,
            p6_keyword,
            p6_rect,
            config,
            screen_rule,
            min_confidence,
            project_name,
            force_project_row_not_found=force_project_row_not_found,
            force_template_screen_not_found=force_template_screen_not_found,
        )
        wizard_flow_started = bool(ctx.get("wizard_detected")) or count_next_presses(evidence.steps) > 0
        ctx["steps_ref"] = evidence.steps

        if path_err:
            do_safe_cleanup()
            ctx["export_dialog_closed"] = bool(cleanup.get("cleanup_success"))
            ctx["close_method_used"] = cleanup.get("cleanup_method", "") if cleanup.get("cleanup_success") else ""
            window_title_after = window_tools.get_window_state(p6_keyword).get("title") or ""
            fields = _base_result_fields(
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                preflight_meta=preflight_meta,
                ctx=ctx,
                cleanup=cleanup,
                export_snap_before=export_snap_before,
            )
            fields["window_title_after"] = window_title_after
            if ctx.get("forced_hook_activation"):
                fields["forced_hook_activation"] = ctx["forced_hook_activation"]
            return finish_result(
                evidence,
                project_name,
                path_err["status"],
                path_err.get("reason", ""),
                **fields,
            )

        post_entries = ctx.get("post_project_selection_entries") or []
        post_blob = ctx.get("post_project_selection_blob") or ""
        validation_after = bool(ctx.get("validation_popup_detected_after_project_selection"))
        tmpl = ctx.get("template_evidence") or {}

        if validation_after:
            ctx["validation_popup_detected_after_project_selection"] = True
            m21_dismiss_projects_validation_popup(
                evidence,
                p6_rect,
                p6_keyword,
                config,
                screen_rule,
                min_confidence,
                post_entries,
            )

        buttons = detect_wizard_buttons(post_blob) if post_blob else {}
        ctx["finish_button_detected"] = buttons.get("finish_button_detected", False)
        evidence_words = find_export_evidence_words(post_blob) if post_blob else []
        closed, close_method, p6_rect = close_export_dialog(
            evidence, p6_keyword, p6_rect, config, screen_rule, post_entries, evidence_words
        )
        ctx["export_dialog_closed"] = closed
        ctx["close_method_used"] = close_method if closed else ""

        if not closed:
            do_safe_cleanup()
        else:
            cleanup = {
                "cleanup_attempted": True,
                "cleanup_success": True,
                "cleanup_method": close_method or "cancel_click",
                "cleanup_reason": "Primary close_export_dialog succeeded",
            }
            save_discovery(evidence, "cleanup_evidence.json", cleanup)

        after_close = capture_and_ocr_step(evidence, "09_after_close", p6_rect, config, screen_rule)
        blocking_after, _ = (
            detect_m16_blocking_popup(after_close.get("entries", []), min_confidence)
            if after_close.get("ok")
            else (False, "")
        )
        screen_state_after = (
            after_close.get("screen_state", screen_state_after) if after_close.get("ok") else screen_state_after
        )
        ctx["screen_state_after"] = screen_state_after
        window_title_after = window_tools.get_window_state(p6_keyword).get("title") or ""

        finish_pressed = finish_pressed_in_steps(evidence.steps)
        next_count = count_next_presses(evidence.steps)
        browse_clicked, path_typed = browse_or_path_in_steps(evidence.steps)
        add_clicked, delete_clicked, template_modified, template_selected = template_action_in_steps(evidence.steps)
        file_created = export_file_created(export_snap_before, snapshot_export_files())
        safe_steps, _ = unsafe_steps_detected(evidence.steps)

        status, reason = decide_status(
            wizard_detected=ctx.get("wizard_detected", False),
            spreadsheet_detected=ctx.get("spreadsheet_detected", False),
            export_type_screen_ok=ctx.get("export_type_screen_ok", False),
            activities_selected=bool(ctx.get("activities_selected")),
            projects_to_export_ok=bool(ctx.get("projects_to_export_screen_detected")),
            project_001_talison=bool(ctx.get("project_001_talison_detected")),
            project_row_selected=bool(ctx.get("project_row_selected")),
            project_selection_attempted=bool(ctx.get("project_selection_attempted")),
            next_pressed_count=next_count,
            template_screen_detected=bool(tmpl.get("template_screen_detected")),
            template_partial=bool(tmpl.get("template_screen_partial")),
            validation_after_selection=validation_after,
            dialog_closed=closed or bool(cleanup.get("cleanup_success")),
            file_created=file_created,
            finish_pressed=finish_pressed,
            blocking_after=blocking_after,
            browse_clicked=browse_clicked,
            path_typed=path_typed,
            template_modified=template_modified,
            template_selected=template_selected,
            add_clicked=add_clicked,
            delete_clicked=delete_clicked,
            cleanup_success=bool(cleanup.get("cleanup_success")),
        )
        if not safe_steps:
            status, reason = "MANUAL_REVIEW_UNSAFE_POPUP", "Unsafe step detected in M23 run"

        fields = _base_result_fields(
            window_title_before=window_title_before,
            screen_state_before=screen_state_before,
            preflight_meta=preflight_meta,
            ctx=ctx,
            cleanup=cleanup,
            export_snap_before=export_snap_before,
        )
        fields["window_title_after"] = window_title_after
        fields["run_duration_seconds"] = round(time.monotonic() - timer_start, 2)
        if ctx.get("forced_hook_activation"):
            fields["forced_hook_activation"] = ctx["forced_hook_activation"]
        return finish_result(evidence, project_name, status, reason, **fields)

    except M22FailSafeError as exc:
        if wizard_needs_cleanup():
            do_safe_cleanup()
        fields = _base_result_fields(
            window_title_before=window_title_before,
            screen_state_before=screen_state_before,
            preflight_meta=preflight_meta,
            ctx={**ctx, "steps_ref": evidence.steps},
            cleanup=cleanup,
            export_snap_before=export_snap_before,
        )
        fields["window_title_after"] = window_title_after
        fields.pop("error", None)
        fields["pyautogui_failsafe"] = True
        return finish_result(
            evidence,
            project_name,
            "SETUP_FAILURE_PYAUTOGUI_FAILSAFE",
            str(exc),
            **fields,
            error=str(exc),
        )
    except M22UnsafeClickError as exc:
        if wizard_needs_cleanup():
            do_safe_cleanup()
        fields = _base_result_fields(
            window_title_before=window_title_before,
            screen_state_before=screen_state_before,
            preflight_meta=preflight_meta,
            ctx={**ctx, "steps_ref": evidence.steps},
            cleanup=cleanup,
            export_snap_before=export_snap_before,
        )
        fields["window_title_after"] = window_title_after
        fields.pop("error", None)
        return finish_result(
            evidence,
            project_name,
            "SETUP_FAILURE_UNSAFE_CLICK_POINT",
            str(exc),
            **fields,
            manual_review_required=True,
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        if wizard_needs_cleanup():
            do_safe_cleanup()
        fields = _base_result_fields(
            window_title_before=window_title_before,
            screen_state_before=screen_state_before,
            preflight_meta=preflight_meta,
            ctx={**ctx, "steps_ref": evidence.steps},
            cleanup=cleanup,
            export_snap_before=export_snap_before,
        )
        fields["window_title_after"] = window_title_after
        fields.pop("error", None)
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            str(exc),
            **fields,
            error=traceback.format_exc(),
        )
    finally:
        if wizard_needs_cleanup() and not cleanup.get("cleanup_attempted"):
            do_safe_cleanup()
        m22_remove_pyautogui_guard()
        m21_remove_rect_clip_capture_patch()


def main() -> int:
    parser = argparse.ArgumentParser(description="M23 discover template screen discovery only")
    parser.add_argument("--project", required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    result = run_m23(args.project, run_id=args.run_id)
    print(f"M23 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in PASS_STATUSES:
        return 0
    if result["status"].startswith("MANUAL_REVIEW"):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
