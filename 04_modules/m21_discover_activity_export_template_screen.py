"""
M21 — Discover Activity Export Template Screen.

Spreadsheet -> Next -> Export Type -> Activities -> Next -> Projects-to-export ->
Next once -> inspect template/file/next wizard screen, then cancel safely.
Discovery only; no Finish, template edit, path entry, or export file creation.
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
from accessibility.hand import keyboard_tools  # noqa: E402
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
    M21_MAX_RUN_SEC,
    M21_MAX_WAIT_SEC,
    count_next_after_marker,
    count_next_presses,
    m21_controlled_wizard_to_post_projects_next,
    m21_dirty_start_preflight_once,
    m21_dismiss_projects_validation_popup,
    m21_install_rect_clip_capture_patch,
    m21_preflight_with_restore_loop,
    m21_remove_rect_clip_capture_patch,
    safe_cancel_export_wizard_if_open,
    unsafe_steps_detected,
)

MODULE_NAME = "m21_discover_activity_export_template_screen"

PASS_STATUSES = frozenset(
    {
        "PASS_TEMPLATE_SCREEN_DISCOVERY",
        "PASS_TEMPLATE_SCREEN_DISCOVERY_PARTIAL",
        "PASS_POST_PROJECTS_SCREEN_DISCOVERY",
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


def template_modified_in_steps(steps: List[str]) -> bool:
    for step in steps:
        lowered = step.lower()
        if any(m in lowered for m in ("modify template", "delete template", "add template", "select template:")):
            return True
    return False


def decide_status(
    *,
    wizard_detected: bool,
    spreadsheet_detected: bool,
    export_type_screen_ok: bool,
    activities_selected: bool,
    projects_to_export_ok: bool,
    project_001_talison: bool,
    next_pressed_count: int,
    post_screen_ok: bool,
    post_screen_type: str,
    post_class_status: str,
    post_class_reason: str,
    dialog_closed: bool,
    file_created: bool,
    finish_pressed: bool,
    blocking_after: bool,
    browse_clicked: bool,
    path_typed: bool,
    template_modified: bool,
    template_selected: bool,
    next_after_post: bool,
    cleanup_success: bool,
) -> Tuple[str, str]:
    if file_created or finish_pressed:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Export file may have been created or Finish pressed"
    if browse_clicked or path_typed or template_modified or template_selected:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Unsafe wizard action detected (Browse/path/template change)"
    if blocking_after:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Blocking popup after close attempt"
    if next_after_post:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Next pressed after post-Projects screen inspection"
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
        return "FAIL_PROJECTS_TO_EXPORT_SCREEN_NOT_FOUND", "001/Talison 1275 not visible on Projects-to-export screen"
    if next_pressed_count < 3:
        return "FAIL_POST_PROJECTS_NEXT_SCREEN_NOT_FOUND", "Third Next from Projects-to-export not confirmed"
    if post_class_status == "FAIL_WIZARD_CLOSED_UNEXPECTEDLY":
        return post_class_status, post_class_reason or "Wizard closed before intentional Cancel"
    if post_class_status == "FAIL_P6_WINDOW_NOT_READY":
        return post_class_status, post_class_reason or "P6 screenshot could not be obtained after third Next"
    if post_class_status == "FAIL_POST_PROJECTS_NEXT_SCREEN_NOT_FOUND":
        if cleanup_success:
            return post_class_status, post_class_reason or "Post-Projects screen not confirmed; wizard cleaned up"
        return post_class_status, post_class_reason or "Post-Projects next screen not confirmed"
    if not post_screen_ok:
        return "FAIL_POST_PROJECTS_NEXT_SCREEN_NOT_FOUND", "Post-Projects next screen not confirmed"
    if not dialog_closed and not cleanup_success:
        return "MANUAL_REVIEW_CANNOT_CONFIRM", "Export wizard not closed safely after discovery"

    if post_class_status in PASS_STATUSES:
        return post_class_status, post_class_reason or f"Post-Projects {post_screen_type} screen discovered; wizard closed"

    if post_screen_type == "template" and (dialog_closed or cleanup_success):
        return "PASS_TEMPLATE_SCREEN_DISCOVERY", "Template screen discovered after Projects-to-export Next; wizard closed"
    if post_screen_type in ("file_path", "projects_to_export_still", "projects_validation_popup") and (dialog_closed or cleanup_success):
        return (
            "PASS_POST_PROJECTS_SCREEN_DISCOVERY",
            f"Post-Projects {post_screen_type} screen discovered; wizard closed",
        )
    if post_screen_type == "generic_wizard" and (dialog_closed or cleanup_success):
        return (
            "PASS_TEMPLATE_SCREEN_DISCOVERY_PARTIAL",
            "Partial post-Projects wizard discovery; wizard closed safely",
        )

    return "FAIL_POST_PROJECTS_NEXT_SCREEN_NOT_FOUND", "Post-Projects evidence or safe close not confirmed"


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
        "# M21 Discover Activity Export Template Screen Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Spreadsheet selected: {result.get('spreadsheet_option_selected')}",
        f"- Export Type screen: {result.get('export_type_screen_detected')}",
        f"- Activities selected: {result.get('activities_export_type_selected')}",
        f"- Projects-to-export screen: {result.get('projects_to_export_screen_detected')}",
        f"- 001 Talison detected: {result.get('project_001_talison_detected')}",
        f"- Next pressed total: {result.get('next_pressed_count_total')}",
        f"- Post-Projects screen type: {result.get('post_projects_screen_type', '')}",
        f"- Post-Projects evidence: {result.get('post_projects_evidence_words', [])}",
        f"- Fallback OCR used: {result.get('fallback_ocr_used')}",
        f"- Project restore attempts: {result.get('project_restore_attempts')}",
        f"- Project restore success: {result.get('project_restore_success')}",
        f"- Validation popup detected: {result.get('validation_popup_detected')}",
        f"- Validation popup dismissed: {result.get('validation_popup_dismissed')}",
        f"- Rect clipped: {result.get('rect_after_clip', {}).get('clipped') if isinstance(result.get('rect_after_clip'), dict) else result.get('rect_after_clip')}",
        f"- Cleanup attempted: {result.get('cleanup_attempted')}",
        f"- Cleanup success: {result.get('cleanup_success')}",
        f"- Cleanup method: {result.get('cleanup_method')}",
        f"- Export dialog closed: {result.get('export_dialog_closed')}",
        f"- Export file created: {result.get('export_file_created')}",
        f"- Final screen: {result.get('screen_state_after', '')}",
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
    template_modified = template_modified_in_steps(ctx.get("steps_ref", []))
    next_count = count_next_presses(ctx.get("steps_ref", []))
    file_created = export_file_created(export_snap_before, snapshot_export_files())
    closed = bool(cleanup.get("cleanup_success")) or bool(ctx.get("export_dialog_closed"))
    return {
        "window_title_before": window_title_before,
        "screen_state_before": screen_state_before,
        "export_wizard_detected": ctx.get("wizard_detected", False),
        "spreadsheet_option_detected": ctx.get("spreadsheet_detected", False),
        "spreadsheet_option_selected": ctx.get("spreadsheet_selected", False),
        "export_type_screen_detected": ctx.get("export_type_screen_ok", False),
        "activities_export_type_detected": bool(ctx.get("activities_selected")),
        "activities_export_type_selected": bool(ctx.get("activities_selected")),
        "projects_to_export_screen_detected": bool(ctx.get("projects_to_export_screen_detected")),
        "project_001_talison_detected": bool(ctx.get("project_001_talison_detected")),
        "next_pressed_count_total": next_count,
        "post_projects_next_screen_detected": bool(ctx.get("post_projects_screen_ok")),
        "post_projects_screen_type": ctx.get("post_projects_screen_type", ""),
        "post_projects_evidence_words": ctx.get("post_projects_evidence_words", []),
        "fallback_ocr_used": ctx.get("fallback_ocr_used"),
        "template_screen_detected": bool(ctx.get("template_screen_detected")),
        "template_selected": template_modified,
        "template_modified": template_modified,
        "browse_clicked": browse_clicked,
        "path_typed": path_typed,
        "finish_pressed": finish_pressed,
        "export_file_created": file_created,
        "export_dialog_closed": closed,
        "close_method_used": ctx.get("close_method_used") or cleanup.get("cleanup_method", ""),
        "cleanup_attempted": cleanup.get("cleanup_attempted", False),
        "cleanup_success": cleanup.get("cleanup_success", False),
        "cleanup_method": cleanup.get("cleanup_method", ""),
        "cleanup_reason": cleanup.get("cleanup_reason", ""),
        "project_restore_attempts": preflight_meta.get("project_restore_attempts", 0),
        "project_restore_success": preflight_meta.get("project_restore_success", False),
        "rect_before_clip": preflight_meta.get("rect_before_clip"),
        "rect_after_clip": preflight_meta.get("rect_after_clip"),
        "validation_popup_detected": bool(ctx.get("validation_popup_detected")),
        "validation_popup_text": ctx.get("validation_popup_text", ""),
        "validation_popup_dismissed": bool(ctx.get("validation_popup_dismissed")),
        "manual_review_required": False,
        "error": None,
        **{k: v for k, v in preflight_meta.items() if k not in ("pollution_detected", "pollution_recovered")},
    }


def run_m21(
    project_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    force_post_projects_next_screen_not_found_after_third_next: bool = False,
    force_projects_to_export_screen_not_found: bool = False,
    force_projects_export_blocked_after_third_next: bool = False,
    skip_project_restore: bool = False,
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
        return (time.monotonic() - timer_start) > M21_MAX_RUN_SEC

    def wizard_needs_cleanup() -> bool:
        return wizard_flow_started or count_next_presses(evidence.steps) > 0 or bool(
            ctx.get("wizard_detected")
        )

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
    if force_projects_to_export_screen_not_found:
        evidence.steps.append("Hook: force_projects_to_export_screen_not_found (armed)")
    if force_projects_export_blocked_after_third_next:
        evidence.steps.append("Hook: force_projects_export_blocked_after_third_next (armed)")
    if force_post_projects_next_screen_not_found_after_third_next:
        evidence.steps.append("Hook: force_post_projects_next_screen_not_found_after_third_next (armed)")

    status = "ERROR"
    reason = "Unhandled M21 run"
    try:
        if timed_out():
            status, reason = "FAIL_TIMEOUT_CONTROLLED", f"M21 exceeded {M21_MAX_RUN_SEC}s before start"
            return finish_result(
                evidence,
                project_name,
                status,
                reason,
                cleanup_attempted=False,
                cleanup_success=False,
                cleanup_method="",
                cleanup_reason="timeout before wizard flow",
            )

        if skip_project_restore:
            p6_rect, window_title_before, screen_state_before, preflight_meta, prep_err = (
                m21_dirty_start_preflight_once(
                    evidence, project_name, p6_keyword, config, screen_rule, min_confidence
                )
            )
            preflight_meta.setdefault("project_restore_attempts", 0)
            preflight_meta.setdefault("project_restore_success", False)
        else:
            p6_rect, window_title_before, screen_state_before, preflight_meta, prep_err = (
                m21_preflight_with_restore_loop(
                    evidence, project_name, p6_keyword, config, screen_rule, min_confidence
                )
            )
        if prep_err:
            do_safe_cleanup()
            fields = _base_result_fields(
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                preflight_meta=preflight_meta,
                ctx={"steps_ref": evidence.steps},
                cleanup=cleanup,
                export_snap_before=export_snap_before,
            )
            fields["screen_state_after"] = screen_state_after
            fields["window_title_after"] = window_title_after
            return finish_result(
                evidence,
                project_name,
                prep_err["status"],
                prep_err.get("reason", ""),
                **fields,
                **{k: v for k, v in prep_err.items() if k not in ("status", "reason")},
            )

        if timed_out():
            status, reason = "FAIL_TIMEOUT_CONTROLLED", f"M21 exceeded {M21_MAX_RUN_SEC}s after preflight"
            do_safe_cleanup()
            return finish_result(evidence, project_name, status, reason, **cleanup)

        assert p6_rect is not None
        p6_rect, ctx, path_err = m21_controlled_wizard_to_post_projects_next(
            evidence,
            p6_keyword,
            p6_rect,
            config,
            screen_rule,
            min_confidence,
            project_name,
            force_post_projects_next_screen_not_found_after_third_next=(
                force_post_projects_next_screen_not_found_after_third_next
            ),
            force_projects_to_export_screen_not_found=force_projects_to_export_screen_not_found,
            force_projects_export_blocked_after_third_next=force_projects_export_blocked_after_third_next,
        )
        wizard_flow_started = bool(ctx.get("wizard_detected")) or count_next_presses(evidence.steps) > 0
        ctx["steps_ref"] = evidence.steps

        if timed_out():
            do_safe_cleanup()
            fields = _base_result_fields(
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                preflight_meta=preflight_meta,
                ctx=ctx,
                cleanup=cleanup,
                export_snap_before=export_snap_before,
            )
            fields["screen_state_after"] = screen_state_after
            fields["window_title_after"] = window_title_after
            return finish_result(
                evidence,
                project_name,
                "FAIL_TIMEOUT_CONTROLLED",
                f"M21 exceeded {M21_MAX_RUN_SEC}s during wizard flow",
                **fields,
            )

        if path_err:
            do_safe_cleanup()
            fields = _base_result_fields(
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                preflight_meta=preflight_meta,
                ctx=ctx,
                cleanup=cleanup,
                export_snap_before=export_snap_before,
            )
            fields["screen_state_after"] = screen_state_after
            fields["window_title_after"] = window_title_after
            return finish_result(
                evidence,
                project_name,
                path_err["status"],
                path_err.get("reason", ""),
                **fields,
            )

        post_entries = ctx.get("post_projects_entries") or ctx.get("post_activities_entries", [])
        post_blob = ctx.get("post_projects_blob") or ""
        if post_entries and not post_blob:
            from eye.ocr import collect_text_blob  # noqa: WPS433

            post_blob = collect_text_blob(post_entries, min_confidence)
            ctx["post_projects_blob"] = post_blob

        post_words = ctx.get("post_projects_evidence_words") or []
        post_type = ctx.get("post_projects_screen_type", "unknown")
        post_ok = bool(ctx.get("post_projects_screen_ok", False))
        post_class_status = ctx.get("post_projects_classification_status", "")
        post_class_reason = ctx.get("post_projects_classification_reason", "")
        validation_popup_detected = post_type == "projects_validation_popup" or bool(
            ctx.get("validation_popup_detected")
        )
        validation_popup_dismissed = False

        if post_type == "projects_validation_popup":
            ctx["validation_popup_detected"] = True
            ctx["validation_popup_text"] = ctx.get("validation_popup_text") or ctx.get("raw_ocr_text", "")[:200]
            dismissed, dismiss_method, p6_rect = m21_dismiss_projects_validation_popup(
                evidence,
                p6_rect,
                p6_keyword,
                config,
                screen_rule,
                min_confidence,
                post_entries,
            )
            validation_popup_dismissed = dismissed
            ctx["validation_popup_dismissed"] = dismissed
            ctx["validation_popup_dismiss_method"] = dismiss_method
            dismiss_cap = capture_and_ocr_step(
                evidence, "08b_after_validation_dismiss", p6_rect, config, screen_rule
            )
            if dismiss_cap.get("ok"):
                post_entries = dismiss_cap.get("entries", post_entries)
                from eye.ocr import collect_text_blob  # noqa: WPS433

                post_blob = collect_text_blob(post_entries, min_confidence)
                ctx["post_projects_entries"] = post_entries
                ctx["post_projects_blob"] = post_blob

        buttons = detect_wizard_buttons(post_blob) if post_blob else {}
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
        screen_state_after = after_close.get("screen_state", screen_state_after) if after_close.get("ok") else screen_state_after
        window_title_after = window_tools.get_window_state(p6_keyword).get("title") or ""

        finish_pressed = finish_pressed_in_steps(evidence.steps)
        next_count = count_next_presses(evidence.steps)
        browse_clicked, path_typed = browse_or_path_in_steps(evidence.steps)
        template_modified = template_modified_in_steps(evidence.steps)
        file_created = export_file_created(export_snap_before, snapshot_export_files())
        safe_steps, _ = unsafe_steps_detected(evidence.steps)

        status, reason = decide_status(
            wizard_detected=ctx.get("wizard_detected", False),
            spreadsheet_detected=ctx.get("spreadsheet_detected", False),
            export_type_screen_ok=ctx.get("export_type_screen_ok", False),
            activities_selected=bool(ctx.get("activities_selected")),
            projects_to_export_ok=bool(ctx.get("projects_to_export_screen_detected")),
            project_001_talison=bool(ctx.get("project_001_talison_detected")),
            next_pressed_count=next_count,
            post_screen_ok=post_ok,
            post_screen_type=post_type,
            post_class_status=post_class_status,
            post_class_reason=post_class_reason,
            dialog_closed=closed or bool(cleanup.get("cleanup_success")),
            file_created=file_created,
            finish_pressed=finish_pressed,
            blocking_after=blocking_after,
            browse_clicked=browse_clicked,
            path_typed=path_typed,
            template_modified=template_modified,
            template_selected=template_modified,
            next_after_post=count_next_after_marker(evidence.steps, "from projects-to-export") > 1,
            cleanup_success=bool(cleanup.get("cleanup_success")),
        )
        if not safe_steps:
            status, reason = "MANUAL_REVIEW_UNSAFE_POPUP", "Unsafe step detected in M21 run"

        fields = _base_result_fields(
            window_title_before=window_title_before,
            screen_state_before=screen_state_before,
            preflight_meta=preflight_meta,
            ctx=ctx,
            cleanup=cleanup,
            export_snap_before=export_snap_before,
        )
        fields.update(
            {
                "window_title_after": window_title_after,
                "screen_state_after": screen_state_after,
                "finish_button_detected": buttons.get("finish_button_detected", False),
                "manual_review_required": status.startswith("MANUAL_REVIEW"),
                "forced_hook_activation": ctx.get("forced_hook_activation"),
                "run_duration_seconds": round(time.monotonic() - timer_start, 2),
            }
        )
        return finish_result(evidence, project_name, status, reason, **fields)

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
        fields["screen_state_after"] = screen_state_after
        fields["window_title_after"] = window_title_after
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
        m21_remove_rect_clip_capture_patch()


def main() -> int:
    parser = argparse.ArgumentParser(description="M21 activity export template screen discovery")
    parser.add_argument("--project", required=True)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--force-post-projects-next-screen-not-found-after-third-next",
        action="store_true",
    )
    parser.add_argument("--force-projects-to-export-screen-not-found", action="store_true")
    args = parser.parse_args()
    result = run_m21(
        args.project,
        run_id=args.run_id,
        force_post_projects_next_screen_not_found_after_third_next=(
            args.force_post_projects_next_screen_not_found_after_third_next
        ),
        force_projects_to_export_screen_not_found=args.force_projects_to_export_screen_not_found,
    )
    print(f"Status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Evidence: {result.get('run_id', '')}")
    return 0 if result["status"] in PASS_STATUSES else 1


if __name__ == "__main__":
    raise SystemExit(main())
